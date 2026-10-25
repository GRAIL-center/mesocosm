"""Diagnostic CLI object contracts and explicit unavailability without allocating resources."""

from __future__ import annotations

import json

import pytest
from typer.testing import CliRunner

from mesocosm.cli.main import app
from mesocosm.core.records import RunBundle


def test_diagnose_cli_reports_three_agents_without_raw_text(tmp_path):
    bundle = RunBundle.create(tmp_path, "run_cli")
    bundle.write_manifest({"agents": [{"id": f"agent-{index}"} for index in range(3)]}, "fixture")
    bundle.record("harness.session", agent_id="agent-0", task="task", prompt="SECRET")
    result = CliRunner().invoke(app, ["diagnose", str(bundle.root), "--json"])
    assert result.exit_code == 0
    assert len(result.stdout.splitlines()) == 1
    data = json.loads(result.stdout)
    assert len(data["agents"]) == 3
    assert "SECRET" not in result.stdout
    failed = CliRunner().invoke(app, ["diagnose", str(tmp_path / "absent"), "--json"])
    assert failed.exit_code == 1
    assert json.loads(failed.stdout)["status"] == "invalid_bundle"


def test_calibrate_cli_requires_docker_without_creating_output(tmp_path, monkeypatch):
    import shutil

    monkeypatch.setattr(shutil, "which", lambda command: None)
    out = tmp_path / "calibration"
    result = CliRunner().invoke(app, ["env", "calibrate", str(tmp_path / "task"), "--out", str(out), "--json"])
    assert result.exit_code == 1
    assert len(result.stdout.splitlines()) == 1
    assert json.loads(result.stdout)["reason"] == "docker_executable_missing"
    assert not out.exists()


@pytest.mark.parametrize("status,matches,code", [("completed", True, 0), ("completed", False, 1),
                                                ("failed_cleanup", True, 1), ("failed", None, 1)])
def test_calibration_measurement_and_cleanup_determine_exit_independently(tmp_path, monkeypatch, status, matches, code):
    import mesocosm.env.reference as reference

    async def fake_calibrate(task_path, out, *, expected_score, verifier_network, allow_unenforced_resources):
        assert expected_score == 7.5 and verifier_network == "none" and allow_unenforced_resources is False
        return {"status": status, "matches_expected": matches, "after": {"original_score": 0}}

    monkeypatch.setattr(reference, "calibrate_native", fake_calibrate)
    result = CliRunner().invoke(app, ["env", "calibrate", str(tmp_path / "task"), "--out", str(tmp_path / "out"),
                                     "--expected-score", "7.5", "--json"])
    assert result.exit_code == code
    assert len(result.stdout.splitlines()) == 1
    assert json.loads(result.stdout)["status"] == status
    assert json.loads(result.stdout)["after"]["original_score"] == 0
