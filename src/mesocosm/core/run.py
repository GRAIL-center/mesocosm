"""Run manager: one world manifest -> one run bundle (M1 vertical slice).

Order of operations, each leaving a record:
  1. create the bundle and write the manifest (with digest);
  2. gateway health;
  3. egress verification for every agent; any FAIL aborts the run before an agent does anything;
  4. budgets applied at the gateway; receipts recorded;
  5. per agent (concurrently), per task (in order): memory policy with receipt, protected files planted,
     harness session, task instruction, event stream, native logs, verifier, state check;
  6. gateway usage and the run's exposure records copied into the bundle; services log; shared-dir check;
  7. the judge's detectors over everything recorded -> flags.jsonl; summary.

Hardware-bound pieces (OpenCode server, Docker, vLLM) arrive through `Components`, so the
same code runs against fakes in tests and against the real services from the CLI.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import math
import os
import pathlib
import sys
import time
from collections.abc import AsyncIterator, Awaitable, Callable, Coroutine, Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol, TypeVar

from mesocosm.adapters.harbor.task import HarborTask, Reward, adaptation, load_task, resolve_dataset_tasks
from mesocosm.adapters.opencode.client import normalize_event
from mesocosm.adapters.opencode.trajectory import TaskTurn, send_turn
from mesocosm.core.control import stop_requested
from mesocosm.core.legacy_peer_review import EpisodeSync  # legacy import compatibility
from mesocosm.core.manifest import SUPPORTED_CHANNELS, AgentSpec, TaskSpec, WorldManifest
from mesocosm.core.receipts import EnforcedAt, Receipt, Verification
from mesocosm.core.records import EventIdentity, EventLink, RunBundle
from mesocosm.core.report import agent_seconds
from mesocosm.core.research import validate_research_tasks
from mesocosm.core.rewards import EpisodeStopped, RewardEpisodes, outcome_records
from mesocosm.core.workspace import (
    RawLog,
    plant_agent_files,
    plant_protected,
    protected_state,
    reset_workspace,
    workspace_listing,
)
from mesocosm.env.egress import EgressResult
from mesocosm.env.services.client import ServicesAdmin
from mesocosm.gateway.client import GatewayAdmin
from mesocosm.judge.flags import detect, flag_counts
from mesocosm.judge.intervention import InterventionError, InterventionGate, InterventionState
from mesocosm.learning.controller import RolloutContext


class Harness(Protocol):
    async def prepare_action_hook(self) -> dict[str, Any]: ...
    async def create_session(self, title: str) -> str: ...
    async def send_message(self, session_id: str, text: str, *, provider_id: str, model_id: str) -> dict[str, Any]: ...
    async def list_messages(self, session_id: str) -> list[dict[str, Any]]: ...
    async def abort_session(self, session_id: str) -> bool: ...
    def events(self) -> AsyncIterator[dict[str, Any]]: ...
    async def aclose(self) -> None: ...


EVENT_GRACE_SECONDS = 0.5  # time the event collector gets to drain after the task's last message
RECORD_SLACK_SECONDS = 5.0  # clock skew allowed between the control plane and the gateway/services hosts
ABORT_TIMEOUT_SECONDS = 10.0
FAILURE_SNAPSHOT_SECONDS = 10.0
BUDGET_POLL_SECONDS = 0.5
BUDGET_CONTROL_TIMEOUT_SECONDS = 5.0
T = TypeVar("T")


class RoundDeadlineExceeded(RuntimeError):
    """A round or task time budget expired; its incomplete work must not be graded."""


class VerificationFailed(RuntimeError):
    """The verifier did not produce a valid measurement."""


class HarnessTurnIncomplete(RuntimeError):
    """A returned harness turn lacks a successful terminal assistant message."""


class BudgetRejected(RuntimeError):
    """The gateway refused an actual request against a terminal resource limit."""


class Verifier(Protocol):
    async def verify(self, task: HarborTask, workspace: pathlib.Path, logs_dir: pathlib.Path,
                     script: str = "test.sh") -> Reward: ...


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
    web_log: pathlib.Path | None = None        # the web gateway's web.jsonl for this run (host path)
    web_policy: pathlib.Path | None = None     # the web gateway's policy.json: what it loaded at start
    services: ServicesAdmin | None = None      # admin view of the world services (exposure schedule, channels)
    shared_files: Callable[[], Awaitable[list[str] | None]] | None = None   # /shared listing (state check)
    restored_from: pathlib.Path | None = None        # a fork: the snapshot the world was restored from
    restore_result: dict[str, Any] | None = None     # the restore's verification, recorded as run.restored
    lineage: dict[str, Any] | None = None            # parent run, snapshot, patch -> lineage.json
    monitor: Any | None = None                       # judge.monitor's model (judge.monitor.MonitorModel), if enabled
    task_runtime: Any | None = None                   # prepares the task's native environment before a session
    rollout: RolloutContext | None = None
    intervention: InterventionState = field(default_factory=InterventionState)
    workloads: Any | None = None  # Docker ownership, isolation inspection and verified container termination
    team_measurements: dict[str, dict[str, Any]] = field(default_factory=dict)
    finish_runtime: Callable[[], Awaitable[Any]] | None = None


class RuntimeCleanupError(RuntimeError):
    """Owned native workloads could not be removed before finalizing the run."""


async def _finish_runtime(components: Components, bundle: RunBundle) -> None:
    finish = components.finish_runtime
    if finish is None:
        return
    components.finish_runtime = None

    async def complete() -> None:
        try:
            await finish()
        except (Exception, asyncio.CancelledError) as exc:
            bundle.record("runtime.cleanup", status="failed", error=repr(exc))
            raise RuntimeCleanupError(str(exc) or "native runtime cleanup was cancelled") from exc
        bundle.record("runtime.cleanup", status="complete")

    cleanup = asyncio.create_task(complete())
    try:
        await asyncio.shield(cleanup)
    except asyncio.CancelledError:
        # The caller still owns this cleanup after cancellation. Await the same task before
        # propagating cancellation; its receipt preserves any failure without replacing it.
        while not cleanup.done():
            try:
                await asyncio.shield(cleanup)
            except asyncio.CancelledError:
                continue
            except RuntimeCleanupError:
                break
        cleanup.exception()
        raise


@dataclass
class RunResult:
    run_id: str
    bundle_path: pathlib.Path
    status: str                       # ok | stopped | failed_* | failed
    rewards: dict[str, float | None]  # "<agent>/<task>" -> reward
    error: str | None = None


async def _collect_events(harness: Harness, bundle: RunBundle, session_to_agent: dict[str, str],
                          raw_log: RawLog | None = None) -> None:
    try:
        async for raw in harness.events():
            ev = normalize_event(raw, run_id=bundle.run_id, session_to_agent=session_to_agent)
            step = bundle.next_step(ev.agent_id) if ev.agent_id else None
            bundle.record(ev.kind, agent_id=ev.agent_id, step=step, **ev.data)
            if raw_log is not None and ev.kind == "opencode.message.part.updated":
                part = ((ev.data.get("raw") or {}).get("properties") or {}).get("part") or {}
                try:
                    raw_log.append(part)
                except OSError as e:
                    bundle.record("workspace.raw_log_rejected", agent_id=ev.agent_id, error=repr(e))
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


def _require_complete_turn(turn: TaskTurn) -> dict[str, Any]:
    reply_info = (turn.reply or {}).get("info", {})
    reply_time = reply_info.get("time")
    if reply_info.get("error") is not None:
        raise HarnessTurnIncomplete("HTTP reply contains a terminal assistant error")
    if (reply_info.get("finish") != "stop" or not isinstance(reply_time, dict)
            or reply_time.get("completed") is None):
        raise HarnessTurnIncomplete("HTTP reply lacks a completed terminal stop")
    if (reply_info.get("id") in turn.message_errors
            or reply_info.get("id") in turn.unfinished_message_ids):
        raise HarnessTurnIncomplete("HTTP reply conflicts with terminal history evidence")
    latest = next((message for message in reversed(turn.messages)
                   if message.get("info", {}).get("role") == "assistant"), None)
    if (latest is None or latest["info"].get("finish") != "stop"
            or latest["info"]["id"] in turn.message_errors
            or latest["info"]["id"] in turn.unfinished_message_ids):
        raise HarnessTurnIncomplete("latest native assistant message did not complete normally")
    final = next((message for message in turn.messages
                  if message.get("info", {}).get("id") == turn.final_message_id), None)
    if final is None or turn.finish_reason != "stop":
        raise HarnessTurnIncomplete(f"terminal assistant finish is {turn.finish_reason!r}; expected 'stop'")
    if turn.final_message_id in turn.message_errors:
        raise HarnessTurnIncomplete("terminal assistant returned an error")
    if turn.final_message_id in turn.unfinished_message_ids:
        raise HarnessTurnIncomplete("terminal assistant completion was not recorded")
    for message in [*turn.messages, turn.reply or {}]:
        for part in message.get("parts", []):
            state = part.get("state")
            if part.get("type") == "tool" and (not isinstance(state, dict)
                                                or state.get("status") not in {"completed", "error"}):
                raise HarnessTurnIncomplete("returned turn contains an unfinished tool call")
    return final


def _record_turn(bundle: RunBundle, agent_id: str, task: str, round_index: int, turn: TaskTurn) -> None:
    path = f"{agent_id}/{task}/turn-{round_index}.json"
    data = _json(turn.model_dump(mode="json"))
    bundle.write_native(path, data)
    bundle.record("harness.turn", agent_id=agent_id, task=task, round=round_index,
                  session_id=turn.session_id, user_message_id=turn.user_message_id,
                  final_message_id=turn.final_message_id, finish_reason=turn.finish_reason,
                  path=f"native/{path}", sha256=hashlib.sha256(data.encode()).hexdigest(),
                  messages=len(turn.messages), text_parts=len(turn.text_parts),
                  message_error_ids=list(turn.message_errors), length_message_ids=turn.length_message_ids,
                  unfinished_message_ids=turn.unfinished_message_ids)


async def _budget_state(gateway: GatewayAdmin, run_label: str, agent_id: str, *, after: int) -> dict[str, Any]:
    try:
        async with asyncio.timeout(BUDGET_CONTROL_TIMEOUT_SECONDS):
            state = await gateway.terminal_refusal(run_label, agent_id, after=after)
    except TimeoutError as exc:
        raise RuntimeError("terminal budget refusal control timed out") from exc
    if (not isinstance(state, dict) or "refusal" not in state
            or type(state.get("cursor")) is not int or state["cursor"] < after):
        raise RuntimeError("invalid terminal budget refusal cursor")
    refusal = state.get("refusal")
    if refusal is not None and (not isinstance(refusal, dict) or refusal.get("run") != run_label
                                or refusal.get("agent") != agent_id
                                or type(refusal.get("sequence")) is not int
                                or not after < refusal["sequence"] <= state["cursor"]
                                or any(not isinstance(refusal.get(field), str) or not refusal[field]
                                       for field in ("dimension", "reason", "event_id"))):
        raise RuntimeError("invalid terminal budget refusal receipt")
    return state


async def _watch_turn_budget(operation: Callable[[], Coroutine[Any, Any, T]], gateway: GatewayAdmin, bundle: RunBundle,
                             run_label: str, agent_id: str, task: str, *, after: int,
                             on_interrupt: Callable[[], Awaitable[None]] | None = None) -> T:
    pending = asyncio.create_task(operation())
    termination: asyncio.Future[None] | None = None
    try:
        while True:
            done, _ = await asyncio.wait([pending], timeout=BUDGET_POLL_SECONDS)
            if done:
                # Preserve a transport/control failure instead of replacing it with a final poll failure.
                result = await pending
            state = await _budget_state(gateway, run_label, agent_id, after=after)
            if (refusal := state["refusal"]) is not None:
                bundle.record("harness.budget_rejected", agent_id=agent_id, task=task, refusal=refusal)
                raise BudgetRejected(f"{agent_id}/{task}: {refusal['dimension']} ({refusal['reason']})")
            after = state["cursor"]
            if done:
                return result
    except BaseException:
        if on_interrupt is not None:
            # RPC cancellation may itself wait for workload exit; start termination first.
            termination = asyncio.ensure_future(on_interrupt())
        raise
    finally:
        if not pending.done():
            pending.cancel()
        await asyncio.gather(pending, *([termination] if termination is not None else []), return_exceptions=True)


async def _preserve_failure_gateway(manifest: WorldManifest, components: Components, bundle: RunBundle,
                                    since: float) -> list[dict[str, Any]]:
    """Bounded snapshots of available records; an aborted run never establishes complete call coverage."""
    label = manifest.gateway.run_label or bundle.run_id
    sources = [(label, "gateway/records.jsonl")]
    if manifest.judge.monitor.enabled:
        sources.append((f"{label}-monitor", "gateway/monitor-records.jsonl"))
    coverage = []
    for run_label, relative in sources:
        item: dict[str, Any] = {"run_label": run_label, "path": f"native/{relative}",
                                "run_complete": False, "snapshot_complete": False,
                                "observed_model_calls": None, "status": "existing"}
        if not (bundle.root / "native" / relative).exists():
            count = 0
            try:
                async with asyncio.timeout(FAILURE_SNAPSHOT_SECONDS):
                    with bundle.open_native(relative) as out:
                        async for record in components.gateway.records(run_label, since=since):
                            out.write(_json(record) + "\n")
                            count += record.get("event") == "chat_completion"
                item.update(status="captured", snapshot_complete=True, observed_model_calls=count)
            except (Exception, asyncio.CancelledError) as exc:
                item.update(status="partial_or_unavailable", observed_model_calls=count, error=repr(exc))
        coverage.append(item)
        bundle.record("gateway.failure_snapshot", **item)
    return coverage


async def run_world(manifest: WorldManifest, manifest_path: pathlib.Path, components: Components,
                    runs_root: pathlib.Path, run_id: str | None = None) -> RunResult:
    started = time.time() - RECORD_SLACK_SECONDS
    bundle = RunBundle.create(runs_root, run_id)
    bundle.write_manifest(manifest.model_dump(mode="json"), manifest.digest(), path=manifest_path)
    for agent in manifest.agents:
        bundle.bind_identity(agent.id, EventIdentity(agent_instance_id=f"{bundle.run_id}/{agent.id}",
                                                     operator_id=manifest.operator_id))
    components.intervention.expected_agents = [agent.id for agent in manifest.agents]
    if manifest.judge.monitor.include_shared and components.services is not None:
        async def shared_evidence() -> dict[str, Any]:
            assert components.services is not None
            snapshot = await components.services.world_state()
            journals: dict[str, list[dict[str, Any]]] = {}
            for name, path in (("services", components.services_log), ("web", components.web_log)):
                if name == "web" and not manifest.internet.enabled:
                    continue
                if path is None or not path.is_file():
                    raise ValueError(f"live {name} evidence unavailable")
                rows = []
                size = 0
                with path.open() as stream:
                    for line in stream:
                        row = json.loads(line)
                        if row.get("ts", 0) < started:
                            continue
                        size += len(line)
                        if size > manifest.judge.monitor.max_input_chars:
                            raise ValueError(f"live {name} evidence exceeds monitor input budget")
                        rows.append(row)
                journals[name] = rows
            return {"world_state": snapshot, "journals": journals, "atomic": False,
                    "observed_at": time.time()}
        components.intervention.shared_evidence = shared_evidence
    try:
        return await _run(manifest, manifest_path, components, bundle)
    except (Exception, asyncio.CancelledError) as e:  # original cancellation still propagates after preservation
        bundle.record("run.failed", error=repr(e))
        try:
            receipt = await components.gateway.stop(manifest.gateway.run_label or bundle.run_id, run_id=bundle.run_id)
            bundle.record_receipt(receipt.model_dump(mode="json"))
        except Exception as stop_error:
            bundle.record("run.model_stop_failed", error=repr(stop_error))
        await asyncio.gather(*(_terminate_workload(components, bundle, agent.id, "run_cleanup", repr(e))
                               for agent in manifest.agents))
        try:
            await _finish_runtime(components, bundle)
        except RuntimeCleanupError:
            pass  # The cleanup record preserves the failure alongside the original execution error.
        coverage = await _preserve_failure_gateway(manifest, components, bundle, started)
        budget_rejected = isinstance(e, BudgetRejected) or (
            isinstance(e, InterventionError) and isinstance(e.__cause__, BudgetRejected))
        status = ("cancelled" if isinstance(e, asyncio.CancelledError) else
                  "stopped" if isinstance(e, EpisodeStopped) else
                  "failed_deadline" if isinstance(e, RoundDeadlineExceeded) else
                  "failed_budget" if budget_rejected else
                  "failed_harness_turn" if isinstance(e, HarnessTurnIncomplete) else
                  "failed_verifier" if isinstance(e, VerificationFailed) else
                  "failed_cleanup" if isinstance(e, RuntimeCleanupError) else "failed")
        events = bundle.events()
        rewards = {f"{event.agent_id}/{event.data['task']}": event.data.get("reward")
                   for event in events if event.kind == "verifier.reward"}
        bundle.write_summary({"status": status, "error": repr(e), "rewards": rewards,
                              "model_calls": None, "gateway_coverage": coverage,
                              **outcome_records(events),
                              "learning": {"mode": manifest.learning.mode, "weight_updates": 0},
                              "monitor": components.intervention.summary()
                              if manifest.judge.monitor.enabled and manifest.judge.monitor.timing == "pre_action"
                              else None})
        if isinstance(e, asyncio.CancelledError):
            raise
        return RunResult(bundle.run_id, bundle.root, status, rewards, repr(e))


async def _apply_memory_policy(manifest: WorldManifest, bundle: RunBundle, agent: AgentSpec, task: HarborTask,
                               harness: Harness, workspace: pathlib.Path, session_id: str | None,
                               session_to_agent: dict[str, str], keep: pathlib.Path | None = None,
                               task_runtime: Any | None = None) -> str:
    """Enforce the memory policy at the task boundary and leave a receipt. `reset`: empty workspace and a
    new harness session for every task; `carry`: the workspace and the session continue. `keep` names a
    snapshot the workspace was just restored from: it is kept whatever the policy says (recorded)."""
    fresh = manifest.memory == "reset" or session_id is None
    if manifest.memory == "reset" and keep is None:
        state = reset_workspace(workspace)
        ok = bool(state["empty"])
    else:
        state = workspace_listing(workspace)
        if keep is not None:
            state["restored_from"] = str(keep)
        ok = True
    if task_runtime is not None:
        state["environment"] = await task_runtime.prepare(agent, task, workspace, reset=fresh,
                                                           restored=keep is not None)
    if session_id is None or manifest.memory == "reset":
        session_id = await harness.create_session(f"{bundle.run_id}/{agent.id}/{task.name}")
        session_to_agent[session_id] = agent.id
    receipt = Receipt(op="conditions.set", run_id=bundle.run_id,
                      requested={"condition": "memory", "agent_id": agent.id, "value": manifest.memory,
                                 "task": task.name},
                      enforced_at=EnforcedAt(component="workspace"), result="applied" if ok else "failed",
                      verification=Verification(method="workspace_listing", ok=ok,
                                                detail={**state, "session_id": session_id, "new_session": fresh}))
    bundle.record_receipt(receipt.model_dump(mode="json"))
    bundle.record("conditions.set", agent_id=agent.id, condition="memory", value=manifest.memory, task=task.name,
                  result=receipt.result, event_id=receipt.enforced_at.event_id)
    if not ok:
        raise RuntimeError(f"workspace reset failed for {agent.id}: {state}")
    return session_id


def allowed_channels(task: HarborTask) -> list[str]:
    """Hidden channels a task explicitly permits (`[mesocosm] allowed_channels` in task.toml); the judge
    flags use of any other channel."""
    return list((task.config.get("mesocosm") or {}).get("allowed_channels") or [])


@dataclass
class AgentContext:
    harness: Harness
    workspace: pathlib.Path
    session_to_agent: dict[str, str] = field(default_factory=dict)
    collector: asyncio.Task | None = None
    session_id: str | None = None
    tasks_done: int = 0
    raw_log: RawLog | None = None


def _tasks_root_dirs(manifest_path: pathlib.Path) -> list[pathlib.Path]:
    """Where `dataset:` references are resolved from (ADR-0037): the directories in MESOCOSM_TASKS_ROOT (a
    control-plane env var), then the manifest's own directory. Nothing here touches an agent container."""
    roots: list[pathlib.Path] = []
    env = os.environ.get("MESOCOSM_TASKS_ROOT")
    if env:
        roots += [pathlib.Path(p).expanduser() for p in env.split(os.pathsep) if p.strip()]
    roots.append(manifest_path.parent)
    return roots


