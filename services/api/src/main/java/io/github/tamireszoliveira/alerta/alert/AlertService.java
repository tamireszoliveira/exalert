package io.github.tamireszoliveira.alerta.alert;

import java.math.BigDecimal;
import java.time.Clock;
import java.util.List;
import java.util.Map;
import java.util.UUID;

import org.springframework.http.HttpStatus;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;
import org.springframework.web.server.ResponseStatusException;

import io.github.tamireszoliveira.alerta.common.Assets;
import io.github.tamireszoliveira.alerta.quote.LatestQuote;
import io.github.tamireszoliveira.alerta.quote.QuoteRepository;

@Service
@Transactional
public class AlertService {

    static final BigDecimal DEFAULT_REARM_PCT = new BigDecimal("0.5");

    private final AlertRepository alerts;
    private final QuoteRepository quotes;
    private final Clock clock;

    AlertService(AlertRepository alerts, QuoteRepository quotes, Clock clock) {
        this.alerts = alerts;
        this.quotes = quotes;
        this.clock = clock;
    }

    @Transactional(readOnly = true)
    public List<AlertResponse> list() {
        List<Alert> all = alerts.findAllByOrderByCreatedAtAsc();
        Map<String, LatestQuote> latest = quotes.latest(all.stream().map(Alert::getAsset).distinct().toList());
        return all.stream().map(alert -> AlertResponse.of(alert, latest.get(alert.getAsset()))).toList();
    }

    @Transactional(readOnly = true)
    public AlertResponse get(UUID id) {
        Alert alert = find(id);
        return AlertResponse.of(alert, quotes.latest(List.of(alert.getAsset())).get(alert.getAsset()));
    }

    @Transactional(readOnly = true)
    public List<CheckerAlert> enabledForChecker() {
        return alerts.findByEnabledTrueOrderByCreatedAtAsc().stream().map(CheckerAlert::of).toList();
    }

    public AlertResponse create(AlertRequest request) {
        Alert alert = new Alert(Assets.normalize(request.asset()), cleanName(request.name()), request.direction(),
                request.threshold(), rearmPct(request), clock.instant());
        return AlertResponse.of(alerts.saveAndFlush(alert), null);
    }

    public AlertResponse update(UUID id, AlertRequest request) {
        Alert alert = find(id);
        boolean enabled = request.enabled() == null ? alert.isEnabled() : request.enabled();
        alert.update(Assets.normalize(request.asset()), cleanName(request.name()), request.direction(),
                request.threshold(), rearmPct(request), enabled, clock.instant());
        alerts.saveAndFlush(alert); // flush agora: a resposta já sai com a versão nova
        return AlertResponse.of(alert, quotes.latest(List.of(alert.getAsset())).get(alert.getAsset()));
    }

    public void delete(UUID id) {
        alerts.delete(find(id));
    }

    Alert find(UUID id) {
        return alerts.findById(id).orElseThrow(() -> new ResponseStatusException(HttpStatus.NOT_FOUND, "alerta não encontrado"));
    }

    private static BigDecimal rearmPct(AlertRequest request) {
        return request.rearmPct() == null ? DEFAULT_REARM_PCT : request.rearmPct();
    }

    private static String cleanName(String name) {
        return name == null || name.isBlank() ? null : name.strip();
    }
}
