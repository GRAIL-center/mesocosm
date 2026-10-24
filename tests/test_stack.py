"""Bounded Compose-client lifecycle; these tests launch local sleep processes, never Docker."""

from __future__ import annotations

import asyncio
import signal
import sys

import pytest

from mesocosm.env.stack import DockerStack, StackCleanupError


def substitute_process(monkeypatch, code):
    create = asyncio.create_subprocess_exec
    created = asyncio.Event()
    processes = []

    async def substitute(*args, **kwargs):
        assert args[:2] == ("docker", "compose")
        assert kwargs["start_new_session"] is True
        process = await create(sys.executable, "-c", code, **kwargs)
        processes.append(process)
        created.set()
        return process

    monkeypatch.setattr(asyncio, "create_subprocess_exec", substitute)
    return processes, created


async def test_teardown_timeout_kills_and_reaps_its_client_group_and_refuses_retry(tmp_path, monkeypatch):
    # A child inherits stdout: killing only the parent would leave communicate() waiting for its pipe.
    code = ("import subprocess, sys, time; "
            "subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)']); "
            "print('child started', flush=True); time.sleep(60)")
    processes, _ = substitute_process(monkeypatch, code)
    stack = DockerStack(tmp_path / "compose.yaml")
    with pytest.raises(StackCleanupError, match="timed out"):
        await asyncio.wait_for(stack.down(timeout=0.2), timeout=3)
    assert processes[0].returncode == -signal.SIGKILL
    assert stack.cleanup_error is not None
    with pytest.raises(StackCleanupError, match="world state is unknown"):
        await stack.down(timeout=0.2)
    assert len(processes) == 1


async def test_cancelled_teardown_reaps_client_and_marks_world_state_unknown(tmp_path, monkeypatch):
    processes, created = substitute_process(monkeypatch, "import time; time.sleep(60)")
    stack = DockerStack(tmp_path / "compose.yaml")
    task = asyncio.create_task(stack.down())
    await created.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, timeout=3)
    assert processes[0].returncode == -signal.SIGKILL
    assert stack.cleanup_error is not None
    assert "cancelled" in str(stack.cleanup_error)


async def test_nonzero_teardown_is_a_lifecycle_error_with_diagnostics(tmp_path, monkeypatch):
    substitute_process(monkeypatch, "import sys; print('daemon refused removal'); sys.exit(3)")
    stack = DockerStack(tmp_path / "compose.yaml")
    with pytest.raises(StackCleanupError, match="daemon refused removal") as exc:
        await stack.down(timeout=1)
    assert exc.value.compose_file == str(tmp_path / "compose.yaml")


async def test_native_runtime_companion_cleans_dynamic_services_and_volumes(tmp_path, monkeypatch):
    import yaml

    base = tmp_path / "compose.yaml"
    companion = tmp_path / "compose.yaml.runtime.yaml"
    base.write_text(yaml.safe_dump({"name": "world", "services": {"agent-1": {}}}))
    companion.write_text(yaml.safe_dump({"name": "world", "services": {"agent-1": {}, "task-db": {}},
                                         "volumes": {"task-state": {}}}))
    stack = DockerStack(base)
    calls = []

    async def docker(*args, **kwargs):
        calls.append(args)
        return "" if "ps" in args else "removed"

    monkeypatch.setattr(stack, "_docker", docker)
    assert await stack.down() == "removed"
    assert all(args[2] == str(companion) for args in calls)
    assert "--remove-orphans" in calls[0] and "-v" in calls[0]
    assert not companion.exists()


async def test_cleanup_companion_cannot_select_another_project(tmp_path, monkeypatch):
    import yaml

    base = tmp_path / "compose.yaml"
    base.write_text(yaml.safe_dump({"name": "world", "services": {}}))
    (tmp_path / "compose.yaml.runtime.yaml").write_text(yaml.safe_dump({"name": "other-world", "services": {}}))
    stack = DockerStack(base)
    with pytest.raises(StackCleanupError, match="different Compose project"):
        await stack.down()