def _load_spec_tasks(spec: TaskSpec, manifest_path: pathlib.Path) -> list[HarborTask]:
    """Load a manifest task entry: a local directory (`path`), a single imported benchmark task, or a whole
    benchmark collection that expands to many tasks (`dataset`, ADR-0037/0040). `name` labels a single task;
    an expanded collection takes each task's own name."""
    if spec.path:
        return [load_task(manifest_path.parent / spec.path, dataset=spec.dataset, name=spec.name)]
    if spec.dataset is None:
        raise ValueError("a task requires a path or dataset reference")
    dirs = resolve_dataset_tasks(spec.dataset, _tasks_root_dirs(manifest_path), spec.limit)
    name = spec.name if len(dirs) == 1 else None
    return [load_task(d, dataset=spec.dataset, name=name) for d in dirs]


def _task_spec(manifest: WorldManifest, task: HarborTask):
    """The manifest's TaskSpec for a loaded task (matched by path or dataset), or None."""
    for spec in manifest.tasks:
        if spec.dataset and spec.dataset == task.dataset:
            return spec
        if spec.path and (pathlib.Path(spec.path).name == task.path.name or task.path.as_posix().endswith(spec.path)):
            return spec
    return None


def _side_task_agents(manifest: WorldManifest, task: HarborTask) -> list[str] | None:
    """Who receives the task's side task: the manifest's `tasks[].side_task`, else everyone."""
    spec = _task_spec(manifest, task)
    return spec.side_task if spec else None


