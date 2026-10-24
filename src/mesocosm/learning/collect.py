"""Join complete native rollouts to measured incentives by explicit identities, never timestamps."""

from __future__ import annotations

import hashlib
import json
import math
import pathlib
from collections import defaultdict
from typing import Any

from mesocosm.adapters.harbor.task import read_reward
from mesocosm.core.manifest import WorldManifest, reviewers
from mesocosm.core.records import Event, RunBundle
from mesocosm.core.rewards import allocate, outcome_records
from mesocosm.gateway.rollout import SAMPLING, BindRollout, UnbindRollout, sampling_request
from mesocosm.learning.config import PolicyIdentity
from mesocosm.learning.rollout import ActionRequest, AgentTrajectory, RolloutBatch

REWARD_RULE_VERSION = "mesocosm.reward-allocation.v1"


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(f"invalid rollout: {message}")


def _digest(data: Any) -> str:
    return hashlib.sha256(json.dumps(data, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _records(path: pathlib.Path) -> list[dict[str, Any]]:
    _require(path.is_file(), f"missing source artifact {path.name}")
    records = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
    _require(all(isinstance(record, dict) for record in records), "source records must be objects")
    return records


def _one(events: list[Event], kind: str, agent: str, task: str) -> Event:
    matches = [event for event in events if event.kind == kind and event.agent_id == agent
               and event.data.get("task") == task]
    _require(len(matches) == 1, f"expected one {kind} for {agent}/{task}")
    return matches[0]


def _finite(value: Any, name: str) -> float:
    _require(type(value) in (int, float) and math.isfinite(value), f"{name} must be a measured finite number")
    return float(value)


def _request(record: dict[str, Any], policy: PolicyIdentity) -> ActionRequest:
    context, request, response = record["rollout"], record.get("request"), record.get("response")
    if not isinstance(request, dict) or not isinstance(response, dict):
        raise ValueError("invalid rollout: missing request/response")
    _require(record.get("status") == 200 and record.get("model_kind") == "vllm", "failed/non-vLLM request")
    _require(record.get("identified_by") == "key", "rollout did not use its agent's key")
    _require(sampling_request(request, policy, record["model_kind"]) == request,
             "actual upstream sampling differs from the training contract")
    _require(all(type(request.get(key)) is type(value) for key, value in SAMPLING.items()),
             "sampling parameter types differ from the training contract")
    _require(response.get("model") in (None, policy.model), "response model differs from bound policy")
    indices = response.get("choice_indices")
    _require(indices == [0] and type(indices[0]) is int, "exactly one sampled choice is required")
    _require(response.get("finish_reason") in {"stop", "tool_calls", "length"}, "unfinished model completion")
    _require(not response.get("unparsed_lines"), "unparsed completion stream")
    _require(not response.get("stream_errors") and response.get("error") is None, "upstream completion error")
    _require(not record.get("stream") or response.get("stream_complete") is True, "incomplete completion stream")
    logprobs = response.get("token_logprobs")
    _require(isinstance(logprobs, list) and bool(logprobs)
             and all(type(value) in (int, float) and math.isfinite(value) and value <= 0 for value in logprobs),
             "missing/invalid sampled token log probabilities")
    action = ActionRequest.model_validate({"request_id": record["request_id"], "agent_id": record["agent"],
                                          "task": context["task"], "episode_id": context["episode_id"],
                                          "session_id": context["session_id"],
                                          "prompt_token_ids": response.get("prompt_token_ids"),
                                          "token_ids": response.get("token_ids"), "token_logprobs": logprobs})
    usage = response.get("usage") or {}
    _require(type(usage.get("prompt_tokens")) is int and usage["prompt_tokens"] == len(action.prompt_token_ids),
             "prompt usage does not match exact prompt token IDs")
    _require(type(usage.get("completion_tokens")) is int and usage["completion_tokens"] == len(action.token_ids),
             "completion usage does not match exact action IDs/logprobs (including stop tokens)")
    return action


def collect_rollouts(bundle: RunBundle | str | pathlib.Path, expected_policy: PolicyIdentity,
                     episode_id: str) -> RolloutBatch:
    """Accept only complete, closed, on-policy native records and independently recorded source rewards.

    Native records retain their order. No text is tokenized, no missing reward becomes zero, and no
    timestamp is used to infer which task or session generated a model action.
    """
    try:
        return _collect_rollouts(bundle, expected_policy, episode_id)
    except (OSError, KeyError, TypeError, AttributeError) as exc:
        raise ValueError(f"invalid rollout source: {exc}") from exc


def _collect_rollouts(bundle: RunBundle | str | pathlib.Path, expected_policy: PolicyIdentity,
                      episode_id: str) -> RolloutBatch:
    bundle = bundle if isinstance(bundle, RunBundle) else RunBundle.open(bundle)
    envelope = bundle.manifest()
    manifest = WorldManifest.model_validate(envelope["manifest"])
    _require(envelope.get("digest") == manifest.digest(), "manifest digest mismatch")
    _require(manifest.mode == "comparison" and manifest.rewards.enabled, "training needs complete rewarded comparisons")
    agents = [agent.id for agent in manifest.agents]
    _require(len(set(agents)) >= 3, "training requires at least three configured agents")
    _require(all(manifest.model_for(agent).id == expected_policy.model for agent in manifest.agents),
             "manifest agent model differs from expected policy")
    summary = json.loads((bundle.root / "summary.json").read_text())
    _require(summary.get("status") == "ok", "source run did not complete successfully")
    run = manifest.gateway.run_label or bundle.run_id
    _require(summary.get("run_label") == run, "source run label mismatch")
    events = bundle.events()
    _require(all(event.run_id == bundle.run_id for event in events), "event belongs to another run")
    _require(not any(event.kind in {"run.failed", "run.aborted", "run.stopped", "harness.failed", "verifier.failed",
                                    "harness.round_timeout", "harness.task_timeout", "harness.events_ended"}
                     for event in events), "source events contain failed/incomplete execution")
    loaded_events = [event for event in events if event.kind == "task.loaded"]
    loaded = [event.data["task"] for event in loaded_events]
    _require(bool(loaded) and len(set(loaded)) == len(loaded), "missing/duplicate source task declarations")
    _require(all(event.data.get("source") == "manifest" for event in loaded_events), "non-manifest training task")
    declared: set[str] = set()
    for spec in manifest.tasks:
        if spec.path is not None:
            _require(bool(envelope.get("path")), "relative task identity requires its manifest path")
            path = (pathlib.Path(envelope["path"]).parent / spec.path).resolve()
            matches = [event for event in loaded_events
                       if (event.data.get("provenance") or {}).get("path") == str(path)]
            _require(len(matches) == 1, "declared task path missing or duplicated in source")
        else:
            matches = [event for event in loaded_events
                       if (event.data.get("provenance") or {}).get("dataset") == spec.dataset]
            _require(bool(matches) and (spec.limit is None or len(matches) <= spec.limit),
                     "declared dataset missing or exceeds its configured limit")
        declared.update(event.data["task"] for event in matches)
    _require(declared == set(loaded), "source includes an undeclared task")
    expected = {(agent, task) for agent in agents for task in loaded}
    for kind in ("harness.session", "verifier.reward", "learning.reward"):
        cells = [(event.agent_id, event.data.get("task")) for event in events if event.kind == kind]
        _require(set(cells) == expected and len(cells) == len(expected), f"incomplete/extra {kind} source matrix")
    outcomes = outcome_records(events)
    for key in ("task_outcomes", "learning_rewards"):
        _require(summary.get(key) == outcomes[key], f"summary {key} differs from source events")

    native = _records(bundle.root / "native" / "gateway" / "records.jsonl")

    # MonitorModel uses a distinct <run>-monitor label. Its calls are observations, never actions.
    _require(all(record.get("run") == run for record in native), "gateway record belongs to another run")
    source_receipts = bundle.receipts()
    receipts = {receipt["enforced_at"]["event_id"]: receipt for receipt in source_receipts}
    _require(len(receipts) == len(source_receipts), "duplicate receipt event IDs")
    active: dict[str, dict[str, Any]] = {}
    bindings: dict[str, tuple[str, dict[str, Any]]] = {}
    bound_requests: dict[str, list[str]] = defaultdict(list)
    requests: dict[tuple[str, str], list[ActionRequest]] = defaultdict(list)
    request_records: dict[str, dict[str, Any]] = {}
    admissions: dict[str, tuple[str, dict[str, Any]]] = {}
    unsettled: set[str] = set()
    journaled = any(record.get("event") == "request_admitted" for record in native)
    for record in native:
        record_kind, record_agent = record.get("event"), record.get("agent")
        if record_kind in {"rollout_denied", "upstream_error", "budget_exhausted", "run_stopped", "hold_expired",
                    "auth_denied", "endpoint_denied", "invalid_request", "request_usage_unknown", "terminal_refusal"}:
            raise ValueError(f"invalid rollout: source gateway request failed ({record_kind})")
        if record_kind not in {"rollout.bound", "rollout.unbound", "request_admitted", "chat_completion"}:
            continue
        if not isinstance(record_agent, str) or record_agent not in agents:
            raise ValueError("invalid rollout: unknown agent in rollout records")
        agent = record_agent
        if record_kind in {"rollout.bound", "rollout.unbound"}:
            context, receipt = record["rollout"], record["receipt"]
            event_id = receipt["enforced_at"]["event_id"]
            _require(receipts.get(event_id) == receipt, "gateway binding receipt differs from bundle receipt")
            _require(receipt.get("result") == "applied" and receipt.get("verification", {}).get("ok") is True,
                     "binding was not confirmed")
            _require(receipt.get("run_id") == bundle.run_id
                     and receipt.get("op") == ("rollout.bind" if record_kind == "rollout.bound" else "rollout.unbind"),
                     "binding receipt operation/run mismatch")
            _require(receipt["verification"]["detail"] == context, "binding receipt context mismatch")
            source_events = [event for event in events if event.kind == record_kind
                             and event.data.get("event_id") == event_id]
            _require(len(source_events) == 1 and source_events[0].agent_id == agent
                     and source_events[0].data == {"event_id": event_id, **context},
                     "binding event differs from gateway source")
            _require(context["episode_id"] == episode_id
                     and context["policy"] == expected_policy.model_dump(mode="json"),
                     "episode/checkpoint policy mismatch")
            _require((agent, context["task"]) in expected, "binding refers to an unknown task")
            if record_kind == "rollout.bound":
                parsed = BindRollout.model_validate(receipt["requested"])
                _require(parsed.agent_id == agent and parsed.run == run and parsed.run_id == bundle.run_id,
                         "binding receipt identity mismatch")
                _require(parsed.context(event_id) == context, "binding context differs from control request")
                _require(agent not in active and event_id not in bindings, "duplicate/replayed binding")
                active[agent] = context
                bindings[event_id] = agent, context
            else:
                closing = UnbindRollout.model_validate(receipt["requested"])
                _require((closing.agent_id, closing.run, closing.episode_id, closing.session_id, closing.run_id)
                         == (agent, run, episode_id, context["session_id"], bundle.run_id), "unbind identity mismatch")
                _require(active.get(agent) == context, "unbind without its exact active binding")
                del active[agent]
        else:
            context = record.get("rollout")
            if not isinstance(context, dict) or active.get(agent) != context:
                raise ValueError("invalid rollout: request outside its exact active binding")
            _require(record.get("missing_usage") is not True, "gateway token usage is unknown")
            if record_kind == "request_admitted":
                request_id = record.get("request_id")
                if not isinstance(request_id, str) or not request_id:
                    raise ValueError("invalid rollout: admission has no request ID")
                _require(record.get("identified_by") == "key" and record.get("model_kind") == "vllm",
                         "admission did not use its bound agent key and vLLM policy")
                _require(request_id not in admissions and request_id not in request_records,
                         "duplicate/replayed request admission")
                admissions[request_id] = agent, context
                unsettled.add(request_id)
                continue
            action = _request(record, expected_policy)
            _require(action.request_id not in request_records, "duplicate/replayed request ID")
            if journaled:
                _require(action.request_id in unsettled and admissions.get(action.request_id) == (agent, context),
                         "completion lacks its exact preceding admission")
                unsettled.remove(action.request_id)
            request_records[action.request_id] = record
            requests[action.agent_id, action.task].append(action)
            bound_requests[context["binding_id"]].append(action.request_id)
    _require(not unsettled, "gateway admissions have no completed settlement")
    _require(not active, "rollout bindings were not closed")
    _require(sum(event.kind in {"rollout.bound", "rollout.unbound"} for event in events) == 2 * len(bindings),
             "missing/extra binding source events")
    _require(set(requests) == expected, "missing agent/task actions")
    _require(summary.get("model_calls") == len(request_records), "summary model call count differs from native records")
    normalized = [event for event in events if event.kind == "gateway.chat_completion"]
    _require(len(normalized) == len(request_records), "missing/extra normalized model events")
    for event in normalized:
        source_record = request_records.get(event.request_id or "")
        if source_record is None:
            raise ValueError("invalid rollout: model event missing from native source")
        _require(event.agent_id == source_record["agent"] and event.data.get("status") == source_record["status"]
                 and event.data.get("usage") == source_record["response"]["usage"]
                 and event.data.get("request_summary") == source_record["request_summary"],
                 "normalized model event differs from its exact native source")
    _require(len({event.request_id for event in normalized}) == len(normalized), "duplicated normalized model event")
    reply_events = [event for event in events if event.kind == "harness.reply"]
    replies = {(event.agent_id, event.data.get("task"), event.data.get("session_id"), event.data.get("round") or 1)
               for event in reply_events}
    _require(len(replies) == len(reply_events), "duplicate harness reply boundaries")
    bound_replies = set()
    for binding_id, (agent, context) in bindings.items():
        session = _one(events, "harness.session", agent, context["task"])
        _require(session.data.get("session_id") == context["session_id"], "harness session differs from binding")
        reply_key = (agent, context["task"], context["session_id"], context["round"])
        _require(reply_key not in bound_replies and bool(bound_requests[binding_id]), "replayed/empty rollout round")
        bound_replies.add(reply_key)
    _require(replies == bound_replies, "harness replies do not complete every bound round")

    trajectories = []
    verified = {(agent, task): _one(events, "verifier.reward", agent, task) for agent, task in expected}
    _require(summary.get("rewards") == {f"{agent}/{task}": event.data.get("reward")
                                        for (agent, task), event in verified.items()}, "task reward summary mismatch")
    for task in loaded:
        verifier = {agent: _finite(verified[agent, task].data.get("reward"), "verifier reward") for agent in agents}
        verifier_artifacts = {}
        for agent in agents:
            logs_dir = bundle.root / "verifier" / agent / task
            original = read_reward(logs_dir)
            _require(original.value == verifier[agent] and original.source == verified[agent, task].data.get("source"),
                     "verifier event differs from its native reward artifact")
            _require(original.labels == (verified[agent, task].data.get("labels") or {})
                     and original.checkpoints == (verified[agent, task].data.get("checkpoints") or {}),
                     "verifier metadata differs from its native reward artifact")
            verifier_artifacts[agent] = {name: hashlib.sha256((logs_dir / "verifier" / name).read_bytes()).hexdigest()
                                          for name in ("reward.txt", "reward.json", "checkpoints.json")
                                          if (logs_dir / "verifier" / name).is_file()}
        reward_events = {agent: _one(events, "learning.reward", agent, task) for agent in agents}
        evidence = reward_events[agents[0]].data.get("evidence") or {}
        _require(evidence.get("verifier_rewards") == verifier, "reward verifier evidence differs from source")
        raw: dict[str, float | None] = dict(verifier)
        if manifest.rewards.source == "peer_verdict":
            topology = manifest.services.peer_review.topology
            _require(evidence.get("topology") == topology, "peer reward topology mismatch")
            verdicts = evidence.get("peer_verdicts") or []
            peer_events = [event for event in events if event.kind == "service.verdict"
                           and event.data.get("epoch") == task and event.data.get("service") == "chat"]
            source_verdicts = [record for record in _records(bundle.root / "native/services/services.jsonl")
                               if record.get("service") == "chat" and record.get("op") == "verdict"
                               and record.get("epoch") == task]
            observed = {(event.agent_id, event.data.get("about")): event.data.get("verdict") for event in peer_events}
            native_peers = {(record.get("agent"), record.get("about")): record for record in source_verdicts}
            _require(len(observed) == len(peer_events) == len(native_peers) == len(source_verdicts)
                     and set(observed) == set(native_peers), "duplicate/missing peer verdict source records")
            for event in peer_events:
                source = native_peers[event.agent_id, event.data.get("about")]
                data = {key: value for key, value in source.items() if key not in {"ts", "op", "agent", "method"}}
                _require(event.data == data, "normalized peer verdict differs from native source")
            given = {(verdict["from"], verdict["about"]): verdict["verdict"] for verdict in verdicts}
            _require(len(given) == len(verdicts) and given == observed, "peer rewards differ from source verdicts")
            _require(all(sender in agents and about in agents and sender != about for sender, about in given),
                     "peer verdict has an unknown identity")
            assignment = reviewers(agents, topology)
            for agent in agents:
                ratings = [given.get((judge, agent)) for judge in agents if agent in assignment[judge]]
                _require(bool(ratings) and all(value in {"accept", "reject"} for value in ratings),
                         "peer reward lacks required verdicts")
                raw[agent] = sum(value == "accept" for value in ratings) / len(ratings)
        allocated = allocate(raw, manifest.rewards)
        for agent in agents:
            event = reward_events[agent]
            data = event.data
            _require(data.get("source") == manifest.rewards.source
                     and data.get("allocation") == manifest.rewards.allocation
                     and data.get("raw_reward") == raw[agent] and data.get("allocated_reward") == allocated[agent]
                     and data.get("evidence") == evidence, "allocated reward does not reproduce its source rule")
            provenance = {"reward_rule_version": REWARD_RULE_VERSION, "reward_spec": manifest.rewards.model_dump(),
                          "source_run_id": bundle.run_id, "manifest_digest": envelope["digest"],
                          "source_event": event.model_dump(mode="json"),
                          "source_event_sha256": _digest(event.model_dump()),
                          "verifier_artifact_sha256": verifier_artifacts,
                          "request_sha256": {action.request_id: _digest(request_records[action.request_id])
                                             for action in requests[agent, task]}}
            trajectories.append(AgentTrajectory(agent_id=agent, task=task, episode_id=episode_id,
                                                 reward=_finite(allocated[agent], "allocated reward"),
                                                 reward_source=manifest.rewards.source,
                                                 reward_evidence={"source_evidence": evidence,
                                                                  "provenance": provenance},
                                                 requests=requests[agent, task]))
    return RolloutBatch(policy=expected_policy, source_run_ids=[bundle.run_id], trajectories=trajectories)
