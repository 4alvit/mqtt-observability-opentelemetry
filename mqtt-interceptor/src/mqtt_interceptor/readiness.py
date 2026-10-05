"""Serve cheap broker readiness beside the existing Prometheus metrics."""

import math
import threading
import time
from collections.abc import Callable, Iterable
from typing import Any
from wsgiref.simple_server import WSGIRequestHandler, WSGIServer, make_server

from prometheus_client import make_wsgi_app
from prometheus_client.exposition import ThreadingWSGIServer


class BrokerReadiness:
    """Require a valid receipt in the current connected MQTT session."""

    def __init__(self, max_age: float = 45) -> None:
        self.max_age = max_age
        self._lock = threading.Lock()
        self._connected = False
        self._received_at: float | None = None

    def connected(self, success: bool) -> None:
        """Every connect/disconnect invalidates the previous session's receipt."""
        with self._lock:
            self._connected = success
            self._received_at = None

    def observe(self, value: int | float) -> None:
        """Record only a finite nonnegative target sample while connected."""
        with self._lock:
            self._received_at = (
                time.monotonic()
                if self._connected and math.isfinite(value) and value >= 0
                else None
            )

    def refresh(self) -> None:
        """Refresh unchanged Mosquitto data only after this session's valid sample."""
        with self._lock:
            if self._connected and self._received_at is not None:
                self._received_at = time.monotonic()

    def is_ready(self) -> bool:
        """Read local state only; never collect metrics or call the broker."""
        with self._lock:
            return (
                self._connected
                and self._received_at is not None
                and 0 <= time.monotonic() - self._received_at < self.max_age
            )


class QuietHandler(WSGIRequestHandler):
    """Preserve the metrics listener's quiet request logging."""

    def log_message(self, format: str, *args: Any) -> None:
        """Do not write an access log for probes or scrapes."""


def start_metrics_server(
    port: int, ready: Callable[[], bool], addr: str = "0.0.0.0"
) -> tuple[WSGIServer, threading.Thread]:
    """Start one listener with unchanged Prometheus output and GET /ready."""
    metrics_app = make_wsgi_app()

    def application(environ: dict[str, Any], start_response: Any) -> Iterable[bytes]:
        if environ.get("PATH_INFO") != "/ready":
            return metrics_app(environ, start_response)  # type: ignore[no-any-return]
        if environ.get("REQUEST_METHOD") != "GET":
            status, body = "405 Method Not Allowed", b"method not allowed\n"
        elif ready():
            status, body = "200 OK", b"ready\n"
        else:
            status, body = "503 Service Unavailable", b"not ready\n"
        start_response(
            status,
            [
                ("Content-Type", "text/plain; charset=utf-8"),
                ("Content-Length", str(len(body))),
                ("Cache-Control", "no-store"),
            ],
        )
        return [body]

    server = make_server(
        addr, port, application, server_class=ThreadingWSGIServer, handler_class=QuietHandler
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, thread
