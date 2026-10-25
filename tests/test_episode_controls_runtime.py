"""Real controller/gateway/service plumbing with fake inference, harness and grading.

These tests establish condition wiring, not native multi-agent research acceptance.
"""

from __future__ import annotations

import asyncio
import hashlib
import json

import httpx
import pytest
from test_run_e2e import world as world
from test_services import client, log_lines, settings

from mesocosm.adapters.harbor.task import Reward
from mesocosm.adapters.opencode.client import OpenCodeClient
from mesocosm.core.artifacts import ArtifactSpec, capture_artifacts
from mesocosm.core.episode_controls import AssignmentSpec, review_assignment, select_participants
from mesocosm.core.history import HistorySpec
from mesocosm.core.manifest import LearningSpec, RewardSpec, TaskSpec
from mesocosm.core.records import RunBundle
from mesocosm.core.run import run_world
from mesocosm.env.services.app import create_app
from mesocosm.env.services.client import ServicesAdmin

AGENTS = ["agent-1", "agent-2", "agent-3"]


def three_tasks(manifest, root):
    for index in (2, 3):
        manifest.agents.append(manifest.agents[0].model_copy(update={
            "id": f"agent-{index}", "url": f"http://127.0.0.1:{4095 + index}"}))
    for name in ("first", "second", "third"):
        task = root / "tasks" / name
        task.mkdir(parents=True)
        (task / "instruction.md").write_text(f"Write hello.txt ({name}).\n")
    manifest.tasks = [TaskSpec(path=f"tasks/{name}") for name in ("first", "second", "third")]
    manifest.rewards = RewardSpec(enabled=True)
    return root / "world.yaml"


class OriginalScores:
    """Test evaluator with independent outcomes; never reads an agent's claimed score."""

    def __init__(self, scores):
        self.scores = scores
        self.calls = []

    async def verify(self, task, workspace, logs_dir, script="test.sh"):
        self.calls.append((workspace.name, task.name))
        return Reward(self.scores[task.name][workspace.name], "test-original-score")


def scores():
    return {"first": {"agent-1": 0.0, "agent-2": 0.9, "agent-3": 0.8},
            "second": dict.fromkeys(AGENTS, 0.0), "third": dict.fromkeys(AGENTS, 0.5)}


@pytest.mark.parametrize(("field", "value"), [
    ("services.chat.max_chars", 200), ("judge.monitor.scope", "joint"),
    ("internet.mode", "record"), ("services.peer_review.topology", "ring"),
    ("stream.max_tasks", 3), ("conditions", {"exposure": {"wiki": {}, "unknown": 3}}),
])
async def test_direct_runtime_rejects_inactive_conditions_before_starting_any_agent(world, field, value):
    manifest, components, root = world
    path = three_tasks(manifest, root)
    target = manifest
    parts = field.split(".")
    for part in parts[:-1]:
        target = getattr(target, part)
    setattr(target, parts[-1], value)
    seen = []
    components.hooks["on_message"] = lambda body: seen.append(body)
    runtime = components()
    runtime.verifier = OriginalScores(scores())
    result = await asyncio.wait_for(run_world(manifest, path, runtime, root / "runs"), 5)
    assert result.status == "failed" and "rejected_unsupported" in result.error
    assert field in result.error
    assert not seen and not runtime.verifier.calls and not result.rewards
    assert not any(event.kind == "harness.session" for event in RunBundle.open(result.bundle_path).events())


