"""Local OpenAI-compatible endpoint for the IDR judge, forwarding to the Adaption API (standard library only).

Usage: ADAPTION_API_KEY=... python3 judge_proxy.py [port]      (default port 8090)

The official judge calls POST /v1/chat/completions. This forwards each call to Adaption's /responses endpoint with
the system messages as `instructions` and the rest as `input`, and returns the reply in chat-completions form. It
retries rate limits and server errors with backoff. The key is read from the environment and never logged.
"""

from __future__ import annotations

import json
import os
import random
import sys
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

API = "https://api.prod.adaptionlabs.ai/api/v1/responses"
KEY = os.environ["ADAPTION_API_KEY"].strip()


def forward(req: dict) -> dict:
    msgs = req.get("messages", [])
    system = "\n\n".join(m["content"] for m in msgs if m.get("role") == "system" and isinstance(m.get("content"), str))
    rest = [{"role": m["role"], "content": m["content"]} for m in msgs if m.get("role") != "system"]
    body = {"model": req["model"], "input": rest, "max_output_tokens": req.get("max_tokens") or 4096}
    if system:
        body["instructions"] = system
    if req.get("temperature") is not None:
        body["temperature"] = req["temperature"]
    data = json.dumps(body).encode()
    last = ""
    for attempt in range(8):
        r = urllib.request.Request(API, data=data, headers={"Content-Type": "application/json",
                                                             "Authorization": f"Bearer {KEY}"})
        try:
            with urllib.request.urlopen(r, timeout=300) as resp:
                out = json.load(resp)
            text = out.get("output_text") or "".join(c.get("text", "") for o in out.get("output", [])
                                                      for c in o.get("content", []) or [] if isinstance(c, dict))
            u = out.get("usage") or {}
            return {"id": out.get("id", "adaption"), "object": "chat.completion", "model": req["model"],
                    "choices": [{"index": 0, "finish_reason": "stop",
                                 "message": {"role": "assistant", "content": text}}],
                    "usage": {"prompt_tokens": u.get("input_tokens", 0), "completion_tokens": u.get("output_tokens", 0),
                              "total_tokens": u.get("input_tokens", 0) + u.get("output_tokens", 0)}}
        except urllib.error.HTTPError as e:
            last = f"HTTP {e.code}"
            if e.code < 500 and e.code != 429:
                raise RuntimeError(f"{last}: {e.read()[:300]!r}") from e
        except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
            last = repr(e)[:120]
        time.sleep(min(10 * 2 ** attempt, 120) + random.random() * 3)
    raise RuntimeError(f"judge unreachable after retries ({last})")


class Handler(BaseHTTPRequestHandler):
    def _send(self, code: int, obj: dict) -> None:
        b = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def do_GET(self) -> None:  # noqa: N802
        self._send(200, {"object": "list", "data": [{"id": "deepseek-v4-flash", "object": "model"}]})

    def do_POST(self) -> None:  # noqa: N802
        try:
            req = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            self._send(200, forward(req))
        except Exception as e:
            self._send(502, {"error": {"message": str(e)[:300], "type": "server_error"}})

    def log_message(self, *a: object) -> None:
        pass


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8090
    ThreadingHTTPServer(("127.0.0.1", port), Handler).serve_forever()
