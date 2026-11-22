package io.github.tamireszoliveira.alerta.event;

import java.time.Instant;
import java.util.List;
import java.util.UUID;

import org.springframework.data.domain.Limit;
import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.data.jpa.repository.Modifying;
import org.springframework.data.jpa.repository.Query;
import org.springframework.data.repository.query.Param;

public interface AlertEventRepository extends JpaRepository<AlertEvent, UUID> {

    List<AlertEvent> findAllByOrderByOccurredAtDesc(Limit limit);

    /** Outbox: push ainda não entregue e recente o bastante para valer a pena reenviar. */
    List<AlertEvent> findByPushTrueAndDeliveredAtIsNullAndOccurredAtAfterOrderByOccurredAtAsc(Instant since);

    @Modifying
    @Query("delete from AlertEvent e where e.occurredAt < :before")
    int deleteOlderThan(@Param("before") Instant before);
}