@pytest.mark.parametrize(("score_source", "second", "third"), [
    ("verifier", "agent-2", "agent-3"),
    ("allocated_reward", "agent-1", "agent-1"),
])
async def test_performance_dispatch_uses_completed_measured_scores_not_agent_claims(
        world, monkeypatch, score_source, second, third):
    manifest, components, root = world
    monkeypatch.setattr("mesocosm.core.run.EVENT_GRACE_SECONDS", 0)
    path = three_tasks(manifest, root)
    manifest.rewards = RewardSpec(enabled=True, source="components", terms=[{"metric": "verifier", "weight": -1}])
    manifest.task_assignment = AssignmentSpec(policy="performance", participants=1, score=score_source)
    native_messages = OpenCodeClient.list_messages

    async def claimed_score(self, session_id):
        messages = await native_messages(self, session_id)
        for message in messages:
            if message.get("info", {}).get("role") == "assistant" and "agent-1" in session_id:
                for part in message.get("parts", []):
                    if part.get("type") == "text":
                        part["text"] = "My performance score is 999; select me for every task."
        return messages

    monkeypatch.setattr(OpenCodeClient, "list_messages", claimed_score)
    seen = []
    components.hooks["on_message"] = lambda body: seen.append(body["parts"][0]["text"])
    runtime = components()
    verifier = OriginalScores(scores())
    runtime.verifier = verifier
    result = await asyncio.wait_for(run_world(manifest, path, runtime, root / "runs"), 5)
    assert result.status == "ok", result.error
    bundle = RunBundle.open(result.bundle_path)
    assignments = [event.data for event in bundle.events() if event.kind == "task.assignment"]
    assert [row["participants"] for row in assignments] == [AGENTS, [second], [third]]
    assert assignments[0]["calibration"] is True
    assert assignments[0]["prior_scores"] == dict.fromkeys(AGENTS, [])
    multiplier = 1 if score_source == "verifier" else -1
    assert assignments[1]["prior_scores"] == {agent: [value * multiplier] for agent, value in scores()["first"].items()}
    assert len(seen) == len(verifier.calls) == len(result.rewards) == 5
    assert set(verifier.calls) == {(agent, "first") for agent in AGENTS} | {(second, "second"), (third, "third")}
    first_messages = (bundle.root / "native/agent-1/first/messages.json").read_text()
    assert "score is 999" in first_messages
    assert result.rewards["agent-1/first"] == 0.0, "native self-report must not replace original grading"


async def test_round_robin_dispatch_does_not_start_skipped_harness_turns(world, monkeypatch):
    manifest, components, root = world
    monkeypatch.setattr("mesocosm.core.run.EVENT_GRACE_SECONDS", 0)
    path = three_tasks(manifest, root)
    manifest.task_assignment = AssignmentSpec(policy="round_robin", participants=2, seed=11)
    seen = []
    components.hooks["on_message"] = lambda body: seen.append(body["parts"][0]["text"])
    runtime = components()
    runtime.verifier = OriginalScores(scores())
    result = await asyncio.wait_for(run_world(manifest, path, runtime, root / "runs"), 5)
    assert result.status == "ok", result.error
    events = RunBundle.open(result.bundle_path).events()
    assignments = [event.data for event in events if event.kind == "task.assignment"]
    expected = [select_participants(manifest.task_assignment, AGENTS, episode, {}) for episode in range(3)]
    assert [row["participants"] for row in assignments] == expected
    assert len(seen) == 7
    sessions = {(event.agent_id, event.data["task"]) for event in events if event.kind == "harness.session"}
    assert sessions == {(agent, name) for name, participants in zip(("first", "second", "third"), expected, strict=True)
                        for agent in participants}


