"""Testes do modo arquivo (alerta_checker.cron) e do núcleo compartilhado.

Unitários (formatação, alerts.toml, leitura das respostas das APIs, regra de
disparo/rearme) e ponta a ponta: o módulo roda de verdade, como no GitHub
Actions, contra um servidor HTTP local que imita AwesomeAPI, brapi, ntfy e Telegram.

    cd services/checker && python3 -m unittest discover -s tests -v
"""

import json
import os
import subprocess
import sys
import tempfile
import threading
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path

from alerta_checker import core, cron

from fakes import STOCK_TIME, FakeApis

CHECKER = Path(__file__).resolve().parent.parent  # services/checker
REPO = CHECKER.parent.parent
ALERTS_TOML = REPO / "alertas" / "alerts.toml"  # o arquivo que o workflow agendado usa


# --------------------------------------------------------------------------- unitários


class FormatTest(unittest.TestCase):
    def test_numbers_use_brazilian_separators(self):
        self.assertEqual(core.fmt_number(1234.5, 2), "1.234,50")

    def test_prices(self):
        self.assertEqual(core.fmt_price("USD-BRL", 4.987), "R$ 4,9870")
        self.assertEqual(core.fmt_price("VALE3", 61.2), "R$ 61,20")
        self.assertEqual(core.fmt_price("EUR-USD", 1.0853), "1,0853 USD")

    def test_limits_keep_only_needed_decimals(self):
        self.assertEqual(core.fmt_limit("USD-BRL", 5.0), "R$ 5,00")
        self.assertEqual(core.fmt_limit("USD-BRL", 4.955), "R$ 4,955")
        self.assertEqual(core.fmt_limit("VALE3", 65.0), "R$ 65,00")

    def test_datetimes(self):
        epoch = core.to_datetime("1591109752")
        self.assertEqual(epoch, datetime(2020, 6, 2, 14, 55, 52, tzinfo=UTC))
        self.assertEqual(core.to_datetime(1591109752000), epoch)
        self.assertEqual(core.to_datetime(STOCK_TIME), datetime(2026, 11, 10, 17, 10, tzinfo=UTC))
        self.assertEqual(core.fmt_dt(core.to_datetime(STOCK_TIME)), "10/11 14:10")
        for junk in (None, "", "ontem", True, {}):
            self.assertIsNone(core.to_datetime(junk))


class ConfigTest(unittest.TestCase):
    def load(self, text):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "alerts.toml"
            path.write_text(text, encoding="utf-8")
            return cron.load_config(path)

    def test_shipped_file(self):
        alerts, options = cron.load_config(ALERTS_TOML)
        self.assertEqual(
            [(a.asset, a.below, a.limit, a.name) for a in alerts],
            [("USD-BRL", True, 5.0, "Dólar"), ("VALE3", True, 65.0, "VALE3")],
        )
        self.assertEqual(alerts[0].text, "Dólar abaixo de R$ 5,00")
        self.assertEqual(alerts[0].key, "USD-BRL<5.0")
        self.assertEqual((options.rearm_pct, options.fail_notice_min), (0.5, 60))

    def test_band_normalisation_and_options(self):
        alerts, options = self.load(
            "[config]\nrearme_pct = 1\nfalha_aviso_min = 30\n"
            '[[alerta]]\nativo = " usd-brl "\nabaixo = 5\nacima = 5.8\n'
            '[[alerta]]\nativo = "USD-BRL"\nabaixo = 5.0\n'  # repetido: fica um só
        )
        self.assertEqual([a.key for a in alerts], ["USD-BRL<5.0", "USD-BRL>5.8"])
        self.assertEqual(alerts[1].text, "USD-BRL acima de R$ 5,80")
        self.assertEqual((options.rearm_pct, options.fail_notice_min), (1.0, 30.0))

    def test_errors_are_explained(self):
        cases = {
            "": "nenhum [[alerta]]",
            "[[alerta]]\nabaixo = 5\n": "falta ativo",
            '[[alerta]]\nativo = "USD-BRL"\n': "defina abaixo e/ou acima",
            '[[alerta]]\nativo = "USD-BRL"\nabaxo = 5\n': "chave desconhecida: abaxo",
            '[[alerta]]\nativo = "USD-BRL"\nabaixo = -1\n': "maior que zero",
            '[[alerta]]\nativo = "USD-BRL"\nabaixo = "5"\n': "maior que zero",
            '[[alerta]]\nativo = "USD-BRL"\nabaixo = nan\n': "maior que zero",
            '[[alerta]]\nativo = "dólar"\nabaixo = 5\n': "inválido",
            '[alerta]\nativo = "USD-BRL"\nabaixo = 5\n': "dois colchetes",
            '[[alertas]]\nativo = "USD-BRL"\nabaixo = 5\n': "seção desconhecida",
            '[config]\nrearme_pct = 80\n[[alerta]]\nativo = "VALE3"\nabaixo = 5\n': "rearme_pct",
            "[[alerta]\n": "TOML inválido",
        }
        for text, fragment in cases.items():
            with self.subTest(text=text), self.assertRaises(core.ConfigError) as caught:
                self.load(text)
            self.assertIn(fragment, str(caught.exception))

    def test_missing_file(self):
        with self.assertRaises(core.ConfigError):
            cron.load_config(CHECKER / "nao-existe.toml")


