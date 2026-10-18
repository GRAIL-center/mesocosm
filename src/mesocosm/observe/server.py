"""Live observation server: tails a run bundle (and the gateway log) and serves one page.

    mesocosm observe serve runs/<run_id> --gateway-log runs/spike/gateway.jsonl

Endpoints: `/` (the page), `/state` (snapshot; `?at=<unix ts>` rebuilds the state up to that time
from the files, for scrubbing), `/events` (SSE: a snapshot whenever something changed, coalesced),
`/session/{id}` (timeline and tool calls), `/agent/{id}/requests`, `/request/{agent}/{id}` (the full
gateway record: what the model saw, what it answered). Nothing is kept per client: an SSE
subscriber waits on a shared condition and reads the latest snapshot, so a slow browser cannot
make the server grow.
"""

from __future__ import annotations

import asyncio
import json
import pathlib
import sys
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from mesocosm.observe.state import REQUEST_BODIES_MAX, RUN_START_SLACK, Observer
from mesocosm.observe.summarize import Summarizer

STATIC = pathlib.Path(__file__).parent / "static"
POLL_SECONDS = 0.25
SSE_MIN_INTERVAL = 0.2
SSE_KEEPALIVE = 15.0


@dataclass
class Source:
    path: pathlib.Path
    apply: str                    # Observer method name
    offset: int = 0
    partial: bytes = b""
    missing_logged: bool = False

    def read_new_lines(self) -> list[bytes]:
        if not self.path.exists():
            return []
        size = self.path.stat().st_size
        if size < self.offset:            # truncated or rotated: start over
            self.offset, self.partial = 0, b""
        if size == self.offset:
            return []
        with self.path.open("rb") as f:
            f.seek(self.offset)
            chunk = f.read(size - self.offset)
        self.offset = size
        data = self.partial + chunk
        lines = data.split(b"\n")
        self.partial = lines.pop()        # incomplete tail, if any
        return [ln for ln in lines if ln.strip()]


def _ts_of(apply: str, rec: dict[str, Any]) -> float:
    if apply == "apply_receipt":
        return float((rec.get("enforced_at") or {}).get("wallclock") or 0)
    return float(rec.get("ts") or 0)


@dataclass
class Follower:
    """Feeds an Observer from the files, either live (tail) or as a timed replay."""
    sources: list[Source]
    observer: Observer = field(default_factory=Observer)
    replay_speed: float | None = None      # None: live tail; else replay at this speed
    summarizer: Summarizer | None = None
    version: int = 0
    changed: asyncio.Condition = field(default_factory=asyncio.Condition)
    task: asyncio.Task | None = None
    replay_position: float | None = None
    replay_done: bool = False

    bundle: pathlib.Path | None = None
    summary_seen: bool = False

    def ensure_started(self) -> None:
        if self.task is None or self.task.done():
            self._load_manifest()
            self.task = asyncio.create_task(self._replay() if self.replay_speed else self._tail())

    def _load_manifest(self) -> None:
        if self.bundle:
            prime_observer(self.observer, self.bundle)

    def _poll_summary(self) -> bool:
        path = self.bundle / "summary.json" if self.bundle else None
        if self.summary_seen or not path or not path.exists():
            return False
        try:
            self.observer.apply_summary(json.loads(path.read_text(encoding="utf-8")))
        except (json.JSONDecodeError, OSError):
            return False
        self.summary_seen = True
        return True

    async def stop(self) -> None:
        if self.task and not self.task.done():
            self.task.cancel()
            try:
                await self.task
            except asyncio.CancelledError:
                pass
        if self.summarizer:
            await self.summarizer.aclose()

    def _feed(self, src: Source, line: bytes) -> bool:
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            return False
        getattr(self.observer, src.apply)(rec)
        return True

    async def _bump(self) -> None:
        async with self.changed:
            self.version += 1
            self.changed.notify_all()
        if self.summarizer:
            self._schedule_summaries()

    def _schedule_summaries(self) -> None:
        """Ask the model for a new line for every session whose activity changed; one task per node,
        bounded by the summarizer's semaphore and its per-node interval."""
        self.summarizer.apply_cached(self.observer)
        for node in self.summarizer.due(self.observer):
            agent = self.observer.agents.get(node.agent_id or "")
            asyncio.create_task(self._summarize(node, agent.task if agent else None))

    async def _summarize(self, node, task) -> None:
        if await self.summarizer.summarize(node, task):
            async with self.changed:
                self.version += 1
                self.changed.notify_all()

    async def _tail(self) -> None:
        while True:
            fed = False
            for src in self.sources:
                for line in src.read_new_lines():
                    fed |= self._feed(src, line)
            fed |= self._poll_summary()
            if fed:
                await self._bump()
            await asyncio.sleep(POLL_SECONDS)

    async def _replay(self) -> None:
        records: list[tuple[float, Source, bytes]] = []
        for src in self.sources:
            for line in src.read_new_lines():
                try:
                    records.append((_ts_of(src.apply, json.loads(line)), src, line))
                except json.JSONDecodeError:
                    continue
        records.sort(key=lambda r: r[0])
        prev_ts = records[0][0] if records else None
        for ts, src, line in records:
            if prev_ts is not None and ts > prev_ts:
                await asyncio.sleep(min((ts - prev_ts) / (self.replay_speed or 1.0), 5.0))
            prev_ts = ts
            self._feed(src, line)
            self.replay_position = ts
            await self._bump()
        self._poll_summary()
        self.replay_done = True
        await self._bump()

    async def wait_for_change(self, seen: int, timeout: float) -> int:
        async with self.changed:
            if self.version == seen:
                try:
                    await asyncio.wait_for(self.changed.wait(), timeout)
                except TimeoutError:
                    pass
            return self.version


