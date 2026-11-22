"""Testes do modo API (alerta_checker.worker), do mercado falso e da probe de liveness.

O worker roda contra dois servidores locais: FakeAlertaApi (endpoints internos da
API Java) e FakeApis (AwesomeAPI, brapi e ntfy). O relógio é controlado pelo teste.

    cd services/checker && python3 -m unittest discover -s tests -v
"""

import dataclasses
import json
import os
import signal
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import urllib.request
from datetime import datetime, timedelta
from datetime import time as clock_time
from pathlib import Path
from unittest import mock

from alerta_checker import core, fake_market, worker

from fake_alerta_api import TOKEN, FakeAlertaApi
from fakes import FakeApis

CHECKER = Path(__file__).resolve().parent.parent
TUESDAY_10AM = datetime(2026, 11, 10, 10, 0, tzinfo=core.BRT)


def serve(server, test):
    threading.Thread(target=server.serve_forever, daemon=True).start()
    test.addCleanup(server.server_close)
    test.addCleanup(server.shutdown)
    return server


class Clock:
    def __init__(self, start):
        self.now = start
        self.mono = 1000.0

    def __call__(self):
        return self.now

    def monotonic(self):
        return self.mono

    def advance(self, minutes):
        self.now += timedelta(minutes=minutes)
        self.mono += minutes * 60


class WorkerCase(unittest.TestCase):
    def setUp(self):
        patcher = mock.patch.dict(os.environ, {"ALERTA_RETRY_WAIT": "0"})
        patcher.start()
        self.addCleanup(patcher.stop)
        self.clock = Clock(TUESDAY_10AM)
        self.market = serve(FakeApis(), self)
        self.api = serve(FakeAlertaApi(clock=self.clock), self)
        self.settings = core.Settings(
            awesomeapi_url=self.market.url,
            awesomeapi_token=None,
            brapi_url=self.market.url,
            brapi_token=None,
            ntfy_server=self.market.url,
            ntfy_topic="topico-teste",
            ntfy_token=None,
            telegram_api_url=self.market.url,
            telegram_token=None,
            telegram_chat_id=None,
        )
        self.worker_settings = worker.WorkerSettings(
            api_url=self.api.url,
            checker_token=TOKEN,
            interval=60,
            stock_min_interval=300,
            market_hours=(clock_time(9), clock_time(18, 30)),
            fail_notice=timedelta(minutes=60),
            heartbeat=None,
            dry_run=False,
        )
        self.worker = self.make_worker()

    def make_worker(self, *, dry_run=False, **overrides):
        settings = dataclasses.replace(self.worker_settings, **overrides)
        return worker.Worker(
            settings,
            worker.ApiClient(settings.api_url, settings.checker_token),
            lambda asset: core.fetch_quote(asset, self.settings),
            core.Notifier(self.settings, dry_run=dry_run),
            clock=self.clock,
            monotonic=self.clock.monotonic,
        )

    def tick(self, minutes=2, **prices):
        for name, value in prices.items():
            asset = name.replace("_", "-")
            if "-" in asset:
                self.market.fx[asset] = value
            else:
                self.market.stocks[asset] = value
        self.clock.advance(minutes)
        return self.worker.cycle()

    def pushes(self):
        return [push["title"] for push in self.market.ntfy]


