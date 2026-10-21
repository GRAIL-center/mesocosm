"""An agent-writable log cannot redirect controller writes out of its workspace."""

from __future__ import annotations

import os

import pytest

from mesocosm.core.workspace import RawLog


@pytest.mark.parametrize("kind", ["symlink", "hardlink", "fifo"])
def test_raw_log_rejects_nonprivate_regular_files(tmp_path, kind):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    outside = tmp_path / "outside.txt"
    outside.write_text("untouched")
    target = workspace / "raw_log.jsonl"
    if kind == "symlink":
        target.symlink_to(outside)
    elif kind == "hardlink":
        os.link(outside, target)
    else:
        os.mkfifo(target)
    log = RawLog(workspace)
    with pytest.raises(OSError):
        log.append({"type": "tool", "callID": "call-1", "tool": "bash",
                    "state": {"status": "completed", "input": {}, "output": "result"}})
    assert outside.read_text() == "untouched"


def test_raw_log_regular_append_remains_readable(tmp_path):
    log = RawLog(tmp_path)
    log.task = "task"
    part = {"type": "tool", "callID": "call-1", "tool": "bash",
            "state": {"status": "completed", "input": {}, "output": "result"}}
    log.append(part)
    log.append(part)
    assert log.path.read_text().count("\n") == 1
    assert log.stats("task")["entries"] == 1


@pytest.mark.parametrize("call_id", [None, "", 3])
def test_raw_log_ignores_unidentifiable_tool_calls(tmp_path, call_id):
    log = RawLog(tmp_path)
    log.append({"type": "tool", "callID": call_id, "state": {"status": "completed"}})
    assert not log.path.exists()
    assert log.seen == set()
