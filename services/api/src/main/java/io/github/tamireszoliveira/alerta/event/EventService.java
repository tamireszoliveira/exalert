package io.github.tamireszoliveira.alerta.event;

import java.time.Clock;
import java.time.Instant;
import java.util.List;
import java.util.Objects;
import java.util.Optional;
import java.util.UUID;

import org.springframework.data.domain.Limit;
import org.springframework.http.HttpStatus;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;
import org.springframework.web.server.ResponseStatusException;

import io.github.tamireszoliveira.alerta.alert.Alert;
import io.github.tamireszoliveira.alerta.alert.AlertRepository;
import io.github.tamireszoliveira.alerta.config.AlertaProperties;

@Service
@Transactional
public class EventService {

    /** Resultado de gravar um evento: {@code created} falso quando o id já existia (reenvio). */
    public record Recorded(EventResponse event, boolean created) {
    }

    private final AlertEventRepository events;
    private final AlertRepository alerts;
    private final AlertaProperties properties;
    private final Clock clock;

    EventService(AlertEventRepository events, AlertRepository alerts, AlertaProperties properties, Clock clock) {
        this.events = events;
        this.alerts = alerts;
        this.properties = properties;
        this.clock = clock;
    }

    /**
     * Grava o evento e, se for disparo ou rearme, muda o estado do alerta na mesma transação.
     *
     * <p>404: o alerta foi apagado. 409: o alerta mudou depois que o checker o leu (versão
     * diferente, estado incompatível ou desativado). Nos dois casos o checker descarta o evento.
     */
    public Recorded record(EventRequest request) {
        Optional<AlertEvent> existing = events.findById(request.id());
        if (existing.isPresent()) {
            return new Recorded(EventResponse.of(existing.get()), false);
        }
        if (request.changesAlertState()) {
            if (request.alertId() == null || request.alertVersion() == null) {
                throw new ResponseStatusException(HttpStatus.BAD_REQUEST, "FIRED e REARMED exigem alertId e alertVersion");
            }
            Alert alert = alerts.findById(request.alertId())
                    .orElseThrow(() -> new ResponseStatusException(HttpStatus.NOT_FOUND, "alerta não existe mais"));
            if (!Objects.equals(alert.getVersion(), request.alertVersion()) || !alert.isEnabled()) {
                throw new ResponseStatusException(HttpStatus.CONFLICT, "o alerta mudou depois da leitura");
            }
            boolean applied = request.type() == EventType.FIRED ? alert.fire(request.occurredAt())
                    : alert.rearm(request.occurredAt());
            if (!applied) {
                throw new ResponseStatusException(HttpStatus.CONFLICT, "estado do alerta não permite " + request.type());
            }
        }
        AlertEvent saved = events.save(new AlertEvent(request, clock.instant()));
        return new Recorded(EventResponse.of(saved), true);
    }

    @Transactional(readOnly = true)
    public List<PendingEvent> pending() {
        Instant since = clock.instant().minus(properties.pendingWindow());
        return events.findByPushTrueAndDeliveredAtIsNullAndOccurredAtAfterOrderByOccurredAtAsc(since).stream()
                .map(PendingEvent::of)
                .toList();
    }

    public void markDelivered(UUID id) {
        AlertEvent event = events.findById(id)
                .orElseThrow(() -> new ResponseStatusException(HttpStatus.NOT_FOUND, "evento não encontrado"));
        event.markDelivered(clock.instant());
    }

    @Transactional(readOnly = true)
    public List<EventResponse> recent(int limit) {
        return events.findAllByOrderByOccurredAtDesc(Limit.of(limit)).stream().map(EventResponse::of).toList();
    }
}
