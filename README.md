# alerta-cotacao

Alerta de preço do dólar e de ações da B3 com push no iPhone: você cadastra
"avise quando VALE3 ficar abaixo de R$ 65" e recebe uma notificação quando cruzar.
Projeto de estudo em monorepo: PWA, API em Java, checker em Python, Postgres,
Docker Compose, chart Helm com Gateway API e GitHub Actions.

```
iPhone (PWA) ──HTTPS──▶ Gateway ──/api──▶ api (Spring Boot) ──▶ Postgres
                           └────/──────▶ web (nginx + PWA)          ▲
                                                                     │ /internal
  AwesomeAPI / brapi ◀── checker (Python, 1 réplica) ────────────────┘
          ntfy ◀────────── (push quando um alerta dispara)
```

## O que tem em cada pasta

| Pasta | O quê |
|---|---|
| `apps/web` | PWA sem etapa de build (HTML, CSS e JS servidos por nginx sem root) |
| `services/api` | API REST em Spring Boot 4.1 / Java 25: alertas, histórico, cotações e endpoints do checker |
| `services/checker` | Python só com biblioteca padrão: consulta cotações, avalia os alertas e manda o push |
| `alertas/` | `alerts.toml` e `state.json` do modo agendado no GitHub Actions (fase 1) |
| `infra/compose` | Gateway local (nginx) que faz no compose o papel do Gateway do cluster |
| `infra/chart` | Chart Helm (`alerta-cotacao`) e o script que valida os manifestos |
| `infra/k3s` | Configuração do Traefik do k3s para atender Gateway API |
| `docs/api.md` | Contrato da API (app e checker) |
| `.github/workflows` | CI por pasta, release no GHCR com deploy opcional, e o agendamento da fase 1 |

## Rodar na sua máquina

Precisa só de Docker.

```bash
make up        # cria o .env com tokens aleatórios e sobe tudo
```

Abra <http://localhost:8080>, vá em **Ajustes** e cole o `APP_TOKEN` do `.env`.
Sem `make`: `cp .env.example .env`, troque os dois tokens e rode `docker compose up --build`.

Para ver um alerta disparar sem esperar o mercado, suba com o mercado falso
(preços controlados por você, checagem a cada 15 s):

```bash
make demo
make preco ATIVO=VALE3 VALOR=64.5   # um alerta "VALE3 abaixo de 65" dispara no próximo ciclo
make logs                            # o push aparece no log (NOTIFY_DRY_RUN=true no .env)
```

Para receber no celular de verdade: instale o app **ntfy**, assine um tópico difícil de
adivinhar, ponha o nome em `NTFY_TOPIC` e `NOTIFY_DRY_RUN=false` no `.env`.

## Avisos de verdade hoje: GitHub Actions agendado (fase 1)

Enquanto o app não roda num servidor, quem avisa é o workflow `alertas.yml`, de 10 em
10 min no horário do pregão, lendo `alertas/alerts.toml`.

1. No iPhone, instale o **ntfy** e assine um tópico (trate o nome como senha):
   `python3 -c "import secrets; print('cotacao-' + secrets.token_hex(8))"`
2. Crie o repositório privado, o secret e suba:

   ```bash
   git init -b main && git add -A && git commit -m "alerta de cotação"
   gh repo create alerta-cotacao --private --source=. --remote=origin
   gh secret set NTFY_TOPIC --body "cotacao-xxxxxxxxxxxxxxxx"
   git push -u origin main
   ```

3. Teste: `gh workflow run alertas.yml -f teste=true` (chega um push com as cotações atuais).

Os alertas ficam em `alertas/alerts.toml` (`ativo`, `abaixo` e/ou `acima`, `nome`). Cada
alerta avisa uma vez e só rearma quando o preço se afasta 0,5% do limite. Secrets
opcionais: `BRAPI_TOKEN` (tickers além de PETR4, VALE3, MGLU3 e ITUB4), `AWESOMEAPI_TOKEN`,
`NTFY_TOKEN`, `TELEGRAM_BOT_TOKEN` + `TELEGRAM_CHAT_ID`.

Limites: o cron do GitHub tem mínimo de 5 min e atrasa em horário de pico; em repositório
privado cada execução gasta 1 minuto da franquia (~1.300 min/mês no cron padrão); a brapi
grátis atualiza a cada ~30 min.

> Vindo do repositório anterior (`check.py` na raiz)? Mova `alerts.toml` e `state.json`
> para `alertas/`. O formato é o mesmo.

Quando o checker do cluster assumir, crie a variável de repositório
`ALERTAS_VIA_ACTIONS=false`; senão cada disparo chega duas vezes.

## Kubernetes local (k3d)

