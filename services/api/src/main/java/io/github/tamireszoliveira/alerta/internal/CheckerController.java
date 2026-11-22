package io.github.tamireszoliveira.alerta.internal;

import java.time.Clock;
import java.util.List;
import java.util.UUID;

import jakarta.validation.Valid;

import org.springframework.http.HttpStatus;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.ResponseStatus;
import org.springframework.web.bind.annotation.RestController;

import io.github.tamireszoliveira.alerta.alert.AlertService;
import io.github.tamireszoliveira.alerta.alert.CheckerAlert;
import io.github.tamireszoliveira.alerta.event.EventRequest;
import io.github.tamireszoliveira.alerta.event.EventResponse;
import io.github.tamireszoliveira.alerta.event.EventService;
import io.github.tamireszoliveira.alerta.event.PendingEvent;
import io.github.tamireszoliveira.alerta.quote.QuoteInput;
import io.github.tamireszoliveira.alerta.quote.QuoteRepository;

/**
 * Endpoints do checker (token CHECKER_TOKEN). Ficam fora de /api: o HTTPRoute do Gateway
 * só encaminha /api para cá, então /internal não é alcançável de fora do cluster.
 */
@RestController
@RequestMapping("/internal")
class CheckerController {

    private final AlertService alerts;
    private final EventService events;
    private final QuoteRepository quotes;
    private final Clock clock;

    CheckerController(AlertService alerts, EventService events, QuoteRepository quotes, Clock clock) {
        this.alerts = alerts;
        this.events = events;
        this.quotes = quotes;
        this.clock = clock;
    }

    @GetMapping("/alerts")
    List<CheckerAlert> alerts() {
        return alerts.enabledForChecker();
    }

    @PostMapping("/quotes")
    @ResponseStatus(HttpStatus.NO_CONTENT)
    void quotes(@RequestBody List<@Valid QuoteInput> body) {
        quotes.saveAll(body, clock.instant());
    }

    /** 201 quando grava; 200 quando o mesmo id já existia (reenvio do checker). */
    @PostMapping("/events")
    ResponseEntity<EventResponse> record(@Valid @RequestBody EventRequest body) {
        EventService.Recorded recorded = events.record(body);
        return ResponseEntity.status(recorded.created() ? HttpStatus.CREATED : HttpStatus.OK).body(recorded.event());
    }

    @GetMapping("/events/pending")
    List<PendingEvent> pending() {
        return events.pending();
    }

    @PostMapping("/events/{id}/delivered")
    @ResponseStatus(HttpStatus.NO_CONTENT)
    void delivered(@PathVariable UUID id) {
        events.markDelivered(id);
    }
}
