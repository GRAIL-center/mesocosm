"""Collector provenance rejection with explicit unit fixtures and real gateway recording code."""

import asyncio
import json

import httpx
import pytest
from test_training_gateway import policy, records, training_gateway

from mesocosm.core.manifest import WorldManifest
from mesocosm.core.records import RunBundle
from mesocosm.core.rewards import allocate, outcome_records
from mesocosm.learning.collect import collect_rollouts


async def complete_bundle(tmp_path, allocation="individual", source="verifier"):
    app, upstream, transport, admin = training_gateway(tmp_path / "gateway")
    bundle = RunBundle.create(tmp_path / "runs", "run-source")
    manifest = WorldManifest.model_validate({"name": "training", "model": {"id": "policy-0"},
                "agents": [{"id": f"agent-{i}"} for i in range(1, 4)], "tasks": [{"path": "task"}],
                "gateway": {"url": "http://gateway", "run_label": "world"},
                "rewards": {"enabled": True, "allocation": allocation, "source": source}})
    if source == "peer_verdict":
        manifest.services.chat.enabled = True
        manifest.services.peer_review.enabled = True
        manifest.services.peer_review.protocol = "ratings"
    bundle.write_manifest(manifest.model_dump(mode="json"), manifest.digest(), path=tmp_path / "world.yaml")
    bundle.record("task.loaded", task="task", provenance={"name": "task", "path": str(tmp_path / "task")},
                  source="manifest")
    raw = {"agent-1": 0.0, "agent-2": 0.5, "agent-3": 1.0}

    async def participant(index):
        agent, session = f"agent-{index}", f"session-{index}"
        bundle.record("harness.session", agent_id=agent, task="task", session_id=session)
        receipt = await admin.bind_rollout(agent, "world", "episode-1", "task", 1, session, policy(),
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
        bundle.record("harness.reply", agent_id=agent, task="task", session_id=session, round=1)
        directory = bundle.root / "verifier" / agent / "task" / "verifier"
        directory.mkdir(parents=True)
        (directory / "reward.txt").write_text(str(raw[agent]))
        bundle.record("verifier.reward", agent_id=agent, task="task", reward=raw[agent], source="reward.txt")

    await asyncio.gather(*(participant(index) for index in range(1, 4)))
    evidence = {"verifier_rewards": raw}
    incentive_raw = raw
    if source == "peer_verdict":
        verdicts = [{"from": agent, "about": other, "verdict": "reject" if other == "agent-1" else "accept",
                     "epoch": "task", "reason": "unit fixture"} for agent in raw for other in raw if agent != other]
        evidence.update(peer_verdicts=verdicts, topology="all")
        journal = []
        for verdict in verdicts:
            data = {"service": "chat", "epoch": "task", "about": verdict["about"],
                    "verdict": verdict["verdict"], "summary": verdict["reason"]}
            bundle.record("service.verdict", agent_id=verdict["from"], **data)
            journal.append({"op": "verdict", "agent": verdict["from"], "ts": 1.0, **data})
        bundle.write_native("services/services.jsonl", "".join(json.dumps(record) + "\n" for record in journal))
        incentive_raw = {"agent-1": 0.0, "agent-2": 1.0, "agent-3": 1.0}
    incentive = allocate(incentive_raw, manifest.rewards)
    for agent in raw:
        bundle.record("learning.reward", agent_id=agent, task="task", raw_reward=incentive_raw[agent],
                      allocated_reward=incentive[agent], source=source, allocation=allocation, evidence=evidence)
    native = records(tmp_path / "gateway")
    bundle.write_native("gateway/records.jsonl", "".join(json.dumps(record) + "\n" for record in native))
    for record in native:
        if record["event"] == "chat_completion":
            bundle.record("gateway.chat_completion", agent_id=record["agent"], request_id=record["request_id"],
                          status=record["status"], request_summary=record["request_summary"],
                          usage=record["response"]["usage"])
    bundle.write_summary({"status": "ok", "run_label": "world", "model_calls": 3,
                          "rewards": {f"{agent}/task": value for agent, value in raw.items()},
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


async def test_peer_reward_requires_exact_native_verdict_sources(tmp_path):
    bundle = await complete_bundle(tmp_path, source="peer_verdict")
    batch = collect_rollouts(bundle, policy(), "episode-1")
    assert [trajectory.reward for trajectory in batch.trajectories] == [0.0, 1.0, 1.0]
    assert all(trajectory.reward_source == "peer_verdict" for trajectory in batch.trajectories)
    assert batch.trajectories[1].reward_evidence["source_evidence"]["verifier_rewards"]["agent-2"] == 0.5
    rewrite(bundle.root / "native/services/services.jsonl", lambda records: records[0].update(summary="altered"))
    with pytest.raises(ValueError, match="native source"):
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