class CycleTest(WorkerCase):
    def test_fires_once_delivers_and_rearms_like_the_file_mode(self):
        dolar = self.api.add_alert("USD-BRL", "BELOW", 5.0, "Dólar")
        self.api.add_alert("VALE3", "BELOW", 65.0)

        report = self.tick(USD_BRL="5.2500")
        self.assertEqual((report.alerts, report.events, self.market.ntfy), (2, [], []))
        self.assertEqual(sorted(q["asset"] for q in self.api.quotes), ["USD-BRL", "VALE3"])
        self.assertEqual(self.api.quotes[0]["quotedAt"], "2026-11-10T17:32:00Z")  # horário da fonte, em UTC

        report = self.tick(USD_BRL="4.9870")
        self.assertEqual([event.type for event in report.events], ["FIRED"])
        self.assertEqual(
            self.market.ntfy,
            [
                {
                    "topic": "topico-teste",
                    "title": "Dólar abaixo de R$ 5,00",
                    "message": "Agora: R$ 4,9870\nCotação de 10/11 14:32 (AwesomeAPI)",
                    "tags": ["chart_with_downwards_trend"],
                    "priority": 4,
                }
            ],
        )
        self.assertEqual(self.api.alerts[dolar]["state"], "FIRED")
        self.assertIsNotNone(self.api.events_of("FIRED")[0]["deliveredAt"])
        self.assertEqual(report.delivered, 1)

        self.tick(USD_BRL="4.9500")  # continua abaixo: silêncio
        self.tick(USD_BRL="5.0100")  # voltou, mas dentro da margem de 0,5%
        self.assertEqual((len(self.market.ntfy), self.api.alerts[dolar]["state"]), (1, "FIRED"))

        report = self.tick(USD_BRL="5.0300")
        self.assertEqual([event.type for event in report.events], ["REARMED"])
        self.assertEqual(self.api.alerts[dolar]["state"], "ARMED")
        self.assertFalse(self.api.events_of("REARMED")[0]["push"])  # rearme não vira push

        self.tick(USD_BRL="4.9900")
        self.assertEqual(self.pushes(), ["Dólar abaixo de R$ 5,00", "Dólar abaixo de R$ 5,00"])

    def test_above_alert_uses_the_upward_tag(self):
        self.api.add_alert("VALE3", "ABOVE", 70.0)
        self.tick(VALE3=70.5)
        self.assertEqual(self.market.ntfy[0]["title"], "VALE3 acima de R$ 70,00")
        self.assertEqual(self.market.ntfy[0]["tags"], ["chart_with_upwards_trend"])

    def test_failed_push_stays_pending_and_is_retried(self):
        self.api.add_alert("USD-BRL", "BELOW", 5.0, "Dólar")
        self.market.fail = {"ntfy": [500, 500, 500]}  # as 3 tentativas do http_call falham
        report = self.tick(USD_BRL="4.9870")
        self.assertEqual((report.delivered, report.undelivered, self.market.ntfy), (0, 1, []))
        self.assertIsNone(self.api.events_of("FIRED")[0]["deliveredAt"])

        report = self.tick()
        self.assertEqual((report.events, report.delivered), ([], 1))  # não gera outro disparo, só entrega
        self.assertEqual(self.pushes(), ["Dólar abaixo de R$ 5,00"])
        self.assertEqual(len(self.api.events_of("FIRED")), 1)

    def test_api_down_means_no_push_and_no_duplicate_later(self):
        self.api.add_alert("USD-BRL", "BELOW", 5.0, "Dólar")
        self.api.fail = {"alerts": [503, 503, 503]}
        self.market.fx["USD-BRL"] = "4.9870"
        with self.assertRaises(core.FetchError) as caught:
            self.tick()
        self.assertIn("API GET /internal/alerts: HTTP 503", str(caught.exception))
        self.assertEqual((self.market.ntfy, self.api.events), ([], {}))
        self.tick()
        self.tick()
        self.assertEqual(self.pushes(), ["Dólar abaixo de R$ 5,00"])

    def test_event_rejected_as_stale_is_dropped(self):
        self.api.add_alert("USD-BRL", "BELOW", 5.0, "Dólar")
        self.api.fail = {"events": [409]}
        report = self.tick(USD_BRL="4.9870")
        self.assertEqual((report.stale, report.events, self.market.ntfy), (1, [], []))
        self.tick()  # o alerta continua armado na API: o próximo ciclo dispara normalmente
        self.assertEqual(self.pushes(), ["Dólar abaixo de R$ 5,00"])

    def test_edit_between_read_and_write_makes_the_event_stale(self):
        alert_id = self.api.add_alert("USD-BRL", "BELOW", 5.0, "Dólar")
        client = worker.ApiClient(self.api.url, TOKEN)
        watched = client.alerts()
        self.api.edit(alert_id, threshold=4.9)  # usuária mudou o limite no app
        quote = core.Quote("USD-BRL", 4.95, None, "teste")
        events = worker.evaluate(watched, {"USD-BRL": quote}, TUESDAY_10AM)
        self.assertFalse(client.post_event(events[0]))
        del self.api.alerts[alert_id]
        self.assertFalse(client.post_event(events[0]))  # 404: apagado também é obsoleto

    def test_repeated_event_is_idempotent(self):
        alert_id = self.api.add_alert("USD-BRL", "BELOW", 5.0, "Dólar")
        client = worker.ApiClient(self.api.url, TOKEN)
        quote = core.Quote("USD-BRL", 4.95, None, "teste")
        event = worker.evaluate(client.alerts(), {"USD-BRL": quote}, TUESDAY_10AM)[0]
        self.assertTrue(client.post_event(event))
        self.assertTrue(client.post_event(event))  # resposta perdida e reenvio: não conta duas vezes
        self.assertEqual((len(self.api.events), self.api.alerts[alert_id]["version"]), (1, 1))

    def test_brapi_is_queried_at_most_every_five_minutes(self):
        self.api.add_alert("VALE3", "BELOW", 65.0)
        self.api.add_alert("USD-BRL", "BELOW", 5.0)
        for _ in range(4):  # ciclos de 2 min: 2, 4, 6, 8
            self.tick()
        self.assertEqual(self.market.count("stock"), 2)  # min 2 e min 8
        self.assertEqual(self.market.count("fx"), 4)  # câmbio: todo ciclo
        self.assertEqual(sum(1 for q in self.api.quotes if q["asset"] == "VALE3"), 2)

    def test_long_outage_warns_once_and_reports_recovery(self):
        self.api.add_alert("VALE3", "BELOW", 65.0)
        price = self.market.stocks.pop("VALE3")  # brapi passa a responder 404
        for _ in range(3):  # 20, 40, 60 min de falha: o aviso sai ao completar 60
            self.tick(minutes=20)
        self.assertEqual(self.pushes(), [])
        self.tick(minutes=20)
        self.tick(minutes=20)
        self.assertEqual(self.pushes(), ["Sem cotação de VALE3"])
        self.assertIn("Último erro: brapi VALE3: HTTP 404", self.market.ntfy[0]["message"])
        self.tick(minutes=20, VALE3=price)
        self.assertEqual(self.pushes(), ["Sem cotação de VALE3", "Cotação de VALE3 normalizada"])
        self.assertEqual(self.market.ntfy[1]["message"], "Agora: R$ 70,10")

    def test_short_outage_is_silent(self):
        self.api.add_alert("VALE3", "BELOW", 65.0)
        price = self.market.stocks.pop("VALE3")
        self.tick(minutes=20)
        self.tick(minutes=20)
        self.tick(minutes=20, VALE3=price)
        self.assertEqual((self.pushes(), self.api.events), ([], {}))

    def test_outside_market_hours_only_delivers_pending(self):
        self.api.add_alert("USD-BRL", "BELOW", 5.0, "Dólar")
        self.market.fx["USD-BRL"] = "4.9870"
        self.api.events["x"] = {
            "id": "x",
            "type": "FIRED",
            "asset": "USD-BRL",
            "direction": "BELOW",
            "push": True,
            "deliveredAt": None,
            "title": "Dólar abaixo de R$ 5,00",
            "message": "pendente de antes",
            "occurredAt": TUESDAY_10AM.isoformat(),
        }
        self.clock.now = TUESDAY_10AM.replace(hour=20)
        report = self.worker.cycle()
        self.assertFalse(report.market_open)
        self.assertEqual((self.api.count("alerts"), self.market.count("fx")), (0, 0))
        self.assertEqual([p["message"] for p in self.market.ntfy], ["pendente de antes"])
        self.assertEqual(report.summary(), "ciclo: push: 1 entregue(s), 0 pendente(s)")

    def test_dry_run_marks_delivered_without_sending(self):
        self.worker = self.make_worker(dry_run=True)
        self.api.add_alert("USD-BRL", "BELOW", 5.0, "Dólar")
        report = self.tick(USD_BRL="4.9870")
        self.assertEqual((report.delivered, self.market.ntfy), (1, []))

    def test_wrong_token_is_reported(self):
        self.worker = self.make_worker(checker_token="outro-token")
        with self.assertRaises(core.FetchError) as caught:
            self.tick()
        self.assertIn("HTTP 401", str(caught.exception))

    def test_summary_line(self):
        self.api.add_alert("USD-BRL", "BELOW", 5.0, "Dólar")
        self.api.add_alert("VALE3", "BELOW", 65.0)
        self.market.stocks.pop("VALE3")
        report = self.tick(USD_BRL="4.9870")
        self.assertEqual(
            report.summary(),
            "ciclo: 2 alerta(s) | USD-BRL R$ 4,9870 | sem cotação: VALE3 | 1 evento(s) | push: 1 entregue(s), 0 pendente(s)",
        )


