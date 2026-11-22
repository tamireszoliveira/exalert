package io.github.tamireszoliveira.alerta.config;

import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;
import org.springframework.http.HttpStatus;
import org.springframework.security.authentication.ProviderManager;
import org.springframework.security.config.annotation.web.builders.HttpSecurity;
import org.springframework.security.config.annotation.web.configurers.AbstractHttpConfigurer;
import org.springframework.security.config.http.SessionCreationPolicy;
import org.springframework.security.web.SecurityFilterChain;
import org.springframework.security.web.authentication.HttpStatusEntryPoint;
import org.springframework.security.web.authentication.www.BasicAuthenticationFilter;

/**
 * Duas credenciais estáticas, sem sessão nem cookie:
 * o PWA usa APP_TOKEN em {@code /api/**}; o checker usa CHECKER_TOKEN em {@code /internal/**}.
 *
 * <p>Os health checks ficam na porta de gerenciamento (8081), que não passa pelo Gateway.
 */
@Configuration(proxyBeanMethods = false)
public class SecurityConfig {

    @Bean
    StaticTokenAuthenticationProvider staticTokenAuthenticationProvider(AlertaProperties properties) {
        return new StaticTokenAuthenticationProvider(properties.appToken(), properties.checkerToken());
    }

    @Bean
    SecurityFilterChain securityFilterChain(HttpSecurity http, StaticTokenAuthenticationProvider provider) throws Exception {
        HttpStatusEntryPoint unauthorized = new HttpStatusEntryPoint(HttpStatus.UNAUTHORIZED);
        BearerTokenFilter bearer = new BearerTokenFilter(new ProviderManager(provider), unauthorized);
        http
                .csrf(AbstractHttpConfigurer::disable) // sem cookie de sessão, não há CSRF a explorar
                .httpBasic(AbstractHttpConfigurer::disable)
                .formLogin(AbstractHttpConfigurer::disable)
                .logout(AbstractHttpConfigurer::disable)
                .requestCache(AbstractHttpConfigurer::disable)
                .sessionManagement(session -> session.sessionCreationPolicy(SessionCreationPolicy.STATELESS))
                .exceptionHandling(errors -> errors.authenticationEntryPoint(unauthorized))
                .addFilterBefore(bearer, BasicAuthenticationFilter.class)
                .authorizeHttpRequests(auth -> auth
                        .requestMatchers("/actuator/health", "/actuator/health/**", "/actuator/info").permitAll()
                        .requestMatchers("/error").permitAll()
                        .requestMatchers("/internal/**").hasRole(StaticTokenAuthenticationProvider.CHECKER)
                        .requestMatchers("/api/**").hasRole(StaticTokenAuthenticationProvider.USER)
                        .anyRequest().denyAll());
        return http.build();
    }
}