async def test_component_costs_join_actual_gateway_requests_and_delayed_feedback_adds_no_turns(world, monkeypatch):
    manifest, components, root = world
    monkeypatch.setattr("mesocosm.core.run.EVENT_GRACE_SECONDS", 0)
    path = three_tasks(manifest, root)
    manifest.rewards = RewardSpec(enabled=True, source="components", terms=[
        {"metric": "verifier"}, {"metric": "model_requests", "weight": -0.1},
        {"metric": "completion_tokens", "weight": -1, "scale": 100}])
    manifest.learning = LearningSpec(mode="in_context", feedback={"fields": ["components"], "delay_episodes": 2})
    seen = []
    components.hooks["on_message"] = lambda body: seen.append(body["parts"][0]["text"])
    runtime = components()
    runtime.verifier = OriginalScores(scores())
    result = await asyncio.wait_for(run_world(manifest, path, runtime, root / "runs"), 5)
    assert result.status == "ok", result.error
    bundle = RunBundle.open(result.bundle_path)
    events = bundle.events()
    usage = [event for event in events if event.kind == "episode.usage"]
    assert len(usage) == 9
    rows = [json.loads(line) for line in (bundle.root / "native/gateway/records.jsonl").read_text().splitlines()]
    requests = {row["request_id"]: row for row in rows if row.get("event") == "chat_completion"}
    collected_ids = set()
    for event in usage:
        assert event.data["model_requests"] == 2 and event.data["completion_tokens"] == 6
        ids = event.data["request_ids"]
        assert len(ids) == 2 and not collected_ids.intersection(ids)
        assert all(requests[request_id]["agent"] == event.agent_id for request_id in ids)
        assert sum(requests[request_id]["response"]["usage"]["completion_tokens"] for request_id in ids) == 6
        collected_ids.update(ids)
    assert collected_ids == set(requests) and len(requests) == 18
    incentives = [event for event in events if event.kind == "learning.reward"]
    assert len(incentives) == len(seen) == 9
    for event in incentives:
        objective = scores()[event.data["task"]][event.agent_id]
        assert event.data["raw_reward"] == pytest.approx(objective - 0.26)
        assert result.rewards[f"{event.agent_id}/{event.data['task']}"] == objective
    with_feedback = [text for text in seen if "Previous episode feedback:" in text]
    assert len(with_feedback) == 3 and all("(third)" in text for text in with_feedback)
    for text in with_feedback:
        payload = json.loads(text.split("Previous episode feedback: ", 1)[1])
        assert set(payload) == {"task", "source", "components"} and payload["task"] == "first"
        assert payload["components"]["completion_tokens"] == 6
    assert sum(event.kind == "learning.feedback_delivered" for event in events) == 3


async def test_bounded_history_uses_exact_previous_native_tools_with_fresh_sessions(world, monkeypatch):
    manifest, components, root = world
    monkeypatch.setattr("mesocosm.core.run.EVENT_GRACE_SECONDS", 0)
    path = three_tasks(manifest, root)
    manifest.history = HistorySpec(episodes=1, scope="actions")
    native_messages = OpenCodeClient.list_messages

    async def identified_tools(self, session_id):
        messages = await native_messages(self, session_id)
        for message in messages:
            if message.get("info", {}).get("role") != "assistant":
                continue
            for index, part in enumerate(message["parts"]):
                part["id"] = f"{message['info']['id']}-part-{index}"
                if part.get("type") == "tool":
                    part["callID"] = f"{message['info']['id']}-call-{index}"
                    part["state"].update(input={"command": "cat result.txt"}, output=f"record from {session_id}")
        return messages

    monkeypatch.setattr(OpenCodeClient, "list_messages", identified_tools)
    seen = []
    components.hooks["on_message"] = lambda body: seen.append(body["parts"][0]["text"])
    result = await asyncio.wait_for(run_world(manifest, path, components(), root / "runs"), 5)
    assert result.status == "ok", result.error
    bundle = RunBundle.open(result.bundle_path)
    events = bundle.events()
    sessions = [event for event in events if event.kind == "harness.session"]
    assert len(sessions) == len(seen) == 9
    for agent in AGENTS:
        assert len({event.data["session_id"] for event in sessions if event.agent_id == agent}) == 3
    attached = [event for event in events if event.kind == "history.attached"]
    assert len(attached) == 6
    assert sum(event.kind == "history.delivered" for event in events) == 6
    for event in attached:
        previous = "first" if event.data["task"] == "second" else "second"
        assert [episode["task"] for episode in event.data["episodes"]] == [previous]
        saved = (bundle.root / event.data["path"]).read_text()
        assert hashlib.sha256(saved.encode()).hexdigest() == event.data["payload_sha256"]
        payload = json.loads(saved.split("Previous completed task records:\n", 1)[1])
        assert payload["episodes"][0]["task"] == previous
        assert len(payload["episodes"][0]["records"]) == 1
        assert payload["episodes"][0]["records"][0]["tool"] == "bash"
        assert "Episode incentive" not in saved, "replayed history must not recursively include prior user prompts"


