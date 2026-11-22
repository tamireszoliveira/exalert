package io.github.tamireszoliveira.alerta.event;

import java.math.BigDecimal;
import java.time.Instant;
import java.util.UUID;

import jakarta.validation.constraints.NotBlank;
import jakarta.validation.constraints.NotNull;
import jakarta.validation.constraints.Size;

import io.github.tamireszoliveira.alerta.alert.Direction;

/**
 * Evento enviado pelo checker em POST /internal/events.
 *
 * @param alertVersion versão do alerta que o checker leu; se mudou desde então, a API responde 409
 * @param push se o evento deve virar notificação (entra no outbox)
 */
public record EventRequest(
        @NotNull UUID id,
        @NotNull EventType type,
        @NotBlank @Size(max = 20) String asset,
        UUID alertId,
        Long alertVersion,
        Direction direction,
        BigDecimal price,
        Instant quotedAt,
        @NotNull Instant occurredAt,
        @NotBlank @Size(max = 200) String title,
        @NotBlank @Size(max = 500) String message,
        boolean push) {

    boolean changesAlertState() {
        return type == EventType.FIRED || type == EventType.REARMED;
    }
}
