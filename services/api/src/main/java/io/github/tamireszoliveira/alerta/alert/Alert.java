package io.github.tamireszoliveira.alerta.alert;

import java.math.BigDecimal;
import java.time.Instant;
import java.util.UUID;

import jakarta.persistence.Column;
import jakarta.persistence.Entity;
import jakarta.persistence.EnumType;
import jakarta.persistence.Enumerated;
import jakarta.persistence.Id;
import jakarta.persistence.Table;
import jakarta.persistence.Version;

/**
 * Um alerta: "avise quando ATIVO ficar abaixo/acima de LIMITE".
 *
 * <p>O estado (ARMED/FIRED) muda só por eventos do checker. A versão otimista
 * ({@code @Version}) deixa a API recusar um evento calculado sobre uma versão antiga
 * do alerta, por exemplo se o limite foi editado entre a leitura e o disparo.
 */
@Entity
@Table(name = "alert")
public class Alert {

    @Id
    private UUID id;

    @Column(nullable = false, length = 20)
    private String asset;

    @Column(length = 60)
    private String name;

    @Enumerated(EnumType.STRING)
    @Column(nullable = false, length = 5)
    private Direction direction;

    @Column(nullable = false, precision = 18, scale = 6)
    private BigDecimal threshold;

    @Column(name = "rearm_pct", nullable = false, precision = 6, scale = 3)
    private BigDecimal rearmPct;

    @Column(nullable = false)
    private boolean enabled;

    @Enumerated(EnumType.STRING)
    @Column(nullable = false, length = 10)
    private AlertState state;

    @Column(name = "fired_at")
    private Instant firedAt;

    @Column(name = "created_at", nullable = false, updatable = false)
    private Instant createdAt;

    @Column(name = "updated_at", nullable = false)
    private Instant updatedAt;

    @Version
    private Long version;

    protected Alert() {
        // exigido pelo JPA
    }

    public Alert(String asset, String name, Direction direction, BigDecimal threshold, BigDecimal rearmPct, Instant now) {
        this.id = UUID.randomUUID();
        this.asset = asset;
        this.name = name;
        this.direction = direction;
        this.threshold = threshold;
        this.rearmPct = rearmPct;
        this.enabled = true;
        this.state = AlertState.ARMED;
        this.createdAt = now;
        this.updatedAt = now;
    }

    /** Edição pela usuária. Mudar ativo, lado ou limite é um alerta novo: volta a ARMED. */
    public void update(String asset, String name, Direction direction, BigDecimal threshold, BigDecimal rearmPct,
            boolean enabled, Instant now) {
        boolean ruleChanged = !this.asset.equals(asset) || this.direction != direction
                || this.threshold.compareTo(threshold) != 0;
        this.asset = asset;
        this.name = name;
        this.direction = direction;
        this.threshold = threshold;
        this.rearmPct = rearmPct;
        this.enabled = enabled;
        if (ruleChanged) {
            this.state = AlertState.ARMED;
            this.firedAt = null;
        }
        this.updatedAt = now;
    }

    /** Disparo informado pelo checker. Só vale para alerta armado. */
    public boolean fire(Instant at) {
        if (state != AlertState.ARMED) {
            return false;
        }
        state = AlertState.FIRED;
        firedAt = at;
        updatedAt = at;
        return true;
    }

    /** Rearme informado pelo checker (o preço se afastou do limite). Mantém firedAt como "último disparo". */
    public boolean rearm(Instant at) {
        if (state != AlertState.FIRED) {
            return false;
        }
        state = AlertState.ARMED;
        updatedAt = at;
        return true;
    }

    public UUID getId() {
        return id;
    }

    public String getAsset() {
        return asset;
    }

    public String getName() {
        return name;
    }

    public Direction getDirection() {
        return direction;
    }

    public BigDecimal getThreshold() {
        return threshold;
    }

    public BigDecimal getRearmPct() {
        return rearmPct;
    }

    public boolean isEnabled() {
        return enabled;
    }

    public AlertState getState() {
        return state;
    }

    public Instant getFiredAt() {
        return firedAt;
    }

    public Instant getCreatedAt() {
        return createdAt;
    }

    public Instant getUpdatedAt() {
        return updatedAt;
    }

    /** Nula antes do primeiro save (é assim que o Spring Data sabe que o alerta é novo). */
    public Long getVersion() {
        return version;
    }
}
