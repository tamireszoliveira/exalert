# api

Spring Boot 4.1 (Java 25): CRUD de alertas para o PWA (`/api`, token `APP_TOKEN`) e os
endpoints do checker (`/internal`, token `CHECKER_TOKEN`). Contrato em `docs/api.md`.

- Esquema do banco: Flyway (`src/main/resources/db/migration`).
- Health: actuator na porta 8081 (`/actuator/health/liveness` e `/readiness`).
- Configuração: `application.yaml`; no compose e no chart tudo vem por variável de ambiente.

```bash
./mvnw verify            # testes; os de integração sobem um PostgreSQL com Testcontainers (precisa de Docker)
./mvnw spring-boot:run   # com um Postgres em localhost:5432 (usuário e senha "alerta") e APP_TOKEN/CHECKER_TOKEN definidos
```
