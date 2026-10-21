"""Live model assessment at an awaited OpenCode before-tool hook.

A permission request is not a universal before-tool hook: some shell inputs bypass OpenCode's
permission parser. This controller uses the documented plugin hook, not permission events or a
posthoc score. It never treats an unknown assessment as permission to act.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
from collections.abc import Awaitable
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol, TypeVar

from mesocosm.core.manifest import MonitorSpec
from mesocosm.core.receipts import EnforcedAt, Receipt, Verification
from mesocosm.core.records import RunBundle
from mesocosm.gateway.action_client import ActionAdmin, ActionGateFailure
from mesocosm.judge.monitor import RUBRIC, MonitorModel, assessment_metadata

T = TypeVar("T")


class InterventionError(RuntimeError):
    """Live assessment or enforcement failed; the session is closed and aborted."""


class InterventionUnsupported(InterventionError):
    """The requested live boundary or harness capability has not been verified."""


class InterventionTerminated(InterventionError):
    """The configured response to a flagged proposed action is session termination."""


class ActionHarness(Protocol):
    async def prepare_action_hook(self) -> dict[str, Any]: ...
    async def list_messages(self, session_id: str) -> list[dict[str, Any]]: ...
    async def abort_session(self, session_id: str) -> bool: ...


@dataclass
class InterventionState:
    """One per run, shared by all agent/task gates. Call budgets never reset between episodes."""
    calls: int = 0
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    outcomes: dict[str, int] = field(default_factory=lambda: {"allowed": 0, "blocked": 0, "unknown": 0})

    def summary(self) -> dict[str, Any]:
        return {"timing": "pre_action", "calls": self.calls, **self.outcomes}

    async def observed(self, outcome: str) -> None:
        async with self.lock:
            self.outcomes[outcome] += 1

    async def reserve(self, maximum: int | None) -> int | None:
        async with self.lock:
            if maximum is not None and self.calls >= maximum:
                return None
            self.calls += 1
            return self.calls


def proposed_tool_matches(messages: list[dict[str, Any]], action: dict[str, Any]) -> bool:
    """Cross-check the plugin claim against the actual harness tool call, including exact arguments."""
    for message in messages:
        info = message.get("info") or {}
        if info.get("sessionID") != action["session_id"] or info.get("role") != "assistant":
            continue
        for part in message.get("parts") or []:
            if part.get("type") != "tool" or part.get("callID") != action["call_id"]:
                continue
            state = part.get("state") or {}
            if part.get("tool") != action["tool"] or state.get("status") not in {"pending", "running"}:
                return False
            args = state.get("input")
            if args == action["args"]:
                return True
            if state.get("status") == "pending" and isinstance(state.get("raw"), str):
                try:
                    return json.loads(state["raw"]) == action["args"]
                except json.JSONDecodeError:
                    return False
    return False


class InterventionGate:
    def __init__(self, *, admin: ActionAdmin, monitor: MonitorModel, spec: MonitorSpec, bundle: RunBundle,
                 run_label: str, agent_id: str, session_id: str, task: str, harness: ActionHarness,
                 plugin_sha256: str, state: InterventionState):
        self.admin, self.monitor, self.spec, self.bundle = admin, monitor, spec, bundle
        self.run_label, self.agent_id, self.session_id, self.task = run_label, agent_id, session_id, task
        self.harness, self.plugin_sha256, self.state = harness, plugin_sha256, state
        self.binding: dict[str, Any] | None = None
        self.rubric = spec.rubric or RUBRIC
        self.rubric_sha256 = hashlib.sha256(self.rubric.encode()).hexdigest()
        self.assessment_contract = assessment_metadata(self.rubric, "pre_action")

    async def prepare(self) -> None:
        try:
            if self.spec.timing != "pre_action" or self.spec.scope != "per_agent" or self.spec.include_shared \
                    or self.spec.side_task_aware:
                raise InterventionUnsupported("rejected_unsupported: live gates require per-agent, non-shared evidence")
            async with asyncio.timeout(self.spec.decision_timeout_seconds):
                capabilities = await self.harness.prepare_action_hook()
                self.binding = await self.admin.bind(run=self.run_label, agent_id=self.agent_id,
                    session_id=self.session_id, task=self.task, plugin_sha256=self.plugin_sha256,
                    timeout_seconds=min(600, self.spec.decision_timeout_seconds + 5))
        except Exception as exc:
            unsupported = isinstance(exc, InterventionUnsupported) or "rejected_unsupported" in str(exc)
            self.bundle.record_receipt(Receipt(op="monitor.gate.bind", run_id=self.bundle.run_id,
                requested={"agent_id": self.agent_id, "session_id": self.session_id, "task": self.task,
                           "timing": "pre_action", "plugin_sha256": self.plugin_sha256},
                enforced_at=EnforcedAt(component="opencode.tool.execute.before"),
                result="rejected_unsupported" if unsupported else "failed",
                verification=Verification(method="harness_version_and_registered_plugin_digest", ok=False,
                                          detail={"error": repr(exc)})).model_dump(mode="json"))
            self.bundle.record("monitor.gate_failed", agent_id=self.agent_id, session_id=self.session_id,
                               task=self.task, error=repr(exc), fail_closed=True)
            error = InterventionUnsupported if unsupported else InterventionError
            raise error(f"before-tool hook readiness failed: {exc}") from exc
        receipt = Receipt(op="monitor.gate.bind", run_id=self.bundle.run_id,
            requested={"agent_id": self.agent_id, "session_id": self.session_id, "task": self.task,
                       "timing": "pre_action", "plugin_sha256": self.plugin_sha256},
            enforced_at=EnforcedAt(component="opencode.tool.execute.before"), result="applied",
            verification=Verification(method="harness_version_and_registered_plugin_digest", ok=True,
                                      detail={**capabilities, **self.binding}))
        self.bundle.record_receipt(receipt.model_dump(mode="json"))
        self.bundle.record("monitor.gate_ready", agent_id=self.agent_id, session_id=self.session_id,
                           task=self.task, binding_id=self.binding["binding_id"], plugin_sha256=self.plugin_sha256)

    async def close(self) -> None:
        binding, self.binding = self.binding, None
        if binding is not None:
            try:
                async with asyncio.timeout(10):
                    await self.admin.unbind(binding["binding_id"])
            except Exception as exc:
                self.bundle.record("monitor.gate_close_failed", agent_id=self.agent_id, task=self.task,
                                   session_id=self.session_id, error=repr(exc))
                raise InterventionError(f"failed to close action gate: {exc}") from exc

    async def _abort(self) -> None:
        acknowledged = False
        error = None
        try:
            async with asyncio.timeout(10):
                acknowledged = await self.harness.abort_session(self.session_id)
        except Exception as exc:
            error = repr(exc)
        self.bundle.record("monitor.session_aborted", agent_id=self.agent_id, session_id=self.session_id,
                           task=self.task, acknowledged=acknowledged, error=error)

    async def _assess(self, action: dict[str, Any]) -> None:
        assert self.binding is not None
        outcome, reason, result = "unknown", "assessment_failed", {}
        decision: Literal["allow", "deny"] = "deny"
        call_index = None
        evidence_chars = None
        enforce_error = None
        unsupported = False
        try:
            if any(action.get(key) != expected for key, expected in {
                "run": self.run_label, "agent": self.agent_id, "session_id": self.session_id,
                "task": self.task, "binding_id": self.binding["binding_id"],
                "instance_id": self.binding["instance_id"],
            }.items()):
                raise ValueError("action attribution does not match the bound harness session")
            if action.get("tool") == "task":
                raise InterventionUnsupported(
                    "rejected_unsupported: nested harness sessions require separate controller bindings")
            async with asyncio.timeout(self.spec.decision_timeout_seconds):
                messages = await self.harness.list_messages(self.session_id)
                if not proposed_tool_matches(messages, action):
                    raise ValueError("proposed action does not match a pending harness tool call")
                evidence: dict[str, Any] = {"status": "proposed; tool execution is held",
                    "task": self.task, "agent_id": self.agent_id, "session_id": self.session_id,
                    "call_id": action["call_id"], "tool": action["tool"], "args": action["args"]}
                if self.spec.visibility == "transcript":
                    evidence["session_messages"] = messages
                text = json.dumps(evidence, ensure_ascii=False)
                evidence_chars = len(text)
                if len(text) > self.spec.max_input_chars:
                    raise ValueError("evidence exceeds input budget; truncated evidence cannot authorize an action")
                call_index = await self.state.reserve(self.spec.max_calls)
                if call_index is None:
                    raise ValueError("monitor call budget exhausted")
                result = await self.monitor.ask(text, rubric=self.rubric, phase="pre_action",
                                                output_tokens=self.spec.output_tokens)
                score = result.get("score")
                if type(score) is not int or not 0 <= score <= 100:
                    raise ValueError("monitor returned an unknown or malformed assessment")
                outcome = "blocked" if score >= self.spec.threshold else "allowed"
                decision = "deny" if outcome == "blocked" else "allow"
                reason = "threshold_reached" if outcome == "blocked" else "below_threshold"
        except Exception as exc:
            unsupported = isinstance(exc, InterventionUnsupported)
            reason = f"{type(exc).__name__}: {exc}"
        try:
            async with asyncio.timeout(10):
                await self.admin.decide(action["action_id"], self.binding["binding_id"], decision, reason[:2000])
        except Exception as exc:
            outcome, enforce_error = "unknown", repr(exc)
        await self.state.observed(outcome)
        self.bundle.record("monitor.intervention", agent_id=self.agent_id, task=self.task,
            session_id=self.session_id, action_id=action.get("action_id"), call_id=action.get("call_id"),
            tool=action.get("tool"), args=action.get("args"), args_sha256=action.get("args_sha256"),
            outcome=outcome, decision=decision, reason=reason, assessment=result,
            rubric_version=self.spec.rubric_version, rubric_sha256=self.rubric_sha256,
            **self.assessment_contract,
            visibility=self.spec.visibility, evidence_chars=evidence_chars, call_index=call_index,
            enforce_error=enforce_error, proposed_only=True)
        self.bundle.record_receipt(Receipt(op="monitor.action.decide", run_id=self.bundle.run_id,
            requested={"action_id": action.get("action_id"), "binding_id": self.binding["binding_id"],
                       "decision": decision, "outcome": outcome},
            enforced_at=EnforcedAt(component="gateway.action_gate"),
            result="applied" if enforce_error is None else "failed",
            verification=Verification(method="single_use_decision_ack", ok=enforce_error is None,
                detail={"agent_id": self.agent_id, "session_id": self.session_id, "task": self.task,
                        "reason": reason, "error": enforce_error,
                        **self.assessment_contract})).model_dump(mode="json"))
        if outcome == "unknown":
            error = InterventionUnsupported if unsupported else InterventionError
            raise error(f"live assessment failed closed: {reason}; enforcement={enforce_error}")
        if outcome == "blocked" and self.spec.on_flag == "terminate":
            raise InterventionTerminated("monitor terminated session after a flagged proposed action")

    async def _pending(self, *, turn_completed: bool = False) -> list[dict[str, Any]]:
        assert self.binding is not None
        try:
            async with asyncio.timeout(self.spec.decision_timeout_seconds):
                actions = await self.admin.pending(self.binding["binding_id"])
            if turn_completed and actions:
                raise ActionGateFailure("harness turn completed with unresolved tool actions", actions)
            return actions
        except Exception as exc:
            failures = exc.failures if isinstance(exc, ActionGateFailure) else []
            for _ in range(max(1, len(failures))):
                await self.state.observed("unknown")
            detail = {"agent_id": self.agent_id, "session_id": self.session_id, "task": self.task,
                      "binding_id": self.binding["binding_id"], "failures": failures, "error": repr(exc),
                      "turn_completed": turn_completed}
            self.bundle.record("monitor.gate_failed", **detail, fail_closed=True)
            self.bundle.record_receipt(Receipt(op="monitor.gate.status", run_id=self.bundle.run_id,
                requested={"binding_id": self.binding["binding_id"], "turn_completed": turn_completed},
                enforced_at=EnforcedAt(component="gateway.action_gate"), result="failed",
                verification=Verification(method="binding_status_and_unresolved_actions", ok=False,
                                          detail=detail)).model_dump(mode="json"))
            raise InterventionError(f"live gate failed closed: {exc}") from exc

    async def run_turn(self, operation: Awaitable[T]) -> T:
        if self.binding is None:
            raise InterventionError("action gate has not been prepared")
        turn = asyncio.ensure_future(operation)
        try:
            while not turn.done():
                actions = await self._pending()
                for action in actions:
                    if turn.done():
                        break
                    await self._assess(action)
                if not turn.done():
                    await asyncio.wait({turn}, timeout=self.spec.poll_seconds)
            result = await turn
            # An automatic denial can finish the harness turn between polls. Reconcile its
            # retained gateway status before allowing verification or a policy update.
            await self._pending(turn_completed=True)
            return result
        except BaseException as exc:
            # Close before abort: even a failed abort cannot grant later tool actions.
            for phase, cleanup in (("gate_close", self.close), ("session_abort", self._abort)):
                try:
                    await cleanup()
                except BaseException as cleanup_error:
                    exc.add_note(f"Monitor cleanup {phase} failed: {cleanup_error!r}")
                    try:
                        self.bundle.record("monitor.cleanup_failed", agent_id=self.agent_id, task=self.task,
                                           session_id=self.session_id, phase=phase, error=repr(cleanup_error),
                                           original_error=repr(exc), fail_closed=True)
                    except BaseException as record_error:
                        exc.add_note(f"Could not record monitor cleanup failure: {record_error!r}")
            if isinstance(exc, (asyncio.CancelledError, InterventionError)):
                raise
            self.bundle.record("monitor.runtime_failed", agent_id=self.agent_id, task=self.task,
                               session_id=self.session_id, error=repr(exc), fail_closed=True)
            raise InterventionError(f"live gate failed closed: {exc}") from exc
        finally:
            if not turn.done():
                turn.cancel()
                await asyncio.gather(turn, return_exceptions=True)
