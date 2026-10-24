"""Synthetic three-agent plumbing verifies control flow, not native agent/container acceptance."""

from __future__ import annotations

import asyncio

import pytest
from test_run_e2e import MANIFEST
from test_run_e2e import world as world

from mesocosm.core.control import STOP_MARKER
from mesocosm.core.manifest import TaskSpec
from mesocosm.core.records import RunBundle
from mesocosm.core.rewards import EpisodeStopped
from mesocosm.core.run import _interruptible_turn, run_world


class Workloads:
    def __init__(self, status="supported"):
        self.status = status
        self.probed = []
        self.terminated = []

    async def isolation(self, agent_ids):
        self.probed.extend(agent_ids)
        return {agent_id: {"agent_id": agent_id, "status": self.status,
                           "private_pid_namespace": self.status == "supported",
                           "method": "synthetic_response_fixture"} for agent_id in agent_ids}

    async def terminate(self, agent_ids):
        self.terminated.extend(agent_ids)
        return {agent_id: {"agent_id": agent_id, "status": "terminated" if self.status == "supported" else "unknown",
                           "confirmed": self.status == "supported", "container_stopped": True,
                           "workload_terminated": True if self.status == "supported" else None,
                           "method": "synthetic_response_fixture"} for agent_id in agent_ids}


def three_agents(manifest):
    for index in (2, 3):
        manifest.agents.append(manifest.agents[0].model_copy(
            update={"id": f"agent-{index}", "url": f"http://127.0.0.1:{4095 + index}"}))
    return {agent.id for agent in manifest.agents}


async def test_stop_marker_cancels_an_active_operation_instead_of_waiting_for_next_task(tmp_path):
    bundle = RunBundle.create(tmp_path / "runs", "stop-plumbing")
    entered, cancelled = asyncio.Event(), asyncio.Event()

    async def active():
        entered.set()
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    operation = asyncio.create_task(_interruptible_turn(active(), bundle))
    await asyncio.wait_for(entered.wait(), 1)
    marker = bundle.root / STOP_MARKER
    marker.parent.mkdir(parents=True)
    marker.write_text("{}")
    with pytest.raises(EpisodeStopped, match="during active turn"):
        await asyncio.wait_for(operation, 1)
    assert cancelled.is_set()


async def test_stop_during_three_active_turns_aborts_sessions_and_records_workload_termination(world):
    manifest, components, root = world
    agents = three_agents(manifest)
    comps = components()
    workloads = comps.workloads = Workloads()
    original = comps.harness_for
    ready, active, aborted, cancelled = asyncio.Event(), set(), set(), set()

    def harness_for(agent):
        harness = original(agent)

        async def send(session_id, text, **kwargs):
            active.add(agent.id)
            if active == agents:
                ready.set()
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.add(agent.id)

        async def abort(session_id):
            aborted.add(agent.id)
            return True

        harness.send_message, harness.abort_session = send, abort
        return harness

    comps.harness_for = harness_for
    run = asyncio.create_task(run_world(manifest, MANIFEST, comps, root / "runs", "stop-three"))
    await asyncio.wait_for(ready.wait(), 2)
    marker = root / "runs" / "stop-three" / STOP_MARKER
    marker.parent.mkdir(parents=True)
    marker.write_text("{}")
    result = await asyncio.wait_for(run, 3)
    assert result.status == "stopped"
    assert active == aborted == cancelled == set(workloads.terminated) == agents
    events = RunBundle.open(result.bundle_path).events()
    assert {event.agent_id for event in events if event.kind == "workload.termination"} == agents
    assert not any(event.kind == "verifier.reward" for event in events)


@pytest.mark.parametrize("slow_phase", ["abort", "cancel_cleanup"])
async def test_deadline_terminates_workloads_before_waiting_for_slow_harness_cleanup(world, slow_phase):
    manifest, components, root = world
    agents = three_agents(manifest)
    task = root / "timed-plumbing"
    task.mkdir()
    (task / "instruction.md").write_text("Synthetic deadline control fixture.")
    (task / "task.toml").write_text("[agent]\ntimeout_sec = 0.5\n")
    manifest.tasks = [TaskSpec(path=str(task))]
    comps = components()
    original = comps.harness_for
    all_active, all_terminated, release_cleanup = asyncio.Event(), asyncio.Event(), asyncio.Event()
    active = set()

    class ObservedWorkloads(Workloads):
        async def terminate(self, agent_ids):
            result = await super().terminate(agent_ids)
            if set(self.terminated) == agents:
                all_terminated.set()
            return result

    workloads = comps.workloads = ObservedWorkloads()

    def harness_for(agent):
        harness = original(agent)

        async def send(session_id, text, **kwargs):
            active.add(agent.id)
            if active == agents:
                all_active.set()
            try:
                await asyncio.Event().wait()
            finally:
                if slow_phase == "cancel_cleanup":
                    await release_cleanup.wait()

        async def abort(session_id):
            if slow_phase == "abort":
                await release_cleanup.wait()
            return True

        harness.send_message, harness.abort_session = send, abort
        return harness

    comps.harness_for = harness_for
    run = asyncio.create_task(run_world(manifest, root / "world.yaml", comps, root / "runs"))
    try:
        await asyncio.wait_for(all_active.wait(), 2)
        await asyncio.wait_for(all_terminated.wait(), 2)
        assert not release_cleanup.is_set() and not run.done()
    finally:
        release_cleanup.set()
        result = await asyncio.wait_for(run, 3)
    assert result.status == "failed_deadline"
    assert set(workloads.terminated) == agents
    assert not any(event.kind == "verifier.reward" for event in RunBundle.open(result.bundle_path).events())


