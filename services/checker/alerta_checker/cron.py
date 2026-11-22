"""Modo arquivo: alerts.toml + state.json, uma checagem por execução.

É o modo do GitHub Actions agendado (fase 1): cada run consulta as cotações,
avisa por push o que disparou e grava o estado, que o workflow commita.

    python -m alerta_checker.cron --config alertas/alerts.toml --state alertas/state.json

Códigos de saída: 0 = ok; 1 = notificação não entregue (ou cotação indisponível
com --strict); 2 = configuração inválida.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import sys
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path

try:
    import tomllib
except ModuleNotFoundError:  # entrou na biblioteca padrão na 3.11
    sys.exit("o checker requer Python 3.11 ou mais novo.")

from .core import (
    BRT,
    FX_RE,
    STOCK_RE,
    TAG_OK,
    TAG_WARNING,
    Alert,
    ConfigError,
    FetchError,
    Notifier,
    Options,
    Quote,
    Settings,
    alert_tag,
    annotate,
    fetch_quote,
    fired_message,
    fmt_dt,
    fmt_price,
    log,
    outage_message,
    outage_title,
    quote_line,
    recovery_message,
    recovery_title,
    to_datetime,
)


def _number(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        return None
    return float(value)


def load_config(path: Path) -> tuple[list[Alert], Options]:
    try:
        with path.open("rb") as handle:
            raw = tomllib.load(handle)
    except FileNotFoundError:
        raise ConfigError(f"{path} não encontrado") from None
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"{path}: TOML inválido ({exc})") from None

    unknown = sorted(set(raw) - {"config", "alerta"})
    if unknown:
        raise ConfigError(f"seção desconhecida: {', '.join(unknown)} (use [config] e [[alerta]])")

    cfg = raw.get("config", {})
    if not isinstance(cfg, dict):
        raise ConfigError("[config] deve ser uma tabela")
    unknown = sorted(set(cfg) - {"rearme_pct", "falha_aviso_min"})
    if unknown:
        raise ConfigError(f"[config]: chave desconhecida: {', '.join(unknown)}")
    rearm = _number(cfg.get("rearme_pct", Options.rearm_pct))
    if rearm is None or not 0 <= rearm <= 50:
        raise ConfigError("[config] rearme_pct deve ser um número entre 0 e 50")
    notice = _number(cfg.get("falha_aviso_min", Options.fail_notice_min))
    if notice is None or notice < 0:
        raise ConfigError("[config] falha_aviso_min deve ser um número >= 0")

    entries = raw.get("alerta")
    if isinstance(entries, dict):
        raise ConfigError("use [[alerta]] (dois colchetes), um bloco por alerta")
    if not isinstance(entries, list) or not entries:
        raise ConfigError("nenhum [[alerta]] definido")

    alerts: dict[str, Alert] = {}
    for number, entry in enumerate(entries, 1):
        where = f"alerta nº {number}"
        if not isinstance(entry, dict):
            raise ConfigError("use [[alerta]] (dois colchetes), um bloco por alerta")
        unknown = sorted(set(entry) - {"ativo", "abaixo", "acima", "nome"})
        if unknown:
            raise ConfigError(f"{where}: chave desconhecida: {', '.join(unknown)} (use ativo, abaixo, acima, nome)")
        asset = entry.get("ativo")
        if not isinstance(asset, str) or not asset.strip():
            raise ConfigError(f'{where}: falta ativo (ex.: "USD-BRL" ou "VALE3")')
        asset = asset.strip().upper()
        if not (FX_RE.fullmatch(asset) or STOCK_RE.fullmatch(asset)):
            raise ConfigError(f'{where}: ativo "{asset}" inválido (use par de moedas como "USD-BRL" ou ticker da B3 como "VALE3")')
        name = entry.get("nome", asset)
        if not isinstance(name, str) or not name.strip():
            raise ConfigError(f"{where}: nome deve ser um texto")
        if "abaixo" not in entry and "acima" not in entry:
            raise ConfigError(f"{where} ({asset}): defina abaixo e/ou acima")
        for side in ("abaixo", "acima"):
            if side not in entry:
                continue
            limit = _number(entry[side])
            if limit is None or limit <= 0:
                raise ConfigError(f"{where} ({asset}): {side} deve ser um número maior que zero")
            alert = Alert(asset, side == "abaixo", limit, name.strip())
            alerts.setdefault(alert.key, alert)
    return list(alerts.values()), Options(rearm, notice)


# --------------------------------------------------------------------------- estado


def load_state(path: Path) -> dict:
    """{"alertas": {chave: {"desde", "preco"}}, "falhas": {ativo: {"desde", "avisado"}}}

    Só alertas já disparados ficam gravados; alerta armado não tem entrada.
    """
    state: dict = {"alertas": {}, "falhas": {}}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return state
    except (OSError, ValueError) as exc:
        annotate("warning", f"{path} ilegível ({exc}); recomeçando do zero, alertas ativos podem repetir")
        return state
    if isinstance(raw, dict):
        for section in state:
            if isinstance(raw.get(section), dict):
                state[section] = {k: v for k, v in raw[section].items() if isinstance(v, dict)}
    return state


def save_state(path: Path, state: dict) -> bool:
    """Grava só se mudou (cada gravação vira commit no workflow). Devolve True se gravou."""
    text = json.dumps(state, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    try:
        current = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        if not state["alertas"] and not state["falhas"]:
            return False
        current = None
    except OSError:
        current = None
    if current == text:
        return False
    temp = path.with_name(path.name + ".tmp")
    temp.write_text(text, encoding="utf-8")
    temp.replace(path)
    return True


# --------------------------------------------------------------------------- checagem


@dataclass
class Row:
    alert: Alert
    quote: Quote | None
    status: str


@dataclass
class Outcome:
    rows: list[Row] = field(default_factory=list)
    quote_errors: dict[str, str] = field(default_factory=dict)
    changes: list[str] = field(default_factory=list)  # mudanças de estado (vão para a mensagem de commit)


def check(alerts: list[Alert], options: Options, state: dict, now: datetime, fetch, send) -> Outcome:
    """Avalia os alertas e atualiza state. fetch(ativo) -> Quote; send(título, texto, tags=, priority=) -> bool."""
    out = Outcome()
    assets = list(dict.fromkeys(alert.asset for alert in alerts))
    quotes: dict[str, Quote] = {}
    for asset in assets:
        try:
            quotes[asset] = fetch(asset)
        except FetchError as exc:
            out.quote_errors[asset] = str(exc)

    fired = state["alertas"]
    for alert in alerts:
        quote = quotes.get(alert.asset)
        if quote is None:
            out.rows.append(Row(alert, None, "sem cotação"))
            continue
        price = fmt_price(alert.asset, quote.price)
        if alert.key not in fired:
            if not alert.hit(quote.price):
                status = "aguardando"
            else:
                if send(alert.text, fired_message(alert, quote), tags=(alert_tag(alert),), priority=4):
                    fired[alert.key] = {"desde": now.isoformat(timespec="seconds"), "preco": quote.price}
                    out.changes.append(f"{alert.text} ({price})")
                    status = "DISPAROU, notificação enviada"
                else:  # estado não avança: a próxima execução tenta de novo
                    status = "disparou, mas a notificação falhou"
        elif alert.rearmed(quote.price, options.rearm_pct):
            del fired[alert.key]
            out.changes.append(f"rearmado: {alert.text} ({price})")
            status = "rearmado"
        else:
            since = to_datetime(fired[alert.key].get("desde"))
            status = f"já avisado em {fmt_dt(since)}" if since else "já avisado"
        out.rows.append(Row(alert, quote, status))

    valid = {alert.key for alert in alerts}
    for key in [key for key in fired if key not in valid]:
        del fired[key]
        out.changes.append(f"removido: {key}")

    # Fonte falhando por muito tempo vira aviso: monitor mudo parece "preço não cruzou".
    failures = state["falhas"]
    for asset in assets:
        entry = failures.get(asset)
        error = out.quote_errors.get(asset)
        if error is not None:
            if entry is None:
                entry = failures[asset] = {"desde": now.isoformat(timespec="seconds"), "avisado": False}
                out.changes.append(f"sem cotação: {asset}")
            since = to_datetime(entry.get("desde")) or now
            due = not entry.get("avisado") and now - since >= timedelta(minutes=options.fail_notice_min)
            if due and send(outage_title(asset), outage_message(since, error), tags=(TAG_WARNING,), priority=3):
                entry["avisado"] = True
                out.changes.append(f"aviso de falha: {asset}")
        elif entry is not None:
            del failures[asset]
            out.changes.append(f"cotação voltou: {asset}")
            if entry.get("avisado"):
                send(recovery_title(asset), recovery_message(quotes[asset]), tags=(TAG_OK,), priority=2)
    for asset in [asset for asset in failures if asset not in assets]:
        del failures[asset]
    return out


def report(outcome: Outcome, *, strict: bool = False) -> None:
    for row in outcome.rows:
        if row.quote is None:
            log(f"{row.alert.text}: {row.status}")
        else:
            log(f"{row.alert.text}: {fmt_price(row.alert.asset, row.quote.price)} [{quote_line(row.quote)}] -> {row.status}")
    for error in outcome.quote_errors.values():
        annotate("error" if strict else "warning", error)

    summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary_path:
        lines = ["| Alerta | Cotação | Referência | Situação |", "|---|---|---|---|"]
        for row in outcome.rows:
            if row.quote is None:
                cell = outcome.quote_errors.get(row.alert.asset, "").replace("|", "/")
                lines.append(f"| {row.alert.text} | n/d | {cell} | {row.status} |")
            else:
                price = fmt_price(row.alert.asset, row.quote.price)
                lines.append(f"| {row.alert.text} | {price} | {quote_line(row.quote)} | {row.status} |")
        with open(summary_path, "a", encoding="utf-8") as handle:
            handle.write("\n".join(lines) + "\n")

    output_path = os.environ.get("GITHUB_OUTPUT")
    if output_path and outcome.changes:
        resumo = "; ".join(outcome.changes).replace("\n", " ")[:200]
        with open(output_path, "a", encoding="utf-8") as handle:
            handle.write(f"resumo={resumo}\n")


def run_test(alerts: list[Alert], fetch, notifier: Notifier) -> int:
    """Envia um push de teste com as cotações atuais; não mexe no estado."""
    lines, failed = [], False
    for asset in dict.fromkeys(alert.asset for alert in alerts):
        try:
            lines.append(f"{asset}: {fmt_price(asset, fetch(asset).price)}")
        except FetchError as exc:
            failed = True
            lines.append(f"{asset}: falhou")
            annotate("error", str(exc))
    lines.append(f"{len(alerts)} alerta(s) configurado(s).")
    log("\n".join(lines))
    delivered = notifier.send("Teste do alerta de cotação", "\n".join(lines), tags=(TAG_OK,))
    if delivered and not notifier.errors:
        log("Notificação de teste enviada.")
    return 1 if failed or not delivered or notifier.errors else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Checa cotações e envia push quando um alerta dispara.")
    parser.add_argument("--config", default="alerts.toml", help="arquivo de alertas (padrão: alerts.toml)")
    parser.add_argument("--state", default="state.json", help="arquivo de estado (padrão: state.json)")
    parser.add_argument("--dry-run", action="store_true", help="mostra o que faria; não notifica nem grava estado")
    parser.add_argument("--test-notify", action="store_true", help="envia um push de teste com as cotações atuais e sai")
    parser.add_argument("--strict", action="store_true", help="sai com 1 se alguma cotação não puder ser obtida")
    args = parser.parse_args(argv)

    try:
        alerts, options = load_config(Path(args.config))
        settings = Settings.from_env()
        notifier = Notifier(settings, dry_run=args.dry_run)
    except ConfigError as exc:
        annotate("error", f"configuração: {exc}")
        return 2

    def fetch(asset: str) -> Quote:
        return fetch_quote(asset, settings)

    if args.test_notify:
        return run_test(alerts, fetch, notifier)

    state_path = Path(args.state)
    state = load_state(state_path)
    outcome = check(alerts, options, state, datetime.now(BRT), fetch, notifier.send)
    report(outcome, strict=args.strict)
    if not args.dry_run:
        save_state(state_path, state)
    if notifier.errors or (args.strict and outcome.quote_errors):
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
