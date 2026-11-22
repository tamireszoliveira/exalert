package io.github.tamireszoliveira.alerta.event;

import java.util.List;

import jakarta.validation.constraints.Max;
import jakarta.validation.constraints.Min;

import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;

/** Histórico para a tela "Histórico" do app: GET /api/events?limit=50. */
@RestController
@RequestMapping("/api/events")
class EventController {

    private final EventService service;

    EventController(EventService service) {
        this.service = service;
    }

    @GetMapping
    List<EventResponse> recent(@RequestParam(defaultValue = "50") @Min(1) @Max(200) int limit) {
        return service.recent(limit);
    }
}