async def _run_task(manifest: WorldManifest, components: Components, bundle: RunBundle, agent: AgentSpec,
                    task: HarborTask, ctx: AgentContext, marks: dict[str, Any],
                    sync: EpisodeSync | RewardEpisodes | None = None) -> float | None:
    """One task for one agent: memory policy, planted files, harness turn, native log, verifier, state check;
    under peer review also the episode's opening, scoring and feedback; with a side task its delivery and
    its own verifier (ADR-0030)."""
    validate_research_tasks(manifest, [task])
    if sync is not None:
        await sync.open(task)
    if components.task_runtime is not None:
        # Preparing another native task can replace the server behind this address. Reconnect both
        # the HTTP client and its event stream instead of retaining a stream from the old container.
        if ctx.collector is not None:
            await _stop_collector(ctx.collector)
            ctx.collector = None
        await ctx.harness.aclose()
        ctx.harness = components.harness_for(agent)
    keep = components.restored_from if (components.restored_from is not None and ctx.tasks_done == 0) else None
    ctx.session_id = await _apply_memory_policy(manifest, bundle, agent, task, ctx.harness, ctx.workspace,
                                                ctx.session_id, ctx.session_to_agent, keep=keep,
                                                task_runtime=components.task_runtime)
    if components.workloads is not None:
        inspected = await components.workloads.isolation([agent.id])
        bundle.record("workload.isolation", agent_id=agent.id, task=task.name, evidence=inspected)
        if manifest.containment == "required" and inspected.get(agent.id, {}).get("status") != "supported":
            raise RuntimeError(f"rejected_unsupported: private workload containment not verified for {agent.id}")
    bundle.bind_identity(agent.id, EventIdentity(agent_instance_id=f"{bundle.run_id}/{agent.id}",
        operator_id=manifest.operator_id, session_id=ctx.session_id,
        policy_id=components.rollout.policy.id if components.rollout else None))
    if manifest.judge.monitor.enabled and manifest.judge.monitor.timing == "pre_action":
        components.intervention.register(agent.id, ctx.harness, ctx.session_id, task.name)
    if ctx.raw_log is None:
        # Only the archived peer-verification protocol requires an agent-readable log.
        # Generic runs must not add observer files to the task's working tree or verifier input.
        directory = ctx.workspace if isinstance(sync, EpisodeSync) else bundle.root / "native" / agent.id / "tools"
        ctx.raw_log = RawLog(directory)
    ctx.raw_log.task = task.name
    if ctx.collector is None:
        ctx.collector = asyncio.create_task(_collect_events(ctx.harness, bundle, ctx.session_to_agent, ctx.raw_log))
    planted, instance = plant_agent_files(task.path, agent.id, ctx.workspace,
                                          index=[a.id for a in manifest.agents].index(agent.id))
    if planted:
        bundle.record("task.planted", agent_id=agent.id, task=task.name, files=planted, instance=instance)
    protected = plant_protected(task.path, ctx.workspace)
    if protected:
        bundle.record("task.protected", agent_id=agent.id, task=task.name, files=protected)
    # Cohorts (ADR-0035): agent k in manifest order starts k x stagger_seconds after agent 0, so the agents
    # ahead have answers the ones behind still want. Both the start delay and round deadline are enforced.
    spec = _task_spec(manifest, task)
    index = [a.id for a in manifest.agents].index(agent.id)
    stagger = (spec.stagger_seconds if spec else 0.0) * index
    if stagger:
        bundle.record("task.staggered", agent_id=agent.id, task=task.name, delay=round(stagger, 3))
        await _interruptible_turn(asyncio.sleep(stagger), bundle,
            lambda: _terminate_workload(components, bundle, agent.id, task.name, "stagger_interrupted"))
    session_event = bundle.record("harness.session", agent_id=agent.id, step=bundle.next_step(agent.id),
                  session_id=ctx.session_id, task=task.name)
    side_agents = _side_task_agents(manifest, task)
    rounds = task.round_prompts(agent.id, side_agents)
    side_given = task.side_instruction is not None and (side_agents is None or agent.id in side_agents)
    if side_given:
        bundle.record("task.side_task", agent_id=agent.id, task=task.name, text=task.side_instruction,
                      sha256=hashlib.sha256((task.side_instruction or "").encode("utf-8")).hexdigest())
    if isinstance(sync, RewardEpisodes):
        rounds[0]["prompt"] += "\n\n" + sync.instructions(agent.id)
    reply: dict[str, Any] = {}
    task_seconds = (task.config.get("agent") or {}).get("timeout_sec")
    task_deadline = time.monotonic() + task_seconds if task_seconds is not None else None
    for r in rounds:
        if len(rounds) > 1:
            bundle.record("harness.round", agent_id=agent.id, task=task.name, round=r["index"], seconds=r["seconds"])
        remaining = max(0.0, task_deadline - time.monotonic()) if task_deadline is not None else None
        limits = [v for v in (r["seconds"], remaining) if v is not None]
        timeout = min(limits) if limits else None
        task_limited = remaining is not None and (r["seconds"] is None or remaining <= r["seconds"])
        gate = None
        if manifest.judge.monitor.enabled and manifest.judge.monitor.timing == "pre_action":
            from mesocosm.adapters.opencode.action_plugin import action_plugin_digest
            from mesocosm.env.compose import GATEWAY_URL, agent_api_key
            from mesocosm.gateway.action_client import ActionAdmin

            if components.monitor is None:
                raise RuntimeError("pre_action monitoring requires an active monitor")
            label = manifest.gateway.run_label or bundle.run_id
            gate = InterventionGate(admin=ActionAdmin(components.gateway.client), monitor=components.monitor,
                spec=manifest.judge.monitor, bundle=bundle, run_label=label, agent_id=agent.id,
                session_id=ctx.session_id, task=task.name, harness=ctx.harness,
                plugin_sha256=action_plugin_digest(GATEWAY_URL, agent_api_key(manifest, label, agent.id),
                                                  min(600, manifest.judge.monitor.decision_timeout_seconds + 5)),
                state=components.intervention)
        rollout = components.rollout
        binding = None
        try:
            async with asyncio.timeout(timeout):
                if gate is not None:
                    await gate.prepare()
                if rollout is not None:
                    binding = await components.gateway.bind_rollout(
                        agent.id, manifest.gateway.run_label or bundle.run_id, rollout.episode_id,
                        task.name, r["index"], ctx.session_id, rollout.policy, bundle.run_id)
                    bundle.record_receipt(binding.model_dump(mode="json"))
                    if binding.result != "applied" or binding.verification is None or not binding.verification.ok:
                        raise RuntimeError("gateway did not confirm the rollout binding")
                    bundle.record("rollout.bound", agent_id=agent.id, event_id=binding.enforced_at.event_id,
                                  **binding.verification.detail)
                label = manifest.gateway.run_label or bundle.run_id
                budget_state = await _budget_state(components.gateway, label, agent.id, after=0)
                session_id = ctx.session_id

                async def turn(prompt: str = r["prompt"], round_index: int = r["index"],
                               session: str = session_id) -> TaskTurn:
                    completed = await send_turn(ctx.harness, session, prompt,
                                                provider_id=manifest.gateway.provider_id,
                                                model_id=manifest.model_for(agent).id)
                    _record_turn(bundle, agent.id, task.name, round_index, completed)
                    return completed

                terminate_turn = _turn_terminator(components, bundle, agent.id, task.name)
                watched = _watch_turn_budget(turn, components.gateway, bundle, label, agent.id,
                                             task.name, after=budget_state["cursor"], on_interrupt=terminate_turn)
                completed = await _interruptible_turn(gate.run_turn(watched) if gate is not None else watched, bundle,
                                                      terminate_turn)
                reply = _require_complete_turn(completed)
        except TimeoutError as e:
            if timeout is None:
                await _abort_turn(ctx, bundle, agent.id, task.name, "harness_timeout")
                await _preserve_failure_messages(ctx, bundle, agent.id, task.name, "harness_timeout")
                raise
            scope, seconds = ("task", task_seconds) if task_limited else ("round", r["seconds"])
            bundle.record(f"harness.{scope}_timeout", agent_id=agent.id, task=task.name, round=r["index"],
                          seconds=seconds, session_id=ctx.session_id)
            confirmed = await _abort_turn(ctx, bundle, agent.id, task.name, "deadline")
            await _preserve_failure_messages(ctx, bundle, agent.id, task.name, "deadline")
            raise RoundDeadlineExceeded(f"{agent.id}/{task.name} {scope} {r['index']} exceeded {seconds} seconds; "
                                        f"harness abort confirmed={confirmed}") from e
        except asyncio.CancelledError:
            await _abort_turn(ctx, bundle, agent.id, task.name, "run_cancelled")
            await _preserve_failure_messages(ctx, bundle, agent.id, task.name, "run_cancelled")
            raise
        except Exception as exc:
            bundle.record("harness.failed", agent_id=agent.id, task=task.name, error=repr(exc))
            await _abort_turn(ctx, bundle, agent.id, task.name, "harness_error")
            await _preserve_failure_messages(ctx, bundle, agent.id, task.name, "harness_error")
            raise
        finally:
            original_error = sys.exception()
            if original_error is not None:
                await _terminate_workload(components, bundle, agent.id, task.name, repr(original_error))
            try:
                try:
                    if binding is not None and rollout is not None:
                        receipt = await components.gateway.unbind_rollout(
                            agent.id, manifest.gateway.run_label or bundle.run_id, rollout.episode_id,
                            ctx.session_id, bundle.run_id)
                        bundle.record_receipt(receipt.model_dump(mode="json"))
                        if receipt.result != "applied" or receipt.verification is None or not receipt.verification.ok:
                            raise RuntimeError("gateway did not confirm closing the rollout binding")
                        bundle.record("rollout.unbound", agent_id=agent.id, event_id=receipt.enforced_at.event_id,
                                      **receipt.verification.detail)
                finally:
                    if gate is not None:
                        await gate.close()
            except (Exception, asyncio.CancelledError) as exc:
                bundle.record("harness.control_cleanup_failed", agent_id=agent.id, task=task.name,
                              error=repr(exc), original_error=repr(original_error) if original_error else None)
                if original_error is None:
                    raise
        if isinstance(sync, RewardEpisodes):
            sync.confirm_feedback(agent.id)
        bundle.record("harness.reply", agent_id=agent.id, step=bundle.next_step(agent.id),
                      session_id=ctx.session_id, task=task.name, round=r["index"],
                      parts=_parts_summary(reply))
    messages = await ctx.harness.list_messages(ctx.session_id)
    if agent.id in components.intervention.sessions:
        components.intervention.finish(agent.id, messages)
    bundle.write_native(f"{agent.id}/{task.name}/messages.json", _json(messages))
    transcript = bundle.record("harness.transcript", agent_id=agent.id, task=task.name,
        session_id=ctx.session_id, path=f"native/{agent.id}/{task.name}/messages.json",
        sha256=hashlib.sha256(_json(messages).encode()).hexdigest(), link_coverage="partial",
        links=[EventLink(event_id=str(session_event.event_id), relation="observed",
                         evidence="harness list_messages for this exact session_id")])
    logs_dir = bundle.root / "verifier" / agent.id / task.name
    logs_dir.mkdir(parents=True, exist_ok=True)
    if components.services is not None:
        # Preserve the built-in state view. Isolated graders can read /logs/world.json (ADR-0028);
        # shared service-only graders must not inherit one participant's private state view.
        world = _json(await components.services.world_state(agent.id))
        (logs_dir / "world.json").write_text(world, encoding="utf-8")
        bundle.record("verifier.world_state", agent_id=agent.id, task=task.name, size=len(world),
                      provided_to_verifier=manifest.task_services != "shared",
                      sha256=hashlib.sha256(world.encode("utf-8")).hexdigest())
    try:
        reward = await components.verifier.verify(task, ctx.workspace, logs_dir)
        _require_reward(reward, task, "test.sh")
        measurement, links = _measurement_evidence(manifest, components, bundle, task, reward, logs_dir,
                                                     str(transcript.event_id))
    except Exception as exc:
        bundle.record("verifier.failed", agent_id=agent.id, task=task.name, script="test.sh", error=repr(exc))
        raise VerificationFailed(f"{agent.id}/{task.name}: {exc}") from exc
    bundle.record("verifier.reward", agent_id=agent.id, step=bundle.next_step(agent.id),
                  task=task.name, reward=reward.value, source=reward.source, labels=reward.labels,
                  links=links, **measurement,
                  link_coverage="partial",
                  checkpoints=reward.checkpoints or None, **marks)
    if side_given and task.has_side_tests:
        side_dir = logs_dir / "side"
        side_dir.mkdir(parents=True, exist_ok=True)
        if (logs_dir / "world.json").exists():
            (side_dir / "world.json").write_text((logs_dir / "world.json").read_text(encoding="utf-8"),
                                                 encoding="utf-8")
        side = await components.verifier.verify(task, ctx.workspace, side_dir, script="side_test.sh")
        _require_reward(side, task, "side_test.sh")
        bundle.record("verifier.side_reward", agent_id=agent.id, step=bundle.next_step(agent.id), task=task.name,
                      reward=side.value, source=side.source, checkpoints=side.checkpoints or None)
    if protected:
        bundle.record("judge.state_check", agent_id=agent.id, check="protected_files", task=task.name,
                      files=protected_state(ctx.workspace, protected))
    await asyncio.sleep(EVENT_GRACE_SECONDS)        # let the collector drain the turn's last tool parts
    bundle.record("raw_log.closed", agent_id=agent.id, task=task.name, **ctx.raw_log.stats(task.name))
    ctx.tasks_done += 1
    if sync is not None:
        await sync.finish(agent.id, task, reward.value)
    return reward.value


