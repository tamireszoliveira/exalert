package io.github.tamireszoliveira.alerta.housekeeping;

import java.time.Clock;
import java.time.Instant;

import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.scheduling.annotation.Scheduled;
import org.springframework.stereotype.Component;
import org.springframework.transaction.annotation.Transactional;

import io.github.tamireszoliveira.alerta.config.AlertaProperties;
import io.github.tamireszoliveira.alerta.event.AlertEventRepository;
import io.github.tamireszoliveira.alerta.quote.QuoteRepository;

/**
 * Apaga cotações e eventos antigos uma vez por dia.
 *
 * <p>Com várias réplicas da API, cada uma roda o job: o DELETE é idempotente, então só
 * custa trabalho repetido. (Se um dia pesar, ShedLock resolve.)
 */
@Component
class RetentionJob {

    private static final Logger log = LoggerFactory.getLogger(RetentionJob.class);

    private final QuoteRepository quotes;
    private final AlertEventRepository events;
    private final AlertaProperties properties;
    private final Clock clock;

    RetentionJob(QuoteRepository quotes, AlertEventRepository events, AlertaProperties properties, Clock clock) {
        this.quotes = quotes;
        this.events = events;
        this.properties = properties;
        this.clock = clock;
    }

    @Scheduled(cron = "0 17 4 * * *", zone = "America/Sao_Paulo")
    @Transactional
    public void purge() {
        Instant now = clock.instant();
        int oldQuotes = quotes.deleteOlderThan(now.minus(properties.quoteRetention()));
        int oldEvents = events.deleteOlderThan(now.minus(properties.eventRetention()));
        log.info("retenção: {} cotações e {} eventos apagados", oldQuotes, oldEvents);
    }
}
