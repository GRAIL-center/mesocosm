"""mitmproxy addon: the web gateway (ADR-0022).

Runs inside the `web` container. Every request from an agent container (identified by its fixed
address, like at the world services) is decided by `mesocosm.env.web.WebPolicy`, refused with a web
filter page or forwarded, and logged to web.jsonl with the response's hash and, for text, an excerpt.
Refusals of HTTPS hosts happen at CONNECT, before any TLS, so nothing is sent to a denied host.
Start with:

    mitmdump -s /app/web_addon.py --set confdir=/ca --set connection_strategy=lazy --listen-port 3128
"""

from __future__ import annotations

import json
import logging
import os
import pathlib
import threading
import time

from mitmproxy import ctx, http

from mesocosm.env.web import Usage, WebPolicy, block_page, policy_state, web_record


class WebGateway:
    def __init__(self) -> None:
        env = os.environ
        self.policy = WebPolicy.from_dict(json.loads(env.get("MESOCOSM_WEB_POLICY", "{}")))
        self.agent_ips: dict[str, str] = json.loads(env.get("MESOCOSM_AGENT_IPS", "{}"))
        self.log_dir = pathlib.Path(env.get("MESOCOSM_WEB_LOG_DIR", "/logs"))
        self.usage = Usage()
        self.lock = threading.Lock()
        self._file = None

    # ---- records -----------------------------------------------------------------------------

    def write(self, rec: dict) -> None:
        with self.lock:
            if self._file is None:
                self.log_dir.mkdir(parents=True, exist_ok=True)
                self._file = (self.log_dir / "web.jsonl").open("a", encoding="utf-8", buffering=1)
            self._file.write(json.dumps(rec, ensure_ascii=False) + "\n")

    def running(self) -> None:
        self.log_dir.mkdir(parents=True, exist_ok=True)
        state = policy_state(self.policy, mode=ctx.options.mode, listen_port=ctx.options.listen_port,
                             agents=sorted(self.agent_ips.values()))
        (self.log_dir / "policy.json").write_text(json.dumps(state, indent=1), encoding="utf-8")
        logging.info("web gateway: %d allow, %d deny patterns, mode %s", len(self.policy.allow),
                     len(self.policy.deny), self.policy.mode)

    def who(self, flow: http.HTTPFlow) -> tuple[str, str]:
        ip = flow.client_conn.peername[0] if flow.client_conn.peername else "?"
        return self.agent_ips.get(ip, f"unknown:{ip}"), ip

    def record(self, flow: http.HTTPFlow, decision: str, rule: str | None, **extra) -> None:
        agent, ip = self.who(flow)
        req = flow.request
        resp = flow.response
        body = None
        if resp is not None and not extra.get("connect"):
            try:
                body = resp.content          # None while streaming; raw bytes are decoded by mitmproxy
            except ValueError:
                body = resp.raw_content
        started = req.timestamp_start
        ended = (resp.timestamp_end if resp is not None and resp.timestamp_end else None) or time.time()
        self.write(web_record(
            agent=agent, ip=ip, method=req.method, scheme=req.scheme, host=req.pretty_host, port=req.port,
            path=req.path if not extra.get("connect") else "", decision=decision, rule=rule,
            status=resp.status_code if resp is not None else None,
            request_bytes=len(req.raw_content or b""), response_bytes=len(body or b""),
            content_type=resp.headers.get("content-type") if resp is not None else None, body=body,
            duration_ms=round((ended - started) * 1000) if started else None,
            error=flow.error.msg if flow.error else extra.get("error"), connect=bool(extra.get("connect")),
            replayed=bool(flow.is_replay)))

    # ---- decisions ---------------------------------------------------------------------------

    def refuse(self, flow: http.HTTPFlow, decision: str, rule: str | None, status: int = 403) -> None:
        reason = "budget" if decision == "denied:budget" else ("network" if decision == "denied:network" else "policy")
        flow.response = http.Response.make(status, block_page(flow.request.pretty_host, reason),
                                           {"Content-Type": "text/html; charset=utf-8", "Cache-Control": "no-store"})
        flow.metadata["web_decision"] = decision
        flow.metadata["web_rule"] = rule

    def http_connect(self, flow: http.HTTPFlow) -> None:
        """A CONNECT for an HTTPS host: refuse here, before TLS, if the host is off limits."""
        decision, rule = self.policy.decide(flow.request.pretty_host, flow.request.port)
        if decision != "allowed":
            self.refuse(flow, decision, rule)
            self.record(flow, decision, rule, connect=True)

    def request(self, flow: http.HTTPFlow) -> None:
        agent, _ = self.who(flow)
        decision, rule = self.policy.decide(flow.request.pretty_host, flow.request.port)
        if decision == "allowed":
            exhausted = self.usage.exhausted(self.policy, agent)
            if exhausted:
                decision, rule = "denied:budget", exhausted
        if decision != "allowed":
            self.refuse(flow, decision, rule, status=429 if decision == "denied:budget" else 403)
            self.record(flow, decision, rule)
            return
        self.usage.add(agent)
        flow.metadata["web_decision"] = "allowed"
        flow.metadata["web_rule"] = rule

    def response(self, flow: http.HTTPFlow) -> None:
        if flow.metadata.get("web_decision") != "allowed":
            return                            # refusals were recorded when decided
        agent, _ = self.who(flow)
        size = len(flow.response.raw_content or b"") if flow.response is not None else 0
        self.usage.bytes[agent] = self.usage.bytes.get(agent, 0) + size
        self.record(flow, "allowed", flow.metadata.get("web_rule"))

    def error(self, flow: http.HTTPFlow) -> None:
        if flow.metadata.get("web_decision") == "allowed" and flow.response is None:
            self.record(flow, "allowed", flow.metadata.get("web_rule"), error=flow.error.msg if flow.error else "error")


addons = [WebGateway()]
