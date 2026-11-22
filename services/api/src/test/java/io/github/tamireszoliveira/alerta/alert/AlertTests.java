package io.github.tamireszoliveira.alerta.alert;

import static org.assertj.core.api.Assertions.assertThat;

import java.math.BigDecimal;
import java.time.Instant;

import org.junit.jupiter.api.Test;

/** Regras de estado do alerta, sem Spring nem banco. */
class AlertTests {

    private static final Instant T0 = Instant.parse("2026-11-10T13:00:00Z");

    private Alert dolarAbaixoDe5() {
        return new Alert("USD-BRL", "Dólar", Direction.BELOW, new BigDecimal("5.00"), new BigDecimal("0.5"), T0);
    }

    @Test
    void startsArmedAndFiresOnlyOnce() {
        Alert alert = dolarAbaixoDe5();
        assertThat(alert.getState()).isEqualTo(AlertState.ARMED);
        assertThat(alert.getVersion()).isNull();
        assertThat(alert.rearm(T0)).isFalse();
        assertThat(alert.fire(T0.plusSeconds(60))).isTrue();
        assertThat(alert.getState()).isEqualTo(AlertState.FIRED);
        assertThat(alert.getFiredAt()).isEqualTo(T0.plusSeconds(60));
        assertThat(alert.fire(T0.plusSeconds(120))).isFalse();
    }

    @Test
    void rearmKeepsTheLastFiringTime() {
        Alert alert = dolarAbaixoDe5();
        alert.fire(T0);
        assertThat(alert.rearm(T0.plusSeconds(600))).isTrue();
        assertThat(alert.getState()).isEqualTo(AlertState.ARMED);
        assertThat(alert.getFiredAt()).isEqualTo(T0);
    }

    @Test
    void changingTheRuleRearmsTheAlert() {
        Alert alert = dolarAbaixoDe5();
        alert.fire(T0);
        alert.update("USD-BRL", "Dólar comercial", Direction.BELOW, new BigDecimal("5.000"), new BigDecimal("1"), true, T0);
        assertThat(alert.getState()).as("só nome e rearme mudaram (5.00 == 5.000)").isEqualTo(AlertState.FIRED);

        alert.update("USD-BRL", "Dólar", Direction.BELOW, new BigDecimal("4.90"), new BigDecimal("0.5"), true, T0);
        assertThat(alert.getState()).isEqualTo(AlertState.ARMED);
        assertThat(alert.getFiredAt()).isNull();
        assertThat(alert.getThreshold()).isEqualByComparingTo("4.90");
    }
}
