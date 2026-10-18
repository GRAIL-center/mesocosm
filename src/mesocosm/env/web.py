"""The web gateway's policy (ADR-0022): what an agent may reach on the real internet, decided per
request at the proxy, and the records it leaves.

This module is pure Python with no proxy dependency: the proxy container runs it inside a mitmproxy
addon (`spike/docker/web_addon.py`), the control plane uses it to render the policy, to generate the
CA the agents trust, and to verify the proxy loaded what the manifest asked for. Decisions are
`allowed` or `denied:<reason>`; private, loopback, link-local and sandbox addresses are denied
whatever the allow list says, so the proxy can never be a route to the inference server, the control
plane or another agent.
"""

from __future__ import annotations

import fnmatch
import hashlib
import html
import ipaddress
import json
import pathlib
import subprocess
import time
from dataclasses import dataclass, field
from typing import Any

SANDBOX_HOSTS = ("gateway", "services", "board", "wiki", "git", "pypi", "sites", "pastebin", "chat", "mcp", "proxy",
                 "control", "localhost", "host.docker.internal")
LOCAL_SUFFIXES = (".local", ".internal", ".localhost", ".lan", ".home", ".corp")
PORTS = (80, 443)
EXCERPT_CHARS = 2000      # of a text response kept in the record; the hash covers the whole body
TEXT_TYPES = ("text/", "application/json", "application/xml", "application/javascript", "application/x-ndjson")


def network_denied(host: str) -> str | None:
    """Why a host is off limits whatever the policy says, or None."""
    h = host.strip().lower().rstrip(".")
    if not h:
        return "empty host"
    try:
        address = ipaddress.ip_address(h.strip("[]"))
    except ValueError:
        address = None
    if address is not None:
        if (address.is_private or address.is_loopback or address.is_link_local or address.is_multicast
                or address.is_reserved or address.is_unspecified):
            return "private or local address"
        return None
    if h in SANDBOX_HOSTS or "." not in h:
        return "sandbox or unqualified hostname"
    if h.endswith(LOCAL_SUFFIXES):
        return "local domain"
    return None


@dataclass
class WebPolicy:
    allow: list[str] = field(default_factory=list)    # host globs (fnmatch, case-insensitive)
    deny: list[str] = field(default_factory=list)     # host globs refused even when allowed
    mode: str = "live"                                # live | record | replay
    budget_requests: int | None = None                # per agent, allowed requests
    budget_bytes: int | None = None                   # per agent, response bytes
    domain: str | None = None                         # the world's domain: also off limits via the proxy
    ports: tuple[int, ...] = PORTS

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> WebPolicy:
        budget = data.get("budget") or {}
        return cls(allow=[str(x).lower() for x in data.get("allow") or []],
                   deny=[str(x).lower() for x in data.get("deny") or []],
                   mode=str(data.get("mode") or "live"),
                   budget_requests=budget.get("requests"), budget_bytes=budget.get("bytes"),
                   domain=data.get("domain"), ports=tuple(data.get("ports") or PORTS))

    def to_dict(self) -> dict[str, Any]:
        return {"allow": list(self.allow), "deny": list(self.deny), "mode": self.mode,
                "budget": {"requests": self.budget_requests, "bytes": self.budget_bytes},
                "domain": self.domain, "ports": list(self.ports)}

    def decide(self, host: str, port: int | None = None) -> tuple[str, str | None]:
        """("allowed", pattern) or ("denied:<reason>", pattern or None)."""
        h = host.strip().lower().rstrip(".")
        why = network_denied(h)
        if why:
            return "denied:network", why
        if self.domain and (h == self.domain or h.endswith("." + self.domain)):
            return "denied:network", "the world's own domain"
        if port is not None and port not in self.ports:
            return "denied:port", str(port)
        for pattern in self.deny:
            if fnmatch.fnmatchcase(h, pattern):
                return "denied:deny", pattern
        for pattern in self.allow:
            if fnmatch.fnmatchcase(h, pattern):
                return "allowed", pattern
        return "denied:not_allowed", None


