"""Collector provenance rejection with explicit unit fixtures and real gateway recording code."""

import asyncio
import copy
import json

import httpx
import pytest
from test_training_gateway import policy, records, training_gateway

from mesocosm.core.episode_controls import component_rewards, episode_metrics, review_assignment
from mesocosm.core.manifest import WorldManifest
from mesocosm.core.records import RunBundle
from mesocosm.core.rewards import allocate, outcome_records
from mesocosm.learning.collect import collect_rollouts


async def complete_bundle(tmp_path, allocation="individual", source="verifier", task_names=("task",)):
    app, upstream, transport, admin = training_gateway(tmp_path / "gateway")
    bundle = RunBundle.create(tmp_path / "runs", "run-source")
    manifest = WorldManifest.model_validate({"name": "training", "model": {"id": "policy-0"},
                "agents": [{"id": f"agent-{i}"} for i in range(1, 4)],
                "tasks": [{"path": task} for task in task_names],
                "gateway": {"url": "http://gateway", "run_label": "world"},
                "rewards": {"enabled": True, "allocation": allocation, "source": source}})
    if source == "peer_verdict":
        manifest.services.chat.enabled = True
        manifest.services.peer_review.enabled = True
        manifest.services.peer_review.protocol = "ratings"
    bundle.write_manifest(manifest.model_dump(mode="json"), manifest.digest(), path=tmp_path / "world.yaml")
    for task in task_names:
        bundle.record("task.loaded", task=task, provenance={"name": task, "path": str(tmp_path / task)},
                      source="manifest")
    raw = {"agent-1": 0.0, "agent-2": 0.5, "agent-3": 1.0}

    async def participant(index, task):
        agent, session = f"agent-{index}", f"session-{task}-{index}"
        bundle.record("harness.session", agent_id=agent, task=task, session_id=session)
        receipt = await admin.bind_rollout(agent, "world", "episode-1", task, 1, session, policy(),
                                           run_id=bundle.run_id)
        bundle.record_receipt(receipt.model_dump(mode="json"))
        bundle.record("rollout.bound", agent_id=agent, event_id=receipt.enforced_at.event_id, **receipt.verification.detail)
        async with httpx.AsyncClient(transport=transport, base_url="http://gateway",
                                    headers={"Authorization": f"Bearer key-{index}"}) as client:
            response = await client.post("/v1/chat/completions", json={"model": "policy-0", "messages": [],
                                         "stream": index == 2, "max_tokens": 10})
            assert response.status_code == 200
        receipt = await admin.unbind_rollout(agent, "world", "episode-1", session, run_id=bundle.run_id)
        bundle.record_receipt(receipt.model_dump(mode="json"))
        bundle.record("rollout.unbound", agent_id=agent, event_id=receipt.enforced_at.event_id, **receipt.verification.detail)
        bundle.record("harness.reply", agent_id=agent, task=task, session_id=session, round=1)
        directory = bundle.root / "verifier" / agent / task / "verifier"
        directory.mkdir(parents=True)
        (directory / "reward.txt").write_text(str(raw[agent]))
        bundle.record("verifier.reward", agent_id=agent, task=task, reward=raw[agent], source="reward.txt")

    journal = []
    for task in task_names:
        await asyncio.gather(*(participant(index, task) for index in range(1, 4)))
        evidence = {"verifier_rewards": raw}
        incentive_raw = raw
        if source == "peer_verdict":
            verdicts = [{"from": agent, "about": other, "verdict": "reject" if other == "agent-1" else "accept",
                         "epoch": task, "reason": "unit fixture"} for agent in raw for other in raw if agent != other]
            evidence.update(peer_verdicts=verdicts, topology="all")
            for verdict in verdicts:
                data = {"service": "chat", "epoch": task, "about": verdict["about"],
                        "verdict": verdict["verdict"], "summary": verdict["reason"]}
                bundle.record("service.verdict", agent_id=verdict["from"], **data)
                journal.append({"op": "verdict", "agent": verdict["from"], "ts": 1.0, **data})
            incentive_raw = {"agent-1": 0.0, "agent-2": 1.0, "agent-3": 1.0}
        incentive = allocate(incentive_raw, manifest.rewards)
        for agent in raw:
            bundle.record("learning.reward", agent_id=agent, task=task, raw_reward=incentive_raw[agent],
                          allocated_reward=incentive[agent], source=source, allocation=allocation, evidence=evidence)
    if journal:
        bundle.write_native("services/services.jsonl", "".join(json.dumps(record) + "\n" for record in journal))
    native = records(tmp_path / "gateway")
    bundle.write_native("gateway/records.jsonl", "".join(json.dumps(record) + "\n" for record in native))
    for record in native:
        if record["event"] == "chat_completion":
            bundle.record("gateway.chat_completion", agent_id=record["agent"], request_id=record["request_id"],
                          status=record["status"], request_summary=record["request_summary"],
                          usage=record["response"]["usage"])
    bundle.write_summary({"status": "ok", "run_label": "world", "model_calls": 3 * len(task_names),
                          "rewards": {f"{agent}/{task}": value for task in task_names for agent, value in raw.items()},
                          **outcome_records(bundle.events())})
    await admin.aclose()
    await app.state.gateway.client.aclose()
    return bundle