O mesmo chart serve para a máquina local e para produção; muda o values.

```bash
k3d cluster create alerta -p "8081:80@loadbalancer"
kubectl apply -f infra/k3s/traefik-gateway.yaml        # liga Gateway API no Traefik do k3s
kubectl -n kube-system get gateway traefik-gateway      # espere aparecer (o Traefik reinstala)

for app in web:apps/web api:services/api checker:services/checker; do
  docker build -t "alerta-cotacao-${app%%:*}:dev" "${app#*:}"
done
k3d image import alerta-cotacao-web:dev alerta-cotacao-api:dev alerta-cotacao-checker:dev -c alerta

helm upgrade --install alerta infra/chart/alerta-cotacao -n alerta --create-namespace \
  -f infra/chart/alerta-cotacao/values-local.yaml
kubectl -n alerta get pods -w
```

Abra <http://alerta.localhost:8081>. O token sai do Secret que o chart gerou:
`kubectl -n alerta get secret alerta-secrets -o jsonpath='{.data.APP_TOKEN}' | base64 -d`.

O que o chart cria: Deployments de `web` e `api` (2 réplicas com PDB), `checker` (1 réplica,
`Recreate`), StatefulSet do Postgres com PVC, `HTTPRoute` (`/api` → api, `/` → web),
NetworkPolicies (tudo fechado por padrão; o Postgres só aceita a API, o checker só sai para
a API e para a internet em 443) e um Secret com tokens gerados uma vez e mantidos nos
upgrades. Os contêineres rodam sem root e com sistema de arquivos só leitura.

Valores que mais importam:

| Valor | Para quê |
|---|---|
| `gateway.parentRefs` | Gateway compartilhado da plataforma (padrão: `traefik-gateway` do k3s) |
| `gateway.create`, `className`, `tls.*` | Gateway só do app, com HTTPS via cert-manager |
| `secrets.existingSecret` | Secret criado fora do chart (Sealed Secrets, External Secrets). Obrigatório com Argo CD/Flux |
| `networkPolicy.gatewayFrom` | De onde vem o tráfego do Gateway (namespace do controller) |
| `networkPolicy.ciliumGateway` | Cilium Gateway: libera a identidade `ingress` com CiliumNetworkPolicy |
| `postgres.enabled=false` + `externalDatabase.jdbcUrl` | Banco gerenciado em vez do StatefulSet |
| `checker.dryRun` | Push só no log (padrão no `values-local.yaml`) |

`values-prod.yaml` é um exemplo com domínio, HTTPS e Gateway do Cilium. Para o cert-manager
emitir o certificado do listener, o suporte a Gateway API precisa estar ligado nele.

Conferir o chart como o CI confere: `pip install kubernetes-validate jsonschema pyyaml && python3 infra/chart/check.py`.

## CI/CD

| Workflow | Quando | O que faz |
|---|---|---|
| `ci.yml` | PR e push na main | Testa só a pasta que mudou (api, checker, web, chart, compose, workflows) e builda as imagens sem publicar. O job `ci-ok` resume tudo: é o único a marcar como obrigatório na proteção da branch |
| `release.yml` | merge na main | Publica `ghcr.io/<você>/alerta-cotacao-{web,api,checker}` com a tag `sha-<commit>` (as três sempre juntas) e, se ligado, faz o deploy com `helm upgrade --rollback-on-failure` e um teste de fumaça |
| `alertas.yml` | cron da fase 1 | Checa as cotações e manda o push (modo arquivo) |

Para ligar o deploy: variável `DEPLOY_ENABLED=true`, environment `production` com o secret
`KUBECONFIG_B64` e a variável `APP_HOST`. Esse modo exige que a API do cluster seja
alcançável pelo runner do GitHub. Para não expor o 6443 na internet, use um runner
self-hosted na mesma rede ou GitOps (Argo CD ou Flux lendo o chart do repositório).

## Testes

```bash
make test-checker   # 61 testes: regras, modo arquivo e worker contra APIs falsas, sem internet
make test-web       # formatação e gráficos do PWA (Node 20+)
make test-api       # Spring Boot inteiro contra PostgreSQL real no Testcontainers (precisa de Docker)
```

## Próximos passos

- **Web Push direto no PWA** (fase 2): precisa do app publicado com HTTPS; a API ganha o
  envio (VAPID) e o checker deixa de usar o ntfy.
- **Cache** (Valkey) da última cotação na API: nessa escala o Postgres responde rápido,
  mas é um bom exercício de Spring Cache.
- **Teste ponta a ponta no CI** com kind: subir o chart e rodar o fluxo com o mercado falso.
- **Backup do Postgres** e métricas (Prometheus) quando sair do estudo.
