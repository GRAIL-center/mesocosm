import json
import pathlib

import pytest

from mesocosm.adapters.harbor.task import adaptation, load_task, read_reward, verifier_command

TASK = pathlib.Path(__file__).resolve().parents[1] / "examples" / "tasks" / "hello-world"


def test_load_task_and_provenance():
    task = load_task(TASK)
    assert task.name == "hello-world"
    assert task.has_environment and task.has_tests and not task.has_solution
    assert "hello.txt" in task.instruction
    prov = task.provenance()
    assert prov["source"] == "harbor" and len(prov["instruction_sha256"]) == 64
    assert prov["task_toml_sha256"] and prov["test_sh_sha256"]
    assert task.config["verifier"]["timeout_sec"] == 60


def test_load_task_rejects_non_task_dir(tmp_path):
    with pytest.raises(FileNotFoundError):
        load_task(tmp_path)


def test_adaptation_marks_non_benchmark_conditions():
    assert adaptation(mode="comparison", memory="reset", budget=None) == {"adapted": False, "reasons": []}
    marked = adaptation(mode="persistent", memory="carry", budget={"completion_tokens": 10}, shared_workspace=True)
    assert marked["adapted"] and marked["reasons"] == ["persistent_mode", "memory_carried", "budget_limited", "shared_workspace"]
    assert adaptation(mode="comparison", memory="reset", budget={"completion_tokens": None})["adapted"] is False


def test_read_reward_txt_json_and_missing(tmp_path):
    v = tmp_path / "verifier"
    v.mkdir()
    (v / "reward.txt").write_text("1\n")
    assert read_reward(tmp_path).value == 1.0 and read_reward(tmp_path).source == "reward.txt"
    (v / "reward.txt").write_text("not-a-number")
    assert read_reward(tmp_path).value is None
    (v / "reward.txt").unlink()
    (v / "reward.json").write_text(json.dumps({"reward": 0.5, "tests_passed": 3}))
    r = read_reward(tmp_path)
    assert r.value == 0.5 and r.labels == {"tests_passed": 3} and r.source == "reward.json"
    (v / "reward.json").unlink()
    assert read_reward(tmp_path).source is None


def test_verifier_command_has_no_network_and_mounts_tests_read_only(tmp_path):
    task = load_task(TASK)
    cmd = verifier_command(task, workspace=tmp_path / "ws", logs_dir=tmp_path / "logs")
    assert cmd[:4] == ["docker", "run", "--rm", "--network"] and cmd[4] == "none"
    assert any(v.endswith("/tests:/tests:ro") for v in cmd)
    assert cmd[-4:] == ["timeout", "60", "sh", "/tests/test.sh"]
