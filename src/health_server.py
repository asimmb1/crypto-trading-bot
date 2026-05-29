"""
health_server.py — Lightweight HTTP server for Railway deployment.

Endpoints:
  GET  /health   → 200 JSON status (circuit breaker, DMS, active pairs)
  POST /confirm  → Reset dead man's switch (replaces `python -m src.confirm`)
  GET  /status   → Full system status JSON

Run in a daemon thread so it doesn't block the bot:
    from src.health_server import start_health_server
    start_health_server()

Railway uses GET /health for its health check.
Use POST /confirm daily to keep the dead man's switch alive.
"""

import json
import os
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from datetime import datetime, timezone

from loguru import logger

PORT = int(os.environ.get("PORT", 8080))


def _get_status() -> dict:
    """Build a status dict from local state files."""
    status = {
        "ok": True,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "env": os.environ.get("ENV", "unknown"),
        "circuit_breaker": {"tripped": False, "reason": None},
        "dead_mans_switch": {"alive": True, "hours_until_halt": None},
        "strategy_matrix": {"loaded": False, "pairs": 0},
    }

    # Circuit breaker
    state_file = "logs/system_state.json"
    if os.path.exists(state_file):
        try:
            with open(state_file) as f:
                state = json.load(f)
            tripped = state.get("locked", False)
            status["circuit_breaker"] = {
                "tripped": tripped,
                "reason": state.get("reason") if tripped else None,
                "tripped_at": state.get("tripped_at") if tripped else None,
            }
            if tripped:
                status["ok"] = False
        except Exception:
            pass

    # Dead man's switch
    heartbeat_file = "logs/heartbeat.json"
    if os.path.exists(heartbeat_file):
        try:
            with open(heartbeat_file) as f:
                hb = json.load(f)
            from src.dead_mans_switch import DeadMansSwitch
            dms = DeadMansSwitch()
            ds = dms.status()
            status["dead_mans_switch"] = {
                "alive": ds["alive"],
                "hours_since_confirm": round(ds["hours_since"], 1),
                "hours_until_halt": round(ds["hours_until_halt"], 1),
                "last_confirmed": ds["last_confirmed"],
            }
            if not ds["alive"]:
                status["ok"] = False
        except Exception:
            pass

    # Strategy matrix
    matrix_file = "logs/profitability_matrix.json"
    if os.path.exists(matrix_file):
        try:
            with open(matrix_file) as f:
                matrix = json.load(f)
            status["strategy_matrix"] = {
                "loaded": True,
                "pairs": len(matrix.get("pairs", {})),
                "generated_at": matrix.get("generated_at", "?")[:10],
            }
        except Exception:
            pass

    return status


class _Handler(BaseHTTPRequestHandler):

    def log_message(self, format, *args):
        # Suppress default HTTP request logs (noisy in prod)
        pass

    def _send_json(self, code: int, data: dict):
        body = json.dumps(data, indent=2).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path in ("/health", "/"):
            status = _get_status()
            code = 200 if status["ok"] else 503
            self._send_json(code, status)

        elif self.path == "/status":
            self._send_json(200, _get_status())

        else:
            self._send_json(404, {"error": "not found"})

    def do_POST(self):
        if self.path == "/confirm":
            try:
                from src.dead_mans_switch import DeadMansSwitch
                dms = DeadMansSwitch()
                dms.confirm()
                logger.info("Dead man's switch confirmed via HTTP /confirm")
                self._send_json(200, {
                    "confirmed": True,
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "message": "Dead man's switch reset. Bot will keep running.",
                })
            except Exception as e:
                logger.error(f"Health server /confirm error: {e}")
                self._send_json(500, {"error": str(e)})

        else:
            self._send_json(404, {"error": "not found"})


def start_health_server():
    """Start the health HTTP server in a background daemon thread."""
    server = HTTPServer(("0.0.0.0", PORT), _Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    logger.info(f"Health server listening on port {PORT} — GET /health | POST /confirm")