def rewrite(path, transform):
    data = [json.loads(line) for line in path.read_text().splitlines()]
    transform(data)
    path.write_text("".join(json.dumps(record) + "\n" for record in data))


def alter_completion(bundle, change):
    rewrite(bundle.root / "native/gateway/records.jsonl",
            lambda data: change(next(record for record in data if record["event"] == "chat_completion")))


def refresh_summary(bundle):
    path = bundle.root / "summary.json"
    summary = json.loads(path.read_text())
    summary.update(outcome_records(bundle.events()))
    path.write_text(json.dumps(summary))


def measured_conditions(bundle, *, terms=None, topology="all", matching_seed=0, success_threshold=1.0,
                        include_metrics=True):
    """Construct declared unit conditions over independently written gateway/verifier artifacts."""
    envelope = bundle.manifest()
    data = envelope["manifest"]
    if terms is not None:
        data["rewards"].update(source="components", terms=terms)
    data["rewards"]["success_threshold"] = success_threshold
    data["services"]["peer_review"].update(topology=topology, matching_seed=matching_seed)
    manifest = WorldManifest.model_validate(data)
    envelope.update(manifest=manifest.model_dump(mode="json"), digest=manifest.digest())
    (bundle.root / "manifest.json").write_text(json.dumps(envelope))
    agents = [agent.id for agent in manifest.agents]
    tasks = [event.data["task"] for event in bundle.events() if event.kind == "task.loaded"]
    native = [json.loads(line) for line in (bundle.root / "native/gateway/records.jsonl").read_text().splitlines()]
    assignments = {task: review_assignment(agents, topology, matching_seed, index)
                   if manifest.services.peer_review.enabled else {} for index, task in enumerate(tasks)}
    if topology == "random_ring":
        rewrite(bundle.root / "native/services/services.jsonl", lambda rows: rows.__setitem__(slice(None), [
            row for row in rows if row["op"] != "verdict"
            or row["about"] in assignments[row["epoch"]][row["agent"]]]))

    def update(events):
        if topology == "random_ring":
            events[:] = [event for event in events if event["kind"] != "service.verdict"
                         or event["data"]["about"] in assignments[event["data"]["epoch"]][event["agent_id"]]]
        for task in tasks:
            rewards = [event for event in events if event["kind"] == "learning.reward" and event["data"]["task"] == task]
            evidence = copy.deepcopy(rewards[0]["data"]["evidence"])
            assignment = assignments[task]
            evidence["assignment"] = assignment
            if manifest.services.peer_review.enabled:
                evidence["topology"] = topology
            if topology == "random_ring":
                evidence["peer_verdicts"] = [verdict for verdict in evidence["peer_verdicts"]
                                             if verdict["about"] in assignment[verdict["from"]]]
            usage = {}
            for agent in agents:
                calls = [record for record in native if record["event"] == "chat_completion"
                         and record["agent"] == agent and record["rollout"]["task"] == task]
                usage[agent] = {"model_requests": len(calls),
                                "completion_tokens": sum(record["response"]["usage"]["completion_tokens"]
                                                         for record in calls),
                                "request_ids": sorted(record["request_id"] for record in calls)}
            metrics = episode_metrics(evidence["verifier_rewards"], evidence.get("peer_verdicts", []),
                                      assignment, usage, success_threshold)
            if include_metrics:
                evidence.update(usage=usage, metrics=metrics)
            raw = (component_rewards(manifest.rewards.terms, metrics) if terms is not None else
                   {agent: metrics[agent]["peer_acceptance"] for agent in agents})
            allocated = allocate(raw, manifest.rewards)
            for event in rewards:
                agent = event["agent_id"]
                event["data"].update(evidence=evidence, source=manifest.rewards.source,
                                     raw_reward=raw[agent], allocated_reward=allocated[agent])

    rewrite(bundle.root / "events.jsonl", update)
    refresh_summary(bundle)
    return manifest


