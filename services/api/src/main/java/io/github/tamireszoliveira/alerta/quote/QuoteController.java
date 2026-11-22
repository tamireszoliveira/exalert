package io.github.tamireszoliveira.alerta.quote;

import java.time.Clock;
import java.time.Duration;

import jakarta.validation.constraints.Max;
import jakarta.validation.constraints.Min;

import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;

import io.github.tamireszoliveira.alerta.common.Assets;

/** Série de preços para o gráfico do app: GET /api/quotes/USD-BRL?hours=24. */
@RestController
@RequestMapping("/api/quotes")
class QuoteController {

    private final QuoteRepository quotes;
    private final Clock clock;

    QuoteController(QuoteRepository quotes, Clock clock) {
        this.quotes = quotes;
        this.clock = clock;
    }

    @GetMapping("/{asset}")
    QuoteSeries series(@PathVariable String asset, @RequestParam(defaultValue = "24") @Min(1) @Max(720) int hours) {
        String normalized = Assets.normalize(asset);
        return new QuoteSeries(normalized, quotes.series(normalized, clock.instant().minus(Duration.ofHours(hours))));
    }
}
