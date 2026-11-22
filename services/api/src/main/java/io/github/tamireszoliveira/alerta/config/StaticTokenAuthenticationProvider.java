package io.github.tamireszoliveira.alerta.config;

import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.util.List;

import org.springframework.security.authentication.AuthenticationProvider;
import org.springframework.security.authentication.BadCredentialsException;
import org.springframework.security.authentication.UsernamePasswordAuthenticationToken;
import org.springframework.security.core.Authentication;
import org.springframework.security.core.authority.SimpleGrantedAuthority;

/**
 * Confere o token Bearer contra os dois tokens configurados.
 *
 * <p>Declarado como bean, também faz o Spring Boot não criar o usuário padrão com senha gerada.
 */
public class StaticTokenAuthenticationProvider implements AuthenticationProvider {

    static final String USER = "USER";
    static final String CHECKER = "CHECKER";

    private final byte[] appToken;
    private final byte[] checkerToken;

    public StaticTokenAuthenticationProvider(String appToken, String checkerToken) {
        if (appToken.equals(checkerToken)) {
            throw new IllegalStateException("APP_TOKEN e CHECKER_TOKEN precisam ser diferentes");
        }
        this.appToken = appToken.getBytes(StandardCharsets.UTF_8);
        this.checkerToken = checkerToken.getBytes(StandardCharsets.UTF_8);
    }

    @Override
    public Authentication authenticate(Authentication authentication) {
        byte[] presented = String.valueOf(authentication.getCredentials()).getBytes(StandardCharsets.UTF_8);
        // MessageDigest.isEqual compara em tempo constante: não vaza o token pelo tempo de resposta.
        if (MessageDigest.isEqual(presented, appToken)) {
            return authenticated("app", USER);
        }
        if (MessageDigest.isEqual(presented, checkerToken)) {
            return authenticated("checker", CHECKER);
        }
        throw new BadCredentialsException("token inválido");
    }

    @Override
    public boolean supports(Class<?> authentication) {
        return UsernamePasswordAuthenticationToken.class.isAssignableFrom(authentication);
    }

    private static Authentication authenticated(String principal, String role) {
        return UsernamePasswordAuthenticationToken.authenticated(principal, null,
                List.of(new SimpleGrantedAuthority("ROLE_" + role)));
    }
}