def change_reward_evidence(bundle, change):
    def update(events):
        for event in events:
            if event["kind"] == "learning.reward":
                change(event["data"]["evidence"])
    rewrite(bundle.root / "events.jsonl", update)
    refresh_summary(bundle)


@pytest.mark.parametrize("allocation", ["individual", "team_mean", "mixed"])
async def test_complete_three_agent_rollouts_preserve_exact_tokens_and_separate_reward_provenance(tmp_path, allocation):
    bundle = await complete_bundle(tmp_path, allocation)
    batch = collect_rollouts(bundle, policy(), "episode-1")
    expected = {"individual": [0.0, 0.5, 1.0], "team_mean": [0.5] * 3, "mixed": [0.25, 0.5, 0.75]}[allocation]
    assert [trajectory.reward for trajectory in batch.trajectories] == expected
    assert batch.source_run_ids == [bundle.run_id]
    for trajectory in batch.trajectories:
        action = trajectory.requests[0]
        assert action.prompt_token_ids == [1, 2] and action.token_ids == [3, 4]
        assert action.token_logprobs == [-0.3, -0.7]
        provenance = trajectory.reward_evidence["provenance"]
        assert provenance["reward_rule_version"] == "mesocosm.reward-allocation.v1"
        assert provenance["source_event"]["kind"] == "learning.reward"
        assert set(provenance["request_sha256"]) == {action.request_id}


@pytest.mark.parametrize("change", [
    lambda record: record.pop("rollout"),
    lambda record: record["rollout"].update(session_id="forged"),
    lambda record: record["rollout"].update(episode_id="another-episode"),
    lambda record: record["rollout"]["policy"].update(base_sha256="c" * 64),
    lambda record: record.update(identified_by="header"),
    lambda record: record.update(agent="unknown"),
    lambda record: record.update(status=500),
    lambda record: record["request"].update(temperature=0.9),
    lambda record: record["request"].update(logit_bias={"3": 1}),
    lambda record: record["response"].pop("token_ids"),
    lambda record: record["response"].update(token_ids=[3, True]),
    lambda record: record["response"].pop("token_logprobs"),
    lambda record: record["response"].update(token_logprobs=[-0.3]),
    lambda record: record["response"].update(token_logprobs=[-0.3, True]),
    lambda record: record["response"].update(token_logprobs=[-0.3, float("nan")]),
    lambda record: record["response"]["usage"].update(completion_tokens=3),
    lambda record: record["response"]["usage"].update(prompt_tokens=3),
    lambda record: record["response"].update(finish_reason=None),
    lambda record: record["response"].update(stream_errors=[{"message": "generation failed"}]),
    lambda record: record["response"].update(choice_indices=[0, 1]),
    lambda record: record["response"].update(choice_indices=[False]),
    lambda record: record.update(stream=True),
])
async def test_collector_rejects_missing_or_spoofed_request_provenance(tmp_path, change):
    bundle = await complete_bundle(tmp_path)
    alter_completion(bundle, change)
    with pytest.raises(ValueError):
        collect_rollouts(bundle, policy(), "episode-1")


async def test_collector_accepts_complete_horizon_limited_sampling_without_fabricating_eos(tmp_path):
    bundle = await complete_bundle(tmp_path)
    alter_completion(bundle, lambda record: record["response"].update(finish_reason="length"))
    batch = collect_rollouts(bundle, policy(), "episode-1")
    assert batch.trajectories[0].requests[0].token_ids == [3, 4]


