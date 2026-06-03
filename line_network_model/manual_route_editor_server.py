#!/usr/bin/env python3
"""Local server for the manual route editor."""

from __future__ import annotations

from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


WORKSPACE = Path(__file__).resolve().parent.parent
MANUAL_ROUTE_FILE = WORKSPACE / "line_network_model" / "manual_intuition_route.csv"


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(WORKSPACE), **kwargs)

    def do_POST(self) -> None:
        if self.path != "/api/manual-route":
            self.send_error(404)
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            self.send_error(400, "Invalid Content-Length")
            return
        body = self.rfile.read(length).decode("utf-8-sig")
        if "line_id,order,station_id,note" not in body.splitlines()[:1]:
            self.send_error(400, "CSV header must be line_id,order,station_id,note")
            return
        MANUAL_ROUTE_FILE.write_text(body, encoding="utf-8")
        self.send_response(204)
        self.end_headers()


def main() -> None:
    server = ThreadingHTTPServer(("127.0.0.1", 8765), Handler)
    print("Manual route editor: http://localhost:8765/line_network_model/manual_route_editor.html")
    server.serve_forever()


if __name__ == "__main__":
    main()

