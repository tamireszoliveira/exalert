"""Servidores HTTP falsos usados nos testes.

FakeApis imita AwesomeAPI, brapi, ntfy e Telegram (cotações e push);
FakeAlertaApi (em fake_alerta_api.py) imita os endpoints internos da API Java.
"""

import json
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from alerta_checker import core

FX_TIME = datetime(2026, 11, 10, 17, 32, tzinfo=UTC)  # 14:32 em Brasília
STOCK_TIME = "2026-11-10T17:10:00.000Z"  # 14:10 em Brasília


class _Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def _reply(self, status, payload):
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _record(self, method, route):
        headers = {name.lower(): value for name, value in self.headers.items()}
        self.server.seen.append((method, route, self.path, headers))
        queue = self.server.fail.get(route)
        if queue:
            self._reply(queue.pop(0), {"error": "falha injetada pelo teste"})
            return False
        return True

    def do_GET(self):
        url = urlparse(self.path)
        if url.path.startswith("/json/last/"):
            if not self._record("GET", "fx"):
                return
            pair = url.path.rsplit("/", 1)[1]
            bid = self.server.fx.get(pair)
            if bid is None:
                self._reply(404, {"status": 404, "code": "CoinNotExists", "message": f"moeda nao encontrada {pair}"})
                return
            self._reply(
                200,
                {
                    pair.replace("-", ""): {
                        "code": pair[:3],
                        "codein": pair[4:],
                        "name": "par de teste",
                        "high": bid,
                        "low": bid,
                        "varBid": "0",
                        "pctChange": "0",
                        "bid": bid,
                        "ask": bid,
                        "timestamp": str(int(FX_TIME.timestamp())),
                        "create_date": "2026-11-10 14:32:00",
                    }
                },
            )
        elif url.path == "/api/v2/stocks/quote":
            if not self._record("GET", "stock"):
                return
            symbol = parse_qs(url.query).get("symbols", [""])[0]
            if symbol not in core.SANDBOX_BRAPI and "authorization" not in self.server.seen[-1][3]:
                self._reply(401, {"error": True, "message": "Token ausente ou inválido."})
                return
            price = self.server.stocks.get(symbol)
            if price is None:
                self._reply(404, {"error": True, "message": "no data found for the request"})
                return
            self._reply(
                200,
                {
                    "results": [
                        {
                            "requestedSymbol": symbol,
                            "symbol": symbol,
                            "changed": False,
                            "data": {"shortName": symbol, "currency": "BRL", "regularMarketPrice": price, "regularMarketTime": STOCK_TIME},
                        }
                    ],
                    "requestedAt": "2026-11-10T17:12:00.000Z",
                    "took": 3,
                },
            )
        else:
            self._reply(404, {"error": "rota desconhecida"})

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
        if self.path == "/":  # ntfy: publicação em JSON na raiz
            if self._record("POST", "ntfy"):
                self.server.ntfy.append(body)
                self._reply(200, {"id": "abc", "event": "message", "topic": body.get("topic")})
        elif self.path.startswith("/bot") and self.path.endswith("/sendMessage"):
            if self._record("POST", "telegram"):
                self.server.telegram.append(body)
                self._reply(200, {"ok": True, "result": {"message_id": 1}})
        else:
            self._reply(404, {"error": "rota desconhecida"})


class FakeApis(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self):
        super().__init__(("127.0.0.1", 0), _Handler)
        self.fx = {"USD-BRL": "5.2500"}
        self.stocks = {"VALE3": 70.10}
        self.fail = {}  # rota -> fila de status HTTP a devolver antes de voltar ao normal
        self.seen = []  # (método, rota, caminho, cabeçalhos)
        self.ntfy = []
        self.telegram = []

    @property
    def url(self):
        return f"http://127.0.0.1:{self.server_address[1]}"

    def count(self, route):
        return sum(1 for entry in self.seen if entry[1] == route)
