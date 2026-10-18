#!/usr/bin/env python3
"""Spike step 4: drive an OpenCode server over HTTP and check the exposure record.

What this verifies (capability-matrix rows):
  * POST /session and POST /session/{id}/message work against `opencode serve`;
  * GET /event (SSE) yields a usable event stream (saved to opencode_events.jsonl);
  * every model call went through the gateway (gateway.jsonl has the agent's requests);
  * the gateway record shows the exact messages/tools the model received.

Usage (host):
    python 04_drive_opencode.py --url http://127.0.0.1:4096 --out runs/spike
Usage (docker control container):
    python 04_drive_opencode.py --url http://agent-1:4096 --out /runs/spike --run spike-docker

Only stdlib + httpx.
"""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import sys
import threading
import time

import httpx

DEFAULT_TASK = (
    "You are working in an empty project directory. "
    "1) Create `hello.py` that prints the sum of the integers from 1 to 100. "
    "2) Run it with python3 and tell me the exact output. "
    "3) Create `NOTES.md` with two sentences describing what you did. "
    "Use your tools; do not just describe the steps."
)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default=os.environ.get("OPENCODE_URL", "http://127.0.0.1:4096"))
    ap.add_argument("--provider", default="mesocosm")
    ap.add_argument("--model", default=os.environ.get("MESOCOSM_MODEL_ID", "qwen3-4b"))
    ap.add_argument("--task-file", default=None)
    ap.add_argument("--out", default="runs/spike")
    ap.add_argument("--run", default=None, help="X-Mesocosm-Run value to filter the gateway log on")
    ap.add_argument("--agent", default="agent-1", help="X-Mesocosm-Agent value to filter the gateway log on")
    ap.add_argument("--timeout", type=float, default=900.0)
    args = ap.parse_args()

    out = pathlib.Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    auth = None
    if os.environ.get("OPENCODE_SERVER_PASSWORD"):
        auth = (os.environ.get("OPENCODE_SERVER_USERNAME", "opencode"), os.environ["OPENCODE_SERVER_PASSWORD"])
    c = httpx.Client(base_url=args.url, timeout=httpx.Timeout(args.timeout, connect=10.0), auth=auth)

    health = c.get("/global/health").json()
    print("health:", health)

    providers = c.get("/config/providers").json()
    ids = []
    for p in providers.get("providers", []):
        ids.append(p.get("id") or p.get("name"))
    print("providers:", ids, "default:", providers.get("default"))
    if args.provider not in ids:
        print(f"WARN provider '{args.provider}' not in providers list; check opencode.json location/OPENCODE_CONFIG",
              file=sys.stderr)

    # SSE listener: raw events to a file; we only need to prove the stream works and see tool events.
    events_path = out / "opencode_events.jsonl"
    stop = threading.Event()
    counts: dict[str, int] = {}

    def listen() -> None:
        try:
            with c.stream("GET", "/event") as r, open(events_path, "a", encoding="utf-8") as f:
                for line in r.iter_lines():
                    if stop.is_set():
                        break
                    if not line.startswith("data:"):
                        continue
                    data = line[5:].strip()
                    if not data:
                        continue
                    f.write(data + "\n")
                    try:
                        ev = json.loads(data)
                        t = ev.get("type", "?")
                        counts[t] = counts.get(t, 0) + 1
                    except json.JSONDecodeError:
                        counts["unparsed"] = counts.get("unparsed", 0) + 1
        except Exception as e:  # noqa: BLE001 - spike: report, do not crash the driver
            print("event stream ended:", repr(e), file=sys.stderr)

    threading.Thread(target=listen, daemon=True).start()
    time.sleep(1.0)

    session = c.post("/session", json={"title": "mesocosm spike"}).json()
    sid = session["id"]
    print("session:", sid)

    task = pathlib.Path(args.task_file).read_text() if args.task_file else DEFAULT_TASK
    t0 = time.time()
    r = c.post(
        f"/session/{sid}/message",
        json={"model": {"providerID": args.provider, "modelID": args.model},
              "parts": [{"type": "text", "text": task}]},
    )
    elapsed = time.time() - t0
    print(f"message status={r.status_code} elapsed={elapsed:.1f}s")
    if r.status_code >= 400:
        print(r.text[:2000])
        return 2
    reply = r.json()
    for part in reply.get("parts", []):
        kind = part.get("type")
        if kind == "text":
            print("  [text]", (part.get("text") or "")[:300].replace("\n", " "))
        elif kind == "tool":
            state = part.get("state") or {}
            print("  [tool]", part.get("tool"), "->", (state.get("status") or ""),
                  (json.dumps(state.get("input"), ensure_ascii=False) if state.get("input") else "")[:160])
        else:
            print(f"  [{kind}]")

    messages = c.get(f"/session/{sid}/message").json()
    (out / "opencode_messages.json").write_text(json.dumps(messages, ensure_ascii=False, indent=1))
    print(f"messages in session: {len(messages)} (saved to {out / 'opencode_messages.json'})")

    time.sleep(1.0)
    stop.set()
    print("event types seen:", counts)

    # Exposure check against the gateway log.
    gw = out / "gateway.jsonl"
    if not gw.exists():
        print(f"WARN no gateway log at {gw}; is MESOCOSM_LOG_DIR the same directory?", file=sys.stderr)
        return 0
    recs = [json.loads(line) for line in gw.read_text(encoding="utf-8").splitlines() if line.strip()]
    recs = [x for x in recs if x.get("agent") == args.agent and x.get("event") in ("chat_completion", "budget_exhausted")
            and x["ts"] >= t0 - 5 and (args.run is None or x.get("run") == args.run)]
    print(f"\ngateway records for agent={args.agent} during this task: {len(recs)}")
    print(f"{'#':>3} {'status':>6} {'ms':>7} {'msgs':>4} {'tools':>5} {'p_tok':>6} {'c_tok':>6} {'ids':>3}  finish / tool calls")
    for i, x in enumerate(recs, 1):
        s = x.get("request_summary", {})
        resp = x.get("response", {}) or {}
        usage = resp.get("usage") or {}
        tcs = ",".join((tc.get("function") or {}).get("name", "?") for tc in resp.get("tool_calls") or [])
        has_ids = "y" if resp.get("prompt_token_ids") is not None else "n"
        print(f"{i:>3} {x.get('status', x.get('event')):>6} {x.get('latency_ms', 0):>7} {s.get('n_messages', 0):>4} "
              f"{s.get('n_tools', 0):>5} {usage.get('prompt_tokens', 0):>6} {usage.get('completion_tokens', 0):>6} {has_ids:>3}  "
              f"{resp.get('finish_reason')} {tcs}")
    if recs:
        last = recs[-1]
        msgs = (last.get("request") or {}).get("messages") or []
        print("\nlast request as the model saw it:")
        for m in msgs[:3]:
            content = m.get("content")
            if isinstance(content, list):
                content = " ".join(str(part.get("text", "")) for part in content if isinstance(part, dict))
            print(f"  [{m.get('role')}] {(content or '')[:240].replace(chr(10), ' ')}")
        if len(msgs) > 3:
            print(f"  ... {len(msgs) - 3} more messages")
        print("  tools offered:", (last.get("request_summary") or {}).get("tool_names"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