class SettingsTest(unittest.TestCase):
    def test_market_hours(self):
        self.assertEqual(worker.parse_market_hours("09:00-18:30"), (clock_time(9), clock_time(18, 30)))
        for always in ("", "sempre", "always", None):
            self.assertIsNone(worker.parse_market_hours(always))
        for bad in ("18:00-09:00", "25:00-26:00", "9h-18h", "09:00"):
            with self.subTest(bad=bad), self.assertRaises(core.ConfigError):
                worker.parse_market_hours(bad)
        hours = worker.parse_market_hours("09:00-18:30")
        cases = {
            TUESDAY_10AM.replace(hour=8, minute=59): False,
            TUESDAY_10AM.replace(hour=9, minute=0): True,
            TUESDAY_10AM.replace(hour=18, minute=29): True,
            TUESDAY_10AM.replace(hour=18, minute=30): False,
            TUESDAY_10AM + timedelta(days=4): False,  # sábado
        }
        for moment, expected in cases.items():
            with self.subTest(moment=moment):
                self.assertEqual(worker.in_market(moment, hours), expected)

    def test_from_env(self):
        with mock.patch.dict(os.environ, {"CHECKER_TOKEN": "abc"}, clear=True):
            settings = worker.WorkerSettings.from_env()
        self.assertEqual(
            (settings.api_url, settings.interval, settings.stock_min_interval, settings.fail_notice, settings.dry_run),
            ("http://localhost:8080", 120, 300, timedelta(minutes=60), False),
        )
        self.assertEqual(settings.heartbeat, Path("/tmp/alerta-checker.heartbeat"))
        problems = [
            {},
            {"CHECKER_TOKEN": "abc", "API_URL": "api:8080"},
            {"CHECKER_TOKEN": "abc", "CHECK_INTERVAL_SECONDS": "dois"},
            {"CHECKER_TOKEN": "abc", "CHECK_INTERVAL_SECONDS": "1"},
            {"CHECKER_TOKEN": "abc", "MARKET_HOURS": "manhã"},
        ]
        for env in problems:
            with self.subTest(env=env), mock.patch.dict(os.environ, env, clear=True), self.assertRaises(core.ConfigError):
                worker.WorkerSettings.from_env()


