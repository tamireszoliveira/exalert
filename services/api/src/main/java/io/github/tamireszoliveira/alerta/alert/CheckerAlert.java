package io.github.tamireszoliveira.alerta.alert;

import java.math.BigDecimal;
import java.util.UUID;

/** Alerta como o checker vê em GET /internal/alerts: só o necessário para avaliar e a versão. */
public record CheckerAlert(
        UUID id,
        String asset,
        String name,
        Direction direction,
        BigDecimal threshold,
        BigDecimal rearmPct,
        AlertState state,
        long version) {

    public static CheckerAlert of(Alert alert) {
        return new CheckerAlert(alert.getId(), alert.getAsset(), alert.getName(), alert.getDirection(),
                alert.getThreshold(), alert.getRearmPct(), alert.getState(), AlertResponse.versionOf(alert));
    }
}
