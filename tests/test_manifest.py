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


def test_persistent_mode_needs_a_stream_or_tasks_and_comparison_needs_tasks(tmp_path):
    data = _valid()
    (tmp_path / "tasks" / "hello-world").mkdir(parents=True)
    (tmp_path / "tasks" / "hello-world" / "instruction.md").write_text("x")
    data["mode"] = "persistent"
    assert validate_manifest(_write(tmp_path, data)).ok
    data["tasks"] = []
    report = validate_manifest(_write(tmp_path, data))
    assert report.errors == ["mode: persistent needs initial tasks or stream.dir"]
    data["stream"] = {"dir": "stream", "poll_seconds": 1, "idle_stop_seconds": 5}
    report = validate_manifest(_write(tmp_path, data))
    assert any("stream.dir" in e and "not a directory" in e for e in report.errors)
    (tmp_path / "stream").mkdir()
    assert validate_manifest(_write(tmp_path, data)).ok
    data["mode"] = "comparison"
    report = validate_manifest(_write(tmp_path, data))
    assert set(report.errors) == {"tasks: at least one task is required in mode: comparison",
                                  "stream.dir applies to mode: persistent only"}


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


def test_peer_review_needs_two_agents_comparison_mode_and_no_board(tmp_path):
    data = _valid()
    (tmp_path / "tasks" / "hello-world").mkdir(parents=True)
    (tmp_path / "tasks" / "hello-world" / "instruction.md").write_text("x")
    data["services"] = {"board": False, "peer_review": {"enabled": True, "max_chars": 200, "max_messages": 5}}
    report = validate_manifest(_write(tmp_path, data))
    assert report.errors == ["services.peer_review needs exactly two agents"]
    data["agents"].append({**data["agents"][0], "id": "agent-2", "url": "http://127.0.0.1:4097"})
    assert validate_manifest(_write(tmp_path, data)).ok
    data["services"]["board"] = True
    data["mode"] = "persistent"
    report = validate_manifest(_write(tmp_path, data))
    assert any("lockstep" in e for e in report.errors) and any("services.board: false" in e for e in report.errors)


def test_personas_and_domain_are_validated(tmp_path):
    data = _valid()
    (tmp_path / "tasks" / "hello-world").mkdir(parents=True)
    (tmp_path / "tasks" / "hello-world" / "instruction.md").write_text("say hello\n")
    data["agents"] = [{"id": "agent-1", "persona": {"username": "jkim", "name": "Jae Kim"}},
                      {"id": "agent-2", "persona": {"username": "jkim"}}]
    report = validate_manifest(_write(tmp_path, data))
    assert not report.ok and any("persona usernames must be unique" in e for e in report.errors)
    data["agents"] = [{"id": "agent-1", "persona": {"username": "agent-2"}}, {"id": "agent-2"}]
    report = validate_manifest(_write(tmp_path, data))
    assert not report.ok and any("agent-2" in e for e in report.errors), "a username may not be another agent's id"
    data["agents"] = [{"id": "agent-1", "persona": {"username": "jkim", "hostname": "jkim-dev"}}, {"id": "agent-2"}]
    data["services"] = {"domain": "corp.example"}
    assert validate_manifest(_write(tmp_path, data)).ok
    manifest = load_manifest(_write(tmp_path, data))
    assert manifest.agents[0].username == "jkim" and manifest.agents[1].username == "agent-2"
    assert manifest.agents[0].email("corp.example") == "jkim@corp.example"
    assert manifest.agents[1].display_name() == "agent-2"
    data["services"] = {"domain": "not a domain"}
    assert not validate_manifest(_write(tmp_path, data)).ok
    data["services"] = {}
    data["gateway"]["provider_id"] = "Bad Provider"
    assert not validate_manifest(_write(tmp_path, data)).ok


def test_world_events_and_roles_are_validated(tmp_path):
    data = _valid()
    (tmp_path / "tasks" / "hello-world").mkdir(parents=True)
    (tmp_path / "tasks" / "hello-world" / "instruction.md").write_text("say hello\n")
    data["services"] = {"git": ["project"], "protected_repos": ["other"],
                        "accounts": [{"user": "deploy", "password": "pw", "roles": ["push", "admin"]}]}
    data["world"] = {"events": [{"at": 10, "kind": "board.post", "as": "ops-bot", "body": "hello"},
                                {"at": 20, "kind": "chat.message", "to": "agent-1", "text": "hi"},
                                {"at": 30, "kind": "wiki.update", "slug": "x"},
                                {"at": 40, "kind": "wiki.update", "slug": "x", "file": "missing.md"},
                                {"at": 5, "kind": "board.reply", "body": "no post id"}]}
    report = validate_manifest(_write(tmp_path, data))
    errors = "\n".join(report.errors)
    assert "protected_repos: 'other' is not in services.git" in errors
    assert "chat.message needs services.peer_review" in errors
    assert "world.events[2] (wiki.update): missing content or file" in errors
    assert "missing.md is not a file" in errors and "world.events[4] (board.reply): missing post_id" in errors
    data["services"]["protected_repos"] = ["project"]
    data["world"]["events"] = [{"at": 10, "kind": "board.post", "as": "ops-bot", "body": "hello"},
                               {"at": 30, "kind": "wiki.publish", "slug": "release-notes"}]
    assert validate_manifest(_write(tmp_path, data)).ok
    manifest = load_manifest(_write(tmp_path, data))
    assert manifest.world.events[0].as_ == "ops-bot" and manifest.world.events[1].as_ == "world"
    assert manifest.services.accounts[0].roles == ["push", "admin"]
    data["services"]["accounts"][0]["roles"] = ["root"]
    assert not validate_manifest(_write(tmp_path, data)).ok
    data["services"]["accounts"][0]["roles"] = []
    data["services"]["enabled"] = False
    data["services"]["git"] = []
    data["services"]["protected_repos"] = []
    assert "world.events need services.enabled" in "\n".join(validate_manifest(_write(tmp_path, data)).errors)


