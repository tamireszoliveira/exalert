package io.github.tamireszoliveira.alerta;

import java.time.Clock;

import org.springframework.boot.SpringApplication;
import org.springframework.boot.autoconfigure.SpringBootApplication;
import org.springframework.boot.context.properties.ConfigurationPropertiesScan;
import org.springframework.context.annotation.Bean;
import org.springframework.scheduling.annotation.EnableScheduling;

@SpringBootApplication
@ConfigurationPropertiesScan
@EnableScheduling
public class AlertaApplication {

    public static void main(String[] args) {
        SpringApplication.run(AlertaApplication.class, args);
    }

    /** Relógio injetável: os testes conseguem fixar o "agora". */
    @Bean
    Clock clock() {
        return Clock.systemUTC();
    }
}
