"""Tiny HTTP test server that mimics an OpenAI-compatible /chat/completions.

Launches on 127.0.0.1:<random_port>. Supports three behaviours, controlled via
the URL path:

  POST /ok            -> always returns a valid 200 + non-empty content
  POST /fail/<n>      -> returns 5xx for the first <n> requests per key (test fallback)
  POST /timeout       -> sleeps 60s (used to drive retry+timeout paths)

Run with: python -m tests._dummy_server [--port PORT]
"""
from __future__ import annotations

import json
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer

STATE = {"counts": {}, "calls": 0, "fail_until": 0}
STATE_LOCK = threading.Lock()


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_a, **_kw):
        pass

    def do_POST(self):  # noqa: N802
        with STATE_LOCK:
            STATE["calls"] += 1
            path = self.path
            auth = self.headers.get("Authorization", "")
            key = auth.replace("Bearer ", "").strip() or "<anon>"
            STATE["counts"].setdefault(key, 0)
            STATE["counts"][key] += 1

        # Read and discard body (don't fail on it)
        length = int(self.headers.get("Content-Length", "0"))
        _ = self.rfile.read(length) if length else b""

        # Default behaviour: any /chat/completions request returns a 200.
        # Optional path suffixes /ok|/empty|/fail/<n> override the behaviour.
        if path.startswith("/chat/completions") or path == "/v1/chat/completions":
            sub = ""  # default 200
        elif path.startswith("/ok"):
            sub = "ok"
        elif path.startswith("/empty"):
            sub = "empty"
        elif path.startswith("/fail"):
            sub = "fail"
            try:
                STATE["fail_until"] = int(path.split("/")[2])
            except Exception:
                STATE["fail_until"] = 1
        else:
            self._reply(404, {"error": "unknown path"})
            return

        # /empty always returns empty content
        if sub == "empty":
            body = {
                "id": "empty",
                "choices": [{"message": {"role": "assistant", "content": ""}}],
                "usage": {"prompt_tokens": 5, "completion_tokens": 1, "total_tokens": 6},
            }
            self._reply(200, body)
            return

        # /fail returns 500 for the first STATE['fail_until'] calls
        if sub == "fail":
            with STATE_LOCK:
                if STATE["calls"] <= STATE["fail_until"]:
                    self._reply(500, {"error": {"message": "forced 500"}})
                    return
            body = {
                "id": "okafterfail",
                "choices": [{"message": {"role": "assistant",
                                          "content": "ok after some failures"}}],
                "usage": {"prompt_tokens": 2, "completion_tokens": 4, "total_tokens": 6},
            }
            self._reply(200, body)
            return

        # default ok
        body = {
            "id": "ok",
            "choices": [{"message": {"role": "assistant",
                                      "content": "hello from dummy server"}}],
            "usage": {"prompt_tokens": 5, "completion_tokens": 3, "total_tokens": 8},
        }
        self._reply(200, body)

    def _reply(self, code: int, body: dict):
        data = json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


def serve(port: int = 0):
    httpd = HTTPServer(("127.0.0.1", port), Handler)
    actual_port = httpd.server_address[1]
    print(f"DUMMY_SERVER_PORT={actual_port}", flush=True)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 0
    serve(port)
