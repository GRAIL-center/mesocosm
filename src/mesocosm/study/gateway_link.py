"""Attach the model gateway to the trials of one study session, so every model call is recorded.

Each trial gets its own bearer keys. The gateway maps a key to the trial and the agent, forwards the
request to the configured model server and logs the exact request and response. After a trial, its
records come back as model calls with digests and token usage. The environment's code is not changed:
it only reads the standard ``OPENAI_BASE_URL`` (or ``OPENAI_API_BASE``) and ``OPENAI_API_KEY``.
"""

from __future__ import annotations

import hashlib
import json
import os
import pathlib
import secrets
import shutil
import socket
import tempfile
import threading
import time
from collections.abc import Mapping, Sequence
from typing import Any

import httpx

from mesocosm.study.condition import StudyError, canonical
from mesocosm.study.relay import start_relay
from mesocosm.study.substrate import ModelCall

DEFAULT_AGENT = "environment"
START_SECONDS = 10.0


def _digest(value: object) -> str:
    return hashlib.sha256(canonical(value).encode()).hexdigest()


class GatewayLink:
    """A model gateway on a local port for the listed trials. Use as a context manager."""

    def __init__(self, log_dir: pathlib.Path, trial_agents: Mapping[str, Sequence[str]], *, upstream: str,
                 upstream_key: str | None = None, upstreams: Mapping[str, Mapping[str, Any]] | None = None) -> None:
        self.log_dir, self.upstream, self.upstream_key = log_dir, upstream, upstream_key
        self.upstreams = dict(upstreams or {})
        self.control_key = secrets.token_urlsafe(24)
        self.keys: dict[str, dict[str, str]] = {
            trial: {agent: secrets.token_urlsafe(24) for agent in (DEFAULT_AGENT, *agents)}
            for trial, agents in trial_agents.items()}
        self.port = 0
        self._server: Any = None
        self._thread: threading.Thread | None = None
        self._relay: socket.socket | None = None
        self._socket_dir: str | None = None

    def __enter__(self) -> GatewayLink:
        try:
            import uvicorn

            from mesocosm.gateway.app import Settings, create_app
        except ImportError as exc:
            raise StudyError("rejected_unsupported: the model gateway needs the 'gateway' extra") from exc
        # The gateway handles one request at a time per agent identity. Naming the identity after the trial
        # keeps the trials of one command, and the agents of one trial, from waiting on each other.
        agent_keys = {key: {"agent": f"{trial}/{agent}", "run": trial}
                      for trial, keys in self.keys.items() for agent, key in keys.items()}
        settings = Settings(upstream=self.upstream, log_dir=self.log_dir, control_key=self.control_key,
                            agent_keys=agent_keys, upstream_key=self.upstream_key,
                            upstreams={model: dict(spec) for model, spec in self.upstreams.items()})
        config = uvicorn.Config(create_app(settings), host="127.0.0.1", port=0, log_level="warning", access_log=False)
        self._server = uvicorn.Server(config)
        self._thread = threading.Thread(target=self._server.run, daemon=True)
        self._thread.start()
        deadline = time.monotonic() + START_SECONDS
        while not self._server.started:
            if time.monotonic() > deadline or not self._thread.is_alive():
                raise StudyError("the model gateway did not start")
            time.sleep(0.02)
        self.port = self._server.servers[0].sockets[0].getsockname()[1]
        try:
            self._check_servers()
        except StudyError:
            self.__exit__()
            raise
        return self

    def _check_servers(self) -> None:
        """Refuse before any trial starts when a model server does not answer: a failed trial is permanent."""
        servers = {self.upstream: self.upstream_key}
        servers.update({str(spec["url"]): os.environ.get(str(spec.get("key_env") or "")) or None
                        for spec in self.upstreams.values() if spec.get("url")})
        for url, key in servers.items():
            try:
                httpx.get(url.rstrip("/") + "/v1/models", timeout=15,
                          headers={"Authorization": f"Bearer {key}"} if key else None).raise_for_status()
            except httpx.HTTPError as exc:
                raise StudyError(f"rejected_unsupported: the model server at {url} does not answer "
                                 f"({type(exc).__name__})") from exc

    def socket(self) -> str:
        """A Unix socket that reaches the gateway, to be bound into a contained trial."""
        if self._relay is None:
            self._socket_dir = tempfile.mkdtemp(prefix="gw-")
            self._relay = start_relay(os.path.join(self._socket_dir, "gateway.sock"), ("127.0.0.1", self.port))
        return os.path.join(str(self._socket_dir), "gateway.sock")

    def __exit__(self, *_: object) -> None:
        if self._relay is not None:
            self._relay.close()
        if self._socket_dir is not None:
            shutil.rmtree(self._socket_dir, ignore_errors=True)
        if self._server is not None:
            self._server.should_exit = True
        if self._thread is not None:
            self._thread.join(timeout=START_SECONDS)

    def environment(self, trial_id: str) -> dict[str, str]:
        keys = self.keys.get(trial_id)
        if keys is None:
            raise StudyError(f"trial {trial_id} has no model keys in this session")
        url = f"http://127.0.0.1:{self.port}/v1"
        local = ",".join(filter(None, [os.environ.get("NO_PROXY") or os.environ.get("no_proxy"), "127.0.0.1"]))
        return {"OPENAI_BASE_URL": url, "OPENAI_API_BASE": url, "OPENAI_API_KEY": keys[DEFAULT_AGENT],
                "MODEL_KEYS": json.dumps({agent: key for agent, key in keys.items() if agent != DEFAULT_AGENT}),
                "NO_PROXY": local, "no_proxy": local}

    def calls(self, trial_id: str) -> tuple[ModelCall, ...]:
        """The trial's completed model calls, in the order the gateway logged them."""
        response = httpx.get(f"http://127.0.0.1:{self.port}/admin/records", params={"run": trial_id},
                             headers={"Authorization": f"Bearer {self.control_key}"}, timeout=60, trust_env=False)
        response.raise_for_status()
        calls = []
        for line in response.text.splitlines():
            record = json.loads(line)
            if record.get("event") != "chat_completion":
                continue
            request, reply = record.get("request") or {}, record.get("response") or {}
            usage = reply.get("usage") or {}
            calls.append(ModelCall(
                agent_id=str(record.get("agent") or DEFAULT_AGENT).rpartition("/")[2], input_sha256=_digest(request),
                output_sha256=_digest({key: reply.get(key) for key in ("content", "reasoning", "tool_calls")}),
                model=request.get("model") if isinstance(request.get("model"), str) else None,
                prompt_tokens=usage.get("prompt_tokens") if isinstance(usage.get("prompt_tokens"), int) else None,
                completion_tokens=(usage.get("completion_tokens")
                                   if isinstance(usage.get("completion_tokens"), int) else None)))
        return tuple(calls)
