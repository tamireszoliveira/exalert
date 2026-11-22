package io.github.tamireszoliveira.alerta.alert;

import java.net.URI;
import java.util.List;
import java.util.UUID;

import jakarta.validation.Valid;

import org.springframework.http.HttpStatus;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.DeleteMapping;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.PutMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.ResponseStatus;
import org.springframework.web.bind.annotation.RestController;

/** CRUD dos alertas usado pelo PWA (token APP_TOKEN). */
@RestController
@RequestMapping("/api/alerts")
class AlertController {

    private final AlertService service;

    AlertController(AlertService service) {
        this.service = service;
    }

    @GetMapping
    List<AlertResponse> list() {
        return service.list();
    }

    @GetMapping("/{id}")
    AlertResponse get(@PathVariable UUID id) {
        return service.get(id);
    }

    @PostMapping
    ResponseEntity<AlertResponse> create(@Valid @RequestBody AlertRequest request) {
        AlertResponse created = service.create(request);
        return ResponseEntity.created(URI.create("/api/alerts/" + created.id())).body(created);
    }

    @PutMapping("/{id}")
    AlertResponse update(@PathVariable UUID id, @Valid @RequestBody AlertRequest request) {
        return service.update(id, request);
    }

    @DeleteMapping("/{id}")
    @ResponseStatus(HttpStatus.NO_CONTENT)
    void delete(@PathVariable UUID id) {
        service.delete(id);
    }
}
