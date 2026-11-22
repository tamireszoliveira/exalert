package io.github.tamireszoliveira.alerta.quote;

import java.math.BigDecimal;
import java.time.Instant;
import java.util.List;

/** Série de preços de um ativo para o gráfico da tela de detalhe. */
public record QuoteSeries(String asset, List<Point> points) {

    public record Point(Instant t, BigDecimal price) {
    }
}