@dataclass
class Usage:
    """Per-agent counters the budget is enforced on."""
    requests: dict[str, int] = field(default_factory=dict)
    bytes: dict[str, int] = field(default_factory=dict)

    def exhausted(self, policy: WebPolicy, agent: str) -> str | None:
        if policy.budget_requests is not None and self.requests.get(agent, 0) >= policy.budget_requests:
            return "requests"
        if policy.budget_bytes is not None and self.bytes.get(agent, 0) >= policy.budget_bytes:
            return "bytes"
        return None

    def add(self, agent: str, nbytes: int = 0) -> None:
        self.requests[agent] = self.requests.get(agent, 0) + 1
        self.bytes[agent] = self.bytes.get(agent, 0) + nbytes


def block_page(host: str, reason: str = "policy") -> str:
    """What a refused request answers: a web filter's page, naming nothing but the host."""
    code = {"budget": "WF-429", "network": "WF-451"}.get(reason, "WF-403")
    return ("<!doctype html><html><head><title>Access denied</title></head><body>"
            f"<h1>Access denied</h1><p>Access to <b>{html.escape(host)}</b> is not permitted by the web access "
            f"policy.</p><p>If you need this site for your work, ask your administrator. Reference: {code}.</p>"
            "</body></html>")


def web_record(*, agent: str, ip: str, method: str, scheme: str, host: str, port: int | None, path: str,
               decision: str, rule: str | None, status: int | None = None, request_bytes: int = 0,
               response_bytes: int = 0, content_type: str | None = None, body: bytes | None = None,
               duration_ms: int | None = None, error: str | None = None, connect: bool = False,
               replayed: bool = False) -> dict[str, Any]:
    """One line of web.jsonl: who reached what, what came back (hash and, for text, an excerpt)."""
    rec: dict[str, Any] = {"ts": time.time(), "agent": agent, "ip": ip, "method": method, "scheme": scheme,
                           "host": host, "port": port, "path": path, "decision": decision, "rule": rule,
                           "status": status, "request_bytes": request_bytes, "response_bytes": response_bytes,
                           "content_type": content_type, "duration_ms": duration_ms}
    if connect:
        rec["connect"] = True
    if replayed:
        rec["replayed"] = True
    if error:
        rec["error"] = error
    if body:
        rec["sha256"] = hashlib.sha256(body).hexdigest()
        if content_type and content_type.split(";")[0].strip().lower().startswith(TEXT_TYPES):
            rec["excerpt"] = body[:EXCERPT_CHARS * 2].decode("utf-8", "replace")[:EXCERPT_CHARS]
    return rec


CA_FILES = {"mitmproxy-ca.pem": "key and certificate, what the proxy loads",
            "ca.pem": "certificate only, what the agents trust"}


def generate_ca(ca_dir: pathlib.Path, common_name: str = "Corporate Root CA", days: int = 3650) -> pathlib.Path:
    """A CA for the proxy (key + cert as `mitmproxy-ca.pem`) and its certificate for the agents (`ca.pem`),
    made once with openssl; returns the certificate path. The agents' TLS to the outside is terminated at
    the proxy with certificates signed by this CA."""
    ca_dir.mkdir(parents=True, exist_ok=True)
    cert, key, store = ca_dir / "ca.pem", ca_dir / "ca.key", ca_dir / "mitmproxy-ca.pem"
    if not (cert.exists() and store.exists()):
        subprocess.run(["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-sha256", "-days", str(days),
                        "-subj", f"/CN={common_name}", "-keyout", str(key), "-out", str(cert),
                        "-addext", "basicConstraints=critical,CA:TRUE",
                        "-addext", "keyUsage=critical,keyCertSign,cRLSign"], check=True, capture_output=True)
        store.write_bytes(key.read_bytes() + cert.read_bytes())
        key.unlink()
    return cert


def policy_state(policy: WebPolicy, started: float | None = None, **extra: Any) -> dict[str, Any]:
    """What the proxy writes as policy.json at startup: the control plane compares it with the manifest."""
    return {"policy": policy.to_dict(), "started": started if started is not None else time.time(), **extra}


def read_policy_state(path: pathlib.Path) -> dict[str, Any] | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def probe_hosts(policy: WebPolicy) -> tuple[str | None, str]:
    """A host the policy allows and one it denies, for the in-container check: the first allow pattern
    without wildcards (None if every pattern has one), and `example.com` unless the policy allows it."""
    allowed = next((p for p in policy.allow if not any(c in p for c in "*?[")), None)
    denied = "example.com" if policy.decide("example.com", 443)[0] != "allowed" \
        else next((p for p in policy.deny if not any(c in p for c in "*?[")), "example.org")
    return allowed, denied