class ParseTest(unittest.TestCase):
    def test_awesomeapi_documented_response(self):
        payload = {
            "USDBRL": {
                "code": "USD",
                "codein": "BRL",
                "name": "Dólar Americano/Real Brasileiro",
                "high": "5.37",
                "low": "5.2257",
                "varBid": "-0.1351",
                "pctChange": "-2.52",
                "bid": "5.2279",
                "ask": "5.2285",
                "timestamp": "1591109752",
                "create_date": "2020-06-02 11:55:53",
            }
        }
        quote = core.parse_awesomeapi("USD-BRL", payload)
        self.assertEqual((quote.price, quote.source), (5.2279, "AwesomeAPI"))
        self.assertEqual(quote.as_of, datetime(2020, 6, 2, 14, 55, 52, tzinfo=UTC))

    def test_awesomeapi_without_the_pair(self):
        for payload in ({}, {"EURBRL": {"bid": "6"}}, [], None, {"USDBRL": "x"}):
            with self.subTest(payload=payload), self.assertRaises(core.FetchError):
                core.parse_awesomeapi("USD-BRL", payload)

    def test_brapi_v2_schema(self):
        payload = {
            "results": [
                {
                    "requestedSymbol": "VALE3",
                    "symbol": "VALE3",
                    "changed": False,
                    "data": {"shortName": "VALE ON", "currency": "BRL", "regularMarketPrice": 61.2, "regularMarketTime": STOCK_TIME},
                }
            ],
            "requestedAt": "2026-11-10T17:12:00.000Z",
            "took": 3,
        }
        quote = core.parse_brapi("VALE3", payload)
        self.assertEqual((quote.price, quote.source), (61.2, "brapi"))
        self.assertEqual(quote.as_of, datetime(2026, 11, 10, 17, 10, tzinfo=UTC))

    def test_brapi_legacy_flat_item_and_renamed_ticker(self):
        flat = {"results": [{"symbol": "VALE3", "regularMarketPrice": 61.2, "regularMarketTime": STOCK_TIME}]}
        self.assertEqual(core.parse_brapi("VALE3", flat).price, 61.2)
        renamed = {"results": [{"requestedSymbol": "VIIA3", "symbol": "BHIA3", "changed": True, "data": {"regularMarketPrice": 3.5}}]}
        quote = core.parse_brapi("VIIA3", renamed)
        self.assertEqual((quote.price, quote.as_of), (3.5, None))

    def test_brapi_without_the_ticker(self):
        for payload in ({}, {"results": []}, {"results": None}, [], {"results": ["x"]}):
            with self.subTest(payload=payload), self.assertRaises(core.FetchError):
                core.parse_brapi("VALE3", payload)

    def test_bad_prices_never_become_quotes(self):
        for bad in (None, 0, -1, "abc", True, float("nan"), float("inf"), ""):
            payload = {"results": [{"symbol": "VALE3", "data": {"regularMarketPrice": bad}}]}
            with self.subTest(price=bad), self.assertRaises(core.FetchError):
                core.parse_brapi("VALE3", payload)
            with self.subTest(bid=bad), self.assertRaises(core.FetchError):
                core.parse_awesomeapi("USD-BRL", {"USDBRL": {"bid": bad}})