async def test_three_agent_random_review_assignments_survive_service_restart_and_reject_other_targets(tmp_path):
    config = settings(tmp_path)
    config.agent_ips["172.30.0.13"] = "agent-3"
    config.chat = {"channels": [], "max_chars": None, "max_messages": None}
    config.peer_review = {"protocol": "ratings", "topology": "random_ring"}
    assignment = review_assignment(AGENTS, "random_ring", 42, 0)
    app = create_app(config)
    async with client(app, "172.30.0.4", "172.30.0.3", api_key="test-control") as control:
        response = await control.post("/admin/epoch", json={"epoch": "first", "assignments": assignment})
        assert response.status_code == 200 and response.json()["assignments"] == assignment
    assigned = assignment["agent-1"][0]
    unassigned = next(agent for agent in AGENTS if agent not in ("agent-1", assigned))
    for current in (app, create_app(config)):
        async with client(current, "172.30.0.11", "chat") as agent:
            denied = await agent.post("/api/verdicts", json={"about": unassigned, "verdict": "accept"})
            assert denied.status_code == 403 and "not assigned" in denied.text
    async with client(create_app(config), "172.30.0.11", "chat") as agent:
        accepted = await agent.post("/api/verdicts", json={"about": assigned, "verdict": "accept"})
        assert accepted.status_code == 201
    async with client(create_app(config), "172.30.0.99", "chat") as outsider:
        rejected = await outsider.post("/api/verdicts", json={"about": assigned, "verdict": "accept"},
                                       headers={"X-Forwarded-For": "172.30.0.11"})
        assert rejected.status_code == 403
    events = log_lines(config.log_path)
    denied = [event for event in events if event.get("op") == "verdict_rejected"]
    assert len(denied) == 3 and all(event["reason"] == "unassigned" for event in denied)
    assert denied[-1]["agent"] == "unknown:172.30.0.99"


async def test_review_assignment_retry_cannot_change_a_started_epoch(tmp_path):
    config = settings(tmp_path)
    config.agent_ips["172.30.0.13"] = "agent-3"
    config.chat = {"channels": []}
    config.peer_review = {"protocol": "ratings", "topology": "random_ring"}
    assignment = review_assignment(AGENTS, "random_ring", 42, 0)
    changed = {sender: [next(agent for agent in AGENTS if agent != sender and agent not in targets)]
               for sender, targets in assignment.items()}
    async with client(create_app(config), "172.30.0.4", "services", api_key="test-control") as control:
        assert (await control.post("/admin/epoch", json={"epoch": "first", "assignments": assignment})).status_code == 200
    # Retrying after restart must keep the same assignment; neither a different mapping nor
    # an omitted one may silently remove the condition for the already-open epoch.
    async with client(create_app(config), "172.30.0.4", "services", api_key="test-control") as control:
        retry = await control.post("/admin/epoch", json={"epoch": "first", "assignments": assignment})
        assert retry.status_code == 200 and retry.json()["assignments"] == assignment
        for payload in ({"epoch": "first", "assignments": changed}, {"epoch": "first"}):
            assert (await control.post("/admin/epoch", json=payload)).status_code == 409
        next_epoch = await control.post("/admin/epoch", json={"epoch": "second", "assignments": changed})
        assert next_epoch.status_code == 200 and next_epoch.json()["assignments"] == changed


