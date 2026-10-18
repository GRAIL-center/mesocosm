"""Run manager: one world manifest -> one run bundle (M1 vertical slice).

Order of operations, each leaving a record:
  1. create the bundle and write the manifest (with digest);
  2. gateway health;
  3. egress verification for every agent; any FAIL aborts the run before an agent does anything;
  4. budgets applied at the gateway; receipts recorded;
  5. per task, per agent: harness session, task instruction, event stream, native logs, verifier;
  6. gateway usage and the run's exposure records copied into the bundle; summary.

Hardware-bound pieces (OpenCode server, Docker, vLLM) arrive through `Components`, so the
same code runs against fakes in tests and against the real services from the CLI.
"""

from __future__ import annotations

import asyncio
import json
import pathlib
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass
from typing import Any, Protocol

from mesocosm.adapters.harbor.task import HarborTask, Reward, adaptation, load_task
from mesocosm.adapters.opencode.client import normalize_event
from mesocosm.core.manifest import SUPPORTED_CHANNELS, AgentSpec, WorldManifest
from mesocosm.core.receipts import EnforcedAt, Receipt, Verification
from mesocosm.core.records import RunBundle
from mesocosm.env.egress import EgressResult
from mesocosm.env.services.client import ServicesAdmin
from mesocosm.gateway.client import GatewayAdmin


class Harness(Protocol):
    async def create_session(self, title: str) -> str: ...
    async def send_message(self, session_id: str, text: str, *, provider_id: str, model_id: str) -> dict[str, Any]: ...
    async def list_messages(self, session_id: str) -> list[dict[str, Any]]: ...
    def events(self) -> AsyncIterator[dict[str, Any]]: ...
    async def aclose(self) -> None: ...


EVENT_GRACE_SECONDS = 0.5  # time the event collector gets to drain after the task's last message
RECORD_SLACK_SECONDS = 5.0  # clock skew allowed between the control plane and the gateway/services hosts


class Verifier(Protocol):
    async def verify(self, task: HarborTask, workspace: pathlib.Path, logs_dir: pathlib.Path) -> Reward: ...


class EgressVerifier(Protocol):
    async def verify(self, agent_ids: list[str]) -> dict[str, EgressResult]: ...


@dataclass
class Components:
    harness_for: Callable[[AgentSpec], Harness]
    gateway: GatewayAdmin
    egress: EgressVerifier
    verifier: Verifier
    workspace_for: Callable[[AgentSpec], pathlib.Path]
    services_log: pathlib.Path | None = None   # the world services' services.jsonl for this run (host path)
    services: ServicesAdmin | None = None      # admin view of the world services (exposure schedule, channels)


@dataclass
class RunResult:
    run_id: str
    bundle_path: pathlib.Path
    status: str                       # ok | failed_isolation | failed
    rewards: dict[str, float | None]  # "<agent>/<task>" -> reward
    error: str | None = None


async def _collect_events(harness: Harness, bundle: RunBundle, session_to_agent: dict[str, str]) -> None:
    try:
        async for raw in harness.events():
            ev = normalize_event(raw, run_id=bundle.run_id, session_to_agent=session_to_agent)
            step = bundle.next_step(ev.agent_id) if ev.agent_id else None
            bundle.record(ev.kind, agent_id=ev.agent_id, step=step, **ev.data)
    except Exception as e:  # noqa: BLE001 - the stream ending is not a run failure; record and move on
        bundle.record("harness.events_ended", error=repr(e))


async def _stop_collector(collector: asyncio.Task) -> None:
    await asyncio.sleep(EVENT_GRACE_SECONDS)
    collector.cancel()
    try:
        await collector
    except asyncio.CancelledError:
        pass


def _parts_summary(reply: dict[str, Any]) -> list[dict[str, Any]]:
    out = []
    for part in reply.get("parts") or []:
        item = {"type": part.get("type")}
        if part.get("type") == "tool":
            item["tool"] = part.get("tool")
            item["status"] = (part.get("state") or {}).get("status")
        elif part.get("type") == "text":
            item["text_len"] = len(part.get("text") or "")
        out.append(item)
    return out


async def run_world(manifest: WorldManifest, manifest_path: pathlib.Path, components: Components,
                    runs_root: pathlib.Path, run_id: str | None = None) -> RunResult:
    bundle = RunBundle.create(runs_root, run_id)
    bundle.write_manifest(manifest.model_dump(mode="json"), manifest.digest())
    try:
        return await _run(manifest, manifest_path, components, bundle)
    except Exception as e:  # noqa: BLE001 - any failure is recorded in the bundle, then reported
        bundle.record("run.failed", error=repr(e))
        bundle.write_summary({"status": "failed", "error": repr(e)})
        return RunResult(bundle.run_id, bundle.root, "failed", {}, repr(e))


