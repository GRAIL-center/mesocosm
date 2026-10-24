"""Rendering and run-time authentication use the same manifest identity."""

from __future__ import annotations

import json

import pytest
import yaml
from typer.testing import CliRunner

from mesocosm.cli.main import app
from mesocosm.core.manifest import load_manifest
from mesocosm.env.compose import harness_auth


@pytest.mark.parametrize("label", [None, "research-world"])
def test_compose_defaults_to_manifest_identity(tmp_path, label):
    path = tmp_path / "world.yaml"
    path.write_text(yaml.safe_dump({
        "name": "world", "model": {"id": "model", "hf_repo": "author/model"},
        "gateway": {"url": "http://gateway:8080", "api_key": "test-control-key",
                    **({"run_label": label} if label else {})},
        "agents": [{"id": "agent-1"}],
    }))
    result = CliRunner().invoke(app, ["env", "compose", str(path), "--json"])
    assert result.exit_code == 0, result.stdout
    data = json.loads(result.stdout)
    expected = label or "spike-docker"
    assert data["run_label"] == expected
    compose = yaml.safe_load(data["compose"])
    username, password = harness_auth(load_manifest(path), expected, "agent-1")
    env = compose["services"]["agent-1"]["environment"]
    assert env["OPENCODE_SERVER_USERNAME"] == username
    assert env["OPENCODE_SERVER_PASSWORD"] == password

    out = tmp_path / "compose.yaml"
    rejected = CliRunner().invoke(app, ["env", "compose", str(path), "--run-label", "different",
                                       "--out", str(out), "--json"])
    assert rejected.exit_code == 1
    assert len(rejected.stdout.splitlines()) == 1
    assert json.loads(rejected.stdout)["status"] == "invalid_configuration"
    assert not out.exists()
