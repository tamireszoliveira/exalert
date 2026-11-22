package io.github.tamireszoliveira.alerta.alert;

import java.util.List;
import java.util.UUID;

import org.springframework.data.jpa.repository.JpaRepository;

public interface AlertRepository extends JpaRepository<Alert, UUID> {

    List<Alert> findAllByOrderByCreatedAtAsc();

    List<Alert> findByEnabledTrueOrderByCreatedAtAsc();
}
