# Atalhos do monorepo. `make help` lista tudo.
.DEFAULT_GOAL := help
SHELL := /bin/bash

COMPOSE      := docker compose
COMPOSE_DEMO := docker compose -f compose.yaml -f compose.mercado-falso.yaml
CHART        := infra/chart/alerta-cotacao

.PHONY: help
help: ## Lista os alvos
	@grep -hE '^[a-zA-Z_-]+:.*## ' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*## "}; {printf "  \033[33m%-14s\033[0m %s\n", $$1, $$2}'

# ------------------------------------------------------------------ local (compose)

.env:
	@cp .env.example .env
	@sed -i.bak "s/troque-este-token-do-app/$$(openssl rand -hex 24)/; s/troque-este-token-do-checker/$$(openssl rand -hex 24)/" .env && rm -f .env.bak
	@echo "Criei o .env com tokens aleatórios. O APP_TOKEN (para colar no app) está lá."

.PHONY: up
up: .env ## Sobe a stack local em http://localhost:8080
	$(COMPOSE) up --build -d
	@echo "App: http://localhost:8080  (cole o APP_TOKEN do .env em Ajustes)"

.PHONY: demo
demo: .env ## Sobe com o mercado falso (preços controlados, checagem a cada 15 s)
	$(COMPOSE_DEMO) up --build -d
	@echo "App: http://localhost:8080  |  mude um preço: make preco ATIVO=VALE3 VALOR=64.5"

.PHONY: preco
preco: ## Muda um preço no mercado falso: make preco ATIVO=VALE3 VALOR=64.5
	@test -n "$(ATIVO)" -a -n "$(VALOR)" || { echo "uso: make preco ATIVO=VALE3 VALOR=64.5"; exit 1; }
	curl -fsS -X PUT "http://127.0.0.1:9000/precos/$(ATIVO)" -d "$(VALOR)"; echo

.PHONY: logs
logs: ## Acompanha os logs (checker e api)
	$(COMPOSE) logs -f checker api

.PHONY: down
down: ## Derruba a stack (mantém o banco)
	$(COMPOSE_DEMO) down

.PHONY: clean
clean: ## Derruba a stack e apaga o banco local
	$(COMPOSE_DEMO) down -v

# ------------------------------------------------------------------ testes e lint

.PHONY: test
test: test-checker test-web test-api ## Roda todos os testes

.PHONY: test-checker
test-checker: ## Testes do checker (Python, só biblioteca padrão)
	cd services/checker && python3 -m unittest discover -s tests

.PHONY: test-web
test-web: ## Testes do PWA (Node 20+)
	cd apps/web && npm test

.PHONY: test-api
test-api: ## Testes da API (precisa de Docker para o Testcontainers)
	cd services/api && ./mvnw --batch-mode verify

.PHONY: lint
lint: ## Lint do checker e do chart
	cd services/checker && ruff check . && ruff format --check .
	helm lint $(CHART) --strict
	helm lint $(CHART) --strict -f $(CHART)/values-local.yaml
	helm lint $(CHART) --strict -f $(CHART)/values-prod.yaml

.PHONY: chart
chart: ## Renderiza o chart com os values locais (para conferir o YAML)
	helm template alerta $(CHART) -f $(CHART)/values-local.yaml
