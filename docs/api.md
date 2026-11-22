# Contrato da API

Uma API, dois públicos, dois tokens:

| Prefixo | Quem chama | Token | Exposto pelo Gateway |
|---|---|---|---|
| `/api/**` | PWA | `APP_TOKEN` | sim |
| `/internal/**` | checker | `CHECKER_TOKEN` | não (só dentro do cluster) |
| `/actuator/health/**` | probes | nenhum | não (porta 8081) |

Autenticação: `Authorization: Bearer <token>`. Sem token ou token errado: `401`.
Token do app em `/internal` (ou o inverso): `403`. Erros vêm em
[RFC 9457](https://www.rfc-editor.org/rfc/rfc9457) (`application/problem+json`); erros de
validação trazem `errors: {campo: mensagem}`.

Datas em ISO 8601 UTC (`2026-11-10T17:32:00Z`); valores em número JSON.
Ativo: par de moedas (`USD-BRL`) ou ticker da B3 (`VALE3`), sem diferenciar maiúsculas.

## App (`/api`)

| Método e caminho | O que faz |
|---|---|
| `GET /api/alerts` | Lista os alertas, cada um com a última cotação do ativo (`lastQuote`, ou `null`) |
| `GET /api/alerts/{id}` | Um alerta |
| `POST /api/alerts` | Cria (`201` + `Location`) |
| `PUT /api/alerts/{id}` | Edita. Mudar ativo, direção ou limite volta o alerta para `ARMED` |
| `DELETE /api/alerts/{id}` | Apaga (`204`). Os eventos ficam no histórico, sem o vínculo |
| `GET /api/events?limit=50` | Histórico, mais novo primeiro (1 a 200) |
| `GET /api/quotes/{ativo}?hours=24` | Série de preços para o gráfico (1 a 720 h) |

Corpo de `POST`/`PUT /api/alerts`:

```json
{ "asset": "USD-BRL", "name": "Dólar", "direction": "BELOW", "threshold": 5.00, "rearmPct": 0.5, "enabled": true }
```

`name`, `rearmPct` (padrão 0,5%) e `enabled` são opcionais. `direction`: `BELOW` ou `ABOVE`.

Resposta (alerta):

```json
{
  "id": "6f1c…", "asset": "USD-BRL", "name": "Dólar", "direction": "BELOW",
  "threshold": 5.000000, "rearmPct": 0.500, "enabled": true,
  "state": "FIRED", "firedAt": "2026-11-10T17:52:00Z",
  "createdAt": "…", "updatedAt": "…", "version": 3,
  "lastQuote": { "price": 4.987000, "quotedAt": "2026-11-10T17:51:00Z", "source": "AwesomeAPI" }
}
```

## Checker (`/internal`)

O checker não guarda estado: a cada ciclo ele lê os alertas, grava cotações e eventos,
e entrega os pushes pendentes. A API aplica a mudança de estado do alerta na mesma
transação em que grava o evento.

| Método e caminho | O que faz |
|---|---|
| `GET /internal/alerts` | Alertas ativos: `id, asset, name, direction, threshold, rearmPct, state, version` |
| `POST /internal/quotes` | Lista de `{asset, price, quotedAt, source}` (`204`). A mesma cotação repetida é ignorada |
| `POST /internal/events` | Grava um evento. `201` gravou; `200` o mesmo `id` já existia (reenvio); `404` alerta apagado; `409` alerta mudou desde a leitura |
| `GET /internal/events/pending` | Outbox: eventos com `push: true` ainda não entregues, das últimas 24 h, mais antigo primeiro |
| `POST /internal/events/{id}/delivered` | Marca o push como entregue (`204`) |

Evento:

```json
{
  "id": "uuid gerado pelo checker", "type": "FIRED",
  "asset": "USD-BRL", "alertId": "6f1c…", "alertVersion": 2, "direction": "BELOW",
  "price": 4.987, "quotedAt": "2026-11-10T17:51:00Z", "occurredAt": "2026-11-10T17:52:00Z",
  "title": "Dólar abaixo de R$ 5,00", "message": "Agora: R$ 4,9870\nCotação de 10/11 14:51 (AwesomeAPI)",
  "push": true
}
```

| `type` | Quando | Muda o alerta | Push |
|---|---|---|---|
| `FIRED` | preço cruzou o limite (alerta `ARMED`) | `ARMED` → `FIRED` | sim |
| `REARMED` | preço se afastou `rearmPct` do limite (alerta `FIRED`) | `FIRED` → `ARMED` | não |
| `SOURCE_DOWN` | fonte sem cotação há `FAIL_NOTICE_MINUTES` | não | sim |
| `SOURCE_UP` | fonte voltou depois de um aviso | não | sim |

`FIRED` e `REARMED` exigem `alertId` e `alertVersion`. Se a versão não bate (a usuária
editou o alerta entre a leitura e o disparo), a API responde `409` e o checker descarta o
evento: o próximo ciclo reavalia com a regra nova.

### Por que um outbox

1. `POST /internal/events` grava o evento e muda o estado do alerta.
2. O checker manda o push e chama `/delivered`.
3. Se o push falhar, o evento continua em `/pending` e sai no ciclo seguinte.

Se a API cair no meio, nada é avaliado naquele ciclo, então ninguém recebe aviso em
dobro. Se o checker reenviar um evento por timeout, o `id` repetido não grava de novo.
