"""Snapshot -> restore -> fork: declared coverage, hash-verified restoration, lineage."""

import json
import pathlib

import yaml

from mesocosm.core.records import RunBundle
from mesocosm.core.snapshot import (
    NOT_CAPTURED,
    LocalWorldState,
    create_snapshot,
    fork_manifest,
    hash_tree,
    queue_state,
    read_tar_names,
    restore_snapshot,
)

ROOT = pathlib.Path(__file__).resolve().parents[1]


def world_on_disk(tmp_path):
    ws = tmp_path / "ws"
    (ws / "agent-1" / "src").mkdir(parents=True)
    (ws / "agent-1" / "src" / "a.py").write_text("print(1)\n")
    (ws / "agent-1" / "notes.md").write_text("done\n")
    (ws / "agent-2").mkdir()
    (ws / "agent-2" / "b.txt").write_text("b\n")
    services = tmp_path / "services-data"
    (services / "git" / "project.git").mkdir(parents=True)
    (services / "git" / "project.git" / "HEAD").write_text("ref: refs/heads/main\n")
    (services / "board.json").write_text(json.dumps({"posts": [{"id": 1, "body": "hello"}]}))
    (services / "pypi").mkdir()
    (services / "pypi" / "big.whl").write_bytes(b"x" * 10)        # not part of a snapshot: the image seeds it
    shared = tmp_path / "shared"
    shared.mkdir()
    (shared / "handoff.txt").write_text("psst\n")
    bundle = RunBundle.create(tmp_path / "runs", "run_s")
    bundle.write_manifest({"name": "demo", "mode": "persistent", "agents": [{"id": "agent-1"}, {"id": "agent-2"}],
                           "model": {"id": "m"}, "gateway": {"url": "http://g", "run_label": "lbl"},
                           "tasks": [{"source": "harbor", "path": "tasks/t1"}], "stream": {"dir": "stream"},
                           "services": {"wiki": {"docs": "world/wiki"}}}, "d", path=tmp_path / "examples" / "w.yaml")
    bundle.record("task.loaded", task="t1", provenance={"path": "/tasks/t1"}, source="manifest")
    bundle.record("task.loaded", task="t2", provenance={"path": "/tasks/t2"}, source="stream")
    bundle.record("task.assigned", agent_id="agent-1", task="t1")
    bundle.record("verifier.reward", agent_id="agent-1", task="t1", reward=1.0)
    return ws, services, shared, bundle


async def test_snapshot_restore_roundtrip_is_hash_verified_and_declares_coverage(tmp_path):
    ws, services, shared, bundle = world_on_disk(tmp_path)
    world = LocalWorldState(services, shared)
    cov = await create_snapshot(bundle.root, ws, world, tmp_path / "snaps", note="before the fork")
    root = pathlib.Path(cov["path"])
    assert cov["parent_run_id"] == "run_s" and cov["not_captured"] == NOT_CAPTURED and cov["note"] == "before the fork"
    assert [c["component"] for c in cov["captured"]] == ["workspace", "workspace", "services", "shared_dir",
                                                         "task_queue", "manifest"]
    assert read_tar_names(root / "workspaces" / "agent-1.tar.gz") == ["notes.md", "src/a.py"]
    assert sorted(p.name for p in (root / "services").iterdir()) == ["board.json", "git"], "the index is not copied"
    queue = json.loads((root / "queue.json").read_text())
    assert queue["pending"] == ["t2"] and queue["assigned"] == {"t1": "agent-1"} and queue["finished"] == {"agent-1/t1": 1.0}
    assert queue_state(bundle) == queue
    files = json.loads((root / "files.json").read_text())
    assert set(files) == {"workspaces/agent-1/notes.md", "workspaces/agent-1/src/a.py", "workspaces/agent-2/b.txt",
                          "services/board.json", "services/git/project.git/HEAD", "shared/handoff.txt"}

    # the world moves on, then comes back exactly
    (ws / "agent-1" / "notes.md").write_text("changed\n")
    (ws / "agent-1" / "extra.txt").write_text("new\n")
    (services / "board.json").write_text("{}")
    (shared / "handoff.txt").unlink()
    result = await restore_snapshot(root, ws, world)
    assert result["ok"] and result["mismatches"] == [] and result["verified_files"] == 6
    assert result["restored"] == {"workspace:agent-1": 2, "workspace:agent-2": 1, "services": 2, "shared_dir": 1}
    assert (ws / "agent-1" / "notes.md").read_text() == "done\n" and not (ws / "agent-1" / "extra.txt").exists()
    assert json.loads((services / "board.json").read_text())["posts"][0]["body"] == "hello"
    assert (shared / "handoff.txt").read_text() == "psst\n" and (services / "pypi" / "big.whl").exists()
    assert hash_tree(ws / "agent-1") == {k[len("workspaces/agent-1/"):]: v for k, v in files.items()
                                         if k.startswith("workspaces/agent-1/")}


async def test_restore_reports_what_does_not_come_back(tmp_path):
    ws, services, shared, bundle = world_on_disk(tmp_path)
    cov = await create_snapshot(bundle.root, ws, LocalWorldState(services, shared), tmp_path / "snaps")
    root = pathlib.Path(cov["path"])

    class LossyWorld(LocalWorldState):
        async def import_services(self, src):
            await super().import_services(src)
            (self.services_dir / "board.json").write_text("{}")      # something the backend failed to restore

    result = await restore_snapshot(root, ws, LossyWorld(services, shared))
    assert not result["ok"] and result["mismatches"] == ["services/board.json"]


async def test_fork_manifest_applies_the_patch_and_records_lineage(tmp_path):
    ws, services, shared, bundle = world_on_disk(tmp_path)
    cov = await create_snapshot(bundle.root, ws, LocalWorldState(services, shared), tmp_path / "snaps")
    root = pathlib.Path(cov["path"])
    out = tmp_path / "forks" / "world.yaml"
    lineage = fork_manifest(root, {"mode": "comparison", "channels": [{"type": "pastebin"}], "stream": None}, out)
    forked = yaml.safe_load(out.read_text())
    assert forked["name"] == "demo-fork" and forked["mode"] == "comparison" and "stream" not in forked
    assert forked["channels"] == [{"type": "pastebin"}]
    assert forked["tasks"][0]["path"] == str((tmp_path / "examples" / "tasks" / "t1").resolve())
    assert forked["services"]["wiki"]["docs"] == str((tmp_path / "examples" / "world" / "wiki").resolve())
    assert lineage == {"parent_run_id": "run_s", "snapshot_id": root.name, "manifest": str(out),
                       "patch": {"mode": "comparison", "channels": [{"type": "pastebin"}], "stream": None}}
