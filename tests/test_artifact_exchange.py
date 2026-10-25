"""Snapshot and real service-handler contracts; these are not native-agent acceptance runs."""

from __future__ import annotations

import hashlib
import json
import os

import httpx
import pytest
from pydantic import ValidationError
from test_services import client, log_lines, settings

from mesocosm.core.artifacts import ArtifactSpec, ArtifactStore, capture_artifacts
from mesocosm.env.services.app import create_app
from mesocosm.env.services.client import ServicesAdmin


def payload(text="print('hello')\n", **over):
    data = text.encode("utf-8")
    return {"source_agent": "agent-1", "source_task": "original-task",
            "files": {"src/answer.py": {"text": text, "sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data)}},
            "missing": ["absent.txt"], "score": 1.0, "show_author": True, "show_score": False, **over}


def artifact_settings(tmp_path):
    st = settings(tmp_path, repos=())
    st.agent_ips["172.30.0.13"] = "agent-3"
    return st


@pytest.mark.parametrize("path", ["/etc/passwd", "../secret", "a/../secret", "a/./b", "a//b", "a/", "", ".",
                                  "a\\b", "C:/secret", "a\x00b", "a\nb"])
def test_paths_reject_traversal_and_nonexact_names(path):
    with pytest.raises(ValidationError):
        ArtifactSpec(enabled=True, paths=[path])


def test_spec_rejects_unused_options_unknown_fields_and_unbounded_capture():
    for values in ({"enabled": True}, {"paths": ["out.txt"]}, {"show_score": True}, {"unknown": True},
                   {"enabled": True, "paths": ["x", "x"]},
                   {"enabled": True, "paths": [str(i) for i in range(33)]},
                   {"enabled": True, "paths": ["x"], "max_bytes": 0},
                   {"enabled": True, "paths": ["x"], "success_threshold": 0.5},
                   {"enabled": True, "paths": ["x"], "success_threshold": float("nan")}):
        with pytest.raises(ValidationError):
            ArtifactSpec(**values)


def test_capture_preserves_exact_utf8_bytes_and_missing_evidence(tmp_path):
    (tmp_path / "src").mkdir()
    data = "π\r\n".encode()
    (tmp_path / "src" / "answer.py").write_bytes(data)
    captured = capture_artifacts(tmp_path, ArtifactSpec(enabled=True, paths=["src/answer.py", "absent", "none/file"]))
    assert captured == {"files": {"src/answer.py": {"text": "π\r\n", "sha256": hashlib.sha256(data).hexdigest(),
                                                    "bytes": len(data)}}, "missing": ["absent", "none/file"]}
    (tmp_path / "src" / "answer.py").write_text("later changes")
    assert captured["files"]["src/answer.py"]["text"] == "π\r\n"
    assert capture_artifacts(tmp_path / "nonexistent", ArtifactSpec()) == {"files": {}, "missing": []}


@pytest.mark.parametrize("kind", ["symlink", "directory_symlink", "broken_symlink", "hardlink", "directory", "fifo"])
def test_capture_rejects_links_and_special_files(tmp_path, kind):
    workspace = tmp_path / "work"
    workspace.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret").write_text("control-plane data")
    target = workspace / "output"
    name = "output"
    if kind == "symlink":
        target.symlink_to(outside / "secret")
    elif kind == "directory_symlink":
        target.symlink_to(outside, target_is_directory=True)
        name += "/secret"
    elif kind == "broken_symlink":
        target.symlink_to(outside / "missing")
    elif kind == "hardlink":
        os.link(outside / "secret", target)
    elif kind == "directory":
        target.mkdir()
    else:
        os.mkfifo(target)
    with pytest.raises(ValueError):
        capture_artifacts(workspace, ArtifactSpec(enabled=True, paths=[name]))


def test_capture_rejects_non_utf8_and_aggregate_oversize(tmp_path):
    (tmp_path / "one").write_bytes(b"\xff")
    with pytest.raises(ValueError, match="not UTF-8"):
        capture_artifacts(tmp_path, ArtifactSpec(enabled=True, paths=["one"]))
    (tmp_path / "one").write_text("123")
    (tmp_path / "two").write_text("456")
    with pytest.raises(ValueError, match="max_bytes"):
        capture_artifacts(tmp_path, ArtifactSpec(enabled=True, paths=["one", "two"], max_bytes=5))


@pytest.mark.parametrize("mutation", ["overwrite", "remove", "replace"])
def test_capture_rejects_mutation_during_read(tmp_path, monkeypatch, mutation):
    target = tmp_path / "answer"
    target.write_text("initial")
    read = os.read
    changed = False

    def mutate(fd, count):
        nonlocal changed
        data = read(fd, count)
        if not changed:
            changed = True
            if mutation == "overwrite":
                target.write_text("changed")
            elif mutation == "remove":
                target.unlink()
            else:
                replacement = tmp_path / "replacement"
                replacement.write_text("initial")
                replacement.replace(target)
        return data

    monkeypatch.setattr(os, "read", mutate)
    with pytest.raises(ValueError, match="changed during capture"):
        capture_artifacts(tmp_path, ArtifactSpec(enabled=True, paths=["answer"]))


def test_missing_leaf_does_not_hide_parent_replacement(tmp_path, monkeypatch):
    (tmp_path / "output").mkdir()
    original_open = os.open

    def replace_parent(path, flags, *args, **kwargs):
        if path == "absent.txt":
            (tmp_path / "output").rename(tmp_path / "moved")
            (tmp_path / "output").mkdir()
            (tmp_path / "output" / "absent.txt").write_text("replacement")
        return original_open(path, flags, *args, **kwargs)

    monkeypatch.setattr(os, "open", replace_parent)
    with pytest.raises(ValueError, match="changed during capture"):
        capture_artifacts(tmp_path, ArtifactSpec(enabled=True, paths=["output/absent.txt"]))


async def test_publication_is_immutable_replayable_and_retrieval_is_logged(tmp_path):
    st = artifact_settings(tmp_path)
    app = create_app(st)
    admin = ServicesAdmin("http://services", "test-control",
                          transport=httpx.ASGITransport(app=app, client=("172.30.0.4", 12345)))
    first = payload("π\r\n", show_author=False, show_score=False)
    try:
        receipt = await admin.publish_artifacts(first)
        same = await admin.publish_artifacts(first)
        second = await admin.publish_artifacts(payload("different"))
    finally:
        await admin.aclose()
    assert receipt["created"] and not same["created"] and same["id"] == receipt["id"]
    assert second["id"] != receipt["id"]
    assert receipt["artifact"]["source_agent"] == "agent-1" and receipt["artifact"]["score"] == 1
    assert len((st.data / "artifacts.jsonl").read_text().splitlines()) == 2
    # Reconstruct the service process from its actual on-disk journal.
    app = create_app(st)
    async with client(app, "172.30.0.12", "board") as reader:
        listing = (await reader.get("/api/artifacts")).json()
        hidden = next(item for item in listing["artifacts"] if item["id"] == receipt["id"])
        assert "author" not in hidden and "source_agent" not in hidden and "score" not in hidden
        assert hidden["missing"] == ["absent.txt"] and "text" not in hidden["files"]["src/answer.py"]
        url = f'/api/artifacts/{receipt["id"]}/files/src/answer.py'
        for _ in range(2):
            response = await reader.get(url)
            assert response.content == "π\r\n".encode()
            assert response.headers["etag"] == f'"{first["files"]["src/answer.py"]["sha256"]}"'
        assert (await reader.put(url, content="overwrite")).status_code == 405
        assert (await reader.get(f'/api/artifacts/{receipt["id"]}/files/absent.txt')).status_code == 404
    reads = [record for record in log_lines(st.log_path) if record["op"] == "artifact_read"]
    assert len(reads) == 2
    assert all(record["artifact_id"] == receipt["id"] and record["source_agent"] == "agent-1"
               and record["agent"] == "agent-2" and record["sha256"] == first["files"]["src/answer.py"]["sha256"]
               for record in reads)
    assert len({record["service_event_id"] for record in reads}) == 2


async def test_metadata_switches_and_three_identified_readers(tmp_path):
    st = artifact_settings(tmp_path)
    st.personas = {"agent-1": "author-name"}
    app = create_app(st)
    async with client(app, "172.30.0.4", "services", api_key="test-control") as control:
        await control.post("/admin/artifacts", json=payload(show_score=True))
    for ip in st.agent_ips:
        async with client(app, ip, "board") as reader:
            visible = (await reader.get("/api/artifacts")).json()["artifacts"][0]
            assert visible["author"] == "author-name" and visible["score"] == 1


async def test_admin_auth_identity_and_board_acl_cannot_be_bypassed_by_host(tmp_path):
    from mesocosm.core.access import AccessSpec

    st = artifact_settings(tmp_path)
    st.access = AccessSpec(services={"board": {"read": ["agent-2"]}, "wiki": {"read": ["agent-3"]}})
    app = create_app(st)
    for ip, key in [("172.30.0.99", None), ("172.30.0.99", "wrong"), ("172.30.0.11", "test-control")]:
        async with client(app, ip, "services", api_key=key) as unauthorized:
            assert (await unauthorized.post("/admin/artifacts", json=payload())).status_code == 404
    async with client(app, "172.30.0.4", "services", api_key="test-control") as control:
        receipt = (await control.post("/admin/artifacts", json=payload())).json()
    routes = ["/api/artifacts", f'/api/artifacts/{receipt["id"]}/files/src/answer.py']
    for ip in ("172.30.0.11", "172.30.0.13", "172.30.0.99"):
        async with client(app, ip, "wiki") as denied:
            for route in routes:
                assert (await denied.get(route)).status_code == 403
    async with client(app, "172.30.0.12", "wiki") as allowed:
        for route in routes:
            assert (await allowed.get(route)).status_code == 200
    st.access = None
    app = create_app(st)
    async with client(app, "172.30.0.99", "board") as unknown:
        for route in routes:
            assert (await unknown.get(route)).status_code == 403


async def test_publication_validates_content_hash_size_paths_and_source(tmp_path):
    st = artifact_settings(tmp_path)
    app = create_app(st)
    original = payload()
    invalid = [payload(source_agent="outsider"), payload(files={"../secret": original["files"]["src/answer.py"]}),
               payload(missing=["/etc/passwd"]), payload(extra_field=True), payload(score=float("nan"))]
    for field, value in [("sha256", "0" * 64), ("bytes", 999), ("text", "tampered")]:
        item = payload()
        item["files"]["src/answer.py"][field] = value
        invalid.append(item)
    async with client(app, "172.30.0.4", "services", api_key="test-control") as control:
        for item in invalid:
            response = await control.post("/admin/artifacts", content=json.dumps(item),
                                          headers={"Content-Type": "application/json"})
            assert response.status_code in (400, 422), response.text
    assert not (st.data / "artifacts.jsonl").exists()


async def test_journal_rejects_tampering_instead_of_replaying_unverified_content(tmp_path):
    st = artifact_settings(tmp_path)
    app = create_app(st)
    async with client(app, "172.30.0.4", "services", api_key="test-control") as control:
        await control.post("/admin/artifacts", json=payload())
    path = st.data / "artifacts.jsonl"
    record = json.loads(path.read_text())
    record["files"]["src/answer.py"]["text"] = "changed"
    path.write_text(json.dumps(record) + "\n")
    with pytest.raises(ValueError, match="SHA-256"):
        ArtifactStore(path)