async def _run_agent_task(manifest: WorldManifest, components: Components, bundle: RunBundle,
                          agent: AgentSpec, task: HarborTask) -> float | None:
    marks = adaptation(mode=manifest.mode, memory=manifest.memory,
                       budget=agent.budget.model_dump(exclude_none=True) or None)
    harness = components.harness_for(agent)
    session_to_agent: dict[str, str] = {}
    collector: asyncio.Task | None = None
    try:
        session_id = await harness.create_session(f"{bundle.run_id}/{agent.id}/{task.name}")
        session_to_agent[session_id] = agent.id
        collector = asyncio.create_task(_collect_events(harness, bundle, session_to_agent))
        bundle.record("harness.session", agent_id=agent.id, step=bundle.next_step(agent.id),
                      session_id=session_id, task=task.name)
        reply = await harness.send_message(session_id, task.instruction, provider_id="mesocosm",
                                           model_id=manifest.model.id)
        bundle.record("harness.reply", agent_id=agent.id, step=bundle.next_step(agent.id),
                      session_id=session_id, parts=_parts_summary(reply))
        messages = await harness.list_messages(session_id)
        bundle.write_native(f"{agent.id}/{task.name}/messages.json", _json(messages))
        await _stop_collector(collector)
        collector = None
        logs_dir = bundle.root / "verifier" / agent.id / task.name
        logs_dir.mkdir(parents=True, exist_ok=True)
        reward = await components.verifier.verify(task, components.workspace_for(agent), logs_dir)
        bundle.record("verifier.reward", agent_id=agent.id, step=bundle.next_step(agent.id),
                      task=task.name, reward=reward.value, source=reward.source, labels=reward.labels, **marks)
        return reward.value
    finally:
        if collector is not None:
            collector.cancel()
        await harness.aclose()