async def test_collector_accepts_legacy_complete_records_without_admission_journal(tmp_path):
    bundle = await complete_bundle(tmp_path)
    rewrite(bundle.root / "native/gateway/records.jsonl",
            lambda records: records.__setitem__(slice(None), [record for record in records
                                                               if record["event"] != "request_admitted"]))
    assert len(collect_rollouts(bundle, policy(), "episode-1").trajectories) == 3


@pytest.mark.parametrize("mutation", ["unsettled", "missing", "duplicate", "unknown", "terminal_refusal",
                                     "agent", "run", "binding", "identity", "late", "sticky_unknown"])
async def test_collector_requires_complete_admissions_bound_to_the_exact_source_request(tmp_path, mutation):
    bundle = await complete_bundle(tmp_path)

    def change(records):
        index = next(index for index, record in enumerate(records) if record["event"] == "request_admitted")
        admission = records[index]
        if mutation == "unsettled":
            extra = copy.deepcopy(admission)
            extra["request_id"] = "interrupted-extra-request"
            records.insert(index, extra)
        elif mutation == "missing":
            records.pop(index)
        elif mutation == "duplicate":
            records.insert(index, copy.deepcopy(admission))
        elif mutation in {"unknown", "terminal_refusal"}:
            extra = copy.deepcopy(admission)
            extra["event"] = "request_usage_unknown" if mutation == "unknown" else "terminal_refusal"
            records.insert(index + 1, extra)
        elif mutation == "agent":
            admission["agent"] = "agent-2" if admission["agent"] != "agent-2" else "agent-1"
        elif mutation == "run":
            admission["run"] = "another-run"
        elif mutation == "binding":
            admission["rollout"]["session_id"] = "another-session"
        elif mutation == "identity":
            admission["identified_by"] = "header"
        elif mutation == "sticky_unknown":
            admission["missing_usage"] = True
        else:
            records.pop(index)
            completion = next(index for index, record in enumerate(records)
                              if record["event"] == "chat_completion" and record["request_id"] == admission["request_id"])
            records.insert(completion + 1, admission)

    rewrite(bundle.root / "native/gateway/records.jsonl", change)
    with pytest.raises(ValueError, match="invalid rollout"):
        collect_rollouts(bundle, policy(), "episode-1")


async def test_peer_reward_requires_exact_native_verdict_sources(tmp_path):
    bundle = await complete_bundle(tmp_path, source="peer_verdict")
    batch = collect_rollouts(bundle, policy(), "episode-1")
    assert [trajectory.reward for trajectory in batch.trajectories] == [0.0, 1.0, 1.0]
    assert all(trajectory.reward_source == "peer_verdict" for trajectory in batch.trajectories)
    assert batch.trajectories[1].reward_evidence["source_evidence"]["verifier_rewards"]["agent-2"] == 0.5
    rewrite(bundle.root / "native/services/services.jsonl", lambda records: records[0].update(summary="altered"))
    with pytest.raises(ValueError, match="native source"):
        collect_rollouts(bundle, policy(), "episode-1")


async def test_component_rewards_recompute_costs_and_per_agent_weights_from_independent_sources(tmp_path):
    bundle = await complete_bundle(tmp_path, source="peer_verdict")
    measured_conditions(bundle, terms=[
        {"metric": "verifier", "weight": 1},
        {"metric": "review_accuracy", "weight": 2, "agent_weights": {"agent-3": -2}},
        {"metric": "model_requests", "weight": -0.1},
        {"metric": "completion_tokens", "weight": -0.2, "scale": 2},
    ])
    batch = collect_rollouts(bundle, policy(), "episode-1")
    # Original scores are 0, .5, 1; actual review accuracies are .5, 1, .5.
    # Every agent made one source-bound call with two completion tokens.
    assert [trajectory.reward for trajectory in batch.trajectories] == pytest.approx([0.7, 2.2, -0.3])
    assert all(trajectory.reward_source == "components" for trajectory in batch.trajectories)
    for trajectory in batch.trajectories:
        evidence = trajectory.reward_evidence
        assert evidence["provenance"]["reward_rule_version"] == "mesocosm.reward-allocation.v2"
        assert evidence["source_evidence"]["usage"][trajectory.agent_id] == {
            "model_requests": 1, "completion_tokens": 2, "request_ids": [trajectory.requests[0].request_id]}


