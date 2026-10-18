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
    data["channels"] = [{"type": "telepathy"}]
    (tmp_path / "tasks" / "hello-world").mkdir(parents=True)
    (tmp_path / "tasks" / "hello-world" / "instruction.md").write_text("x")
    report = validate_manifest(_write(tmp_path, data))
    assert not report.ok
    joined = "\n".join(report.errors)
    assert "rejected_unsupported: agent 'agent-1' harness 'openhands'" in joined
    assert "rejected_unsupported: condition 'hidden_channel'" in joined
    assert "rejected_unsupported: channels[0] type 'telepathy'" in joined


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


def test_warnings_for_activations_and_missing_budget(tmp_path):
    data = _valid()
    data["observe"] = {"activations": True}
    del data["agents"][0]["budget"]
    (tmp_path / "tasks" / "hello-world").mkdir(parents=True)
    (tmp_path / "tasks" / "hello-world" / "instruction.md").write_text("x")
    report = validate_manifest(_write(tmp_path, data))
    assert report.ok
    assert any("unverified until M5" in w for w in report.warnings)
    assert any("no agent has a budget" in w for w in report.warnings)


def test_persistent_mode_is_rejected_until_m6(tmp_path):
    data = _valid()
    data["mode"] = "persistent"
    (tmp_path / "tasks" / "hello-world").mkdir(parents=True)
    (tmp_path / "tasks" / "hello-world" / "instruction.md").write_text("x")
    report = validate_manifest(_write(tmp_path, data))
    assert not report.ok
    assert report.errors == ["rejected_unsupported: mode 'persistent' needs the task stream and snapshot manager (M6); "
                             "use mode: comparison"]


def test_not_a_mapping_is_an_error(tmp_path):
    p = tmp_path / "bad.yaml"
    p.write_text("- just\n- a list\n")
    report = validate_manifest(p)
    assert not report.ok and "YAML mapping" in report.errors[0]


def test_channels_are_validated_by_type(tmp_path):
    data = _valid()
    (tmp_path / "tasks" / "hello-world").mkdir(parents=True)
    (tmp_path / "tasks" / "hello-world" / "instruction.md").write_text("x")
    data["channels"] = [{"type": "pastebin"}, {"type": "shared_dir"}]
    report = validate_manifest(_write(tmp_path, data))
    assert report.ok, report.errors
    data["channels"] = [{"type": "telepathy"}, {"type": "pastebin"}, {"type": "pastebin"}]
    report = validate_manifest(_write(tmp_path, data))
    assert any("rejected_unsupported: channels[0] type 'telepathy'" in e for e in report.errors)
    assert any("each type at most once" in e for e in report.errors)
    data["channels"] = [{"type": "pastebin"}]
    data["services"] = {"enabled": False}
    report = validate_manifest(_write(tmp_path, data))
    assert any("pastebin channel needs services.enabled" in e for e in report.errors)
    data["services"] = {"git": ["Bad Name"], "wiki": {"docs": "nowhere"}}
    data["channels"] = []
    report = validate_manifest(_write(tmp_path, data))
    assert any("services.git[0]" in e for e in report.errors) and any("services.wiki.docs" in e for e in report.errors)


def test_exposure_and_accounts_are_validated(tmp_path):
    data = _valid()
    (tmp_path / "tasks" / "hello-world").mkdir(parents=True)
    (tmp_path / "tasks" / "hello-world" / "instruction.md").write_text("x")
    data["conditions"] = {"exposure": {"wiki": {"secret": 30}}}
    data["services"] = {"accounts": [{"user": "deploy", "password": "pw"},
                                     {"user": "root-backup", "password": "h", "trap": True, "note": "wiki/old-ops"}]}
    report = validate_manifest(_write(tmp_path, data))
    assert report.ok, report.errors
    data["conditions"] = {"exposure": {"pages": ["secret"]}}
    report = validate_manifest(_write(tmp_path, data))
    assert any("conditions.exposure: expected {wiki:" in e for e in report.errors)
    data["conditions"] = {"exposure": {"wiki": {"secret": -1}}}
    assert any("conditions.exposure" in e for e in validate_manifest(_write(tmp_path, data)).errors)
    data["conditions"] = {"exposure": {"wiki": {"secret": 30}}}
    data["services"]["enabled"] = False
    report = validate_manifest(_write(tmp_path, data))
    assert any("rejected_unsupported: conditions.exposure needs services.enabled" in e for e in report.errors)
    data["services"] = {"accounts": [{"user": "deploy", "password": "pw"}, {"user": "deploy", "password": "other"}]}
    data["conditions"] = {}
    assert any("services.accounts: user names must be unique" in e for e in validate_manifest(_write(tmp_path, data)).errors)
    data["services"] = {"accounts": [{"user": "Not Valid", "password": "pw"}]}
    assert not validate_manifest(_write(tmp_path, data)).ok
