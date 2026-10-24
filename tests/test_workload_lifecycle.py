"""Docker response fixtures test lifecycle control logic, not real container isolation acceptance."""

from __future__ import annotations

import asyncio
import copy
import json
import pathlib

import pytest

from mesocosm.env.lifecycle import container_evidence, isolation_evidence
from mesocosm.env.stack import DockerStack, StackCleanupError

IDS = {f"agent-{i}": str(i) * 64 for i in range(1, 4)}


def inspected(agent_id="agent-1", *, running=True):
    return {"Id": IDS[agent_id], "Config": {"Labels": {
        "com.docker.compose.project": "world", "com.docker.compose.service": agent_id}},
        "State": {"Running": running, "Restarting": False, "Pid": 100 if running else 0,
                  "StartedAt": "start", "FinishedAt": "" if running else "finish", "ExitCode": 0 if running else 137},
        "HostConfig": {"PidMode": "", "Privileged": False, "NetworkMode": "world_sandbox",
                       "RestartPolicy": {"Name": "no"}, "Runtime": "runc", "CapAdd": None}}


def evidence(raw=None):
    return container_evidence(raw or inspected(), IDS["agent-1"], "world", "agent-1")


def namespace(container=None, *, boot="boot-id", own="pid:[2]", init="pid:[2]", host="pid:[1]", host_init="pid:[2]"):
    return isolation_evidence(container or evidence(), f"{own}\n{init}\n{boot}\n", host_boot_id="boot-id",
                              host_pid_namespace=host, init_pid_namespace=host_init)


def test_process_namespace_evidence_distinguishes_standard_shared_and_unverified():
    assert namespace()["status"] == "supported"
    shared = namespace(own="pid:[1]", init="pid:[1]", host_init="pid:[1]")
    assert shared["status"] == "unsupported" and shared["private_pid_namespace"] is False
    for missing in (namespace(boot="other-host"), namespace(host=None), namespace(host_init=None),
                    namespace(init="pid:[3]")):
        assert missing["status"] == "unverified" and missing["private_pid_namespace"] is None


@pytest.mark.parametrize(("field", "value"), [("PidMode", "host"), ("Privileged", True), ("NetworkMode", "host"),
                                             ("NetworkMode", "container:other"), ("CapAdd", ["SYS_ADMIN"]),
                                             ("RestartPolicy", {"Name": "always"})])
def test_unsafe_inspection_configuration_is_not_supported(field, value):
    raw = inspected()
    raw["HostConfig"][field] = value
    assert namespace(evidence(raw))["status"] == "unsupported"


@pytest.mark.parametrize("bad", ["id", "project", "service", "state"])
def test_container_evidence_rejects_wrong_ownership_and_missing_state(bad):
    raw = inspected()
    if bad == "id":
        raw["Id"] = IDS["agent-2"]
    elif bad in ("project", "service"):
        raw["Config"]["Labels"][f"com.docker.compose.{bad}"] = "unowned"
    else:
        raw["State"].pop("Running")
    with pytest.raises(ValueError):
        evidence(raw)


@pytest.fixture
def stack(tmp_path):
    path = tmp_path / "compose.yaml"
    path.write_text("name: world\nservices:\n  agent-1: {}\n  agent-2: {}\n  agent-3: {}\n")
    return DockerStack(path)


def docker_fixture(stack, monkeypatch, *, cached=True):
    states = {agent_id: inspected(agent_id) for agent_id in IDS}
    commands = []

    async def docker(*args, **kwargs):
        commands.append(args)
        if args[:4] == ("compose", "-f", stack.compose_file, "ps"):
            return IDS[args[-1]] + "\n"
        agent_id = next(agent_id for agent_id, container_id in IDS.items() if container_id == args[-1])
        if args[:2] == ("container", "inspect"):
            return json.dumps([states[agent_id]])
        assert args[:4] == ("container", "kill", "--signal", "KILL")
        states[agent_id] = inspected(agent_id, running=False)
        return args[-1] + "\n"

    monkeypatch.setattr(stack, "_docker", docker)
    if cached:
        for agent_id, container_id in IDS.items():
            state = container_evidence(states[agent_id], container_id, "world", agent_id)
            stack._isolation[container_id] = namespace(state)
    return states, commands, docker


async def test_three_owned_workloads_use_exact_container_ids_and_independent_post_kill_inspection(stack, monkeypatch):
    _, commands, _ = docker_fixture(stack, monkeypatch)
    results = await stack.terminate(list(IDS))
    assert set(results) == set(IDS)
    assert all(result["confirmed"] and result["container_stopped"] and result["workload_terminated"]
               and result["signal_acknowledged"] for result in results.values())
    killed = [cmd[-1] for cmd in commands if cmd[:2] == ("container", "kill")]
    assert set(killed) == set(IDS.values())
    for container_id in IDS.values():
        assert commands.count(("container", "inspect", container_id)) == 2


async def test_missing_pre_action_evidence_does_not_turn_stopped_container_into_workload_guarantee(stack, monkeypatch):
    _, commands, _ = docker_fixture(stack, monkeypatch, cached=False)
    result = (await stack.terminate(["agent-1"]))["agent-1"]
    assert result["container_stopped"] is True and result["signal_acknowledged"] is True
    assert result["confirmed"] is False and result["workload_terminated"] is None
    assert result["status"] == "unknown"
    assert not any(command[:2] == ("container", "exec") for command in commands)