class HttpTest(unittest.TestCase):
    def test_malformed_url_is_reported_without_echoing_it(self):
        # O token do Telegram vai no caminho da URL; nenhum erro pode devolvê-la.
        for url in ("http://127.0.0.1:9/bot segredo/sendMessage", "sem-esquema/segredo"):
            with self.subTest(url=url), self.assertRaises(core.FetchError) as caught:
                core.http_call("Canal", url, attempts=1)
            self.assertEqual(str(caught.exception), "Canal: URL ou cabeçalho inválido (confira os valores configurados)")

    def test_network_error_names_only_the_label(self):
        with self.assertRaises(core.FetchError) as caught:
            core.http_call("Canal", "http://127.0.0.1:9/segredo", attempts=1, timeout=3)
        self.assertTrue(str(caught.exception).startswith("Canal: erro de rede ("))
        self.assertNotIn("segredo", str(caught.exception))


class Sim:
    """Roda cron.check() com cotações e entrega de push controladas pelo teste."""

    def __init__(self, *alerts):
        self.alerts = list(alerts)
        self.options = core.Options()
        self.state = {"alertas": {}, "falhas": {}}
        self.now = datetime(2026, 11, 10, 10, 0, tzinfo=core.BRT)
        self.prices = {}
        self.down = set()
        self.push_ok = True
        self.sent = []

    def fetch(self, asset):
        if asset in self.down:
            raise core.FetchError(f"fonte {asset}: HTTP 503", 503)
        return core.Quote(asset, self.prices[asset], None, "teste")

    def send(self, title, message, *, tags=(), priority=3):
        if self.push_ok:
            self.sent.append(title)
        return self.push_ok

    def tick(self, minutes=10, **prices):
        self.prices.update({name.replace("_", "-"): value for name, value in prices.items()})
        self.now += timedelta(minutes=minutes)
        return cron.check(self.alerts, self.options, self.state, self.now, self.fetch, self.send)


DOLAR = core.Alert("USD-BRL", True, 5.0, "Dólar")
VALE_BAIXO = core.Alert("VALE3", True, 65.0, "VALE3")
VALE_ALTO = core.Alert("VALE3", False, 80.0, "VALE3")


