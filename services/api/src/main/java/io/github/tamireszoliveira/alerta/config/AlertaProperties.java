package io.github.tamireszoliveira.alerta.config;

import java.time.Duration;

import jakarta.validation.constraints.NotBlank;
import jakarta.validation.constraints.Size;

import org.springframework.boot.context.properties.ConfigurationProperties;
import org.springframework.boot.context.properties.bind.DefaultValue;
import org.springframework.validation.annotation.Validated;

/**
 * Configuração própria do app (prefixo {@code alerta.*} no application.yaml).
 *
 * @param appToken token que o PWA envia em {@code /api/**} (variável APP_TOKEN)
 * @param checkerToken token que o checker envia em {@code /internal/**} (variável CHECKER_TOKEN)
 * @param quoteRetention por quanto tempo guardar cotações (gráfico do app)
 * @param eventRetention por quanto tempo guardar o histórico de eventos
 * @param pendingWindow até quando um push não entregue ainda vale a pena reenviar
 */
@Validated
@ConfigurationProperties("alerta")
public record AlertaProperties(
        @NotBlank(message = "defina APP_TOKEN") @Size(min = 16, message = "APP_TOKEN precisa de 16+ caracteres") String appToken,
        @NotBlank(message = "defina CHECKER_TOKEN") @Size(min = 16, message = "CHECKER_TOKEN precisa de 16+ caracteres") String checkerToken,
        @DefaultValue("30d") Duration quoteRetention,
        @DefaultValue("180d") Duration eventRetention,
        @DefaultValue("24h") Duration pendingWindow) {
}
