package io.github.tamireszoliveira.alerta.quote;

import java.math.BigDecimal;
import java.time.Instant;

import jakarta.validation.constraints.NotBlank;
import jakarta.validation.constraints.NotNull;
import jakarta.validation.constraints.Positive;
import jakarta.validation.constraints.Size;

/** Cotação enviada pelo checker em POST /internal/quotes. */
public record QuoteInput(
        @NotBlank @Size(max = 20) String asset,
        @NotNull @Positive BigDecimal price,
        @NotNull Instant quotedAt,
        @NotBlank @Size(max = 20) String source) {
}
