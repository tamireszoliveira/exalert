"""Modo API: laço contínuo que lê os alertas da API, consulta as cotações e avisa.

Roda como serviço (compose ou Kubernetes) e sempre com UMA réplica: duas
mandariam cada push em dobro. A cada ciclo:

  1. GET  /internal/alerts                 alertas ativos, com estado e versão
  2. consulta AwesomeAPI/brapi             respeitando o intervalo mínimo da brapi
  3. POST /internal/quotes                 cotações novas (alimentam o gráfico do app)
  4. POST /internal/events                 disparo, rearme e aviso de fonte; a API muda
                                           o estado do alerta e guarda o texto do push
  5. GET  /internal/events/pending         outbox: eventos ainda não entregues
     POST /internal/events/{id}/delivered  depois que o push saiu

A API é a fonte da verdade. Push que falha continua pendente e é reenviado no
ciclo seguinte (por até 24 h). Se a API estiver fora, o ciclo não avalia nada,
então ninguém recebe aviso em dobro.

    python -m alerta_checker.worker          # laço (o que roda no contêiner)
    python -m alerta_checker.worker --once   # um ciclo, ignorando o horário, e sai
"""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import signal
import sys
import threading
import time
import traceback
import urllib.parse
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from datetime import time as clock_time
from pathlib import Path

from .core import (
    BRT,
    TAG_DOWN,
    TAG_OK,
    TAG_UP,
    TAG_WARNING,
    Alert,
    ConfigError,
    FetchError,
    Notifier,
    Quote,
    Settings,
    fetch_quote,
    fired_message,
    fmt_price,
    http_call,
    is_fx,
    log,
    outage_message,
    outage_title,
    recovery_message,
    recovery_title,
)

FIRED, REARMED, SOURCE_DOWN, SOURCE_UP = "FIRED", "REARMED", "SOURCE_DOWN", "SOURCE_UP"
HOURS_RE = re.compile(r"(\d{1,2}):(\d{2})-(\d{1,2}):(\d{2})")


# --------------------------------------------------------------------------- configuração


def _env(name: str) -> str | None:
    value = os.environ.get(name, "").strip()
    return value or None


def _number_env(name: str, default: float, minimum: float) -> float:
    raw = _env(name)
    if raw is None:
        return default
    try:
        value = float(raw)
    except ValueError:
        raise ConfigError(f"{name} deve ser um número (recebi {raw!r})") from None
    if not math.isfinite(value) or value < minimum:
        raise ConfigError(f"{name} deve ser um número >= {minimum:g}")
    return value


def parse_market_hours(raw: str | None) -> tuple[clock_time, clock_time] | None:
    """'09:00-18:30' -> janela de seg a sex (horário de Brasília). Vazio ou 'sempre' -> sem janela."""
    text = (raw or "").strip().lower()
    if text in ("", "sempre", "always", "24h"):
        return None
    match = HOURS_RE.fullmatch(text)
    if not match:
        raise ConfigError(f"MARKET_HOURS inválido: {raw!r} (use HH:MM-HH:MM, ex.: 09:00-18:30, ou 'sempre')")
    h1, m1, h2, m2 = (int(part) for part in match.groups())
    try:
        start, end = clock_time(h1, m1), clock_time(h2, m2)
    except ValueError:
        raise ConfigError(f"MARKET_HOURS inválido: {raw!r} (hora fora de 00:00-23:59)") from None
    if start >= end:
        raise ConfigError(f"MARKET_HOURS inválido: {raw!r} (o início precisa ser antes do fim)")
    return start, end


def in_market(now: datetime, hours: tuple[clock_time, clock_time] | None) -> bool:
    if hours is None:
        return True
    local = now.astimezone(BRT)
    return local.weekday() < 5 and hours[0] <= local.time() < hours[1]


def describe_hours(hours: tuple[clock_time, clock_time] | None) -> str:
    if hours is None:
        return "o tempo todo"
    return f"seg-sex {hours[0]:%H:%M}-{hours[1]:%H:%M} (Brasília)"


