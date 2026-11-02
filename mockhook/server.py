"""Tiny webhook receiver for local development (``python -m mockhook``).

Stdlib only so it starts without the database. Environment:

    MOCKHOOK_PORT    listen port (9000); bound on the compose network only
    MOCKHOOK_LOG     JSON-lines file, one line per delivery
    MOCKHOOK_SECRET  if set, deliveries with a bad signature get 401
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

_write_lock = threading.Lock()


def check_signature(secret: str, header: str, body: bytes, now: int,
                    tolerance_s: int = 300) -> bool:
    # same scheme as app.reporting.webhooks.verify, duplicated to stay stdlib-only
    parts = dict(p.strip().partition("=")[::2] for p in (header or "").split(",") if "=" in p)
    ts = parts.get("t", "")
    if not ts.isdigit() or abs(now - int(ts)) > tolerance_s:
        return False
    expected = hmac.new(secret.encode(), f"{ts}.".encode() + body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, parts.get("v1", ""))


def handle_delivery(path: str, headers: Any, body: bytes, env: dict[str, str],
                    now: float) -> tuple[int, dict[str, Any] | None]:
    """Decide the response code and the log record for one request."""
    if not path.startswith("/hooks/"):
        return 404, None
    secret = env.get("MOCKHOOK_SECRET", "")
    sig_ok = None
    if secret:
        sig_ok = check_signature(secret, headers.get("X-Tidewright-Signature", ""), body, int(now))
    try:
        parsed: Any = json.loads(body)
    except ValueError:
        parsed = body.decode("utf-8", "replace")
    record = {
        "received_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now)),
        "path": path,
        "tenant_slug": path.removeprefix("/hooks/").strip("/"),
        "event": headers.get("X-Tidewright-Event"),
        "delivery": headers.get("X-Tidewright-Delivery"),
        "signature": headers.get("X-Tidewright-Signature"),
        "signature_ok": sig_ok,
        "body": parsed,
    }
    return (401 if sig_ok is False else 204), record


def append_record(log_path: Path, record: dict[str, Any]) -> None:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with _write_lock, log_path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, sort_keys=True) + "\n")


class Handler(BaseHTTPRequestHandler):
    server_version = "mockhook/1.0"

    def do_GET(self) -> None:  # noqa: N802
        self.send_response(200 if self.path == "/healthz" else 404)
        self.end_headers()

    def do_POST(self) -> None:  # noqa: N802
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(min(length, 1_000_000))
        status, record = handle_delivery(self.path, self.headers, body, dict(os.environ),
                                         time.time())
        if record is not None:
            record["status"] = status
            append_record(Path(os.environ.get("MOCKHOOK_LOG", "var/mockhook/deliveries.jsonl")),
                          record)
        self.send_response(status)
        self.end_headers()


def main() -> None:
    port = int(os.environ.get("MOCKHOOK_PORT", "9000"))
    # 0.0.0.0 inside the container; compose publishes no port for this service
    server = ThreadingHTTPServer(("0.0.0.0", port), Handler)  # noqa: S104
    print(f"mockhook listening on :{port}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