class FakeMarketTest(unittest.TestCase):
    def test_answers_like_the_real_apis(self):
        server = serve(fake_market.FakeMarket("127.0.0.1", 0, {"USD-BRL": 5.18, "VALE3": 70.1}), self)
        url = f"http://127.0.0.1:{server.server_address[1]}"
        settings = dataclasses.replace(core.Settings.from_env(), awesomeapi_url=url, brapi_url=url, awesomeapi_token=None, brapi_token=None)
        self.assertEqual(core.fetch_quote("USD-BRL", settings).price, 5.18)
        self.assertEqual(core.fetch_quote("VALE3", settings).source, "brapi")

        request = urllib.request.Request(f"{url}/precos/vale3", data=b"64.5", method="PUT")
        with urllib.request.urlopen(request, timeout=5) as response:
            self.assertEqual(json.load(response), {"VALE3": 64.5})
        quote = core.fetch_quote("VALE3", settings)
        self.assertEqual(quote.price, 64.5)
        self.assertIsNotNone(quote.as_of)

        urllib.request.urlopen(urllib.request.Request(f"{url}/precos/VALE3", method="DELETE"), timeout=5).close()
        with mock.patch.dict(os.environ, {"ALERTA_RETRY_WAIT": "0"}), self.assertRaises(core.FetchError) as caught:
            core.fetch_quote("VALE3", settings)
        self.assertEqual(caught.exception.status, 404)

    def test_initial_prices_from_env(self):
        self.assertEqual(fake_market.parse_prices("usd-brl=4.9, VALE3=60"), {"USD-BRL": 4.9, "VALE3": 60.0})
        self.assertIn("USD-BRL", fake_market.parse_prices(None))


