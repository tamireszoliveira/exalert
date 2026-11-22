package io.github.tamireszoliveira.alerta;

import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.delete;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.post;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.put;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.header;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

import java.time.Instant;
import java.time.temporal.ChronoUnit;
import java.util.UUID;

import com.jayway.jsonpath.JsonPath;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;

import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.boot.webmvc.test.autoconfigure.AutoConfigureMockMvc;
import org.springframework.context.annotation.Import;
import org.springframework.http.HttpHeaders;
import org.springframework.http.MediaType;
import org.springframework.test.web.servlet.MockMvc;
import org.springframework.test.web.servlet.ResultActions;
import org.springframework.test.web.servlet.request.MockHttpServletRequestBuilder;

import io.github.tamireszoliveira.alerta.alert.AlertRepository;
import io.github.tamireszoliveira.alerta.event.AlertEventRepository;
import io.github.tamireszoliveira.alerta.quote.QuoteRepository;

/**
 * A API inteira (segurança, validação, JPA, Flyway e SQL) contra um PostgreSQL real
 * do Testcontainers. Precisa de Docker: roda no CI e na sua máquina com ./mvnw verify.
 */
@SpringBootTest(properties = {
        "alerta.app-token=" + AlertaApiIntegrationTests.APP,
        "alerta.checker-token=" + AlertaApiIntegrationTests.CHECKER })
@AutoConfigureMockMvc
@Import(TestcontainersConfiguration.class)
class AlertaApiIntegrationTests {

    static final String APP = "token-do-app-para-testes";
    static final String CHECKER = "token-do-checker-para-testes";

    @Autowired
    MockMvc mvc;

    @Autowired
    AlertRepository alerts;

    @Autowired
    AlertEventRepository events;

    @Autowired
    QuoteRepository quotes;

    @BeforeEach
    void cleanDatabase() {
        events.deleteAll();
        alerts.deleteAll();
        quotes.deleteOlderThan(Instant.parse("2999-01-01T00:00:00Z"));
    }

    // ------------------------------------------------------------------ segurança

    @Test
    void eachTokenOpensOnlyItsOwnPaths() throws Exception {
        mvc.perform(get("/api/alerts")).andExpect(status().isUnauthorized());
        mvc.perform(get("/api/alerts").header(HttpHeaders.AUTHORIZATION, "Bearer outro-token-qualquer"))
                .andExpect(status().isUnauthorized());
        mvc.perform(get("/api/alerts").header(HttpHeaders.AUTHORIZATION, "Bearer " + CHECKER))
                .andExpect(status().isForbidden());
        mvc.perform(get("/internal/alerts").header(HttpHeaders.AUTHORIZATION, "Bearer " + APP))
                .andExpect(status().isForbidden());
        mvc.perform(get("/api/alerts").header(HttpHeaders.AUTHORIZATION, "bearer " + APP)).andExpect(status().isOk());
        mvc.perform(get("/internal/alerts").header(HttpHeaders.AUTHORIZATION, "Bearer " + CHECKER))
                .andExpect(status().isOk());
        mvc.perform(get("/qualquer-outra-coisa").header(HttpHeaders.AUTHORIZATION, "Bearer " + APP))
                .andExpect(status().isForbidden());
    }

    // ------------------------------------------------------------------ app

    @Test
    void createsUpdatesAndDeletesAlerts() throws Exception {
        String id = createDollarBelow5();
        app(get("/api/alerts"))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.length()").value(1))
                .andExpect(jsonPath("$[0].name").value("Dólar"))
                .andExpect(jsonPath("$[0].lastQuote").isEmpty());

        app(put("/api/alerts/" + id).contentType(MediaType.APPLICATION_JSON).content("""
                {"asset": "USD-BRL", "name": "Dólar", "direction": "BELOW", "threshold": 4.90}
                """))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.threshold").value(4.9))
                .andExpect(jsonPath("$.state").value("ARMED"))
                .andExpect(jsonPath("$.version").value(1));

        app(put("/api/alerts/" + id).contentType(MediaType.APPLICATION_JSON).content("""
                {"asset": "USD-BRL", "direction": "BELOW", "threshold": 4.90, "enabled": false}
                """))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.enabled").value(false))
                .andExpect(jsonPath("$.name").isEmpty());
        checker(get("/internal/alerts")).andExpect(jsonPath("$.length()").value(0)); // desativado: o checker não vê

        app(delete("/api/alerts/" + id)).andExpect(status().isNoContent());
        app(get("/api/alerts/" + id))
                .andExpect(status().isNotFound())
                .andExpect(jsonPath("$.detail").value("alerta não encontrado"));
    }

