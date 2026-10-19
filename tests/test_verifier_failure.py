"""A verifier process or malformed output cannot become a successful measured run."""

from __future__ import annotations

import pytest

from mesocosm.adapters.harbor.task import load_task
from mesocosm.core.run import DockerVerifier


@pytest.mark.parametrize("exit_code,reward", [(125, "1"), (124, "0"), (0, None), (0, "nan")])
async def test_failed_verifier_does_not_return_a_reward(tmp_path, monkeypatch, exit_code, reward):
    task_dir = tmp_path / "task"
    (task_dir / "tests").mkdir(parents=True)
    (task_dir / "instruction.md").write_text("Verifier regression fixture\n")
    (task_dir / "tests" / "test.sh").write_text("exit 0\n")
    logs = tmp_path / "logs"
    (logs / "verifier").mkdir(parents=True)
    if reward is not None:
        (logs / "verifier" / "reward.txt").write_text(reward)

    class Process:
        returncode = exit_code

        async def communicate(self):
            return b"native verifier failed\n", None

    async def start(*args, **kwargs):
        return Process()

    monkeypatch.setattr("mesocosm.core.run.asyncio.create_subprocess_exec", start)
    with pytest.raises(RuntimeError, match="verifier"):
        await DockerVerifier(image="native-task:revision").verify(load_task(task_dir), tmp_path / "ws", logs)
    assert (logs / "verifier.log").read_text() == "native verifier failed\n"


async def test_verifier_requires_an_explicit_environment_image(tmp_path):
    task_dir = tmp_path / "task"
    (task_dir / "tests").mkdir(parents=True)
    (task_dir / "instruction.md").write_text("Verifier regression fixture\n")
    (task_dir / "tests" / "test.sh").write_text("exit 0\n")
    with pytest.raises(RuntimeError, match="image"):
        await DockerVerifier().verify(load_task(task_dir), tmp_path / "ws", tmp_path / "logs")