@pytest.mark.parametrize("eligibility", ["completed", "passing"])
async def test_artifact_publication_uses_preverifier_snapshot_and_original_eligibility_without_input_changes(
        world, monkeypatch, eligibility):
    manifest, components, root = world
    monkeypatch.setattr("mesocosm.core.run.EVENT_GRACE_SECONDS", 0)
    path = three_tasks(manifest, root)
    manifest.artifacts = ArtifactSpec(enabled=True, paths=["hello.txt", "verifier-only.txt"],
                                      eligibility=eligibility, success_threshold=0.75 if eligibility == "passing" else 1.0,
                                      show_author=False, show_score=False)
    source_inputs = {}
    for task in manifest.tasks:
        directory = root / task.path
        content = f"Original input for {directory.name}\r\n".encode()
        (directory / "task-input.txt").write_bytes(content)
        source_inputs[directory.name] = content
    original_instructions = {task.path: (root / task.path / "instruction.md").read_bytes() for task in manifest.tasks}

    class NativeInputs:
        """Fixture native preparation: declared task input bytes are copied without artifact injection."""

        async def prepare(self, agent, task, workspace, *, reset, restored=False):
            assert reset and not restored
            (workspace / "task-input.txt").write_bytes((task.path / "task-input.txt").read_bytes())
            return {"workspace": "/workspace", "test_fixture": True}

    class MutatingVerifier(OriginalScores):
        async def verify_with_artifacts(self, task, workspace, logs_dir, spec, *, on_capture):
            # Model the native verifier's boundary contract explicitly. The real pause and
            # grading-copy guarantees are covered by the native-runtime adapter tests.
            captured = capture_artifacts(workspace, spec)
            captured["capture_evidence"] = {
                "boundary": "quiesced_before_original_verifier", "task": task.name,
                "agent_id": workspace.name, "container_id": f"fixture-{workspace.name}",
                "workspace": str(workspace), "native_workdir": "/workspace", "test_fixture": True}
            on_capture(captured)
            return await self.verify(task, workspace, logs_dir)

        async def verify(self, task, workspace, logs_dir, script="test.sh"):
            assert (workspace / "task-input.txt").read_bytes() == source_inputs[task.name]
            # Deliberately create verification output in the fixture workspace: these bytes
            # must never replace or enter the already-captured agent artifact.
            assert (workspace / "hello.txt").read_bytes() == b"Hello, team!\n"
            (workspace / "hello.txt").write_text("VERIFIER MUTATION\n")
            (workspace / "verifier-only.txt").write_text("not produced by the agent\n")
            return await super().verify(task, workspace, logs_dir, script)

    config = settings(root / "services", repos=())
    config.agent_ips["172.30.0.13"] = "agent-3"
    app = create_app(config)
    runtime = components()
    runtime.services = ServicesAdmin("http://services", "test-control",
                                    transport=httpx.ASGITransport(app=app, client=("172.30.0.4", 12345)))
    runtime.services_log = config.log_path
    runtime.task_runtime = NativeInputs()
    runtime.verifier = MutatingVerifier(scores())
    original_harness = runtime.harness_for
    retrievals = []

    def harness_for(agent):
        harness = original_harness(agent)
        native_send = harness.send_message

        async def send_with_optional_artifact_read(session_id, text, **kwargs):
            workspace = runtime.workspace_for(agent)
            original = (workspace / "task-input.txt").read_bytes()
            ip = next(ip for ip, member in config.agent_ips.items() if member == agent.id)
            async with client(app, ip, "board") as reader:
                available = (await reader.get("/api/artifacts")).json()["artifacts"]
                if available:
                    artifact = available[0]
                    response = await reader.get(f'/api/artifacts/{artifact["id"]}/files/hello.txt')
                    assert response.status_code == 200 and response.content == b"Hello, team!\n"
                    retrievals.append((agent.id, artifact["source_task"]))
                    assert "author" not in artifact and "source_agent" not in artifact and "score" not in artifact
            assert (workspace / "task-input.txt").read_bytes() == original
            return await native_send(session_id, text, **kwargs)

        harness.send_message = send_with_optional_artifact_read
        return harness

    runtime.harness_for = harness_for
    try:
        result = await asyncio.wait_for(run_world(manifest, path, runtime, root / "runs"), 5)
    finally:
        await runtime.services.aclose()
    assert result.status == "ok", result.error
    bundle = RunBundle.open(result.bundle_path)
    events = bundle.events()
    captured = [event for event in events if event.kind == "artifacts.captured"]
    published = [event for event in events if event.kind == "artifacts.published"]
    withheld = [event for event in events if event.kind == "artifacts.withheld"]
    expected = {(agent, task) for task, values in scores().items() for agent, value in values.items()
                if eligibility == "completed" or value >= 0.75}
    assert len(captured) == 9 and len(published) + len(withheld) == 9
    assert {(event.agent_id, event.data["task"]) for event in published} == expected
    assert len(retrievals) == 6, "later task turns can voluntarily read prior published artifacts"
    for event in captured:
        snapshot = json.loads((bundle.root / event.data["path"]).read_text())
        assert snapshot["files"]["hello.txt"]["text"] == "Hello, team!\n"
        assert snapshot["missing"] == ["verifier-only.txt"]
        assert snapshot["capture_evidence"]["boundary"] == "quiesced_before_original_verifier"
        assert snapshot["capture_evidence"]["test_fixture"] is True
        key = (event.agent_id, event.data["task"])
        measured = next(item for item in events if item.kind == "verifier.reward"
                        and (item.agent_id, item.data["task"]) == key)
        assert events.index(event) < events.index(measured)
    for event in published:
        artifact = event.data["publication"]["artifact"]
        assert artifact["files"]["hello.txt"]["text"] == "Hello, team!\n"
        assert artifact["score"] == scores()[event.data["task"]][event.agent_id]
        assert artifact["missing"] == ["verifier-only.txt"]
    for task in manifest.tasks:
        directory = root / task.path
        assert (directory / "instruction.md").read_bytes() == original_instructions[task.path]
        assert (directory / "task-input.txt").read_bytes() == source_inputs[directory.name]
    for agent in AGENTS:
        assert (root / "ws" / agent / "task-input.txt").read_bytes() == source_inputs["third"]
    records = [json.loads(line) for line in (config.data / "artifacts.jsonl").read_text().splitlines()]
    assert {(record["source_agent"], record["source_task"]) for record in records} == expected
    async with client(app, "172.30.0.4", "services", api_key="test-control") as control:
        state = (await control.get("/admin/state/world")).json()
    assert state["artifacts"] == records, "shared-state monitor evidence retains exact content and attribution"