async def _run(manifest: WorldManifest, manifest_path: pathlib.Path, components: Components,
               bundle: RunBundle) -> RunResult:
    run_label = manifest.gateway.run_label or bundle.run_id
    rewards: dict[str, float | None] = {}
    # A fixed run label is shared by successive runs: keep only this run's gateway and services records.
    started = time.time() - RECORD_SLACK_SECONDS

    health = await components.gateway.health()
    bundle.record("gateway.health", **health)

    isolation = await components.egress.verify([a.id for a in manifest.agents])
    for agent_id, result in isolation.items():
        bundle.record("isolation.check", agent_id=agent_id, **result.to_dict())
    if not all(r.ok for r in isolation.values()):
        bundle.record("run.aborted", reason="failed_isolation")
        bundle.write_summary({"status": "failed_isolation", "run_label": run_label,
                              "isolation": {k: v.to_dict() for k, v in isolation.items()}})
        return RunResult(bundle.run_id, bundle.root, "failed_isolation", rewards, "egress check failed")

    # Hidden channels are a condition enforced by the environment; the in-container check is the
    # evidence. Every channel the manifest names must be confirmed present by every agent, and every
    # supported channel it does not name must be confirmed absent.
    for channel in sorted(SUPPORTED_CHANNELS):
        wanted = channel in manifest.channel_types()
        expect = "(enabled)" if wanted else "(disabled)"
        confirmations = {agent_id: [m for s, m in r.lines
                                    if s == "OK" and m.startswith(f"channel {channel} ") and expect in m]
                         for agent_id, r in isolation.items()}
        ok = all(len(v) == 1 for v in confirmations.values())
        receipt = Receipt(op="channels.set", run_id=bundle.run_id, requested={"channel": channel, "enabled": wanted},
                          enforced_at=EnforcedAt(component="env"), result="applied" if ok else "failed",
                          verification=Verification(method="egress-check", ok=ok, detail=confirmations))
        bundle.record_receipt(receipt.model_dump(mode="json"))
        bundle.record("conditions.set", condition=f"channel:{channel}", enabled=wanted, result=receipt.result,
                      event_id=receipt.enforced_at.event_id)
        if not ok:
            bundle.record("run.aborted", reason="failed_channel_verification", channel=channel)
            bundle.write_summary({"status": "failed_channel_verification", "run_label": run_label, "channel": channel,
                                  "confirmations": confirmations})
            return RunResult(bundle.run_id, bundle.root, "failed_channel_verification", rewards,
                             f"channel {channel}: enabled={wanted} not confirmed by every agent")

    exposure = manifest.conditions.get("exposure")
    if exposure is not None:
        wanted = exposure.get("wiki", {}) if isinstance(exposure, dict) else {}
        state = await components.services.state() if components.services else None
        applied = state.get("wiki_visibility") if state else None
        ok = applied == wanted
        detail = {"applied": applied, "channels": (state or {}).get("channels"),
                  "services_started": (state or {}).get("started")}   # the schedule counts from here
        receipt = Receipt(op="conditions.set", run_id=bundle.run_id,
                          requested={"condition": "exposure", "wiki": wanted},
                          enforced_at=EnforcedAt(component="services"), result="applied" if ok else "failed",
                          verification=Verification(method="services /admin/state", ok=ok, detail=detail))
        bundle.record_receipt(receipt.model_dump(mode="json"))
        bundle.record("conditions.set", condition="exposure", wiki=wanted, result=receipt.result,
                      event_id=receipt.enforced_at.event_id)
        if not ok:
            bundle.record("run.aborted", reason="failed_exposure_verification")
            bundle.write_summary({"status": "failed_exposure_verification", "run_label": run_label,
                                  "wanted": wanted, "applied": applied})
            return RunResult(bundle.run_id, bundle.root, "failed_exposure_verification", rewards,
                             "exposure schedule not confirmed by the services")

    for agent in manifest.agents:
        budget = agent.budget.model_dump(exclude_none=True)
        if budget:
            receipt = await components.gateway.set_budget(agent.id, budget, run_id=bundle.run_id)
            bundle.record_receipt(receipt.model_dump(mode="json"))
            bundle.record("conditions.set", agent_id=agent.id, condition="budget", result=receipt.result,
                          event_id=receipt.enforced_at.event_id)

    tasks = [load_task(manifest_path.parent / t.path) for t in manifest.tasks if t.path]
    for task in tasks:
        marks = adaptation(mode=manifest.mode, memory=manifest.memory, budget=None)
        bundle.record("task.loaded", task=task.name, provenance=task.provenance(), **marks)
        # Agents work on the task at the same time; each has its own harness, session and collector.
        results = await asyncio.gather(*(_run_agent_task(manifest, components, bundle, agent, task)
                                         for agent in manifest.agents))
        for agent, value in zip(manifest.agents, results, strict=True):
            rewards[f"{agent.id}/{task.name}"] = value

    usage = await components.gateway.usage()
    bundle.record("gateway.usage", **usage)
    exposure = [r for r in await components.gateway.records(run_label) if float(r.get("ts") or 0) >= started]
    bundle.write_native("gateway/records.jsonl", "".join(_json(r) + "\n" for r in exposure))
    for rec in exposure:
        if rec.get("event") in ("chat_completion", "budget_exhausted"):
            bundle.record(f"gateway.{rec['event']}", agent_id=rec.get("agent"), request_id=rec.get("request_id"),
                          status=rec.get("status"), request_summary=rec.get("request_summary"),
                          usage=(rec.get("response") or {}).get("usage"))

    service_records = 0
    if components.services_log and components.services_log.exists():
        lines = [ln for ln in components.services_log.read_text(encoding="utf-8").splitlines() if ln.strip()]
        records = [json.loads(ln) for ln in lines]
        records = [r for r in records if float(r.get("ts") or 0) >= started]
        bundle.write_native("services/services.jsonl", "".join(_json(r) + "\n" for r in records))
        for rec in records:
            service_records += 1
            bundle.record(f"service.{rec.get('op', 'event')}", agent_id=rec.get("agent"), service=rec.get("service"),
                          **{k: v for k, v in rec.items() if k not in ("ts", "service", "op", "agent", "method")})

    bundle.write_summary({"status": "ok", "run_label": run_label, "rewards": rewards,
                          "model_calls": sum(1 for r in exposure if r.get("event") == "chat_completion"),
                          "service_records": service_records, "events": len(bundle.events())})
    return RunResult(bundle.run_id, bundle.root, "ok", rewards)


def _json(obj: Any) -> str:
    import json

    return json.dumps(obj, ensure_ascii=False)


class DockerVerifier:
    """Runs the task's tests/test.sh in a network-less container (adapters.harbor.verifier_command)."""

    def __init__(self, image: str = "debian:bookworm-slim"):
        self.image = image

    async def verify(self, task: HarborTask, workspace: pathlib.Path, logs_dir: pathlib.Path) -> Reward:
        from mesocosm.adapters.harbor.task import read_reward, verifier_command

        cmd = verifier_command(task, workspace=workspace, logs_dir=logs_dir, image=self.image)
        proc = await asyncio.create_subprocess_exec(*cmd, stdout=asyncio.subprocess.PIPE,
                                                    stderr=asyncio.subprocess.STDOUT)
        out, _ = await proc.communicate()
        (logs_dir / "verifier.log").write_bytes(out)
        return read_reward(logs_dir)


RunFn = Callable[..., Awaitable[RunResult]]
