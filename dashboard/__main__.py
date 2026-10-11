"""Read-only dashboard for the bot: python -m dashboard --data . [--port 8080]

Serves one page and a JSON summary built from the bot's files. It never writes anything and has
no way to place or cancel orders. By default it listens on 127.0.0.1 only; on a server, reach it
through an SSH tunnel rather than exposing the port.
"""

from __future__ import annotations

import argparse
import json
import logging
from functools import partial
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from .data import summary

STATIC = Path(__file__).parent / "static"
log = logging.getLogger("dashboard")


class Handler(BaseHTTPRequestHandler):
    server_version = "studenti-dashboard"

    def __init__(self, *args, data_dir: Path, suffix: str, **kwargs):
        self.data_dir = data_dir
        self.suffix = suffix
        super().__init__(*args, **kwargs)

    def do_GET(self) -> None:
        path = self.path.split("?", 1)[0]
        if path in ("/", "/index.html"):
            self._send(HTTPStatus.OK, "text/html; charset=utf-8", (STATIC / "index.html").read_bytes())
        elif path == "/api/summary":
            body = json.dumps(summary(self.data_dir, self.suffix)).encode()
            self._send(HTTPStatus.OK, "application/json", body)
        elif path == "/healthz":
            self._send(HTTPStatus.OK, "text/plain", b"ok")
        else:
            self._send(HTTPStatus.NOT_FOUND, "text/plain", b"not found")

    def _send(self, status: HTTPStatus, content_type: str, body: bytes) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt: str, *args) -> None:
        log.debug(fmt, *args)


def make_server(data_dir: Path, host: str, port: int, suffix: str) -> ThreadingHTTPServer:
    return ThreadingHTTPServer((host, port), partial(Handler, data_dir=data_dir, suffix=suffix))


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--data", default=".", help="the bot's workdir (state, status and ledger files)")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8080)
    p.add_argument("--paper", action="store_true", help="show the paper-trading files instead of live")
    args = p.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    server = make_server(Path(args.data), args.host, args.port, "" if args.paper else ".live")
    log.info("dashboard on http://%s:%d (data: %s)", args.host, args.port, args.data)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