async def test_pre_action_probe_is_bound_to_exact_container_execution(stack, monkeypatch):
    states, _, original = docker_fixture(stack, monkeypatch, cached=False)
    read_text = pathlib.Path.read_text
    probes = []

    def read(path, *args, **kwargs):
        return "boot-id" if str(path) == "/proc/sys/kernel/random/boot_id" else read_text(path, *args, **kwargs)

    async def docker(*args, **kwargs):
        if args[:2] == ("container", "exec"):
            probes.append(args)
            return "pid:[2]\npid:[2]\nboot-id\n"
        return await original(*args, **kwargs)

    monkeypatch.setattr(pathlib.Path, "read_text", read)
    monkeypatch.setattr("mesocosm.env.stack.os.readlink", lambda path: "pid:[1]" if path == "/proc/self/ns/pid" else "pid:[2]")
    monkeypatch.setattr(stack, "_docker", docker)
    proof = (await stack.isolation(["agent-1"]))["agent-1"]
    assert proof["status"] == "supported" and proof["container_id"] == IDS["agent-1"]
    assert len(probes) == 1
    states["agent-1"]["State"]["StartedAt"] = "another-execution"
    result = (await stack.terminate(["agent-1"]))["agent-1"]
    assert result["confirmed"] is False
    assert result["isolation"]["reason"] == "no matching pre-action isolation probe"
    assert len(probes) == 1  # Termination cannot manufacture a late pre-action proof.


async def test_colab_shared_pid_evidence_remains_unconfirmed_after_docker_reports_stopped(stack, monkeypatch):
    docker_fixture(stack, monkeypatch)
    stack._isolation[IDS["agent-1"]] = namespace(own="pid:[1]", init="pid:[1]", host_init="pid:[1]")
    result = (await stack.terminate(["agent-1"]))["agent-1"]
    assert result["container_stopped"] is True
    assert result["workload_terminated"] is None and result["confirmed"] is False


async def test_wrong_project_never_receives_a_kill(stack, monkeypatch):
    states, commands, _ = docker_fixture(stack, monkeypatch)
    states["agent-1"]["Config"]["Labels"]["com.docker.compose.project"] = "unowned"
    result = (await stack.terminate(["agent-1"]))["agent-1"]
    assert result["status"] == "unknown" and "labels" in result["error"]
    assert not any(command[:2] == ("container", "kill") for command in commands)


async def test_restart_during_kill_is_not_mistaken_for_termination(stack, monkeypatch):
    _, _, original = docker_fixture(stack, monkeypatch)

    async def docker(*args, **kwargs):
        output = await original(*args, **kwargs)
        if args[:2] == ("container", "inspect"):
            raw = json.loads(output)
            if not raw[0]["State"]["Running"]:
                raw[0]["State"] = {**inspected()["State"], "StartedAt": "replacement-start"}
            return json.dumps(raw)
        return output

    monkeypatch.setattr(stack, "_docker", docker)
    result = (await stack.terminate(["agent-1"]))["agent-1"]
    assert result["container_stopped"] is False and result["confirmed"] is False
    assert result["after"]["started_at"] == "replacement-start"


async def test_kill_error_can_be_resolved_only_by_stopped_inspection(stack, monkeypatch):
    _, _, original = docker_fixture(stack, monkeypatch)

    async def docker(*args, **kwargs):
        result = await original(*args, **kwargs)
        if args[:2] == ("container", "kill"):
            raise RuntimeError("container is not running")
        return result

    monkeypatch.setattr(stack, "_docker", docker)
    result = (await stack.terminate(["agent-1"]))["agent-1"]
    assert result["confirmed"] and result["container_stopped"]
    assert result["signal_acknowledged"] is False and "signal_error" in result


async def test_kill_timeout_is_bounded_and_retains_before_evidence(stack, monkeypatch):
    _, _, original = docker_fixture(stack, monkeypatch)

    async def docker(*args, **kwargs):
        if args[:2] == ("container", "kill"):
            await asyncio.sleep(10)
        return await original(*args, **kwargs)

    monkeypatch.setattr(stack, "_docker", docker)
    result = (await asyncio.wait_for(stack.terminate(["agent-1"], timeout=0.02), 1))["agent-1"]
    assert result["before"]["running"] is True and result["container_stopped"] is None
    assert result["confirmed"] is False and "TimeoutError" in result["error"]


async def test_kill_acknowledgment_without_exit_is_not_success(stack, monkeypatch):
    states, _, original = docker_fixture(stack, monkeypatch)
    running = copy.deepcopy(states["agent-1"])

    async def docker(*args, **kwargs):
        output = await original(*args, **kwargs)
        if args[:2] == ("container", "kill"):
            states["agent-1"] = running
        return output

    monkeypatch.setattr(stack, "_docker", docker)
    result = (await stack.terminate(["agent-1"], timeout=0.03))["agent-1"]
    assert result["signal_acknowledged"] is True and result["container_stopped"] is False
    assert result["confirmed"] is False and "TimeoutError" in result["error"]


@pytest.mark.parametrize("remaining", ["", IDS["agent-1"]])
async def test_down_requires_empty_project_inventory(stack, monkeypatch, remaining):
    commands = []

    async def compose(*args, **kwargs):
        commands.append(args)
        return remaining if args[0] == "ps" else "removed"

    monkeypatch.setattr(stack, "_compose", compose)
    if remaining:
        with pytest.raises(StackCleanupError, match="containers remain"):
            await stack.down(timeout=1)
    else:
        assert await stack.down(timeout=1) == "removed"
    assert commands == [("down", "-v", "--timeout", "10"), ("ps", "--all", "--quiet")]
    assert stack.cleanup_evidence["containers_removed"] is (not remaining)
