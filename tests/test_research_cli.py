"""New audit/evaluation commands preserve the single-object JSON contract."""
from __future__ import annotations

import json

from typer.testing import CliRunner

from mesocosm.cli.main import app
from mesocosm.core.records import RunBundle


def test_audit_and_evaluation_commands(tmp_path):
    runner = CliRunner()
    bundle = RunBundle.create(tmp_path, "test-bundle")
    bundle.write_manifest({}, "declared-test-digest")
    event = bundle.record("monitor.intervention", agent_id="a", assessment={"score": 10}, threshold=70,
                          outcome="allowed")
    bundle.write_summary({"status": "ok", "model_calls": 0})

    def run(args, code=0):
        result = runner.invoke(app, [*args, "--json"])
        assert result.exit_code == code, result.stdout
        assert len(result.stdout.splitlines()) == 1
        return json.loads(result.stdout)

    run(["judge", "review-assessment", str(bundle.root), event.event_id, "rejected", "fixture-reviewer"])
    evaluation = run(["evaluate", str(bundle.root)])
    assert evaluation["monitor_confusion"]["pre_action"]["tn"] == 1
    assert run(["audit", "graph", str(bundle.root)])["graph_valid"]
    anchor = tmp_path / "independently-retained.json"
    run(["audit", "anchor", str(bundle.root), str(anchor)])
    assert run(["audit", "verify", str(bundle.root), str(anchor)])["integrity"] == "verified"
    bundle.record("later.record")
    assert run(["audit", "verify", str(bundle.root), str(anchor)], 1)["integrity"] == "failed"
    assert run(["audit", "anchor", str(bundle.root), str(bundle.root / "anchor.json")], 1)["status"] == "failed_anchor"
    assert run(["judge", "review-assessment", str(bundle.root), "missing", "confirmed", "r"], 1)


def test_malformed_assessment_remains_unknown_json(tmp_path):
    bundle = RunBundle.create(tmp_path)
    bundle.write_manifest({}, "test-digest")
    bundle.record("monitor.intervention", assessment=["malformed"], threshold=70)
    result = CliRunner().invoke(app, ["evaluate", str(bundle.root), "--json"])
    assert result.exit_code == 0
    data = json.loads(result.stdout)
    assert data["monitor_confusion"]["pre_action"]["unknown"] == 1