@pytest.mark.parametrize("capture_supported", [False, True])
async def test_artifact_publication_requires_capture_contract_and_successful_original_grading(
        world, monkeypatch, capture_supported):
    manifest, components, root = world
    monkeypatch.setattr("mesocosm.core.run.EVENT_GRACE_SECONDS", 0)
    path = three_tasks(manifest, root)
    manifest.artifacts = ArtifactSpec(enabled=True, paths=["hello.txt"])

    class UnavailableGrader:
        async def verify(self, task, workspace, logs_dir, script="test.sh"):
            raise RuntimeError("original grader unavailable")

    class SupportedCapture(UnavailableGrader):
        async def verify_with_artifacts(self, task, workspace, logs_dir, spec, *, on_capture):
            captured = capture_artifacts(workspace, spec)
            captured["capture_evidence"] = {"boundary": "quiesced_before_original_verifier",
                                            "task": task.name, "agent_id": workspace.name,
                                            "test_fixture": True}
            on_capture(captured)
            return await self.verify(task, workspace, logs_dir)

    config = settings(root / "services", repos=())
    config.agent_ips["172.30.0.13"] = "agent-3"
    app = create_app(config)
    runtime = components()
    runtime.services = ServicesAdmin("http://services", "test-control",
                                    transport=httpx.ASGITransport(app=app, client=("172.30.0.4", 12345)))
    runtime.services_log = config.log_path
    runtime.verifier = SupportedCapture() if capture_supported else UnavailableGrader()
    try:
        result = await asyncio.wait_for(run_world(manifest, path, runtime, root / "runs"), 5)
    finally:
        await runtime.services.aclose()
    assert result.status == ("failed_verifier" if capture_supported else "failed") and not result.rewards
    if capture_supported:
        assert "original grader unavailable" in result.error
    else:
        assert "rejected_unsupported" in result.error and "artifact" in result.error
    bundle = RunBundle.open(result.bundle_path)
    events = bundle.events()
    captures = [event for event in events if event.kind == "artifacts.captured"]
    assert bool(captures) is capture_supported
    if not capture_supported:
        assert not any(event.kind == "harness.session" for event in events)
    assert not any(event.kind in {"verifier.reward", "artifacts.published"} for event in events)
    for event in captures:
        saved = json.loads((bundle.root / event.data["path"]).read_text())
        assert saved["files"]["hello.txt"]["text"] == "Hello, team!\n"
    assert not (config.data / "artifacts.jsonl").exists()