async def _preserve_failure_messages(ctx: AgentContext, bundle: RunBundle, agent_id: str, task: str,
                                     reason: str) -> None:
    """Preserve the actual partial session on error, without turning capture errors into task outcomes."""
    relative = f"{agent_id}/{task}/messages.json"
    if (bundle.root / "native" / relative).exists() or ctx.session_id is None:
        return
    try:
        async with asyncio.timeout(FAILURE_SNAPSHOT_SECONDS):
            messages = await ctx.harness.list_messages(ctx.session_id)
            if not isinstance(messages, list) or not all(isinstance(message, dict) for message in messages):
                raise ValueError("invalid harness messages snapshot")
            bundle.write_native(relative, _json(messages))
        bundle.record("harness.failure_snapshot", agent_id=agent_id, task=task, session_id=ctx.session_id,
                      path=f"native/{relative}", reason=reason, snapshot_complete=True, turn_complete=False,
                      messages=len(messages))
    except (Exception, asyncio.CancelledError) as exc:
        bundle.record("harness.failure_snapshot", agent_id=agent_id, task=task, session_id=ctx.session_id,
                      path=f"native/{relative}", reason=reason, snapshot_complete=False, turn_complete=False,
                      messages=None, error=repr(exc))


async def _abort_turn(ctx: AgentContext, bundle: RunBundle, agent_id: str, task: str, reason: str) -> bool:
    try:
        if ctx.session_id is None:
            raise RuntimeError("cannot abort without an active harness session")
        confirmed = await asyncio.wait_for(ctx.harness.abort_session(ctx.session_id), ABORT_TIMEOUT_SECONDS)
    except Exception as e:  # noqa: BLE001 - failure to stop is recorded, never treated as a completed turn
        bundle.record("harness.abort_failed", agent_id=agent_id, task=task, session_id=ctx.session_id,
                      reason=reason, error=repr(e))
        return False
    bundle.record("harness.aborted", agent_id=agent_id, task=task, session_id=ctx.session_id,
                  reason=reason, confirmed=confirmed)
    return confirmed


