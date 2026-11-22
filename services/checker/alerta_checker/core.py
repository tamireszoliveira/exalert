"""Núcleo compartilhado: cotações, regra de disparo, formatação e envio de push.

Usado pelos dois modos do checker:
  - alerta_checker.cron    lê alerts.toml/state.json (GitHub Actions agendado)
  - alerta_checker.worker  lê os alertas da API e roda em laço (compose/Kubernetes)

Só biblioteca padrão; requer Python 3.11+.
"""

from __future__ import annotations

import http.client
import json
import math
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta, timezone

BRT = timezone(timedelta(hours=-3), "BRT")  # Brasília (sem horário de verão)
USER_AGENT = "alerta-cotacao/2.0"
SANDBOX_BRAPI = ("PETR4", "VALE3", "MGLU3", "ITUB4")  # tickers que a brapi atende sem token
EMOJI = {
    "chart_with_downwards_trend": "\U0001f4c9",
    "chart_with_upwards_trend": "\U0001f4c8",
    "warning": "⚠️",
    "white_check_mark": "✅",
}

FX_RE = re.compile(r"[A-Z]{3,6}-[A-Z]{3,6}")
STOCK_RE = re.compile(r"\^?[A-Z][A-Z0-9.]{1,11}")
TOPIC_RE = re.compile(r"[-_A-Za-z0-9]{1,64}")


class ConfigError(Exception):
    """alerts.toml ou variáveis de ambiente inválidos."""


class FetchError(Exception):
    """Falha ao consultar uma cotação ou ao enviar uma notificação."""

    def __init__(self, message: str, status: int | None = None):
        super().__init__(message)
        self.status = status


# --------------------------------------------------------------------------- formatação


_SWAP = str.maketrans(",.", ".,")


def is_fx(asset: str) -> bool:
    return "-" in asset


def fmt_number(value: float, decimals: int) -> str:
    """1234.5 -> '1.234,50' (padrão brasileiro)."""
    return f"{value:,.{decimals}f}".translate(_SWAP)


def _currency(asset: str, text: str) -> str:
    quote_ccy = asset.split("-", 1)[1] if is_fx(asset) else "BRL"
    return f"R$ {text}" if quote_ccy.startswith("BRL") else f"{text} {quote_ccy}"  # BRLT = real turismo


def fmt_price(asset: str, price: float) -> str:
    return _currency(asset, fmt_number(price, 4 if is_fx(asset) else 2))


def fmt_limit(asset: str, limit: float) -> str:
    decimals = next((d for d in range(2, 6) if round(limit, d) == limit), 6)
    return _currency(asset, fmt_number(limit, decimals))


def fmt_dt(moment: datetime) -> str:
    return moment.astimezone(BRT).strftime("%d/%m %H:%M")


def to_datetime(value: object) -> datetime | None:
    """Aceita epoch (s ou ms) ou ISO 8601; devolve None se não der para interpretar."""
    try:
        if value is None or isinstance(value, bool):
            return None
        if isinstance(value, (int, float)) or (isinstance(value, str) and value.strip().isdigit()):
            stamp = float(value)
            if stamp > 1e11:  # milissegundos
                stamp /= 1000
            return datetime.fromtimestamp(stamp, tz=UTC)
        if isinstance(value, str):
            moment = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
            return moment if moment.tzinfo else moment.replace(tzinfo=UTC)
    except (ValueError, OverflowError, OSError):
        return None
    return None


# --------------------------------------------------------------------------- alerta


@dataclass(frozen=True)
class Alert:
    asset: str  # "USD-BRL" (câmbio) ou "VALE3" (B3)
    below: bool  # True = avisa abaixo do limite; False = acima
    limit: float
    name: str

    @property
    def key(self) -> str:
        """Identidade no state.json (modo arquivo): mudar o limite cria um alerta novo (armado)."""
        return f"{self.asset}{'<' if self.below else '>'}{self.limit!r}"

    @property
    def text(self) -> str:
        side = "abaixo" if self.below else "acima"
        return f"{self.name} {side} de {fmt_limit(self.asset, self.limit)}"

    def hit(self, price: float) -> bool:
        return price < self.limit if self.below else price > self.limit

    def rearmed(self, price: float, rearm_pct: float) -> bool:
        margin = self.limit * rearm_pct / 100
        return price >= self.limit + margin if self.below else price <= self.limit - margin