    @Test
    void invalidInputComesBackAsProblemDetail() throws Exception {
        app(post("/api/alerts").contentType(MediaType.APPLICATION_JSON).content("""
                {"asset": "dólar!", "direction": "BELOW", "threshold": -1}
                """))
                .andExpect(status().isBadRequest())
                .andExpect(jsonPath("$.detail").value("dados inválidos"))
                .andExpect(jsonPath("$.errors.asset").exists())
                .andExpect(jsonPath("$.errors.threshold").value("o limite deve ser maior que zero"));
        app(post("/api/alerts").contentType(MediaType.APPLICATION_JSON).content("""
                {"asset": "VALE3", "direction": "PARA_BAIXO", "threshold": 65}
                """))
                .andExpect(status().isBadRequest());
        app(get("/api/alerts/nao-e-uuid")).andExpect(status().isBadRequest());
        app(get("/api/events?limit=0")).andExpect(status().isBadRequest());
    }

    // ------------------------------------------------------------------ checker

    @Test
    void checkerCycleFiresDeliversAndRearms() throws Exception {
        String id = createDollarBelow5();
        checker(get("/internal/alerts"))
                .andExpect(jsonPath("$[0].id").value(id))
                .andExpect(jsonPath("$[0].state").value("ARMED"))
                .andExpect(jsonPath("$[0].version").value(0));

        String quotedAt = Instant.now().minus(1, ChronoUnit.HOURS).truncatedTo(ChronoUnit.SECONDS).toString();
        String quote = """
                {"asset": "USD-BRL", "price": 4.987, "quotedAt": "%s", "source": "AwesomeAPI"}
                """.formatted(quotedAt);
        checker(post("/internal/quotes").contentType(MediaType.APPLICATION_JSON).content("[" + quote + "," + quote + "]"))
                .andExpect(status().isNoContent());
        app(get("/api/alerts"))
                .andExpect(jsonPath("$[0].lastQuote.price").value(4.987))
                .andExpect(jsonPath("$[0].lastQuote.source").value("AwesomeAPI"));
        app(get("/api/quotes/usd-brl?hours=24"))
                .andExpect(jsonPath("$.asset").value("USD-BRL"))
                .andExpect(jsonPath("$.points.length()").value(1)); // a cotação repetida foi descartada

        String fired = event(UUID.randomUUID(), "FIRED", id, 0, true);
        checker(post("/internal/events").contentType(MediaType.APPLICATION_JSON).content(fired))
                .andExpect(status().isCreated());
        checker(post("/internal/events").contentType(MediaType.APPLICATION_JSON).content(fired))
                .andExpect(status().isOk()); // reenvio do mesmo id: idempotente
        app(get("/api/alerts/" + id))
                .andExpect(jsonPath("$.state").value("FIRED"))
                .andExpect(jsonPath("$.firedAt").isNotEmpty())
                .andExpect(jsonPath("$.version").value(1));

        String stale = event(UUID.randomUUID(), "FIRED", id, 0, true);
        checker(post("/internal/events").contentType(MediaType.APPLICATION_JSON).content(stale))
                .andExpect(status().isConflict());

        String eventId = JsonPath.read(fired, "$.id");
        checker(get("/internal/events/pending"))
                .andExpect(jsonPath("$.length()").value(1))
                .andExpect(jsonPath("$[0].id").value(eventId))
                .andExpect(jsonPath("$[0].title").value("Dólar abaixo de R$ 5,00"));
        checker(post("/internal/events/" + eventId + "/delivered")).andExpect(status().isNoContent());
        checker(get("/internal/events/pending")).andExpect(jsonPath("$.length()").value(0));
        app(get("/api/events"))
                .andExpect(jsonPath("$[0].type").value("FIRED"))
                .andExpect(jsonPath("$[0].deliveredAt").isNotEmpty());

        checker(post("/internal/events").contentType(MediaType.APPLICATION_JSON)
                .content(event(UUID.randomUUID(), "REARMED", id, 1, false)))
                .andExpect(status().isCreated());
        app(get("/api/alerts/" + id)).andExpect(jsonPath("$.state").value("ARMED"));
        checker(get("/internal/events/pending")).andExpect(jsonPath("$.length()").value(0)); // rearme não é push
    }