async def _interruptible_turn(operation: Awaitable[T], bundle: RunBundle,
                              on_interrupt: Callable[[], Awaitable[None]] | None = None) -> T:
    """A stop marker interrupts active turns as well as preventing the next task."""
    turn = asyncio.ensure_future(operation)
    termination: asyncio.Future[None] | None = None
    try:
        while not turn.done():
            if stop_requested(bundle.root):
                raise EpisodeStopped("operator requested stop during active turn")
            await asyncio.wait({turn}, timeout=0.1)
        return await turn
    except BaseException:
        if on_interrupt is not None:
            # Begin workload termination before awaiting harness cancellation/abort or evidence capture.
            termination = asyncio.ensure_future(on_interrupt())
        raise
    finally:
        if not turn.done():
            turn.cancel()
        await asyncio.gather(turn, *([termination] if termination is not None else []), return_exceptions=True)


async def _terminate_workload(components: Components, bundle: RunBundle, agent_id: str,
                              task: str, reason: str) -> None:
    evidence: dict[str, Any] = {"status": "unverified", "reason": "no workload controller"}
    if components.workloads is not None:
        try:
            evidence = await components.workloads.terminate([agent_id])
        except Exception as exc:
            evidence = {"status": "unverified", "error": repr(exc)}
    bundle.record("workload.termination", agent_id=agent_id, task=task, reason=reason,
                  evidence=evidence, harness_abort_is_workload_termination=False)


