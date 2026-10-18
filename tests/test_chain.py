"""Chains run manifests in sequence, retry only infrastructure failures, and resume from their log."""

import json
import pathlib

import yaml

from mesocosm.core.chain import ChainPlan, expand, load_plan, run_chain
from mesocosm.core.records import RunBundle
from mesocosm.core.run import RunResult


class FakeRun:
    """Returns the scripted status per (manifest name, attempt); creates a bundle so the result points somewhere."""

    def __init__(self, runs_root: pathlib.Path, script: dict[str, list[str]]):
        self.runs_root, self.script, self.calls = runs_root, script, []

    async def __call__(self, manifest: pathlib.Path) -> RunResult:
        n = sum(1 for m in self.calls if m == manifest.name)
        self.calls.append(manifest.name)
        statuses = self.script.get(manifest.name, ["ok"])
        status = statuses[min(n, len(statuses) - 1)]
        bundle = RunBundle.create(self.runs_root)
        bundle.record("gateway.health", ok=True)
        return RunResult(bundle.run_id, bundle.root, status, {"agent-1/t": 1.0} if status == "ok" else {},
                         None if status == "ok" else status)


def plan_file(tmp_path, **overrides):
    for name in ("a", "b"):
        (tmp_path / f"{name}.yaml").write_text("name: x\n")
    data = {"name": "nightly", "runs": [{"manifest": "a.yaml", "repeat": 2}, {"manifest": "b.yaml"}],
            "retries": 1, "retry_delay_seconds": 0, **overrides}
    p = tmp_path / "plan.yaml"
    p.write_text(yaml.safe_dump(data))
    return p


async def test_chain_retries_infrastructure_failures_and_logs_every_attempt(tmp_path):
    plan_path = plan_file(tmp_path)
    plan = load_plan(plan_path)
    assert isinstance(plan, ChainPlan) and [e["index"] for e in expand(plan, plan_path)] == [1, 2, 3]
    fake = FakeRun(tmp_path / "runs", {"a.yaml": ["failed", "ok"]})     # the first a.yaml run hits an infra failure
    summary = await run_chain(plan, plan_path, fake, tmp_path / "chains", chain_id="c1")
    assert summary["ok"] == 3 and summary["failed"] == 0 and [r["attempts"] for r in summary["runs"]] == [2, 1, 1]
    assert fake.calls == ["a.yaml", "a.yaml", "a.yaml", "b.yaml"]
    log = [json.loads(ln) for ln in (tmp_path / "chains" / "c1.jsonl").read_text().splitlines()]
    assert [(r["index"], r["attempt"], r["status"]) for r in log] == [(1, 1, "failed"), (1, 2, "ok"), (2, 1, "ok"),
                                                                      (3, 1, "ok")]
    assert json.loads((tmp_path / "chains" / "c1.json").read_text())["chain_id"] == "c1"


async def test_contradictions_are_not_retried_and_stop_can_end_the_chain(tmp_path):
    plan_path = plan_file(tmp_path, on_failure="stop")
    fake = FakeRun(tmp_path / "runs", {"a.yaml": ["failed_channel_verification"]})
    summary = await run_chain(load_plan(plan_path), plan_path, fake, tmp_path / "chains", chain_id="c2")
    assert [r["status"] for r in summary["runs"]] == ["failed_channel_verification", "not_run", "not_run"]
    assert [r["attempts"] for r in summary["runs"]] == [1, 0, 0] and summary["failed"] == 1 and summary["not_run"] == 2
    assert fake.calls == ["a.yaml"], "a manifest that contradicts the world is not retried"


async def test_resume_skips_finished_runs(tmp_path):
    plan_path = plan_file(tmp_path, retries=0)
    first = FakeRun(tmp_path / "runs", {"b.yaml": ["failed"]})
    summary = await run_chain(load_plan(plan_path), plan_path, first, tmp_path / "chains", chain_id="c3")
    assert [r["status"] for r in summary["runs"]] == ["ok", "ok", "failed"]
    second = FakeRun(tmp_path / "runs", {})
    resumed = await run_chain(load_plan(plan_path), plan_path, second, tmp_path / "chains", chain_id="c3", resume=True)
    assert second.calls == ["b.yaml"] and resumed["resumed"] is True
    assert [(r["status"], r.get("skipped", False)) for r in resumed["runs"]] == [("ok", True), ("ok", True), ("ok", False)]
