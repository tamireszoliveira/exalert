"""Imitação dos endpoints internos da API Java, usada nos testes do worker.

Segue o mesmo contrato que services/api implementa (docs/api.md):
token Bearer, versão otimista do alerta, evento idempotente pelo id e outbox de push.
"""

import json
import threading
import uuid
from datetime import UTC, datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

TOKEN = "token-do-checker-para-testes"


def parse_iso(text):
    return datetime.fromisoformat(text.replace("Z", "+00:00"))


class _Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def _reply(self, status, payload=None):
        body = b"" if payload is None else json.dumps(payload).encode()
        self.send_response(status)
        if payload is not None:
            self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _guard(self, route):
        api = self.server
        api.calls.append((self.command, route))
        if self.headers.get("Authorization") != f"Bearer {api.token}":
            self._reply(401, {"title": "Unauthorized", "status": 401})
            return False
        queue = api.fail.get(route)
        if queue:
            self._reply(queue.pop(0), {"title": "falha injetada pelo teste"})
            return False
        return True

    def _body(self):
        return json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"null")

    def do_GET(self):
        api = self.server
        path = urlparse(self.path).path
        if path == "/internal/alerts":
            if self._guard("alerts"):
                with api.lock:
                    self._reply(200, [{k: v for k, v in a.items() if k != "enabled"} for a in api.alerts.values() if a["enabled"]])
        elif path == "/internal/events/pending":
            if self._guard("pending"):
                since = api.clock() - timedelta(hours=24)
                with api.lock:
                    items = [
                        {k: e[k] for k in ("id", "type", "asset", "direction", "title", "message", "occurredAt")}
                        for e in api.events.values()
                        if e["push"] and e["deliveredAt"] is None and parse_iso(e["occurredAt"]) > since
                    ]
                self._reply(200, sorted(items, key=lambda e: e["occurredAt"]))
        else:
            self._reply(404, {"title": "Not Found"})

    def do_POST(self):
        api = self.server
        path = urlparse(self.path).path
        if path == "/internal/quotes":
            if self._guard("quotes"):
                body = self._body()
                with api.lock:
                    api.quotes.extend(body)
                self._reply(204)
        elif path == "/internal/events":
            if not self._guard("events"):
                return
            event = self._body()
            with api.lock:
                if event["id"] in api.events:  # repetição (timeout do lado do checker): idempotente
                    self._reply(200, api.events[event["id"]])
                    return
                alert = api.alerts.get(event.get("alertId")) if event.get("alertId") else None
                if event.get("alertId"):
                    if alert is None:
                        self._reply(404, {"title": "alerta não existe mais"})
                        return
                    expected = {"FIRED": "ARMED", "REARMED": "FIRED"}[event["type"]]
                    if alert["version"] != event.get("alertVersion") or alert["state"] != expected or not alert["enabled"]:
                        self._reply(409, {"title": "alerta mudou desde a leitura"})
                        return
                    alert["state"] = "FIRED" if event["type"] == "FIRED" else "ARMED"
                    alert["version"] += 1
                stored = dict(event, deliveredAt=None)
                api.events[event["id"]] = stored
            self._reply(201, stored)
        elif path.startswith("/internal/events/") and path.endswith("/delivered"):
            if not self._guard("delivered"):
                return
            event_id = path.split("/")[3]
            with api.lock:
                event = api.events.get(event_id)
                if event is None:
                    self._reply(404, {"title": "evento não existe"})
                    return
                event["deliveredAt"] = api.clock().isoformat()
            self._reply(204)
        else:
            self._reply(404, {"title": "Not Found"})


class FakeAlertaApi(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, token=TOKEN, clock=lambda: datetime.now(UTC)):
        super().__init__(("127.0.0.1", 0), _Handler)
        self.token = token
        self.clock = clock  # "agora" da API (a janela de 24 h do outbox)
        self.lock = threading.Lock()
        self.alerts = {}
        self.quotes = []
        self.events = {}
        self.calls = []
        self.fail = {}  # rota -> fila de status HTTP a devolver antes de voltar ao normal

    @property
    def url(self):
        return f"http://127.0.0.1:{self.server_address[1]}"

    def add_alert(self, asset, direction, threshold, name=None, rearm_pct=0.5, enabled=True):
        alert_id = str(uuid.uuid4())
        self.alerts[alert_id] = {
            "id": alert_id,
            "asset": asset,
            "name": name or asset,
            "direction": direction,
            "threshold": threshold,
            "rearmPct": rearm_pct,
            "state": "ARMED",
            "version": 0,
            "enabled": enabled,
        }
        return alert_id

    def edit(self, alert_id, **changes):
        """Como o PUT da API: mudar ativo, direção ou limite rearma o alerta."""
        alert = self.alerts[alert_id]
        if any(key in changes for key in ("asset", "direction", "threshold")):
            alert["state"] = "ARMED"
        alert.update(changes)
        alert["version"] += 1

    def count(self, route):
        return sum(1 for _, name in self.calls if name == route)

    def events_of(self, kind):
        return [event for event in self.events.values() if event["type"] == kind]
