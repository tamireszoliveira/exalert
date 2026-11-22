package io.github.tamireszoliveira.alerta.alert;

import java.math.BigDecimal;
import java.time.Instant;
import java.util.UUID;

import io.github.tamireszoliveira.alerta.quote.LatestQuote;

/** Alerta como o app vê: inclui a última cotação conhecida do ativo (ou null). */
public record AlertResponse(
        UUID id,
        String asset,
        String name,
        Direction direction,
        BigDecimal threshold,
        BigDecimal rearmPct,
        boolean enabled,
        AlertState state,
        Instant firedAt,
        Instant createdAt,
        Instant updatedAt,
        long version,
        LatestQuote lastQuote) {

    static AlertResponse of(Alert alert, LatestQuote lastQuote) {
        return new AlertResponse(alert.getId(), alert.getAsset(), alert.getName(), alert.getDirection(),
                alert.getThreshold(), alert.getRearmPct(), alert.isEnabled(), alert.getState(), alert.getFiredAt(),
                alert.getCreatedAt(), alert.getUpdatedAt(), versionOf(alert), lastQuote);
    }

    static long versionOf(Alert alert) {
        return alert.getVersion() == null ? 0 : alert.getVersion();
    }
}
