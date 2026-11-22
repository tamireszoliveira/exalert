"""Mercado falso para desenvolvimento: imita AwesomeAPI e brapi com preços que você controla.

Serve para ver um alerta disparar sem esperar o mercado andar. No compose ele sobe
com o perfil "mercado-falso" e o checker passa a consultar aqui.

    python -m alerta_checker.fake_market --port 9000

    curl localhost:9000/precos                        # preços atuais
    curl -X PUT localhost:9000/precos/VALE3 -d 64.5   # muda (cria) um preço
    curl -X DELETE localhost:9000/precos/VALE3        # ticker passa a responder 404

Preços iniciais: FAKE_PRICES="USD-BRL=5.18,VALE3=70.10" (ou o padrão abaixo).
"""

from __future__ import annotations

import argparse
import json
import math
import os
import signal
import sys
import threading
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, unquote, urlparse

DEFAULT_PRICES = {"USD-BRL": 5.18, "EUR-BRL": 6.02, "VALE3": 70.10, "PETR4": 38.50, "ITUB4": 36.20, "MGLU3": 9.80}


def parse_prices(raw: str | None) -> dict[str, float]:
    if not raw:
        return dict(DEFAULT_PRICES)
    prices = {}
    for item in raw.split(","):
        name, _, value = item.partition("=")
        prices[name.strip().upper()] = float(value)
    return prices


class Market:
    def __init__(self, prices: dict[str, float]):
        self.lock = threading.Lock()
        self.prices = dict(prices)

    def get(self, asset: str) -> float | None:
        with self.lock:
            return self.prices.get(asset.upper())

    def set(self, asset: str, price: float) -> None:
        with self.lock:
            self.prices[asset.upper()] = price

    def delete(self, asset: str) -> bool:
        with self.lock:
            return self.prices.pop(asset.upper(), None) is not None

    def snapshot(self) -> dict[str, float]:
        with self.lock:
            return dict(self.prices)


class Handler(BaseHTTPRequestHandler):
    server_version = "mercado-falso/1.0"

    def log_message(self, fmt: str, *args: object) -> None:
        sys.stderr.write(f"mercado-falso: {fmt % args}\n")

    @property
    def market(self) -> Market:
        return self.server.market  # type: ignore[attr-defined]

    def _reply(self, status: int, payload: object) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802 (nome exigido pelo BaseHTTPRequestHandler)
        url = urlparse(self.path)
        now = datetime.now(UTC)
        if url.path in ("/healthz", "/"):
            self._reply(200, {"status": "ok"})
        elif url.path == "/precos":
            self._reply(200, self.market.snapshot())
        elif url.path.startswith("/json/last/"):  # AwesomeAPI: /json/last/USD-BRL[,EUR-BRL]
            answer = {}
            for pair in unquote(url.path.rsplit("/", 1)[1]).upper().split(","):
                price = self.market.get(pair)
                if price is None:
                    self._reply(404, {"status": 404, "code": "CoinNotExists", "message": f"moeda nao encontrada {pair}"})
                    return
                text = f"{price:.4f}"
                answer[pair.replace("-", "")] = {
                    "code": pair.split("-")[0],
                    "codein": pair.split("-")[-1],
                    "name": f"{pair} (mercado falso)",
                    "high": text,
                    "low": text,
                    "varBid": "0",
                    "pctChange": "0",
                    "bid": text,
                    "ask": text,
                    "timestamp": str(int(now.timestamp())),
                    "create_date": now.strftime("%Y-%m-%d %H:%M:%S"),
                }
            self._reply(200, answer)
        elif url.path == "/api/v2/stocks/quote":  # brapi v2: ?symbols=VALE3
            symbol = parse_qs(url.query).get("symbols", [""])[0].strip().upper()
            price = self.market.get(symbol)
            if price is None:
                self._reply(404, {"error": True, "message": "no data found for the request"})
                return
            stamp = now.isoformat(timespec="milliseconds").replace("+00:00", "Z")
            self._reply(
                200,
                {
                    "results": [
                        {
                            "requestedSymbol": symbol,
                            "symbol": symbol,
                            "changed": False,
                            "data": {"shortName": symbol, "currency": "BRL", "regularMarketPrice": price, "regularMarketTime": stamp},
                        }
                    ],
                    "requestedAt": stamp,
                    "took": 0,
                },
            )
        else:
            self._reply(404, {"error": "rota desconhecida"})

    def do_PUT(self) -> None:  # noqa: N802
        url = urlparse(self.path)
        if not url.path.startswith("/precos/"):
            self._reply(404, {"error": "use PUT /precos/<ATIVO> com o preço no corpo"})
            return
        asset = unquote(url.path.split("/", 2)[2]).strip().upper()
        raw = self.rfile.read(int(self.headers.get("Content-Length") or 0)).decode("utf-8", "replace").strip()
        try:
            value = json.loads(raw) if raw else None
            price = float(value["preco"] if isinstance(value, dict) else value)  # type: ignore[arg-type]
        except (ValueError, TypeError, KeyError):
            self._reply(400, {"error": 'corpo deve ser um número (64.5) ou {"preco": 64.5}'})
            return
        if not asset or not math.isfinite(price) or price <= 0:
            self._reply(400, {"error": "ativo vazio ou preço inválido"})
            return
        self.market.set(asset, price)
        self._reply(200, {asset: price})

    def do_DELETE(self) -> None:  # noqa: N802
        url = urlparse(self.path)
        asset = unquote(url.path.split("/", 2)[2]).upper() if url.path.startswith("/precos/") else ""
        self._reply(200 if asset and self.market.delete(asset) else 404, {"removido": asset})


class FakeMarket(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, host: str, port: int, prices: dict[str, float]):
        super().__init__((host, port), Handler)
        self.market = Market(prices)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Mercado falso (AwesomeAPI + brapi) para desenvolvimento.")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=int(os.environ.get("PORT", "9000")))
    args = parser.parse_args(argv)
    server = FakeMarket(args.host, args.port, parse_prices(os.environ.get("FAKE_PRICES")))
    print(f"mercado falso em http://{args.host}:{server.server_address[1]} com {server.market.snapshot()}", flush=True)
    # Como PID 1 no contêiner, sem handler o SIGTERM seria ignorado (e o stop levaria 10 s).
    signal.signal(signal.SIGTERM, lambda *_: threading.Thread(target=server.shutdown, daemon=True).start())
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