async def test_stop_interrupts_staggered_agents_while_another_agent_is_still_preparing(world):
    manifest, components, root = world
    agents = three_agents(manifest)
    manifest.tasks = [manifest.tasks[0].model_copy(update={"stagger_seconds": 30.0})]
    comps = components()
    workloads = comps.workloads = Workloads()
    preparation_entered, preparation_cancelled = asyncio.Event(), asyncio.Event()
    sent = []
    components.hooks["on_message"] = sent.append

    class Runtime:
        async def prepare(self, agent, task, workspace, **kwargs):
            if agent.id == "agent-1":
                preparation_entered.set()
                try:
                    await asyncio.Event().wait()
                finally:
                    preparation_cancelled.set()
            return {"fixture": True}

    comps.task_runtime = Runtime()
    run = asyncio.create_task(run_world(manifest, MANIFEST, comps, root / "runs", "stop-stagger"))
    await asyncio.wait_for(preparation_entered.wait(), 2)
    bundle = RunBundle.open(root / "runs" / "stop-stagger")
    try:
        async with asyncio.timeout(2):
            while len([event for event in bundle.events() if event.kind == "task.staggered"]) < 2:
                await asyncio.sleep(0.01)
        marker = bundle.root / STOP_MARKER
        marker.parent.mkdir(parents=True)
        marker.write_text("{}")
        result = await asyncio.wait_for(asyncio.shield(run), 2)
    finally:
        if not run.done():
            run.cancel()
            await asyncio.gather(run, return_exceptions=True)
    assert result.status == "stopped"
    assert preparation_cancelled.is_set() and set(workloads.terminated) == agents
    assert not sent
    assert not any(event.kind == "harness.reply" or event.kind == "verifier.reward" for event in bundle.events())


@pytest.mark.parametrize("failure", ["prepare", "transcript", "verifier"])
async def test_failure_outside_turn_closes_model_admission_and_terminates_all_three_workloads(world, failure):
    manifest, components, root = world
    agents = three_agents(manifest)
    comps = components()
    workloads = comps.workloads = Workloads()
    reason = f"original {failure} failure"

    if failure == "prepare":
        class Runtime:
            async def prepare(self, *args, **kwargs):
                raise RuntimeError(reason)
        comps.task_runtime = Runtime()
    elif failure == "transcript":
        original = comps.harness_for

        def harness_for(agent):
            harness = original(agent)

            async def messages(session_id):
                raise RuntimeError(reason)

            harness.list_messages = messages
            return harness

        comps.harness_for = harness_for
    else:
        class Verifier:
            async def verify(self, *args, **kwargs):
                raise RuntimeError(reason)
        comps.verifier = Verifier()

    result = await asyncio.wait_for(run_world(manifest, MANIFEST, comps, root / "runs"), 5)
    assert result.status == ("failed_verifier" if failure == "verifier" else "failed")
    assert reason in result.error
    assert set(workloads.terminated) == agents
    bundle = RunBundle.open(result.bundle_path)
    cleanup = [event for event in bundle.events() if event.kind == "workload.termination"]
    assert {event.agent_id for event in cleanup} == agents
    assert all(event.data["harness_abort_is_workload_termination"] is False for event in cleanup)
    assert not any(event.kind == "verifier.reward" for event in bundle.events())
    assert any(receipt["op"] == "run.stop" and receipt["result"] == "applied" for receipt in bundle.receipts())


@pytest.mark.parametrize("status", ["unsupported", "unverified"])
async def test_required_containment_refuses_before_any_of_three_agents_receives_a_task(world, status):
    manifest, components, root = world
    agents = three_agents(manifest)
    manifest.containment = "required"
    sent = []
    components.hooks["on_message"] = sent.append
    comps = components()
    workloads = comps.workloads = Workloads(status)
    result = await run_world(manifest, MANIFEST, comps, root / "runs")
    assert result.status == "failed" and "private workload containment not verified" in result.error
    assert not sent
    assert set(workloads.terminated) == agents
    events = RunBundle.open(result.bundle_path).events()
    probes = [event for event in events if event.kind == "workload.isolation"]
    assert probes and all(event.data["evidence"][event.agent_id]["status"] == status for event in probes)
    assert not any(event.kind == "harness.reply" or event.kind == "verifier.reward" for event in events)


async def test_adapted_mode_retains_unsupported_containment_evidence_for_all_three_agents(world):
    manifest, components, root = world
    agents = three_agents(manifest)
    manifest.containment = "adapted"
    comps = components()
    workloads = comps.workloads = Workloads("unsupported")
    result = await run_world(manifest, MANIFEST, comps, root / "runs")
    assert result.status == "ok"
    assert set(result.rewards) == {f"{agent_id}/hello-world" for agent_id in agents}
    bundle = RunBundle.open(result.bundle_path)
    assert bundle.manifest()["manifest"]["containment"] == "adapted"
    probes = [event for event in bundle.events() if event.kind == "workload.isolation"]
    assert {event.agent_id for event in probes} == agents
    assert all(event.data["evidence"][event.agent_id]["status"] == "unsupported" for event in probes)
    assert all(event.data["evidence"][event.agent_id]["private_pid_namespace"] is False for event in probes)
    assert not workloads.terminated