@dataclass(frozen=True)
class WorkerSettings:
    api_url: str
    checker_token: str
    interval: float  # segundos entre ciclos
    stock_min_interval: float  # segundos entre duas consultas do mesmo ticker na brapi
    market_hours: tuple[clock_time, clock_time] | None
    fail_notice: timedelta  # tempo sem cotação até avisar por push
    heartbeat: Path | None
    dry_run: bool

    @classmethod
    def from_env(cls) -> WorkerSettings:
        token = _env("CHECKER_TOKEN")
        if not token:
            raise ConfigError("CHECKER_TOKEN não definido (é o mesmo token configurado na API)")
        api_url = (_env("API_URL") or "http://localhost:8080").rstrip("/")
        if not api_url.startswith(("http://", "https://")):
            raise ConfigError("API_URL deve começar com http:// ou https://")
        heartbeat = _env("HEARTBEAT_FILE") or "/tmp/alerta-checker.heartbeat"
        return cls(
            api_url=api_url,
            checker_token=token,
            interval=_number_env("CHECK_INTERVAL_SECONDS", 120, minimum=5),
            stock_min_interval=_number_env("STOCK_MIN_INTERVAL_SECONDS", 300, minimum=0),
            market_hours=parse_market_hours(os.environ.get("MARKET_HOURS", "09:00-18:30")),
            fail_notice=timedelta(minutes=_number_env("FAIL_NOTICE_MINUTES", 60, minimum=0)),
            heartbeat=None if heartbeat.lower() == "off" else Path(heartbeat),
            dry_run=(_env("NOTIFY_DRY_RUN") or "").lower() in ("1", "true", "sim", "yes"),
        )


# --------------------------------------------------------------------------- modelo


@dataclass(frozen=True)
class Watched:
    """Alerta como a API devolve em /internal/alerts."""

    id: str
    version: int
    fired: bool
    rearm_pct: float
    alert: Alert


def parse_alerts(payload: object) -> list[Watched]:
    if not isinstance(payload, list):
        raise FetchError("API: /internal/alerts não devolveu uma lista")
    items = []
    for raw in payload:
        try:
            below = {"BELOW": True, "ABOVE": False}[raw["direction"]]
            asset = str(raw["asset"]).strip().upper()
            limit = float(raw["threshold"])
            rearm = float(raw.get("rearmPct", 0.5))
            if not (math.isfinite(limit) and limit > 0 and math.isfinite(rearm) and rearm >= 0):
                raise ValueError(raw)
            alert = Alert(asset, below, limit, str(raw.get("name") or asset))
            items.append(Watched(str(raw["id"]), int(raw["version"]), raw["state"] == FIRED, rearm, alert))
        except (KeyError, TypeError, ValueError):
            log(f"AVISO: alerta ignorado, formato inesperado: {str(raw)[:200]}")
    return items