@dataclass(frozen=True)
class Options:
    rearm_pct: float = 0.5  # % de afastamento do limite para o alerta voltar a valer
    fail_notice_min: float = 60  # minutos sem cotação até avisar que a fonte está falhando


def _env(name: str) -> str | None:
    value = os.environ.get(name, "").strip()  # secret não definido chega como string vazia
    return value or None


@dataclass(frozen=True)
class Settings:
    awesomeapi_url: str
    awesomeapi_token: str | None
    brapi_url: str
    brapi_token: str | None
    ntfy_server: str
    ntfy_topic: str | None
    ntfy_token: str | None
    telegram_api_url: str
    telegram_token: str | None
    telegram_chat_id: str | None

    @classmethod
    def from_env(cls) -> Settings:
        def url(name: str, default: str) -> str:
            return (_env(name) or default).rstrip("/")

        return cls(
            awesomeapi_url=url("AWESOMEAPI_URL", "https://economia.awesomeapi.com.br"),
            awesomeapi_token=_env("AWESOMEAPI_TOKEN"),
            brapi_url=url("BRAPI_URL", "https://brapi.dev"),
            brapi_token=_env("BRAPI_TOKEN"),
            ntfy_server=url("NTFY_SERVER", "https://ntfy.sh"),
            ntfy_topic=_env("NTFY_TOPIC"),
            ntfy_token=_env("NTFY_TOKEN"),
            telegram_api_url=url("TELEGRAM_API_URL", "https://api.telegram.org"),
            telegram_token=_env("TELEGRAM_BOT_TOKEN"),
            telegram_chat_id=_env("TELEGRAM_CHAT_ID"),
        )


# --------------------------------------------------------------------------- HTTP


def _retry_wait() -> float:
    try:
        return max(0.0, float(os.environ.get("ALERTA_RETRY_WAIT", "2")))
    except ValueError:
        return 2.0


def http_call(
    label: str,
    url: str,
    *,
    data: bytes | None = None,
    headers: dict[str, str] | None = None,
    timeout: float = 15.0,
    attempts: int = 3,
) -> bytes:
    """GET (ou POST, se houver data) com nova tentativa em erro de rede, 5xx e 429.

    As mensagens de erro citam só o label: a URL pode conter segredo (token do Telegram).
    """
    all_headers = {"User-Agent": USER_AGENT, "Accept": "application/json", **(headers or {})}
    method = "GET" if data is None else "POST"
    last, status = "erro desconhecido", None
    for attempt in range(1, attempts + 1):
        wait = _retry_wait() * attempt
        try:
            request = urllib.request.Request(url, data=data, headers=all_headers, method=method)
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return response.read()
        except urllib.error.HTTPError as exc:
            status = exc.code
            try:
                snippet = " ".join(exc.read(400).decode("utf-8", "replace").split())[:200]
            except (OSError, AttributeError, ValueError):
                snippet = ""
            last = f"HTTP {exc.code}" + (f" ({snippet})" if snippet else "")
            if exc.code != 429 and exc.code < 500:
                break  # erro do nosso lado (ticker, token...): repetir não resolve
            if exc.code == 429:
                try:
                    wait = max(0.0, float(exc.headers.get("Retry-After", "")))
                except (AttributeError, TypeError, ValueError):
                    pass
                if wait > 30:
                    break
        except (http.client.InvalidURL, ValueError):  # o texto dessas exceções traz a URL: não repassar
            status, last = None, "URL ou cabeçalho inválido (confira os valores configurados)"
            break
        except (urllib.error.URLError, http.client.HTTPException, OSError) as exc:
            status = None
            last = f"erro de rede ({getattr(exc, 'reason', exc)})"
        if attempt < attempts:
            time.sleep(wait)
    raise FetchError(f"{label}: {last}", status)


