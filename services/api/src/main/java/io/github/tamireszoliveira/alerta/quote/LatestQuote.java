package io.github.tamireszoliveira.alerta.quote;

import java.math.BigDecimal;
import java.time.Instant;

/** Última cotação conhecida de um ativo. */
public record LatestQuote(BigDecimal price, Instant quotedAt, String source) {
}