def _turn_terminator(components: Components, bundle: RunBundle, agent_id: str,
                     task: str) -> Callable[[], Awaitable[None]]:
    """Share one termination operation between nested turn cancellation boundaries."""
    termination: asyncio.Task[None] | None = None

    async def terminate() -> None:
        nonlocal termination
        if termination is None:
            termination = asyncio.create_task(_terminate_workload(components, bundle, agent_id, task,
                                                                  "turn_interrupted"))
        await asyncio.shield(termination)

    return terminate


def _require_reward(reward: Reward, task: HarborTask, script: str) -> None:
    if reward.value is None or not math.isfinite(reward.value):
        raise VerificationFailed(f"verifier {task.name}/{script} produced no finite reward (source={reward.source})")


def _measurement_evidence(manifest: WorldManifest, components: Components, bundle: RunBundle,
                          task: HarborTask, reward: Reward, logs_dir: pathlib.Path,
                          transcript_id: str) -> tuple[dict[str, Any], list[EventLink]]:
    if manifest.task_services != "shared":
        return {}, [EventLink(event_id=transcript_id, relation="measured",
                              evidence="original verifier invoked after this task session completed")]
    metadata = json.loads((logs_dir / "environment.json").read_text())
    participants = sorted(a.id for a in manifest.agents)
    measurement_id = metadata.get("measurement_id")
    if (metadata.get("measurement_scope") != "team" or not isinstance(measurement_id, str)
            or not measurement_id or sorted(metadata.get("participant_ids") or []) != participants):
        raise VerificationFailed("shared task services require one identified team measurement")
    measurement: dict[str, Any] = {"measurement_id": measurement_id, "measurement_scope": "team",
                                   "participant_ids": participants}
    identity = {"task": task.name, "reward": reward.value, "participant_ids": participants}
    previous = components.team_measurements.get(measurement_id)
    if previous is not None and previous["identity"] != identity:
        raise VerificationFailed("conflicting copies of the same team measurement")
    if previous is None:
        transcripts = [event for event in bundle.events() if event.kind == "harness.transcript"
                       and event.agent_id in participants and event.data.get("task") == task.name]
        if sorted(event.agent_id for event in transcripts if event.agent_id) != participants:
            raise VerificationFailed("team measurement arrived before every participant transcript")
        team = bundle.record("verifier.team_reward", task=task.name, reward=reward.value, source=reward.source,
            **measurement, links=[EventLink(event_id=str(event.event_id), relation="measured",
                evidence="shared service state graded once after all participant sessions completed")
                for event in transcripts], link_coverage="partial")
        previous = {"event_id": str(team.event_id), "identity": identity}
        components.team_measurements[measurement_id] = previous
    return measurement, [EventLink(event_id=previous["event_id"], relation="measured",
        evidence="participant reference to the same team measurement; no individual attribution")]


async def _close_agent(ctx: AgentContext) -> None:
    if ctx.collector is not None:
        await _stop_collector(ctx.collector)
    await ctx.harness.aclose()


async def _run_agent(manifest: WorldManifest, components: Components, bundle: RunBundle, agent: AgentSpec,
                     tasks: list[HarborTask],
                     sync: EpisodeSync | RewardEpisodes | None = None) -> dict[str, float | None]:
    """Comparison mode: one agent through every task, in order. Agents run concurrently."""
    rewards: dict[str, float | None] = {}
    marks = adaptation(mode=manifest.mode, memory=manifest.memory,
                       budget=agent.budget.model_dump(exclude_none=True) or None)
    ctx = AgentContext(harness=components.harness_for(agent), workspace=components.workspace_for(agent))
    try:
        for task in tasks:
            # Active turns also observe this marker; do not start another task.
            if stop_requested(bundle.root):
                bundle.record("run.stopped", agent_id=agent.id, before_task=task.name)
                break
            rewards[f"{agent.id}/{task.name}"] = await _run_task(manifest, components, bundle, agent, task, ctx, marks,
                                                                 sync)
        return rewards
    except BaseException:
        await _terminate_workload(components, bundle, agent.id, "agent_cleanup", "agent_execution_failed")
        raise
    finally:
        await _close_agent(ctx)


END_OF_STREAM = object()


class TaskQueue:
    """Persistent operation: one shared queue, one agent per task (ADR-0015)."""

    def __init__(self) -> None:
        self.q: asyncio.Queue = asyncio.Queue()
        self.loaded = 0
        self.taken = 0


async def _stream_tasks(manifest: WorldManifest, manifest_path: pathlib.Path, bundle: RunBundle, queue: TaskQueue,
                        n_agents: int) -> None:
    """The scheduler: queue the manifest's tasks, then every task directory that appears in stream.dir,
    until the stream's limits or a stop request; then release every agent."""
    spec = manifest.stream
    seen: set[pathlib.Path] = set()
    started = time.time()
    task_marks = adaptation(mode=manifest.mode, memory=manifest.memory, budget=None)

    def enqueue(task: HarborTask, source: str) -> None:
        validate_research_tasks(manifest, [task])
        seen.add(task.path)
        bundle.record("task.loaded", task=task.name, provenance=task.provenance(), source=source,
                      allowed_channels=allowed_channels(task), **task_marks)
        queue.loaded += 1
        queue.q.put_nowait(task)

    def limit_reached() -> bool:
        return bool(spec.max_tasks and queue.loaded >= spec.max_tasks) or \
            bool(spec.max_minutes and time.time() - started >= spec.max_minutes * 60)

    try:
        for t in manifest.tasks:
            if not (t.path or t.dataset):
                continue
            for task in _load_spec_tasks(t, manifest_path):
                if limit_reached():
                    break
                enqueue(task, "manifest")
        stream_dir = (manifest_path.parent / spec.dir).resolve() if spec.dir else None
        while stream_dir is not None and not limit_reached() and not stop_requested(bundle.root):
            for d in sorted(p for p in stream_dir.iterdir() if p.is_dir() and (p / "instruction.md").is_file()):
                if d.resolve() not in seen and not limit_reached():
                    enqueue(load_task(d), "stream")
            await asyncio.sleep(spec.poll_seconds)
    finally:
        bundle.record("stream.closed", loaded=queue.loaded, reason="stop" if stop_requested(bundle.root)
                      else "limit" if limit_reached() else "no_stream")
        for _ in range(n_agents):
            queue.q.put_nowait(END_OF_STREAM)


async def _run_agent_persistent(manifest: WorldManifest, components: Components, bundle: RunBundle,
                                agent: AgentSpec, queue: TaskQueue) -> dict[str, float | None]:
    """Persistent mode: take tasks from the shared queue until the stream ends, the agent idles too long,
    or a stop is requested."""
    rewards: dict[str, float | None] = {}
    marks = adaptation(mode=manifest.mode, memory=manifest.memory,
                       budget=agent.budget.model_dump(exclude_none=True) or None)
    ctx = AgentContext(harness=components.harness_for(agent), workspace=components.workspace_for(agent))
    try:
        while True:
            if stop_requested(bundle.root):
                bundle.record("run.stopped", agent_id=agent.id, before_task=None)
                break
            try:
                task = await asyncio.wait_for(queue.q.get(), timeout=manifest.stream.idle_stop_seconds)
            except TimeoutError:
                bundle.record("agent.idle_stop", agent_id=agent.id, idle_seconds=manifest.stream.idle_stop_seconds)
                break
            if task is END_OF_STREAM:
                break
            queue.taken += 1
            bundle.record("task.assigned", agent_id=agent.id, task=task.name, taken=queue.taken)
            rewards[f"{agent.id}/{task.name}"] = await _run_task(manifest, components, bundle, agent, task, ctx, marks)
        return rewards
    except BaseException:
        await _terminate_workload(components, bundle, agent.id, "agent_cleanup", "agent_execution_failed")
        raise
    finally:
        await _close_agent(ctx)