def iso_utc(moment: datetime) -> str:
    return moment.astimezone(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


@dataclass(frozen=True)
class Event:
    type: str
    asset: str
    title: str
    message: str
    push: bool  # vira notificação (entra no outbox da API)
    occurred_at: datetime
    alert_id: str | None = None
    alert_version: int | None = None
    direction: str | None = None
    price: float | None = None
    quoted_at: datetime | None = None
    id: str = field(default_factory=lambda: str(uuid.uuid4()))

    def payload(self) -> dict:
        return {
            "id": self.id,
            "type": self.type,
            "asset": self.asset,
            "alertId": self.alert_id,
            "alertVersion": self.alert_version,
            "direction": self.direction,
            "price": self.price,
            "quotedAt": iso_utc(self.quoted_at) if self.quoted_at else None,
            "occurredAt": iso_utc(self.occurred_at),
            "title": self.title,
            "message": self.message,
            "push": self.push,
        }


def evaluate(watched: list[Watched], quotes: dict[str, Quote], now: datetime) -> list[Event]:
    """Regra de disparo, a mesma do modo arquivo: avisa uma vez e rearma longe do limite."""
    events = []
    for item in watched:
        quote = quotes.get(item.alert.asset)
        if quote is None:
            continue
        common = {
            "asset": item.alert.asset,
            "occurred_at": now,
            "alert_id": item.id,
            "alert_version": item.version,
            "direction": "BELOW" if item.alert.below else "ABOVE",
            "price": quote.price,
            "quoted_at": quote.as_of or now,
        }
        if not item.fired and item.alert.hit(quote.price):
            events.append(Event(FIRED, title=item.alert.text, message=fired_message(item.alert, quote), push=True, **common))
        elif item.fired and item.alert.rearmed(quote.price, item.rearm_pct):
            text = f"Rearmado: {item.alert.text}"
            events.append(Event(REARMED, title=text, message=f"Agora: {fmt_price(quote.asset, quote.price)}", push=False, **common))
    return events


@dataclass
class Outage:
    since: datetime
    warned: bool = False


class SourceHealth:
    """Fonte sem cotação por muito tempo vira push: monitor mudo parece "preço não cruzou"."""

    def __init__(self, notice_after: timedelta):
        self.notice_after = notice_after
        self.down: dict[str, Outage] = {}

    def observe(self, assets: list[str], errors: dict[str, str], quotes: dict[str, Quote], now: datetime) -> list[Event]:
        events = []
        for asset in assets:
            outage = self.down.get(asset)
            if asset in errors:
                if outage is None:
                    outage = self.down[asset] = Outage(now)
                if not outage.warned and now - outage.since >= self.notice_after:
                    message = outage_message(outage.since, errors[asset])
                    events.append(Event(SOURCE_DOWN, asset, outage_title(asset), message, True, now))
            elif outage is not None:
                if outage.warned:
                    quote = quotes[asset]
                    events.append(Event(SOURCE_UP, asset, recovery_title(asset), recovery_message(quote), True, now, price=quote.price))
                else:
                    del self.down[asset]  # falha curta: some sem barulho
        for asset in [asset for asset in self.down if asset not in assets]:
            del self.down[asset]
        return events

    def confirm(self, event: Event) -> None:
        """Chamado quando a API gravou o evento: só então o aviso conta como dado."""
        if event.type == SOURCE_DOWN and event.asset in self.down:
            self.down[event.asset].warned = True
        elif event.type == SOURCE_UP:
            self.down.pop(event.asset, None)


class QuoteBook:
    """Consulta cotações respeitando um intervalo mínimo por ticker da B3.

    A brapi grátis tem cota mensal e atraso de ~30 min; consultar a cada ciclo só gastaria cota.
    """

    def __init__(self, fetch: Callable[[str], Quote], stock_min_interval: float, monotonic: Callable[[], float]):
        self.fetch = fetch
        self.stock_min_interval = stock_min_interval
        self.monotonic = monotonic
        self.cache: dict[str, tuple[float, Quote]] = {}

    def get(self, asset: str) -> tuple[Quote | None, str | None, bool]:
        """(cotação, erro, é_nova)."""
        now = self.monotonic()
        cached = self.cache.get(asset)
        if cached and not is_fx(asset) and now - cached[0] < self.stock_min_interval:
            return cached[1], None, False
        try:
            quote = self.fetch(asset)
        except FetchError as exc:
            return None, str(exc), False
        self.cache[asset] = (now, quote)
        return quote, None, True

    def keep_only(self, assets: list[str]) -> None:
        for asset in [asset for asset in self.cache if asset not in assets]:
            del self.cache[asset]


# --------------------------------------------------------------------------- API


class ApiClient:
    """Cliente dos endpoints internos da API (autenticados com CHECKER_TOKEN)."""

    def __init__(self, base_url: str, token: str, *, timeout: float = 10.0):
        self.base_url = base_url.rstrip("/")
        self.token = token
        self.timeout = timeout

    def _call(self, path: str, payload: object = None, *, post: bool = False) -> object:
        headers = {"Authorization": f"Bearer {self.token}"}
        data = None
        if payload is not None:
            data = json.dumps(payload, ensure_ascii=False).encode()
            headers["Content-Type"] = "application/json"
        elif post:
            data = b""
        label = f"API {'GET' if data is None else 'POST'} {path}"
        raw = http_call(label, self.base_url + path, data=data, headers=headers, timeout=self.timeout)
        if not raw.strip():
            return None
        try:
            return json.loads(raw)
        except ValueError:
            raise FetchError(f"{label}: resposta não é JSON") from None

    def alerts(self) -> list[Watched]:
        return parse_alerts(self._call("/internal/alerts"))

    def post_quotes(self, quotes: list[Quote], received_at: datetime) -> None:
        body = [{"asset": q.asset, "price": q.price, "quotedAt": iso_utc(q.as_of or received_at), "source": q.source} for q in quotes]
        self._call("/internal/quotes", body)

    def post_event(self, event: Event) -> bool:
        """True se a API gravou (ou já tinha) o evento; False se ficou obsoleto.

        409: o alerta mudou depois da leitura (limite editado, outro estado).
        404: o alerta foi apagado.
        """
        try:
            self._call("/internal/events", event.payload())
        except FetchError as exc:
            if exc.status in (404, 409):
                return False
            raise
        return True

    def pending(self) -> list[dict]:
        payload = self._call("/internal/events/pending")
        if not isinstance(payload, list):
            raise FetchError("API: /internal/events/pending não devolveu uma lista")
        return [item for item in payload if isinstance(item, dict) and item.get("id")]

    def mark_delivered(self, event_id: str) -> None:
        self._call(f"/internal/events/{urllib.parse.quote(str(event_id), safe='')}/delivered", post=True)


# --------------------------------------------------------------------------- ciclo


def push_style(event: dict) -> tuple[tuple[str, ...], int]:
    kind = event.get("type")
    if kind == FIRED:
        return ((TAG_DOWN if event.get("direction") == "BELOW" else TAG_UP),), 4
    if kind == SOURCE_DOWN:
        return (TAG_WARNING,), 3
    if kind == SOURCE_UP:
        return (TAG_OK,), 2
    return (), 3


@dataclass
class CycleReport:
    market_open: bool = True
    alerts: int = 0
    quotes: dict[str, Quote] = field(default_factory=dict)
    errors: dict[str, str] = field(default_factory=dict)
    events: list[Event] = field(default_factory=list)
    stale: int = 0
    delivered: int = 0
    undelivered: int = 0

    def summary(self) -> str:
        parts = []
        if self.market_open:
            prices = ", ".join(f"{asset} {fmt_price(asset, quote.price)}" for asset, quote in self.quotes.items())
            parts.append(f"{self.alerts} alerta(s)" + (f" | {prices}" if prices else ""))
            if self.errors:
                parts.append(f"sem cotação: {', '.join(self.errors)}")
            if self.events:
                parts.append(f"{len(self.events)} evento(s)")
            if self.stale:
                parts.append(f"{self.stale} evento(s) descartado(s): alerta mudou")
        if self.delivered or self.undelivered:
            parts.append(f"push: {self.delivered} entregue(s), {self.undelivered} pendente(s)")
        return "ciclo: " + (" | ".join(parts) if parts else "fora do pregão, nada pendente")


class Worker:
    def __init__(
        self,
        settings: WorkerSettings,
        api: ApiClient,
        fetch: Callable[[str], Quote],
        notifier: Notifier,
        *,
        clock: Callable[[], datetime] = lambda: datetime.now(BRT),
        monotonic: Callable[[], float] = time.monotonic,
    ):
        self.settings = settings
        self.api = api
        self.notifier = notifier
        self.clock = clock
        self.book = QuoteBook(fetch, settings.stock_min_interval, monotonic)
        self.health = SourceHealth(settings.fail_notice)

    def cycle(self, *, market_open: bool | None = None) -> CycleReport:
        now = self.clock()
        report = CycleReport(market_open=in_market(now, self.settings.market_hours) if market_open is None else market_open)
        if report.market_open:
            self._check(now, report)
        self._deliver(report)
        return report

    def _check(self, now: datetime, report: CycleReport) -> None:
        watched = self.api.alerts()
        report.alerts = len(watched)
        assets = list(dict.fromkeys(item.alert.asset for item in watched))
        self.book.keep_only(assets)
        fresh = []
        for asset in assets:
            quote, error, is_new = self.book.get(asset)
            if quote is None:
                report.errors[asset] = error or "erro desconhecido"
                log(f"AVISO: {error}")
                continue
            report.quotes[asset] = quote
            if is_new:
                fresh.append(quote)
        if fresh:
            self.api.post_quotes(fresh, now)

        for event in evaluate(watched, report.quotes, now) + self.health.observe(assets, report.errors, report.quotes, now):
            if self.api.post_event(event):
                self.health.confirm(event)
                report.events.append(event)
                log(f"{event.type}: {event.title}" + (f" ({fmt_price(event.asset, event.price)})" if event.price else ""))
            else:
                report.stale += 1

    def _deliver(self, report: CycleReport) -> None:
        self.notifier.errors.clear()  # o worker não termina: não acumular erro de ciclos passados
        for event in self.api.pending():
            tags, priority = push_style(event)
            if self.notifier.send(str(event.get("title", "")), str(event.get("message", "")), tags=tags, priority=priority):
                self.api.mark_delivered(event["id"])
                report.delivered += 1
            else:
                report.undelivered += 1


# --------------------------------------------------------------------------- laço


def touch(path: Path | None) -> None:
    if path is None:
        return
    try:
        path.write_text(datetime.now(UTC).isoformat(timespec="seconds") + "\n", encoding="utf-8")
    except OSError as exc:
        log(f"AVISO: não consegui gravar o heartbeat em {path} ({exc})")


def run_forever(worker: Worker, settings: WorkerSettings, stop: threading.Event) -> None:
    log(
        f"checker iniciado: API {settings.api_url}, ciclo a cada {settings.interval:g}s, "
        f"cotações {describe_hours(settings.market_hours)}" + (", push em dry-run (só no log)" if settings.dry_run else "")
    )
    touch(settings.heartbeat)
    was_open = None
    while not stop.is_set():
        try:
            report = worker.cycle()
            if report.market_open != was_open and was_open is not None:
                log("pregão aberto: checando cotações" if report.market_open else "fora do horário: só entrega de push pendente")
            was_open = report.market_open
            if report.market_open or report.delivered or report.undelivered:
                log(report.summary())
        except FetchError as exc:
            log(f"ERRO: ciclo interrompido: {exc}")
        except Exception:  # o laço não pode morrer por um erro inesperado; o próximo ciclo tenta de novo
            log("ERRO inesperado no ciclo:\n" + traceback.format_exc())
        touch(settings.heartbeat)
        stop.wait(settings.interval)
    log("checker encerrado")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Checa as cotações dos alertas cadastrados na API e envia push.")
    parser.add_argument("--once", action="store_true", help="roda um ciclo, ignorando o horário de pregão, e sai")
    args = parser.parse_args(argv)

    try:
        settings = Settings.from_env()
        worker_settings = WorkerSettings.from_env()
        notifier = Notifier(settings, dry_run=worker_settings.dry_run)
    except ConfigError as exc:
        log(f"ERRO de configuração: {exc}")
        return 2

    api = ApiClient(worker_settings.api_url, worker_settings.checker_token)
    worker = Worker(worker_settings, api, lambda asset: fetch_quote(asset, settings), notifier)

    if args.once:
        try:
            report = worker.cycle(market_open=True)
        except FetchError as exc:
            log(f"ERRO: {exc}")
            return 1
        log(report.summary())
        return 1 if report.undelivered else 0

    stop = threading.Event()
    for signum in (signal.SIGTERM, signal.SIGINT):
        signal.signal(signum, lambda *_: stop.set())
    run_forever(worker, worker_settings, stop)
    return 0


if __name__ == "__main__":
    sys.exit(main())
