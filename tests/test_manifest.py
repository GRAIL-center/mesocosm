import pathlib

import yaml

from mesocosm.core.manifest import load_manifest, validate_manifest

EXAMPLES = pathlib.Path(__file__).resolve().parents[1] / "examples"


def _write(tmp_path, data):
    p = tmp_path / "world.yaml"
    p.write_text(yaml.safe_dump(data))
    return p


def _valid():
    return yaml.safe_load((EXAMPLES / "spike-world.yaml").read_text())


def test_example_manifest_is_valid():
    report = validate_manifest(EXAMPLES / "spike-world.yaml")
    assert report.ok, report.errors
    assert report.digest and len(report.digest) == 64
    assert report.resolved["mesocosm"]
    assert report.warnings == []


def test_digest_is_stable_and_content_sensitive(tmp_path):
    a = load_manifest(EXAMPLES / "spike-world.yaml")
    data = _valid()
    data["agents"][0]["budget"]["requests"] = 201
    b = load_manifest(_write(tmp_path, data))
    assert a.digest() == load_manifest(EXAMPLES / "spike-world.yaml").digest()
    assert a.digest() != b.digest()


def test_unsupported_harness_and_condition_are_rejected_explicitly(tmp_path):
    data = _valid()
    data["agents"][0]["harness"] = "openhands"
    data["conditions"] = {"hidden_channel": True}
    data["channels"] = [{"kind": "wiki"}]
    (tmp_path / "tasks" / "hello-world").mkdir(parents=True)
    (tmp_path / "tasks" / "hello-world" / "instruction.md").write_text("x")
    report = validate_manifest(_write(tmp_path, data))
    assert not report.ok
    joined = "\n".join(report.errors)
    assert "rejected_unsupported: agent 'agent-1' harness 'openhands'" in joined
    assert "rejected_unsupported: condition 'hidden_channel'" in joined
    assert "channels are not implemented until M2" in joined


def test_duplicate_agents_and_missing_task_dir(tmp_path):
    data = _valid()
    data["agents"].append(dict(data["agents"][0]))
    report = validate_manifest(_write(tmp_path, data))
    assert not report.ok
    assert "duplicate agent ids" in report.errors[0]

    data = _valid()
    report = validate_manifest(_write(tmp_path, data))  # tasks/hello-world does not exist next to tmp file
    assert not report.ok
    assert "has no instruction.md" in report.errors[0]


def test_warnings_for_persistent_reset_and_activations(tmp_path):
    data = _valid()
    data["mode"] = "persistent"
    data["observe"] = {"activations": True}
    del data["agents"][0]["budget"]
    (tmp_path / "tasks" / "hello-world").mkdir(parents=True)
    (tmp_path / "tasks" / "hello-world" / "instruction.md").write_text("x")
    report = validate_manifest(_write(tmp_path, data))
    assert report.ok
    assert any("persistent mode with memory=reset" in w for w in report.warnings)
    assert any("unverified until M5" in w for w in report.warnings)
    assert any("no agent has a budget" in w for w in report.warnings)


def test_not_a_mapping_is_an_error(tmp_path):
    p = tmp_path / "bad.yaml"
    p.write_text("- just\n- a list\n")
    report = validate_manifest(p)
    assert not report.ok and "YAML mapping" in report.errors[0]