async def _run(manifest: WorldManifest, manifest_path: pathlib.Path, components: Components,
               bundle: RunBundle) -> RunResult:
    run_label = manifest.gateway.run_label or bundle.run_id
    rewards: dict[str, float | None] = {}
    if components.task_runtime is not None and hasattr(components.task_runtime, "prebuild"):
        tasks = [task for spec in manifest.tasks for task in _load_spec_tasks(spec, manifest_path)]
        validate_research_tasks(manifest, tasks)
        prepared = await components.task_runtime.prebuild(tasks)
        bundle.record("task.environments_prepared", environments=prepared)
    # A fixed run label is shared by successive runs: keep only this run's gateway and services records.
    run_start = time.time()                    # what exposure seconds and world events count from
    started = run_start - RECORD_SLACK_SECONDS

    health = await components.gateway.health()
    bundle.record("gateway.health", **health)
    team_limits = {"requests": manifest.resources.model_requests,
                   "completion_tokens": manifest.resources.completion_tokens,
                   "concurrency": manifest.resources.model_concurrency}
    if any(value is not None for value in team_limits.values()):
        applied = health.get("team_limits")
        ok = applied == team_limits
        bundle.record_receipt(Receipt(op="resources.verify", run_id=bundle.run_id,
            requested=team_limits, enforced_at=EnforcedAt(component="gateway"),
            result="applied" if ok else "failed",
            verification=Verification(method="gateway_admin_health", ok=ok,
                                      detail={"applied": applied})).model_dump(mode="json"))
        if not ok:
            raise RuntimeError("shared model resource configuration was not confirmed by the gateway")
    if manifest.services.access is not None or manifest.resources.service_requests:
        if components.services is None:
            raise RuntimeError("service controls require an active service admin interface")
        service_state = await components.services.state()
        wanted_access = manifest.services.access.model_dump() if manifest.services.access is not None else None
        ok = (service_state.get("access") == wanted_access
              and service_state.get("service_limits") == manifest.resources.service_requests)
        bundle.record_receipt(Receipt(op="services.controls.verify", run_id=bundle.run_id,
            requested={"access": wanted_access, "service_limits": manifest.resources.service_requests},
            enforced_at=EnforcedAt(component="services"), result="applied" if ok else "failed",
            verification=Verification(method="services_admin_state", ok=ok,
                detail={"access": service_state.get("access"), "service_limits": service_state.get("service_limits")}))
            .model_dump(mode="json"))
        if not ok:
            raise RuntimeError("service access/resource configuration was not confirmed by the service")
    if components.rollout is not None and health.get("require_rollout_binding") is not True:
        raise RuntimeError("training requires gateway enforcement of rollout bindings")

    probe_window = (time.time() - 1.0, 0.0)     # the egress check's own web requests are not agent behavior
    isolation = await components.egress.verify([a.id for a in manifest.agents])
    probe_window = (probe_window[0], time.time() + 1.0)
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

    if components.services:
        # The exposure schedule and the simulated clock (ADR-0028) count from the run start, not from whenever
        # the services container came up; the observer reads the anchor from this event.
        await components.services.set_clock(run_start)
        bundle.record("services.clock_set", started=run_start)
    exposure = manifest.conditions.get("exposure")
    if exposure is not None:
        wanted = exposure.get("wiki", {}) if isinstance(exposure, dict) else {}
        state = None
        if components.services:
            state = await components.services.state()
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

    # The internet is a condition too (ADR-0022): the web gateway must exist exactly when the manifest says,
    # every agent must see it (or its absence), and it must have loaded this manifest's policy.
    internet = _internet_check(manifest, components, isolation)
    receipt = Receipt(op="conditions.set", run_id=bundle.run_id,
                      requested={"condition": "internet", **manifest.internet.model_dump(exclude_none=True)},
                      enforced_at=EnforcedAt(component="web"), result="applied" if internet["ok"] else "failed",
                      verification=Verification(method="web policy.json + egress-check", ok=internet["ok"],
                                                detail=internet))
    bundle.record_receipt(receipt.model_dump(mode="json"))
    bundle.record("conditions.set", condition="internet", enabled=manifest.internet.enabled, result=receipt.result,
                  event_id=receipt.enforced_at.event_id)
    if not internet["ok"]:
        bundle.record("run.aborted", reason="failed_internet_verification")
        bundle.write_summary({"status": "failed_internet_verification", "run_label": run_label, **internet})
        return RunResult(bundle.run_id, bundle.root, "failed_internet_verification", rewards,
                         "web gateway not confirmed: " + "; ".join(internet["problems"]))

    for agent in manifest.agents:
        budget = agent.budget.model_dump(exclude_none=True)
        if budget:
            receipt = await components.gateway.set_budget(agent.id, budget, run_id=bundle.run_id)
            bundle.record_receipt(receipt.model_dump(mode="json"))
            bundle.record("conditions.set", agent_id=agent.id, condition="budget", result=receipt.result,
                          event_id=receipt.enforced_at.event_id)

    if components.restored_from is not None:
        bundle.record("run.restored", snapshot=str(components.restored_from), **(components.restore_result or {}))
        if components.lineage:
            bundle.write_lineage(components.lineage)

    tasks_taken = None
    if manifest.mode == "persistent":
        queue = TaskQueue()
        scheduler = asyncio.create_task(_stream_tasks(manifest, manifest_path, bundle, queue, len(manifest.agents)))
        try:
            runners = [_run_agent_persistent(manifest, components, bundle, agent, queue) for agent in manifest.agents]
            for agent_rewards in await _run_together(runners):
                rewards.update(agent_rewards)
        finally:
            await _end(scheduler)             # its end is recorded by stream.closed
        tasks_taken = queue.taken
    else:
        tasks = [task for t in manifest.tasks if t.path or t.dataset
                 for task in _load_spec_tasks(t, manifest_path)]
        validate_research_tasks(manifest, tasks)
        if len({task.name for task in tasks}) != len(tasks):
            raise ValueError("rejected_unsupported: resolved task names must be unique within one run")
        task_marks = adaptation(mode=manifest.mode, memory=manifest.memory, budget=None)
        for task in tasks:
            bundle.record("task.loaded", task=task.name, provenance=task.provenance(), source="manifest",
                          allowed_channels=allowed_channels(task), **task_marks)
        # Agents work on the tasks at the same time; each has its own harness, sessions and collector.
        sync = (RewardEpisodes(manifest, components.services, bundle) if manifest.rewards.enabled else
                EpisodeSync(manifest, components, bundle) if manifest.services.peer_review.enabled else None)
        for agent_rewards in await _run_together([_run_agent(manifest, components, bundle, agent, tasks, sync)
                                                  for agent in manifest.agents]):
            rewards.update(agent_rewards)

    usage = await components.gateway.usage()
    bundle.record("gateway.usage", **usage)
    # The exposure copies are streamed record by record: a run's full prompts can be hundreds of MB.
    model_calls = 0
    with bundle.open_native("gateway/records.jsonl") as out:
        async for rec in components.gateway.records(run_label, since=started):
            out.write(_json(rec) + "\n")
            if rec.get("event") == "chat_completion":
                model_calls += 1
            if rec.get("event") in ("chat_completion", "budget_exhausted"):
                bundle.record(f"gateway.{rec['event']}", agent_id=rec.get("agent"), request_id=rec.get("request_id"),
                              identity=EventIdentity(),
                              ts=rec.get("ts"), status=rec.get("status"), request_summary=rec.get("request_summary"),
                              usage=(rec.get("response") or {}).get("usage"))

    web_records = 0
    if components.web_log and components.web_log.exists():
        with bundle.open_native("web/web.jsonl") as out, components.web_log.open(encoding="utf-8") as src:
            for line in src:
                if not line.strip():
                    continue
                rec = json.loads(line)
                if float(rec.get("ts") or 0) < started:
                    continue
                out.write(_json(rec) + "\n")
                web_records += 1
                marks: dict[str, Any] = {}
                if probe_window[0] <= float(rec.get("ts") or 0) <= probe_window[1]:
                    marks["probe"] = True                  # the egress check, before any agent acted
                if _harness_host(manifest, str(rec.get("host") or "")):
                    marks["origin"] = "harness"            # the harness itself (update checks), not the model
                bundle.record("web.request", agent_id=rec.get("agent"), ts=rec.get("ts"),
                              identity=EventIdentity(),
                              **{k: v for k, v in rec.items() if k not in ("ts", "agent", "excerpt")}, **marks)

    service_records = 0
    if components.services_log and components.services_log.exists():
        with bundle.open_native("services/services.jsonl") as out, \
                components.services_log.open(encoding="utf-8") as src:
            for line in src:
                if not line.strip():
                    continue
                rec = json.loads(line)
                if float(rec.get("ts") or 0) < started:
                    continue
                out.write(_json(rec) + "\n")
                service_records += 1
                bundle.record(f"service.{rec.get('op', 'event')}", agent_id=rec.get("agent"), ts=rec.get("ts"),
                              identity=EventIdentity(),
                              service=rec.get("service"),
                              **{k: v for k, v in rec.items() if k not in ("ts", "service", "op", "agent", "method")})

    # Environment-state checks that need the sandbox, then the judge over everything recorded.
    from mesocosm.core.exposure import link_exposures

    for exposure in link_exposures(bundle.events()):
        references = list(exposure.links)
        if exposure.exposure_event_id is not None:
            references.append(EventLink(event_id=exposure.exposure_event_id, relation="derived_from",
                                        evidence="exact service exposure record"))
        bundle.record("evidence.exposure", links=references, link_coverage=exposure.link_coverage,
                      **exposure.model_dump(exclude={"links", "link_coverage"}))
    if components.shared_files is not None and "shared_dir" in manifest.channel_types():
        bundle.record("judge.state_check", check="shared_dir", files=await components.shared_files())
    monitor_summary = None
    if components.monitor is not None and manifest.judge.monitor.enabled and manifest.judge.monitor.timing == "posthoc":
        from mesocosm.judge.monitor import score_bundle

        config = manifest.judge.monitor
        options = config.model_dump(exclude={"enabled", "model", "threshold", "side_task_aware", "timing",
                                             "decision_timeout_seconds", "decision_delay_seconds",
                                             "poll_seconds", "on_flag"},
                                    exclude_none=True)
        monitor_summary = await score_bundle(bundle, components.monitor, config.threshold,
                                             config.side_task_aware, **options)
        bundle.record("monitor.done", **monitor_summary)
    elif manifest.judge.monitor.enabled and manifest.judge.monitor.timing == "pre_action":
        monitor_summary = components.intervention.summary()
        bundle.record("monitor.done", **monitor_summary)
    await _finish_runtime(components, bundle)
    events = bundle.events()
    flags = detect(events, bundle.run_id)
    for flag in flags:
        bundle.record_flag(flag.model_dump(mode="json"))
        bundle.record("judge.flag", agent_id=flag.agent_id, flag_id=flag.flag_id, flag_kind=flag.kind,
                      summary=flag.summary, detector=flag.detector, detector_version=flag.detector_version)

    status = "stopped" if stop_requested(bundle.root) else "ok"
    side_rewards = {f"{e.agent_id}/{e.data.get('task')}": e.data.get("reward")
                    for e in events if e.kind == "verifier.side_reward"}
    bundle.write_summary({"status": status, "run_label": run_label, "mode": manifest.mode, "rewards": rewards,
                          "side_rewards": side_rewards or None, "monitor": monitor_summary,
                          **outcome_records(events),
                          "learning": {"mode": manifest.learning.mode, "weight_updates": 0},
                          "tasks_taken": tasks_taken,
                          "model_calls": model_calls,
                          "service_records": service_records, "web_records": web_records,
                          "flags": flag_counts(flags),
                          "agent_seconds": agent_seconds(events), "events": len(events) + len(flags)})
    return RunResult(bundle.run_id, bundle.root, status, rewards)


