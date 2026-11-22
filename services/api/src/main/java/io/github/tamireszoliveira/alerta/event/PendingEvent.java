package io.github.tamireszoliveira.alerta.event;

import java.time.Instant;
import java.util.UUID;

import io.github.tamireszoliveira.alerta.alert.Direction;

/** Item do outbox em GET /internal/events/pending: o que o checker precisa para mandar o push. */
public record PendingEvent(
        UUID id,
        EventType type,
        String asset,
        Direction direction,
        String title,
        String message,
        Instant occurredAt) {

    static PendingEvent of(AlertEvent event) {
        return new PendingEvent(event.getId(), event.getType(), event.getAsset(), event.getDirection(),
                event.getTitle(), event.getMessage(), event.getOccurredAt());
    }
}
