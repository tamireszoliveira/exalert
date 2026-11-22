# checker

Consulta as cotações (AwesomeAPI para câmbio, brapi para B3), aplica a regra de
disparo e manda o push (ntfy e/ou Telegram). Só biblioteca padrão, Python 3.11+.

| Módulo | Uso |
|---|---|
| `alerta_checker.worker` | Modo API: laço contínuo que lê os alertas da API (compose e Kubernetes) |
| `alerta_checker.cron` | Modo arquivo: `alerts.toml` + `state.json`, uma checagem por execução (GitHub Actions) |
| `alerta_checker.fake_market` | Mercado falso para desenvolvimento |
| `alerta_checker.health` | Probe de liveness (idade do heartbeat) |
| `alerta_checker.core` | Cotações, regra, formatação e envio, compartilhados pelos dois modos |

Configuração do worker por ambiente: `API_URL`, `CHECKER_TOKEN`, `CHECK_INTERVAL_SECONDS`
(120), `STOCK_MIN_INTERVAL_SECONDS` (300), `MARKET_HOURS` (`09:00-18:30`, seg-sex),
`FAIL_NOTICE_MINUTES` (60), `NOTIFY_DRY_RUN`, `HEARTBEAT_FILE`, além de `NTFY_TOPIC`,
`BRAPI_TOKEN` e afins.

```bash
python3 -m unittest discover -s tests   # testes, sem internet
ruff check . && ruff format --check .
```
