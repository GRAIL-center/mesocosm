"""Manifest-to-runtime condition wiring. These configuration tests are not native-agent acceptance."""

from __future__ import annotations

import json
import pathlib

import pytest
import yaml
from test_rewards import manifest_data

from mesocosm.core.manifest import load_manifest, validate_manifest
from mesocosm.env.compose import render_compose
from mesocosm.env.services.app import Settings

ROOT = pathlib.Path(__file__).resolve().parents[1]
AGENTS = ["agent-1", "agent-2", "agent-3"]


def write_world(tmp_path, changes=None):
    data = manifest_data()
    data.update({"isolate_agents": True,
                 "services": {"chat": {"enabled": True}, "access": {
                     "services": {"chat": {"read": AGENTS, "write": AGENTS}, "board": {"read": AGENTS}},
                     "direct_messages": {"agent-1": ["agent-2"], "agent-2": ["agent-3"], "agent-3": ["agent-1"]},
                     "channels": {"general": {"read": AGENTS, "write": ["agent-1"]}},
                     "git": {"project": {"read": AGENTS, "write": ["agent-1"]}}}},
                 "resources": {"model_requests": 30, "completion_tokens": 3000, "model_concurrency": 2,
                               "service_requests": {"board": 10, "chat": 20, "git": 8}}})
    if changes:
        data.update(changes)
    task = tmp_path / "task"
    task.mkdir(exist_ok=True)
    (task / "instruction.md").write_text("Configuration validation fixture.")
    path = tmp_path / "world.yaml"
    path.write_text(yaml.safe_dump(data))
    return path


def test_access_and_real_resource_conditions_reach_runtime_settings(tmp_path, monkeypatch):
    path = write_world(tmp_path)
    report = validate_manifest(path)
    assert report.ok, report.errors
    manifest = load_manifest(path)
    compose = render_compose(manifest, repo_root=ROOT, run_label="condition-wiring")
    env = compose["services"]["services"]["environment"]
    for key in ("MESOCOSM_ACCESS", "MESOCOSM_SERVICE_LIMITS", "MESOCOSM_AGENT_IPS"):
        monkeypatch.setenv(key, env[key])
    runtime = Settings()
    assert runtime.access.model_dump() == manifest.services.access.model_dump()
    assert runtime.service_limits == {"board": 10, "chat": 20, "git": 8}
    assert set(runtime.agent_ips.values()) == set(AGENTS)
    model_resources = json.loads(compose["services"]["gateway"]["environment"]["MESOCOSM_TEAM_RESOURCES"])
    assert model_resources == {"requests": 30, "completion_tokens": 3000, "concurrency": 2}
    for agent in AGENTS:
        service = compose["services"][agent]
        assert list(service["networks"]) == [f"sandbox-{agent}"]
        assert {"NET_RAW", "NET_ADMIN"}.issubset(service["cap_drop"])
        assert "network_mode" not in service and "ports" not in service
        assert not any("MESOCOSM_" in key for key in service.get("environment", {}))


@pytest.mark.parametrize("changes,expected", [
    ({"isolate_agents": False}, "isolate_agents=true"),
    ({"channels": [{"type": "shared_dir"}]}, "shared mount bypasses"),
    ({"services": {"enabled": False, "access": {}}}, "services.enabled=true"),
    ({"services": {"access": {"direct_messages": {"agent-1": ["absent"]}}}}, "unknown agents"),
    ({"services": {"access": {"git": {"absent": {"read": AGENTS}}}}}, "unknown repositories"),
    ({"services": {"access": {"channels": {"absent": {"read": AGENTS}}}}}, "unknown chat channels"),
    ({"services": {"access": {"services": {"pypi": {"write": AGENTS}}}}}, "no write operation"),
    ({"resources": {"service_requests": {"board": 0}}}, "positive request limits"),
    ({"resources": {"service_requests": {"invented": 1}}}, "known service names"),
    ({"resources": {"model_concurrency": 0}}, "greater than or equal to 1"),
])
def test_unenforceable_conditions_fail_before_render(tmp_path, changes, expected):
    report = validate_manifest(write_world(tmp_path, changes))
    assert not report.ok
    assert any(expected in error for error in report.errors), report.errors


@pytest.mark.parametrize("limits", [{"board": 0}, {"board": True}, {"invented": 1}])
def test_direct_service_configuration_also_rejects_invalid_limits(monkeypatch, limits):
    monkeypatch.setenv("MESOCOSM_SERVICE_LIMITS", json.dumps(limits))
    with pytest.raises(ValueError, match="positive integer request caps"):
        Settings()


def test_external_internet_is_explicitly_outside_internal_access_graph(tmp_path):
    report = validate_manifest(write_world(tmp_path, {"internet": {"enabled": True, "allow": ["example.com"]}}))
    assert report.ok, report.errors
    assert any("additional channel" in warning for warning in report.warnings)