class ProcessTest(unittest.TestCase):
    """O worker como processo de verdade: o que o contêiner roda."""

    def setUp(self):
        self.market = serve(FakeApis(), self)
        self.api = serve(FakeAlertaApi(), self)
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.heartbeat = Path(folder.name) / "heartbeat"
        self.env = {
            "PATH": os.environ.get("PATH", ""),
            "PYTHONPATH": str(CHECKER),
            "NO_PROXY": "127.0.0.1",
            "ALERTA_RETRY_WAIT": "0",
            "API_URL": self.api.url,
            "CHECKER_TOKEN": TOKEN,
            "AWESOMEAPI_URL": self.market.url,
            "BRAPI_URL": self.market.url,
            "NTFY_SERVER": self.market.url,
            "NTFY_TOPIC": "topico-teste",
            "MARKET_HOURS": "sempre",
            "CHECK_INTERVAL_SECONDS": "5",
            "HEARTBEAT_FILE": str(self.heartbeat),
        }

    def run_module(self, module, *args, **env):
        environment = {k: v for k, v in {**self.env, **env}.items() if v is not None}
        return subprocess.run(
            [sys.executable, "-m", module, *args], env=environment, capture_output=True, text=True, timeout=60, check=False
        )

    def test_loop_runs_until_sigterm(self):
        self.api.add_alert("USD-BRL", "BELOW", 6.0, "Dólar")  # 5,25 < 6: dispara no primeiro ciclo
        process = subprocess.Popen(
            [sys.executable, "-m", "alerta_checker.worker"], env=self.env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True
        )
        self.addCleanup(lambda: process.poll() is None and process.kill())
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline and self.api.count("pending") < 2:  # espera dois ciclos
            time.sleep(0.2)
        self.assertEqual(self.run_module("alerta_checker.health", "--max-age", "60").returncode, 0)
        process.send_signal(signal.SIGTERM)
        output, _ = process.communicate(timeout=10)
        self.assertEqual(process.returncode, 0, output)
        self.assertIn("checker iniciado: API http://127.0.0.1:", output)
        self.assertIn("FIRED: Dólar abaixo de R$ 6,00 (R$ 5,2500)", output)
        self.assertIn("checker encerrado", output)
        self.assertEqual([push["title"] for push in self.market.ntfy], ["Dólar abaixo de R$ 6,00"])  # 2 ciclos, 1 push
        self.assertNotIn(TOKEN, output)

    def test_once_and_configuration_errors(self):
        self.api.add_alert("VALE3", "BELOW", 65.0)
        once = self.run_module("alerta_checker.worker", "--once", MARKET_HOURS="09:00-09:01")  # --once ignora o horário
        self.assertEqual(once.returncode, 0, once.stdout + once.stderr)
        self.assertIn("ciclo: 1 alerta(s) | VALE3 R$ 70,10", once.stdout)
        no_token = self.run_module("alerta_checker.worker", "--once", CHECKER_TOKEN=None)
        self.assertEqual(no_token.returncode, 2)
        self.assertIn("CHECKER_TOKEN não definido", no_token.stdout)
        no_channel = self.run_module("alerta_checker.worker", "--once", NTFY_TOPIC=None)
        self.assertEqual(no_channel.returncode, 2)
        dry = self.run_module("alerta_checker.worker", "--once", NTFY_TOPIC=None, NOTIFY_DRY_RUN="true")
        self.assertEqual(dry.returncode, 0, dry.stdout)
        api_down = self.run_module("alerta_checker.worker", "--once", API_URL="http://127.0.0.1:9")
        self.assertEqual(api_down.returncode, 1)
        self.assertIn("ERRO: API GET /internal/alerts: erro de rede", api_down.stdout)

    def test_health_probe(self):
        self.assertEqual(self.run_module("alerta_checker.health").returncode, 1)  # sem arquivo
        self.heartbeat.write_text("x")
        self.assertEqual(self.run_module("alerta_checker.health", "--max-age", "60").returncode, 0)
        old = time.time() - 3600
        os.utime(self.heartbeat, (old, old))
        stale = self.run_module("alerta_checker.health", "--max-age", "600")
        self.assertEqual(stale.returncode, 1)
        self.assertIn("heartbeat parado", stale.stdout)


if __name__ == "__main__":
    unittest.main()
