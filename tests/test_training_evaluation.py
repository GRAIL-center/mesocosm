"""Held-out controller orchestration with explicit fake runtime/optimizer fixtures."""

from __future__ import annotations

import json
import shutil

import pytest
import yaml
from test_training_controller import setup

from mesocosm.learning.controller import run_training


def heldout_setup(tmp_path, monkeypatch, **kwargs):
    configured, path, out, run_one, calls, worlds = setup(tmp_path, monkeypatch, **kwargs)
    for name in ("train-task", "heldout-task"):
        directory = tmp_path / name
        directory.mkdir()
        (directory / "instruction.md").write_text(f"Unit controller fixture: {name}")
    data = yaml.safe_load((tmp_path / "world.yaml").read_text())
    data["tasks"] = [{"path": "train-task"}]
    (tmp_path / "world.yaml").write_text(yaml.safe_dump(data))
    data["tasks"] = [{"path": "heldout-task"}]
    (tmp_path / "evaluation.yaml").write_text(yaml.safe_dump(data))
    configured = configured.model_copy(update={"evaluation_world": "evaluation.yaml"})

    def verify(batch, policy, spec):
        assert batch.policy == policy
        calls.append(f"verify_policy:{policy.model}")
        return {"status": "verified", "optimizer_steps": 0}

    monkeypatch.setattr("mesocosm.learning.training.verify_rollout_policy", verify)
    monkeypatch.setattr("mesocosm.learning.controller.independent_evaluation", lambda bundle: {
        "run_id": bundle.name, "task_outcomes": [], "learning_rewards": [], "fixture": True})
    return configured, path, out, run_one, calls, worlds


async def test_actual_run_callback_executes_pre_post_worlds_without_optimizer_updates(tmp_path, monkeypatch):
    configured, path, out, run_one, calls, worlds = heldout_setup(tmp_path, monkeypatch)
    result = await run_training(configured, path, out, run_one)
    assert len(worlds) == 4 and calls.count("update") == 1
    assert [world.tasks[0].path.rsplit("/", 1)[-1] for _, world, _ in worlds] == [
        "heldout-task", "train-task", "train-task", "heldout-task"]
    assert [world.model.id for _, world, _ in worlds] == ["base", "base", "updated", "updated"]
    assert [record["phase"] for record in result["evaluations"]] == ["pre", "post"]
    assert all(record["optimizer_steps"] == 0 and record["evaluation_only"] for record in result["evaluations"])
    assert result["evaluations"][0]["policy"]["id"] != result["evaluations"][1]["policy"]["id"]
    assert result["performance_improvement_demonstrated"] is False
    assert (out / "evaluation-rollout-pre.json").is_file()
    assert (out / "evaluation-rollout-post.json").is_file()


async def test_copied_heldout_content_is_rejected_before_any_world_or_gpu_call(tmp_path, monkeypatch):
    configured, path, out, run_one, calls, _ = heldout_setup(tmp_path, monkeypatch)
    shutil.copyfile(tmp_path / "train-task/instruction.md", tmp_path / "heldout-task/instruction.md")
    with pytest.raises(ValueError, match="overlaps"):
        await run_training(configured, path, out, run_one)
    assert calls == [] and not out.exists()


async def test_failed_pre_evaluation_persists_failure_and_prevents_updates(tmp_path, monkeypatch):
    configured, path, out, run_one, calls, _ = heldout_setup(
        tmp_path, monkeypatch, failures={"world": RuntimeError("held-out runtime failed")})
    with pytest.raises(RuntimeError, match="held-out runtime failed"):
        await run_training(configured, path, out, run_one)
    result = json.loads((out / "training.json").read_text())
    assert result["status"] == "failed" and result["episodes"] == [] and result["updates"] == []
    assert result["evaluations"][0]["status"] == "failed"
    assert result["evaluations"][0]["world_result"]
    assert "update" not in calls and calls[-1] == "close"


async def test_zero_advantage_still_evaluates_unchanged_policy_after_training(tmp_path, monkeypatch):
    def no_update(batch, policy, spec, out):
        return {"status": "no_update", "next_policy": policy.model_dump(), "optimizer_steps": 0}

    configured, path, out, run_one, calls, worlds = heldout_setup(tmp_path, monkeypatch, updater=no_update)
    result = await run_training(configured, path, out, run_one)
    assert result["status"] == "no_update" and len(worlds) == 3
    assert [record["phase"] for record in result["evaluations"]] == ["pre", "post"]
    assert result["evaluations"][0]["policy"] == result["evaluations"][1]["policy"]
    assert "activate" not in calls


async def test_input_mutation_after_world_prevents_training_on_changed_task(tmp_path, monkeypatch):
    configured, path, out, run_one, calls, _ = heldout_setup(tmp_path, monkeypatch)

    async def mutate(manifest, context):
        result = await run_one(manifest, context)
        (tmp_path / "heldout-task/instruction.md").write_text("changed after the world")
        return result

    with pytest.raises(ValueError, match="input drift"):
        await run_training(configured, path, out, mutate)
    assert "update" not in calls
    result = json.loads((out / "training.json").read_text())
    assert result["evaluations"][0]["status"] == "failed"
    assert result["evaluations"][0]["bundle"]