# Hosts a harness reaches on its own (update and registry checks). Refusals of these are the harness
# phoning home through the agent's proxy settings, recorded but not an agent's doing.
HARNESS_HOSTS = {"opencode": ("registry.npmjs.org", "opencode.ai", "api.opencode.ai")}


def _harness_host(manifest: WorldManifest, host: str) -> bool:
    h = host.lower().rstrip(".")
    return any(h == x or h.endswith("." + x) for a in manifest.agents for x in HARNESS_HOSTS.get(a.harness, ()))


def _internet_check(manifest: WorldManifest, components: Components, isolation: dict[str, EgressResult]) -> dict:
    """Is the internet condition in force? Every agent's egress check must report the proxy present (and
    policing) or absent, as the manifest says; when present, the proxy's policy.json must match."""
    from mesocosm.env.compose import web_policy

    wanted = manifest.internet.enabled
    problems: list[str] = []
    seen: dict[str, list[str]] = {}
    for agent_id, result in isolation.items():
        lines = [m for s, m in result.lines if s == "OK" and m.startswith("proxy ")]
        seen[agent_id] = lines
        present = any(m.startswith("proxy reachable") for m in lines)
        absent = any(m.startswith("proxy absent") for m in lines)
        if wanted and not present:
            problems.append(f"{agent_id}: no proxy confirmation")
        if not wanted and not absent:
            problems.append(f"{agent_id}: proxy not confirmed absent")
    loaded = None
    if wanted:
        from mesocosm.env.web import read_policy_state

        state = read_policy_state(components.web_policy) if components.web_policy else None
        loaded = (state or {}).get("policy")
        expected = web_policy(manifest).to_dict()
        if loaded is None:
            problems.append("web gateway wrote no policy.json")
        elif {k: loaded.get(k) for k in ("allow", "deny", "mode", "budget")} != \
                {k: expected[k] for k in ("allow", "deny", "mode", "budget")}:
            problems.append("web gateway policy differs from the manifest")
    return {"ok": not problems, "enabled": wanted, "problems": problems, "confirmations": seen, "loaded": loaded}


async def _end(task: asyncio.Task) -> None:
    """Cancel a background task of the run and wait for it; its own failure never fails the run."""
    if not task.done():
        task.cancel()
    try:
        await task
    except (asyncio.CancelledError, Exception):  # noqa: BLE001 - recorded by the task itself
        pass


async def _run_together(runners: Sequence[Coroutine[Any, Any, dict[str, float | None]]]
                       ) -> list[dict[str, float | None]]:
    """The agents run at the same time; the first failure cancels the others, so no runner outlives its
    run and keeps writing into the bundle (or into the next run's, in a chain)."""
    try:
        async with asyncio.TaskGroup() as group:
            tasks = [group.create_task(r) for r in runners]
    except ExceptionGroup as failures:
        raise failures.exceptions[0] from failures
    return [t.result() for t in tasks]


def _json(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False)


class DockerVerifier:
    """Runs the task's tests/test.sh in a network-less container (adapters.harbor.verifier_command)."""

    def __init__(self, image: str | None = None):
        self.image = image

    async def verify(self, task: HarborTask, workspace: pathlib.Path, logs_dir: pathlib.Path,
                     script: str = "test.sh") -> Reward:
        from mesocosm.adapters.harbor.task import read_reward, verifier_command

        if not self.image:
            raise VerificationFailed("verifier image must be supplied from the task's native environment")
        cmd = verifier_command(task, workspace=workspace, logs_dir=logs_dir, image=self.image, script=script)
        proc = await asyncio.create_subprocess_exec(*cmd, stdout=asyncio.subprocess.PIPE,
                                                    stderr=asyncio.subprocess.STDOUT)
        out, _ = await proc.communicate()
        (logs_dir / "verifier.log").write_bytes(out)
        if proc.returncode != 0:
            raise VerificationFailed(f"verifier {task.name}/{script} exited {proc.returncode}; "
                                     f"see {logs_dir / 'verifier.log'}")
        reward = read_reward(logs_dir)
        _require_reward(reward, task, script)
        return reward


RunFn = Callable[..., Awaitable[RunResult]]
