# syntax=docker/dockerfile:1

# ---------------------------------------------------------------- build
# Compila com o Maven Wrapper (mesma versão do CI e da sua máquina). Os testes de
# integração precisam de Docker e rodam no CI, não aqui.
FROM eclipse-temurin:25-jdk-noble AS build
WORKDIR /src
COPY mvnw pom.xml ./
COPY .mvn/ .mvn/
COPY src/ src/
RUN --mount=type=cache,target=/root/.m2 \
    sh ./mvnw --batch-mode --no-transfer-progress -DskipTests package \
 && java -Djarmode=tools -jar target/alerta-api.jar extract --layers --destination extracted

# ---------------------------------------------------------------- runtime
# Uma camada por grupo (dependências mudam pouco; o código, muito): o push e o pull
# de uma versão nova só levam a camada "application".
FROM eclipse-temurin:25-jre-noble
WORKDIR /app
RUN groupadd --system --gid 10001 app \
 && useradd --system --uid 10001 --gid app --no-create-home --shell /usr/sbin/nologin app
COPY --from=build /src/extracted/dependencies/ ./
COPY --from=build /src/extracted/spring-boot-loader/ ./
COPY --from=build /src/extracted/snapshot-dependencies/ ./
COPY --from=build /src/extracted/application/ ./
USER 10001:10001
# 8080: API (via Gateway). 8081: actuator (probes; nunca exposto para fora).
EXPOSE 8080 8081
ENV JAVA_TOOL_OPTIONS="-XX:MaxRAMPercentage=75 -XX:+ExitOnOutOfMemoryError"
ENTRYPOINT ["java", "-jar", "application.jar"]