def _json(raw: bytes, label: str) -> object:
    try:
        return json.loads(raw)
    except ValueError:
        raise FetchError(f"{label}: resposta não é JSON") from None


# --------------------------------------------------------------------------- cotações


@dataclass(frozen=True)
class Quote:
    asset: str
    price: float
    as_of: datetime | None  # horário da cotação informado pela fonte
    source: str


def _price(value: object, label: str) -> float:
    try:
        price = math.nan if isinstance(value, bool) else float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        raise FetchError(f"{label}: preço ausente ou inválido na resposta") from None
    if not math.isfinite(price) or price <= 0:  # preço zerado/NaN não pode disparar "abaixo de"
        raise FetchError(f"{label}: preço fora do esperado ({price})")
    return price


def parse_awesomeapi(asset: str, payload: object) -> Quote:
    """{"USDBRL": {"bid": "5.2279", "ask": "5.2285", "timestamp": "1591109752", ...}}"""
    label = f"AwesomeAPI {asset}"
    item = payload.get(asset.replace("-", "")) if isinstance(payload, dict) else None
    if not isinstance(item, dict):
        raise FetchError(f"{label}: par não veio na resposta")
    return Quote(asset, _price(item.get("bid"), label), to_datetime(item.get("timestamp")), "AwesomeAPI")


def parse_brapi(asset: str, payload: object) -> Quote:
    """v2: {"results": [{"requestedSymbol", "symbol", "changed", "data": {"regularMarketPrice", ...}}]}

    A rota antiga (/api/quote/X) traz os mesmos campos direto no item, sem "data"; aceita as duas.
    """
    label = f"brapi {asset}"
    results = payload.get("results") if isinstance(payload, dict) else None
    items = [item for item in results if isinstance(item, dict)] if isinstance(results, list) else []
    wanted = asset.upper()
    match = next(
        (item for item in items if wanted in (str(item.get("requestedSymbol", "")).upper(), str(item.get("symbol", "")).upper())),
        None,
    )
    if match is None and len(items) == 1:  # pedimos um ticker só: o único item é o nosso
        match = items[0]
    if match is None:
        raise FetchError(f"{label}: ticker não veio na resposta (existe na B3?)")
    snapshot = match["data"] if isinstance(match.get("data"), dict) else match
    return Quote(
        asset,
        _price(snapshot.get("regularMarketPrice"), label),
        to_datetime(snapshot.get("regularMarketTime")),
        "brapi",
    )


def fetch_quote(asset: str, settings: Settings) -> Quote:
    if is_fx(asset):
        label = f"AwesomeAPI {asset}"
        headers = {"x-api-key": settings.awesomeapi_token} if settings.awesomeapi_token else {}
        raw = http_call(label, f"{settings.awesomeapi_url}/json/last/{asset}", headers=headers)
        return parse_awesomeapi(asset, _json(raw, label))

    label = f"brapi {asset}"
    headers = {"Authorization": f"Bearer {settings.brapi_token}"} if settings.brapi_token else {}
    url = f"{settings.brapi_url}/api/v2/stocks/quote?symbols={urllib.parse.quote(asset, safe='')}"
    try:
        raw = http_call(label, url, headers=headers)
    except FetchError as exc:
        if exc.status in (401, 403) and not settings.brapi_token:
            hint = f"sem BRAPI_TOKEN só funcionam {', '.join(SANDBOX_BRAPI)}"
            raise FetchError(f"{exc} [{hint}]", exc.status) from None
        raise
    return parse_brapi(asset, _json(raw, label))


# --------------------------------------------------------------------------- notificação


def log(message: str) -> None:
    print(message, flush=True)


def annotate(level: str, message: str) -> None:
    """No GitHub Actions vira anotação do run; fora dele, linha comum."""
    if os.environ.get("GITHUB_ACTIONS") == "true":
        escaped = message.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")
        log(f"::{level}::{escaped}")
    else:
        log(f"{'ERRO' if level == 'error' else 'AVISO'}: {message}")