    @Test
    void sourceEventsAndDeletedAlerts() throws Exception {
        checker(post("/internal/events").contentType(MediaType.APPLICATION_JSON)
                .content(event(UUID.randomUUID(), "SOURCE_DOWN", null, null, true, Instant.now().minus(10, ChronoUnit.MINUTES))))
                .andExpect(status().isCreated());
        checker(get("/internal/events/pending")).andExpect(jsonPath("$[0].type").value("SOURCE_DOWN"));

        checker(post("/internal/events").contentType(MediaType.APPLICATION_JSON)
                .content(event(UUID.randomUUID(), "FIRED", UUID.randomUUID().toString(), 0, true)))
                .andExpect(status().isNotFound());
        checker(post("/internal/events/" + UUID.randomUUID() + "/delivered")).andExpect(status().isNotFound());

        String id = createDollarBelow5();
        checker(post("/internal/events").contentType(MediaType.APPLICATION_JSON)
                .content(event(UUID.randomUUID(), "FIRED", id, 0, true)))
                .andExpect(status().isCreated());
        app(delete("/api/alerts/" + id)).andExpect(status().isNoContent());
        app(get("/api/events?limit=10"))
                .andExpect(jsonPath("$.length()").value(2))
                .andExpect(jsonPath("$[0].type").value("FIRED"))
                .andExpect(jsonPath("$[0].alertId").isEmpty()); // histórico fica, sem o vínculo
    }

    // ------------------------------------------------------------------ apoio

    private String createDollarBelow5() throws Exception {
        String body = app(post("/api/alerts").contentType(MediaType.APPLICATION_JSON).content("""
                {"asset": " usd-brl ", "name": "Dólar", "direction": "BELOW", "threshold": 5.00}
                """))
                .andExpect(status().isCreated())
                .andExpect(header().exists(HttpHeaders.LOCATION))
                .andExpect(jsonPath("$.asset").value("USD-BRL"))
                .andExpect(jsonPath("$.state").value("ARMED"))
                .andExpect(jsonPath("$.rearmPct").value(0.5))
                .andExpect(jsonPath("$.enabled").value(true))
                .andExpect(jsonPath("$.version").value(0))
                .andReturn().getResponse().getContentAsString();
        return JsonPath.read(body, "$.id");
    }

    /** Evento no formato que o checker envia (alerta_checker/worker.py, Event.payload). */
    private static String event(UUID id, String type, String alertId, Integer alertVersion, boolean push) {
        return event(id, type, alertId, alertVersion, push, Instant.now());
    }

    private static String event(UUID id, String type, String alertId, Integer alertVersion, boolean push, Instant at) {
        String now = at.truncatedTo(ChronoUnit.SECONDS).toString();
        return """
                {"id": "%s", "type": "%s", "asset": "USD-BRL", "alertId": %s, "alertVersion": %s,
                 "direction": "BELOW", "price": 4.987, "quotedAt": "%s", "occurredAt": "%s",
                 "title": "Dólar abaixo de R$ 5,00", "message": "Agora: R$ 4,9870", "push": %s}
                """.formatted(id, type, alertId == null ? "null" : "\"" + alertId + "\"",
                alertVersion == null ? "null" : alertVersion, now, now, push);
    }

    private ResultActions app(MockHttpServletRequestBuilder request) throws Exception {
        return mvc.perform(request.header(HttpHeaders.AUTHORIZATION, "Bearer " + APP));
    }

    private ResultActions checker(MockHttpServletRequestBuilder request) throws Exception {
        return mvc.perform(request.header(HttpHeaders.AUTHORIZATION, "Bearer " + CHECKER));
    }
}
