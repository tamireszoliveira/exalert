package io.github.tamireszoliveira.alerta.event;

import java.math.BigDecimal;
import java.time.Instant;
import java.util.UUID;

import jakarta.persistence.Column;
import jakarta.persistence.Entity;
import jakarta.persistence.EnumType;
import jakarta.persistence.Enumerated;
import jakarta.persistence.Id;
import jakarta.persistence.PostLoad;
import jakarta.persistence.PostPersist;
import jakarta.persistence.Table;
import jakarta.persistence.Transient;

import org.springframework.data.domain.Persistable;

import io.github.tamireszoliveira.alerta.alert.Direction;

/**
 * Evento do histórico e, quando {@code push} é verdadeiro, item do outbox de notificações.
 *
 * <p>O id vem do checker (idempotência: reenviar o mesmo evento não duplica). Por isso a
 * entidade implementa Persistable: sem isso o Spring Data veria um id preenchido e faria
 * um SELECT + merge em vez de um INSERT.
 */
@Entity
@Table(name = "alert_event")
public class AlertEvent implements Persistable<UUID> {

    @Id
    private UUID id;

    @Column(name = "alert_id")
    private UUID alertId;

    @Column(nullable = false, length = 20)
    private String asset;

    @Enumerated(EnumType.STRING)
    @Column(nullable = false, length = 15)
    private EventType type;

    @Enumerated(EnumType.STRING)
    @Column(length = 5)
    private Direction direction;

    @Column(precision = 18, scale = 6)
    private BigDecimal price;

    @Column(name = "quoted_at")
    private Instant quotedAt;

    @Column(nullable = false, length = 200)
    private String title;

    @Column(nullable = false, length = 500)
    private String message;

    @Column(nullable = false)
    private boolean push;

    @Column(name = "occurred_at", nullable = false)
    private Instant occurredAt;

    @Column(name = "delivered_at")
    private Instant deliveredAt;

    @Column(name = "created_at", nullable = false, updatable = false)
    private Instant createdAt;

    @Transient
    private boolean isNew = true;

    protected AlertEvent() {
        // exigido pelo JPA
    }

    AlertEvent(EventRequest request, Instant now) {
        this.id = request.id();
        this.alertId = request.alertId();
        this.asset = request.asset();
        this.type = request.type();
        this.direction = request.direction();
        this.price = request.price();
        this.quotedAt = request.quotedAt();
        this.title = request.title();
        this.message = request.message();
        this.push = request.push();
        this.occurredAt = request.occurredAt();
        this.createdAt = now;
    }

    void markDelivered(Instant at) {
        if (deliveredAt == null) {
            deliveredAt = at;
        }
    }

    @Override
    public UUID getId() {
        return id;
    }

    @Override
    public boolean isNew() {
        return isNew;
    }

    @PostLoad
    @PostPersist
    void markNotNew() {
        this.isNew = false;
    }

    public UUID getAlertId() {
        return alertId;
    }

    public String getAsset() {
        return asset;
    }

    public EventType getType() {
        return type;
    }

    public Direction getDirection() {
        return direction;
    }

    public BigDecimal getPrice() {
        return price;
    }

    public Instant getQuotedAt() {
        return quotedAt;
    }

    public String getTitle() {
        return title;
    }

    public String getMessage() {
        return message;
    }

    public boolean isPush() {
        return push;
    }

    public Instant getOccurredAt() {
        return occurredAt;
    }

    public Instant getDeliveredAt() {
        return deliveredAt;
    }
}