def test_mcp_servers_and_skills_are_validated(tmp_path):
    data = _valid()
    (tmp_path / "tasks" / "hello-world").mkdir(parents=True)
    (tmp_path / "tasks" / "hello-world" / "instruction.md").write_text("say hello\n")
    good = tmp_path / "skills" / "team-board"
    good.mkdir(parents=True)
    (good / "SKILL.md").write_text("---\nname: team-board\ndescription: Board conventions\n---\n# Board\n")
    bad_name = tmp_path / "skills" / "Bad_Name"
    bad_name.mkdir()
    (bad_name / "SKILL.md").write_text("---\nname: Bad_Name\ndescription: x\n---\n")
    mismatch = tmp_path / "skills" / "other"
    mismatch.mkdir()
    (mismatch / "SKILL.md").write_text("---\nname: team-board\ndescription: x\n---\n")
    no_desc = tmp_path / "skills" / "quiet"
    no_desc.mkdir()
    (no_desc / "SKILL.md").write_text("---\nname: quiet\n---\n")
    (tmp_path / "skills" / "empty").mkdir()
    data["agents"] = [{"id": "agent-1", "skills": ["skills/team-board", "skills/Bad_Name", "skills/other",
                                                   "skills/quiet", "skills/empty", "skills/team-board"],
                       "mcp": {"Bad Name": {"type": "local", "command": ["x"]},
                               "team": {"type": "remote", "url": "http://mcp/"},
                               "fs": {"type": "local", "command": ["python3", "-m", "fs_mcp"], "environment": {"A": "1"}}}}]
    data["services"] = {"mcp": True}
    report = validate_manifest(_write(tmp_path, data))
    errors = "\n".join(report.errors)
    assert "name 'Bad_Name' must be 1-64 lowercase" in errors
    assert "name 'team-board' must match the directory name 'other'" in errors
    assert "skill 'skills/quiet': description must be 1-1024 characters" in errors
    assert "has no SKILL.md" in errors and "skill 'team-board' listed twice" in errors
    assert "mcp server name 'Bad Name' must match" in errors and "'team' is the world's own" in errors
    data["agents"][0]["skills"] = ["skills/team-board"]
    del data["agents"][0]["mcp"]["Bad Name"]
    del data["agents"][0]["mcp"]["team"]
    assert validate_manifest(_write(tmp_path, data)).ok
    manifest = load_manifest(_write(tmp_path, data))
    assert manifest.agents[0].mcp["fs"].command == ["python3", "-m", "fs_mcp"] and manifest.services.mcp
    data["agents"][0]["mcp"]["fs"] = {"type": "local", "command": []}
    assert not validate_manifest(_write(tmp_path, data)).ok, "a local server needs a command"
    data["agents"][0]["mcp"]["fs"] = {"type": "stdio", "command": ["x"]}
    assert not validate_manifest(_write(tmp_path, data)).ok, "type is local or remote"
    data["agents"][0]["mcp"] = {}
    data["services"] = {"enabled": False, "mcp": True, "git": []}
    assert "services.mcp needs services.enabled" in "\n".join(validate_manifest(_write(tmp_path, data)).errors)


def test_internet_access_is_validated(tmp_path):
    data = _valid()
    (tmp_path / "tasks" / "hello-world").mkdir(parents=True)
    (tmp_path / "tasks" / "hello-world" / "instruction.md").write_text("say hello\n")
    data["internet"] = {"enabled": True}
    assert "empty allow list" in "\n".join(validate_manifest(_write(tmp_path, data)).errors)
    data["internet"] = {"enabled": True, "allow": ["PyPI.org", "https://x.org", "a b"], "mode": "replay"}
    errors = "\n".join(validate_manifest(_write(tmp_path, data)).errors)
    assert errors.count("must be a lowercase host glob") == 3 and "replay needs internet.replay_file" in errors
    data["internet"] = {"enabled": True, "allow": ["pypi.org", "*.pythonhosted.org"], "mode": "record",
                        "budget": {"requests": 100}}
    report = validate_manifest(_write(tmp_path, data))
    assert report.ok, report.errors
    manifest = load_manifest(_write(tmp_path, data))
    assert manifest.internet.mode == "record" and manifest.internet.budget.requests == 100
    data["internet"]["mode"] = "live"
    data["internet"]["replay_file"] = "flows.mitm"
    assert "only read in replay mode" in "\n".join(validate_manifest(_write(tmp_path, data)).errors)
    (tmp_path / "flows.mitm").write_bytes(b"")
    data["internet"] = {"enabled": True, "mode": "replay", "replay_file": "flows.mitm"}
    assert validate_manifest(_write(tmp_path, data)).ok, "replay needs no allow list: nothing reaches the internet"
    data["internet"] = {"enabled": False, "allow": ["pypi.org"]}
    report = validate_manifest(_write(tmp_path, data))
    assert report.ok and any("ignored while internet.enabled is false" in w for w in report.warnings)
    data["internet"] = {"enabled": True, "allow": ["pypi.org"], "budget": {"requests": 0}}
    assert not validate_manifest(_write(tmp_path, data)).ok
