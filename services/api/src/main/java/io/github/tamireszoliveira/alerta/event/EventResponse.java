package io.github.tamireszoliveira.alerta.event;

import java.math.BigDecimal;
import java.time.Instant;
import java.util.UUID;

import io.github.tamireszoliveira.alerta.alert.Direction;

/** Evento do histórico (GET /api/events) e resposta de POST /internal/events. */
public record EventResponse(
        UUID id,
        UUID alertId,
        String asset,
        EventType type,
        Direction direction,
        BigDecimal price,
        Instant quotedAt,
        String title,
        String message,
        boolean push,
        Instant occurredAt,
        Instant deliveredAt) {

    static EventResponse of(AlertEvent event) {
        return new EventResponse(event.getId(), event.getAlertId(), event.getAsset(), event.getType(),
                event.getDirection(), event.getPrice(), event.getQuotedAt(), event.getTitle(), event.getMessage(),
                event.isPush(), event.getOccurredAt(), event.getDeliveredAt());
    }
}
