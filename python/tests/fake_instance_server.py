"""A stand-in instance: serves ``/health`` on ``$PORT``, or misbehaves on request.

``--exit N`` exits with code N before listening, ``--never-ready`` listens but
answers every request 503, and ``--ignore-term`` survives SIGTERM so a stop has
to escalate.
"""

from __future__ import annotations

import argparse
import os
import signal
import sys
from http.server import BaseHTTPRequestHandler, HTTPServer


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--ok", action="store_true", help="the default: serve and answer")
    parser.add_argument("--exit", type=int, default=None)
    parser.add_argument("--never-ready", action="store_true")
    parser.add_argument("--ignore-term", action="store_true")
    args = parser.parse_args()
    print(f"fake instance starting on {os.environ['PORT']}", flush=True)
    if args.exit is not None:
        return args.exit
    if args.ignore_term:
        signal.signal(signal.SIGTERM, signal.SIG_IGN)
    never_ready = args.never_ready

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802 - the stdlib's name
            ok = self.path == "/health" and not never_ready
            self.send_response(200 if ok else 503)
            self.end_headers()
            self.wfile.write(b"ok" if ok else b"no")

        def log_message(self, *_args: object) -> None:
            return

    HTTPServer(("127.0.0.1", int(os.environ["PORT"])), Handler).serve_forever()
    return 0


if __name__ == "__main__":
    sys.exit(main())