class CheckTest(unittest.TestCase):
    def test_fires_once_and_stays_quiet(self):
        sim = Sim(DOLAR)
        self.assertEqual(sim.tick(USD_BRL=5.20).rows[0].status, "aguardando")
        self.assertEqual(sim.sent, [])
        out = sim.tick(USD_BRL=4.99)
        self.assertEqual(sim.sent, ["Dólar abaixo de R$ 5,00"])
        self.assertEqual(out.changes, ["Dólar abaixo de R$ 5,00 (R$ 4,9900)"])
        self.assertEqual(sim.state["alertas"]["USD-BRL<5.0"]["preco"], 4.99)
        for price in (4.95, 4.80, 4.999):
            sim.tick(USD_BRL=price)
        self.assertEqual(len(sim.sent), 1)

    def test_limit_itself_does_not_fire(self):
        sim = Sim(DOLAR)
        sim.tick(USD_BRL=5.00)
        self.assertEqual(sim.sent, [])

    def test_already_true_on_first_run_fires(self):
        sim = Sim(VALE_BAIXO)
        sim.tick(VALE3=61.0)
        self.assertEqual(sim.sent, ["VALE3 abaixo de R$ 65,00"])

    def test_rearms_only_beyond_the_margin(self):
        sim = Sim(DOLAR)  # margem padrão: 0,5% de 5,00 = 0,025
        sim.tick(USD_BRL=4.99)
        sim.tick(USD_BRL=5.01)  # voltou, mas dentro da margem: ainda "já avisado"
        self.assertIn("USD-BRL<5.0", sim.state["alertas"])
        sim.tick(USD_BRL=4.99)
        self.assertEqual(len(sim.sent), 1)
        out = sim.tick(USD_BRL=5.025)
        self.assertEqual(out.rows[0].status, "rearmado")
        self.assertEqual(sim.state["alertas"], {})
        sim.tick(USD_BRL=4.98)
        self.assertEqual(len(sim.sent), 2)

    def test_above_alert_is_symmetric(self):
        sim = Sim(VALE_ALTO)
        sim.tick(VALE3=80.0)
        self.assertEqual(sim.sent, [])
        sim.tick(VALE3=80.01)
        self.assertEqual(sim.sent, ["VALE3 acima de R$ 80,00"])
        sim.tick(VALE3=79.70)  # margem: 0,40 -> rearma em 79,60
        self.assertIn("VALE3>80.0", sim.state["alertas"])
        sim.tick(VALE3=79.60)
        self.assertEqual(sim.state["alertas"], {})

    def test_band_on_one_asset_fetches_once(self):
        sim = Sim(VALE_BAIXO, VALE_ALTO)
        calls = []
        original = sim.fetch
        sim.fetch = lambda asset: (calls.append(asset), original(asset))[1]
        sim.tick(VALE3=70.0)
        self.assertEqual(calls, ["VALE3"])
        sim.tick(VALE3=81.0)
        self.assertEqual(sim.sent, ["VALE3 acima de R$ 80,00"])

    def test_failed_push_is_retried_next_run(self):
        sim = Sim(DOLAR)
        sim.push_ok = False
        out = sim.tick(USD_BRL=4.99)
        self.assertEqual(out.rows[0].status, "disparou, mas a notificação falhou")
        self.assertEqual((sim.state["alertas"], out.changes), ({}, []))
        sim.push_ok = True
        sim.tick()
        self.assertEqual(sim.sent, ["Dólar abaixo de R$ 5,00"])

    def test_quote_failure_keeps_alert_state(self):
        sim = Sim(DOLAR, VALE_BAIXO)
        sim.tick(USD_BRL=4.99, VALE3=70.0)
        sim.down.add("USD-BRL")
        out = sim.tick()
        self.assertEqual(out.quote_errors, {"USD-BRL": "fonte USD-BRL: HTTP 503"})
        self.assertEqual([row.status for row in out.rows], ["sem cotação", "aguardando"])
        self.assertIn("USD-BRL<5.0", sim.state["alertas"])
        self.assertEqual(len(sim.sent), 1)

    def test_long_outage_warns_once_and_reports_recovery(self):
        sim = Sim(DOLAR)
        sim.tick(USD_BRL=5.20)
        sim.down.add("USD-BRL")
        for _ in range(6):  # 10 a 60 min de falha: o aviso sai ao completar 60 min
            sim.tick()
        self.assertEqual(sim.sent, [])
        sim.tick()
        sim.tick()
        self.assertEqual(sim.sent, ["Sem cotação de USD-BRL"])
        self.assertTrue(sim.state["falhas"]["USD-BRL"]["avisado"])
        sim.down.clear()
        sim.tick()
        self.assertEqual(sim.sent, ["Sem cotação de USD-BRL", "Cotação de USD-BRL normalizada"])
        self.assertEqual(sim.state["falhas"], {})

    def test_short_outage_is_silent(self):
        sim = Sim(DOLAR)
        sim.down.add("USD-BRL")
        self.assertEqual(sim.tick(USD_BRL=5.20).changes, ["sem cotação: USD-BRL"])
        self.assertIn("USD-BRL", sim.state["falhas"])
        self.assertEqual(sim.tick().changes, [])  # segue fora do ar: estado igual, nenhum commit novo
        sim.down.clear()
        self.assertEqual(sim.tick().changes, ["cotação voltou: USD-BRL"])
        self.assertEqual((sim.sent, sim.state["falhas"]), ([], {}))

    def test_changing_the_limit_makes_a_new_alert(self):
        sim = Sim(DOLAR)
        sim.tick(USD_BRL=4.99)
        sim.alerts = [core.Alert("USD-BRL", True, 4.9, "Dólar")]
        out = sim.tick()
        self.assertEqual(sim.state["alertas"], {})
        self.assertEqual(out.changes, ["removido: USD-BRL<5.0"])
        sim.tick(USD_BRL=4.89)
        self.assertEqual(sim.sent, ["Dólar abaixo de R$ 5,00", "Dólar abaixo de R$ 4,90"])

    def test_state_file_roundtrip(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "state.json"
            state = cron.load_state(path)
            self.assertFalse(cron.save_state(path, state))  # nada a gravar: não cria o arquivo
            self.assertFalse(path.exists())
            state["alertas"]["USD-BRL<5.0"] = {"desde": "2026-11-10T10:10:00-03:00", "preco": 4.99}
            self.assertTrue(cron.save_state(path, state))
            self.assertFalse(cron.save_state(path, state))  # igual ao que está gravado
            self.assertEqual(cron.load_state(path), state)
            path.write_text("<<<<<<< conflito", encoding="utf-8")
            self.assertEqual(cron.load_state(path), {"alertas": {}, "falhas": {}})


# --------------------------------------------------------------------------- ponta a ponta


class EndToEndTest(unittest.TestCase):
    TOPIC = "cotacao-topico-secreto-do-teste"

    def setUp(self):
        self.api = FakeApis()
        threading.Thread(target=self.api.serve_forever, daemon=True).start()
        self.addCleanup(self.api.server_close)
        self.addCleanup(self.api.shutdown)
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.config = Path(folder.name) / "alerts.toml"
        self.config.write_text(ALERTS_TOML.read_text(encoding="utf-8"), encoding="utf-8")
        self.state = Path(folder.name) / "state.json"

    def run_check(self, *args, **env):
        environment = {
            "PATH": os.environ.get("PATH", ""),
            "NO_PROXY": "127.0.0.1",
            "ALERTA_RETRY_WAIT": "0",
            "AWESOMEAPI_URL": self.api.url,
            "BRAPI_URL": self.api.url,
            "NTFY_SERVER": self.api.url,
            "TELEGRAM_API_URL": self.api.url,
            "NTFY_TOPIC": self.TOPIC,
        }
        environment.update(env)
        environment = {name: value for name, value in environment.items() if value is not None}
        environment["PYTHONPATH"] = str(CHECKER)
        command = [sys.executable, "-m", "alerta_checker.cron", "--config", str(self.config), "--state", str(self.state), *args]
        return subprocess.run(command, env=environment, capture_output=True, text=True, timeout=120, check=False)

    def saved(self):
        return json.loads(self.state.read_text(encoding="utf-8"))

    def test_full_cycle_dollar_and_stock(self):
        first = self.run_check()
        self.assertEqual((first.returncode, self.api.ntfy), (0, []), first.stdout + first.stderr)
        self.assertIn("Dólar abaixo de R$ 5,00: R$ 5,2500 [Cotação de 10/11 14:32 (AwesomeAPI)] -> aguardando", first.stdout)
        self.assertIn("VALE3 abaixo de R$ 65,00: R$ 70,10 [Cotação de 10/11 14:10 (brapi)] -> aguardando", first.stdout)
        self.assertFalse(self.state.exists())

        self.api.fx["USD-BRL"] = "4.9870"
        self.assertEqual(self.run_check().returncode, 0)
        self.assertEqual(
            self.api.ntfy,
            [
                {
                    "topic": self.TOPIC,
                    "title": "Dólar abaixo de R$ 5,00",
                    "message": "Agora: R$ 4,9870\nCotação de 10/11 14:32 (AwesomeAPI)",
                    "tags": ["chart_with_downwards_trend"],
                    "priority": 4,
                }
            ],
        )
        self.assertEqual(self.saved()["alertas"]["USD-BRL<5.0"]["preco"], 4.987)

        self.assertIn("já avisado em", self.run_check().stdout)  # continua abaixo: silêncio
        self.api.fx["USD-BRL"] = "5.0100"  # voltou, mas dentro da margem de rearme
        self.run_check()
        self.assertIn("USD-BRL<5.0", self.saved()["alertas"])
        self.assertEqual(len(self.api.ntfy), 1)

        self.api.fx["USD-BRL"] = "5.0300"
        self.assertIn("-> rearmado", self.run_check().stdout)
        self.assertEqual(self.saved()["alertas"], {})

        self.api.fx["USD-BRL"] = "4.9900"
        self.api.stocks["VALE3"] = 64.5
        self.run_check()
        self.assertEqual(
            [(push["title"], push["message"]) for push in self.api.ntfy[1:]],
            [
                ("Dólar abaixo de R$ 5,00", "Agora: R$ 4,9900\nCotação de 10/11 14:32 (AwesomeAPI)"),
                ("VALE3 abaixo de R$ 65,00", "Agora: R$ 64,50\nCotação de 10/11 14:10 (brapi)"),
            ],
        )
        self.assertEqual(sorted(self.saved()["alertas"]), ["USD-BRL<5.0", "VALE3<65.0"])

    def test_transient_errors_are_retried(self):
        self.api.fx["USD-BRL"] = "4.9870"
        self.api.fail = {"fx": [500, 502], "stock": [429], "ntfy": [503]}
        result = self.run_check("--strict")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual((self.api.count("fx"), self.api.count("stock"), self.api.count("ntfy")), (3, 2, 2))
        self.assertEqual(len(self.api.ntfy), 1)

    def test_source_down_is_tolerated_on_schedule_and_fatal_with_strict(self):
        self.api.fail = {"fx": [500, 500, 500]}
        tolerant = self.run_check()
        self.assertEqual(tolerant.returncode, 0)
        self.assertIn("AVISO: AwesomeAPI USD-BRL: HTTP 500", tolerant.stdout)
        self.assertIn("Dólar abaixo de R$ 5,00: sem cotação", tolerant.stdout)
        self.assertFalse(self.saved()["falhas"]["USD-BRL"]["avisado"])
        self.api.fail = {"fx": [500, 500, 500]}
        strict = self.run_check("--strict")
        self.assertEqual(strict.returncode, 1)
        self.assertIn("ERRO: AwesomeAPI USD-BRL: HTTP 500", strict.stdout)
        self.assertEqual(self.api.ntfy, [])
        self.assertEqual(self.run_check("--strict").returncode, 0)  # fonte voltou
        self.assertEqual(self.saved()["falhas"], {})

    def test_long_outage_pushes_a_warning_and_then_recovery(self):
        since = (datetime.now(core.BRT) - timedelta(hours=2)).isoformat(timespec="seconds")
        self.state.write_text(json.dumps({"alertas": {}, "falhas": {"USD-BRL": {"desde": since, "avisado": False}}}))
        self.api.fail = {"fx": [503, 503, 503]}
        self.assertEqual(self.run_check().returncode, 0)
        self.assertEqual([push["title"] for push in self.api.ntfy], ["Sem cotação de USD-BRL"])
        self.assertIn("Último erro: AwesomeAPI USD-BRL: HTTP 503", self.api.ntfy[0]["message"])
        self.assertTrue(self.saved()["falhas"]["USD-BRL"]["avisado"])
        self.run_check()
        self.assertEqual([push["title"] for push in self.api.ntfy][1:], ["Cotação de USD-BRL normalizada"])
        self.assertEqual(self.saved()["falhas"], {})

    def test_push_failure_exits_1_and_retries_next_run(self):
        self.api.fx["USD-BRL"] = "4.9870"
        self.api.fail = {"ntfy": [500, 500, 500]}
        failed = self.run_check()
        self.assertEqual(failed.returncode, 1)
        self.assertIn("notificação não enviada: ntfy: HTTP 500", failed.stdout)
        self.assertFalse(self.state.exists())  # não marcou como avisado
        self.assertEqual(self.run_check().returncode, 0)
        self.assertEqual(len(self.api.ntfy), 1)
        self.assertIn("USD-BRL<5.0", self.saved()["alertas"])

    def test_dry_run_sends_and_saves_nothing(self):
        self.api.fx["USD-BRL"] = "4.9870"
        result = self.run_check("--dry-run", NTFY_TOPIC=None)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("[dry-run] notificação: Dólar abaixo de R$ 5,00 | Agora: R$ 4,9870", result.stdout)
        self.assertEqual((self.api.count("ntfy"), self.state.exists()), (0, False))

    def test_test_notify(self):
        result = self.run_check("--test-notify")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(
            self.api.ntfy,
            [
                {
                    "topic": self.TOPIC,
                    "title": "Teste do alerta de cotação",
                    "message": "USD-BRL: R$ 5,2500\nVALE3: R$ 70,10\n2 alerta(s) configurado(s).",
                    "tags": ["white_check_mark"],
                    "priority": 3,
                }
            ],
        )
        self.assertFalse(self.state.exists())
        self.api.fail = {"stock": [500, 500, 500]}
        self.assertEqual(self.run_check("--test-notify").returncode, 1)  # push sai, mas acusa a fonte

    def test_tokens_travel_in_headers_and_never_reach_the_log(self):
        secrets = {
            "NTFY_TOPIC": self.TOPIC,
            "NTFY_TOKEN": "tk_segredoNtfy123",
            "BRAPI_TOKEN": "segredoBrapi456",
            "AWESOMEAPI_TOKEN": "segredoAwesome789",
            "TELEGRAM_BOT_TOKEN": "123456:segredoTelegramABC",
            "TELEGRAM_CHAT_ID": "987654321",
        }
        self.api.fx["USD-BRL"] = "4.9870"
        self.api.fail = {"telegram": [401]}  # um canal quebrado: o outro entrega e o run acusa
        broken = self.run_check(GITHUB_ACTIONS="true", **secrets)
        self.assertEqual(broken.returncode, 1)
        self.assertIn("::error::notificação não enviada: Telegram: HTTP 401", broken.stdout)
        self.assertEqual(len(self.api.ntfy), 1)
        self.assertIn("USD-BRL<5.0", self.saved()["alertas"])  # entregue por um canal: não repete

        self.api.fx["USD-BRL"] = "5.2000"
        self.run_check(**secrets)
        self.api.fx["USD-BRL"] = "4.9500"
        ok = self.run_check(**secrets)
        self.assertEqual(ok.returncode, 0, ok.stdout + ok.stderr)
        self.assertEqual(
            self.api.telegram,
            [
                {
                    "chat_id": "987654321",
                    "text": "\U0001f4c9 Dólar abaixo de R$ 5,00\nAgora: R$ 4,9500\nCotação de 10/11 14:32 (AwesomeAPI)",
                }
            ],
        )

        by_route = {route: (path, headers) for _, route, path, headers in self.api.seen}
        self.assertEqual(by_route["fx"][1]["x-api-key"], "segredoAwesome789")
        self.assertEqual(by_route["stock"][1]["authorization"], "Bearer segredoBrapi456")
        self.assertEqual(by_route["ntfy"][1]["authorization"], "Bearer tk_segredoNtfy123")
        self.assertEqual(by_route["telegram"][0], "/bot123456:segredoTelegramABC/sendMessage")
        for output in (broken.stdout, broken.stderr, ok.stdout, ok.stderr):
            for value in secrets.values():
                self.assertNotIn(value, output)

    def test_configuration_problems_exit_2(self):
        no_channel = self.run_check(NTFY_TOPIC=None)
        self.assertEqual(no_channel.returncode, 2)
        self.assertIn("nenhum canal de push", no_channel.stdout)
        self.assertEqual(self.run_check(NTFY_TOPIC="tópico com espaço").returncode, 2)
        self.assertEqual(self.run_check(TELEGRAM_CHAT_ID="1").returncode, 2)
        self.config.write_text('[[alerta]]\nativo = "USD-BRL"\nabaxo = 5\n', encoding="utf-8")
        typo = self.run_check()
        self.assertEqual(typo.returncode, 2)
        self.assertIn("chave desconhecida: abaxo", typo.stdout)
        self.assertEqual(self.api.seen, [])

    def test_ticker_outside_the_sandbox_needs_a_token(self):
        self.config.write_text('[[alerta]]\nativo = "ITSA4"\nabaixo = 9\n', encoding="utf-8")
        self.api.stocks["ITSA4"] = 8.5
        missing = self.run_check("--strict")
        self.assertEqual(missing.returncode, 1)
        self.assertIn("sem BRAPI_TOKEN só funcionam PETR4, VALE3, MGLU3, ITUB4", missing.stdout)
        self.assertEqual(self.api.count("stock"), 1)  # 401 não é repetido
        self.assertEqual(self.run_check("--strict", BRAPI_TOKEN="abc").returncode, 0)
        self.assertEqual(self.api.ntfy[0]["title"], "ITSA4 abaixo de R$ 9,00")

    def test_unknown_assets_fail_cleanly(self):
        self.config.write_text('[[alerta]]\nativo = "XXX-BRL"\nabaixo = 5\n[[alerta]]\nativo = "PETR4"\nabaixo = 5\n', encoding="utf-8")
        result = self.run_check("--strict")
        self.assertEqual(result.returncode, 1)
        self.assertIn("AwesomeAPI XXX-BRL: HTTP 404", result.stdout)
        self.assertIn("brapi PETR4: HTTP 404", result.stdout)
        self.assertNotIn("Traceback", result.stdout + result.stderr)

    def test_github_summary_and_commit_message_output(self):
        summary = self.state.with_name("summary.md")
        output = self.state.with_name("output.txt")
        self.api.fx["USD-BRL"] = "4.9870"
        self.run_check(GITHUB_STEP_SUMMARY=str(summary), GITHUB_OUTPUT=str(output))
        self.assertEqual(output.read_text(encoding="utf-8"), "resumo=Dólar abaixo de R$ 5,00 (R$ 4,9870)\n")
        table = summary.read_text(encoding="utf-8")
        self.assertIn(
            "| Dólar abaixo de R$ 5,00 | R$ 4,9870 | Cotação de 10/11 14:32 (AwesomeAPI) | DISPAROU, notificação enviada |", table
        )
        self.assertIn("| VALE3 abaixo de R$ 65,00 | R$ 70,10 | Cotação de 10/11 14:10 (brapi) | aguardando |", table)


if __name__ == "__main__":
    unittest.main()
