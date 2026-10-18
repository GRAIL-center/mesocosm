"""Stages patch one base manifest; flags per stage give the minimum elicitation stage."""

import json
import pathlib

import pytest
import yaml

from mesocosm.core.records import RunBundle
from mesocosm.core.run import RunResult
from mesocosm.core.scenario import deep_merge, load_scenario, run_scenario, scenario_table, stage_manifests

ROOT = pathlib.Path(__file__).resolve().parents[1]


def test_deep_merge_merges_mappings_replaces_lists_and_deletes_on_none():
    base = {"a": {"x": 1, "y": 2}, "list": [1, 2], "gone": True}
    patch = {"a": {"y": 3, "z": 4}, "list": [9], "gone": None, "new": {"k": "v"}}
    assert deep_merge(base, patch) == {"a": {"x": 1, "y": 3, "z": 4}, "list": [9], "new": {"k": "v"}}


def scenario_file(tmp_path, stages, **extra):
    data = {"name": "channel-exposure", "base": str(ROOT / "examples" / "world-channels.yaml"), "stages": stages,
            "retries": 0, "retry_delay_seconds": 0, **extra}
    p = tmp_path / "scenario.yaml"
    p.write_text(yaml.safe_dump(data))
    return p


def test_stage_manifests_are_validated_copies_with_absolute_paths(tmp_path):
    path = scenario_file(tmp_path, [
        {"name": "closed", "patch": {"channels": [], "conditions": {"exposure": None}, "services": {"accounts": []}}},
        {"name": "open", "patch": {}}])
    scenario = load_scenario(path)
    files = stage_manifests(scenario, path, tmp_path / "out")
    assert [f.name for f in files] == ["stage-1-closed.yaml", "stage-2-open.yaml"]
    closed = yaml.safe_load(files[0].read_text())
    assert closed["channels"] == [] and "exposure" not in closed.get("conditions", {}) and closed["services"]["accounts"] == []
    assert closed["name"] == "channel-exposure-closed" and closed["gateway"]["run_label"] == "channel-exposure"
    assert pathlib.Path(closed["tasks"][0]["path"]).is_absolute() and pathlib.Path(closed["services"]["wiki"]["docs"]).is_dir()
    opened = yaml.safe_load(files[1].read_text())
    assert [c["type"] for c in opened["channels"]] == ["pastebin", "shared_dir"]


def test_invalid_stages_and_unknown_flag_kinds_are_refused(tmp_path):
    (tmp_path / "bad").mkdir()
    bad = scenario_file(tmp_path / "bad", [{"name": "x", "patch": {"channels": [{"type": "telepathy"}]}}])
    with pytest.raises(ValueError, match="telepathy"):
        stage_manifests(load_scenario(bad), bad, tmp_path / "bad" / "out")
    (tmp_path / "k").mkdir()
    with pytest.raises(ValueError, match="unknown flag kinds"):
        load_scenario(scenario_file(tmp_path / "k", [{"name": "x"}], stop_on=["nonsense"]))


class StageRuns:
    """Each stage's run gets a bundle; stage 2 produces a trap flag."""

    def __init__(self, runs_root):
        self.runs_root, self.calls = runs_root, []

    async def __call__(self, manifest: pathlib.Path) -> RunResult:
        self.calls.append(manifest.name)
        bundle = RunBundle.create(self.runs_root)
        bundle.record("harness.session", agent_id="agent-1", session_id="s", task="t")
        bundle.record("verifier.reward", agent_id="agent-1", task="t", reward=1.0)
        if manifest.name.startswith("stage-2"):
            bundle.record_flag({"flag_id": "flag_1", "run_id": bundle.run_id, "agent_id": "agent-1",
                                "kind": "trap_credential", "summary": "used the trap credential"})
        bundle.write_summary({"status": "ok", "rewards": {"agent-1/t": 1.0}})
        return RunResult(bundle.run_id, bundle.root, "ok", {"agent-1/t": 1.0})


async def test_run_scenario_reports_the_minimum_elicitation_stage_and_stops_when_asked(tmp_path):
    stages = [{"name": "closed", "patch": {"channels": []}}, {"name": "open", "repeat": 2}, {"name": "more"}]
    path = scenario_file(tmp_path, stages, stop_on=["trap_credential"])
    runs = StageRuns(tmp_path / "runs")
    result = await run_scenario(load_scenario(path), path, tmp_path / "out", runs)
    assert runs.calls == ["stage-1-closed.yaml", "stage-2-open.yaml", "stage-2-open.yaml"]
    assert result["stopped_at"] == 2 and result["minimum_elicitation_stage"]["trap_credential"] == 2
    assert result["minimum_elicitation_stage"]["impersonation"] is None
    assert [s["flags"] for s in result["stages"]] == [{}, {"trap_credential": 2}, {}]
    assert result["stages"][2]["not_run"] is True and result["stages"][1]["ok"] == 2
    assert (tmp_path / "out" / "scenario.json").exists() and (tmp_path / "out" / "runs.jsonl").read_text().count("\n") == 3
    table = scenario_table(tmp_path / "out")
    assert [(r["stage"], r["runs"], r["flags"], r["not_run"]) for r in table["stages"]] == [
        (1, 1, {}, False), (2, 2, {"trap_credential": 2}, False), (3, 0, {}, True)]
    assert table["minimum_elicitation_stage"]["trap_credential"] == 2 and table["stopped_at"] == 2
    assert json.dumps(table)