@pytest.mark.parametrize("allocation, expected", [
    ("individual", [-0.5, 0.0, 0.5]), ("team_mean", [0.0, 0.0, 0.0]), ("mixed", [-0.25, 0.0, 0.25]),
])
async def test_non_peer_component_reward_retains_allocation_semantics(tmp_path, allocation, expected):
    bundle = await complete_bundle(tmp_path, allocation=allocation)
    measured_conditions(bundle, terms=[{"metric": "verifier"}, {"metric": "model_requests", "weight": -0.5}])
    assert [trajectory.reward for trajectory in collect_rollouts(bundle, policy(), "episode-1").trajectories] == expected


@pytest.mark.parametrize("mutation", ["missing_usage", "request_count", "tokens", "request_ids", "metrics",
                                     "peer_verdicts", "assignment"])
async def test_component_evidence_cannot_override_native_measurements(tmp_path, mutation):
    bundle = await complete_bundle(tmp_path, source="peer_verdict")
    measured_conditions(bundle, terms=[{"metric": "verifier"}, {"metric": "review_accuracy"},
                                       {"metric": "completion_tokens", "weight": -0.1}])

    def alter(evidence):
        if mutation == "missing_usage":
            evidence.pop("usage")
        elif mutation == "request_count":
            evidence["usage"]["agent-1"]["model_requests"] = 0
        elif mutation == "tokens":
            evidence["usage"]["agent-1"]["completion_tokens"] = 0
        elif mutation == "request_ids":
            evidence["usage"]["agent-1"]["request_ids"] = evidence["usage"]["agent-2"]["request_ids"]
        elif mutation == "metrics":
            evidence["metrics"]["agent-1"]["review_accuracy"] = 1
        elif mutation == "peer_verdicts":
            evidence["peer_verdicts"][0]["verdict"] = "reject"
        else:
            evidence["assignment"]["agent-1"] = ["agent-2"]

    change_reward_evidence(bundle, alter)
    with pytest.raises(ValueError, match="invalid rollout"):
        collect_rollouts(bundle, policy(), "episode-1")


async def test_missing_native_cost_usage_cannot_be_replaced_by_reward_evidence(tmp_path):
    bundle = await complete_bundle(tmp_path)
    measured_conditions(bundle, terms=[{"metric": "model_requests", "weight": -1}])
    alter_completion(bundle, lambda record: record["response"].pop("usage"))
    with pytest.raises(ValueError, match="usage"):
        collect_rollouts(bundle, policy(), "episode-1")


async def test_review_accuracy_uses_configured_original_outcome_threshold(tmp_path):
    bundle = await complete_bundle(tmp_path, source="peer_verdict")
    measured_conditions(bundle, terms=[{"metric": "review_accuracy"}], success_threshold=0.5)
    assert [trajectory.reward for trajectory in collect_rollouts(bundle, policy(), "episode-1").trajectories] == [1] * 3
    (bundle.root / "verifier/agent-2/task/verifier/reward.txt").write_text("0.0")
    with pytest.raises(ValueError, match="native reward artifact"):
        collect_rollouts(bundle, policy(), "episode-1")


async def test_random_review_matching_uses_declared_task_order_and_native_bound_costs(tmp_path):
    tasks = ("first", "second", "third", "fourth")
    bundle = await complete_bundle(tmp_path, source="peer_verdict", task_names=tasks)
    manifest = measured_conditions(bundle, terms=[{"metric": "review_accuracy"}],
                                   topology="random_ring", matching_seed=17)
    batch = collect_rollouts(bundle, policy(), "episode-1")
    assignments = []
    for index, task in enumerate(tasks):
        trajectories = [trajectory for trajectory in batch.trajectories if trajectory.task == task]
        expected = review_assignment([agent.id for agent in manifest.agents], "random_ring", 17, index)
        assignments.append(expected)
        assert all(trajectory.reward_evidence["source_evidence"]["assignment"] == expected
                   for trajectory in trajectories)
        for trajectory in trajectories:
            usage = trajectory.reward_evidence["source_evidence"]["usage"][trajectory.agent_id]
            assert usage["model_requests"] == 1
            assert usage["request_ids"] == [trajectory.requests[0].request_id]
    assert len({json.dumps(value, sort_keys=True) for value in assignments}) > 1
    # A consistent-looking rewritten reward event must not select another task's matching.
    changed = next(value for value in assignments if value != assignments[0])
    change_reward_evidence(bundle, lambda evidence: evidence.update(assignment=changed))
    with pytest.raises(ValueError, match="assignment"):
        collect_rollouts(bundle, policy(), "episode-1")