async def sse_events(follower: Follower, meta: Callable[[], dict[str, Any]],
                     is_disconnected: Callable[[], Awaitable[bool]]) -> AsyncIterator[bytes]:
    """One snapshot per change, coalesced to SSE_MIN_INTERVAL; a keepalive comment when quiet.
    Nothing is queued per subscriber: a slow client just sees fewer, newer snapshots."""
    seen = -1
    while not await is_disconnected():
        version = await follower.wait_for_change(seen, SSE_KEEPALIVE)
        if version == seen:
            yield b": keepalive\n\n"
            continue
        seen = version
        payload = json.dumps({"meta": meta(), "state": follower.observer.snapshot()}, ensure_ascii=False)
        yield f"event: state\ndata: {payload}\n\n".encode()
        await asyncio.sleep(SSE_MIN_INTERVAL)


def make_sources(bundle: pathlib.Path, gateway_log: pathlib.Path | None,
                 services_log: pathlib.Path | None = None) -> list[Source]:
    if gateway_log is None:
        candidate = bundle / "native" / "gateway" / "records.jsonl"
        gateway_log = candidate if candidate.exists() else None
    if services_log is None:
        candidate = bundle / "native" / "services" / "services.jsonl"
        services_log = candidate if candidate.exists() else None
    sources = [Source(bundle / "events.jsonl", "apply_bundle_event"),
               Source(bundle / "receipts.jsonl", "apply_receipt")]
    if gateway_log is not None:
        sources.append(Source(gateway_log, "apply_gateway_record"))
    if services_log is not None:
        sources.append(Source(services_log, "apply_service_record"))
    return sources


def prime_observer(obs: Observer, bundle: pathlib.Path) -> None:
    """Before any record: the run's agents (manifest.json) and its start (first bundle event), so that
    shared gateway/services logs replayed in time order drop what belongs to earlier runs."""
    manifest = bundle / "manifest.json"
    if manifest.exists():
        try:
            obs.apply_manifest(json.loads(manifest.read_text(encoding="utf-8")))
        except (json.JSONDecodeError, OSError):
            pass
    events = bundle / "events.jsonl"
    if events.exists():
        with events.open("rb") as f:
            first = f.readline()
        try:
            ts = json.loads(first).get("ts")
            if ts is not None:
                obs.run_started = float(ts) - RUN_START_SLACK
        except (json.JSONDecodeError, ValueError):
            pass


def state_at(bundle: pathlib.Path, gateway_log: pathlib.Path | None, at: float,
             gpu_hour_cost: float | None = None) -> dict[str, Any]:
    """Rebuild the state from the files up to `at`; nothing is retained afterwards."""
    obs = Observer()
    obs.gpu_hour_cost = gpu_hour_cost
    prime_observer(obs, bundle)
    records: list[tuple[float, str, dict[str, Any]]] = []
    sources = make_sources(bundle, gateway_log)
    obs._live_services = any(src.apply == "apply_service_record" for src in sources)
    for src in sources:
        for line in src.read_new_lines():
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            records.append((_ts_of(src.apply, rec), src.apply, rec))
    for ts, apply, rec in sorted(records, key=lambda r: r[0]):
        if ts > at:
            break
        getattr(obs, apply)(rec)
    return obs.snapshot()