class Notifier:
    """Envia por todos os canais configurados (ntfy e/ou Telegram)."""

    def __init__(self, settings: Settings, *, dry_run: bool = False):
        self.settings = settings
        self.dry_run = dry_run
        self.errors: list[str] = []
        self.channels = []
        if settings.ntfy_topic:
            if not TOPIC_RE.fullmatch(settings.ntfy_topic):
                raise ConfigError("NTFY_TOPIC inválido: use só letras, números, - e _ (até 64 caracteres)")
            self.channels.append(self._ntfy)
        if settings.telegram_token or settings.telegram_chat_id:
            if not (settings.telegram_token and settings.telegram_chat_id):
                raise ConfigError("para o Telegram, defina TELEGRAM_BOT_TOKEN e TELEGRAM_CHAT_ID")
            self.channels.append(self._telegram)
        if not self.channels and not dry_run:
            raise ConfigError("nenhum canal de push: defina NTFY_TOPIC (ou TELEGRAM_BOT_TOKEN + TELEGRAM_CHAT_ID)")

    def send(self, title: str, message: str, *, tags: tuple[str, ...] = (), priority: int = 3) -> bool:
        """True se ao menos um canal entregou. Falhas ficam em self.errors."""
        if self.dry_run:
            log(f"[dry-run] notificação: {title} | {message.replace(chr(10), ' | ')}")
            return True
        delivered = False
        for channel in self.channels:
            try:
                channel(title, message, tags, priority)
                delivered = True
            except FetchError as exc:
                self.errors.append(str(exc))
                annotate("error", f"notificação não enviada: {exc}")
        return delivered

    def _ntfy(self, title: str, message: str, tags: tuple[str, ...], priority: int) -> None:
        settings = self.settings
        payload = {
            "topic": settings.ntfy_topic,
            "title": title,
            "message": message,
            "tags": list(tags),
            "priority": priority,
        }
        headers = {"Content-Type": "application/json"}
        if settings.ntfy_token:
            headers["Authorization"] = f"Bearer {settings.ntfy_token}"
        # Publicação em JSON vai para a raiz do servidor, não para /<tópico>.
        http_call("ntfy", f"{settings.ntfy_server}/", data=json.dumps(payload).encode(), headers=headers)

    def _telegram(self, title: str, message: str, tags: tuple[str, ...], priority: int) -> None:
        settings = self.settings
        emoji = EMOJI.get(tags[0], "") if tags else ""
        body = {"chat_id": settings.telegram_chat_id, "text": f"{emoji} {title}\n{message}".strip()}
        raw = http_call(
            "Telegram",
            f"{settings.telegram_api_url}/bot{settings.telegram_token}/sendMessage",
            data=json.dumps(body).encode(),
            headers={"Content-Type": "application/json"},
        )
        answer = _json(raw, "Telegram")
        if not (isinstance(answer, dict) and answer.get("ok")):
            raise FetchError("Telegram: resposta sem ok=true")


# --------------------------------------------------------------------------- mensagens
# Os dois modos usam os mesmos textos, para o push ser igual venha de onde vier.

TAG_DOWN = "chart_with_downwards_trend"
TAG_UP = "chart_with_upwards_trend"
TAG_WARNING = "warning"
TAG_OK = "white_check_mark"


def quote_line(quote: Quote) -> str:
    if quote.as_of is None:
        return f"Fonte: {quote.source}"
    return f"Cotação de {fmt_dt(quote.as_of)} ({quote.source})"


def alert_tag(alert: Alert) -> str:
    return TAG_DOWN if alert.below else TAG_UP


def fired_message(alert: Alert, quote: Quote) -> str:
    return f"Agora: {fmt_price(alert.asset, quote.price)}\n{quote_line(quote)}"


def outage_title(asset: str) -> str:
    return f"Sem cotação de {asset}"


def outage_message(since: datetime, error: str) -> str:
    return f"Falhando desde {fmt_dt(since)}.\nÚltimo erro: {error[:300]}"


def recovery_title(asset: str) -> str:
    return f"Cotação de {asset} normalizada"


def recovery_message(quote: Quote) -> str:
    return f"Agora: {fmt_price(quote.asset, quote.price)}"