async def test_random_ring_peer_source_requires_assignment_without_optional_cost_metrics(tmp_path):
    bundle = await complete_bundle(tmp_path, source="peer_verdict")
    measured_conditions(bundle, topology="random_ring", matching_seed=8, include_metrics=False)
    assert [trajectory.reward for trajectory in collect_rollouts(bundle, policy(), "episode-1").trajectories] == [0, 1, 1]
    change_reward_evidence(bundle, lambda evidence: evidence.pop("assignment"))
    with pytest.raises(ValueError, match="assignment"):
        collect_rollouts(bundle, policy(), "episode-1")


async def test_collector_rejects_reordered_task_declarations_for_random_matching(tmp_path):
    bundle = await complete_bundle(tmp_path, source="peer_verdict", task_names=("first", "second"))
    measured_conditions(bundle, terms=[{"metric": "review_accuracy"}], topology="random_ring")

    def swap(events):
        positions = [index for index, event in enumerate(events) if event["kind"] == "task.loaded"]
        events[positions[0]], events[positions[1]] = events[positions[1]], events[positions[0]]

    rewrite(bundle.root / "events.jsonl", swap)
    with pytest.raises(ValueError, match="task order"):
        collect_rollouts(bundle, policy(), "episode-1")


async def test_collector_rejects_a_declared_task_that_never_ran(tmp_path):
    bundle = await complete_bundle(tmp_path)
    path = bundle.root / "manifest.json"
    envelope = json.loads(path.read_text())
    envelope["manifest"]["tasks"].append({"path": "missing-task"})
    changed = WorldManifest.model_validate(envelope["manifest"])
    envelope.update(manifest=changed.model_dump(mode="json"), digest=changed.digest())
    path.write_text(json.dumps(envelope))
    with pytest.raises(ValueError, match="declared task path missing"):
        collect_rollouts(bundle, policy(), "episode-1")


@pytest.mark.parametrize("mutation", ["replay", "missing_agent", "unclosed", "receipt", "event", "summary", "reward"])
async def test_collector_rejects_replayed_incomplete_or_changed_source_artifacts(tmp_path, mutation):
    bundle = await complete_bundle(tmp_path)
    if mutation == "replay":
        rewrite(bundle.root / "native/gateway/records.jsonl",
                lambda records: records.insert(4, next(record for record in records if record["event"] == "chat_completion")))
    elif mutation == "missing_agent":
        rewrite(bundle.root / "native/gateway/records.jsonl",
                lambda records: records.__setitem__(slice(None), [record for record in records if record["agent"] != "agent-3"]))
    elif mutation == "unclosed":
        rewrite(bundle.root / "native/gateway/records.jsonl",
                lambda records: records.__setitem__(slice(None), [record for record in records if record["event"] != "rollout.unbound"]))
    elif mutation == "receipt":
        rewrite(bundle.root / "receipts.jsonl", lambda records: records.pop())
    elif mutation == "event":
        rewrite(bundle.root / "events.jsonl", lambda records: next(record for record in records
                if record["kind"] == "gateway.chat_completion")["data"].update(status=500))
    elif mutation == "summary":
        path = bundle.root / "summary.json"
        data = json.loads(path.read_text())
        data["learning_rewards"][0]["allocated_reward"] = 20
        path.write_text(json.dumps(data))
    else:
        (bundle.root / "verifier/agent-1/task/verifier/reward.txt").write_text("20")
    with pytest.raises(ValueError):
        collect_rollouts(bundle, policy(), "episode-1")