def create_app(bundle: pathlib.Path, gateway_log: pathlib.Path | None = None,
               replay_speed: float | None = None, summarizer: Summarizer | None = None,
               services_log: pathlib.Path | None = None, gpu_hour_cost: float | None = None) -> FastAPI:
    bundle = pathlib.Path(bundle)
    if not bundle.is_dir():
        raise FileNotFoundError(f"not a run bundle directory: {bundle}")
    sources = make_sources(bundle, gateway_log, services_log)
    follower = Follower(sources=sources, replay_speed=replay_speed, summarizer=summarizer, bundle=bundle)
    follower.observer._live_services = any(src.apply == "apply_service_record" for src in sources)
    follower.observer.gpu_hour_cost = gpu_hour_cost

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        yield
        await follower.stop()

    app = FastAPI(title="mesocosm observe", lifespan=lifespan)
    app.state.follower = follower
    app.state.bundle = bundle
    app.state.gateway_log = gateway_log

    def meta() -> dict[str, Any]:
        return {"mode": "replay" if replay_speed else "live", "replay_speed": replay_speed,
                "replay_position": follower.replay_position, "replay_done": follower.replay_done,
                "version": follower.version, "server_ts": time.time(), "bundle": str(bundle),
                "summarizer": summarizer.stats() if summarizer else None}

    @app.get("/")
    async def index():
        return FileResponse(STATIC / "index.html")

    # the page's sprites and its pixel font (ADR-0032): served from the package, nothing from the network
    app.mount("/static", StaticFiles(directory=STATIC), name="static")

    @app.get("/health")
    async def health():
        return {"ok": True, **meta()}

    @app.get("/state")
    async def state(at: float | None = None):
        follower.ensure_started()
        if at is not None:
            return {"meta": {**meta(), "at": at}, "state": state_at(bundle, gateway_log, at, gpu_hour_cost)}
        return {"meta": meta(), "state": follower.observer.snapshot()}

    @app.get("/events")
    async def events(request: Request):
        follower.ensure_started()
        return StreamingResponse(sse_events(follower, meta, request.is_disconnected), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    @app.get("/session/{session_id}")
    async def session(session_id: str):
        details = follower.observer.session_details(session_id)
        if details is None:
            raise HTTPException(404, "unknown session")
        return details

    @app.get("/agent/{agent_id}/requests")
    async def agent_requests(agent_id: str):
        return {"agent_id": agent_id, "requests": follower.observer.agent_requests(agent_id)}

    @app.get("/request/{agent_id}/{request_id}")
    async def request_record(agent_id: str, request_id: str):
        rec = follower.observer.request_record(agent_id, request_id)
        if rec is None:
            raise HTTPException(404, f"request body not retained (older than the last {REQUEST_BODIES_MAX}) "
                                     "or unknown")
        return rec

    return app


def derived_path(bundle: pathlib.Path) -> pathlib.Path:
    """Model-written lines live next to the bundle, never inside it: <runs>/<run_id>.observe/summaries.jsonl."""
    return bundle.parent / f"{bundle.name}.observe" / "summaries.jsonl"


def serve(bundle: pathlib.Path, gateway_log: pathlib.Path | None, host: str, port: int,
          replay_speed: float | None, summarizer_url: str | None = None, summarizer_model: str | None = None,
          summarizer_key: str = "", summary_lang: str = "en", services_log: pathlib.Path | None = None,
          gpu_hour_cost: float | None = None) -> None:
    import uvicorn

    summarizer = None
    derived = derived_path(pathlib.Path(bundle))
    if summarizer_url and summarizer_model:
        summarizer = Summarizer(summarizer_url, summarizer_model, summarizer_key, summary_lang, derived_path=derived)
    elif derived.exists():          # replay: reuse the lines written during the live run, call no model
        summarizer = Summarizer(None, None, derived_path=derived)
    app = create_app(bundle, gateway_log, replay_speed, summarizer, services_log, gpu_hour_cost)
    print(f"[observe] http://{host}:{port}  bundle={bundle} gateway_log={gateway_log} "
          f"mode={'replay x' + str(replay_speed) if replay_speed else 'live'} "
          f"summaries={'model ' + summarizer_model if summarizer_model else 'derived file' if summarizer else 'rules'}",
          file=sys.stderr)
    uvicorn.run(app, host=host, port=port, log_level="warning")
