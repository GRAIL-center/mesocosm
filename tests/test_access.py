"""Three identities exercise the real service handlers and Git backend under explicit access grants."""

from __future__ import annotations

import asyncio
import subprocess

import pytest
from pydantic import ValidationError
from test_services import _serve, client, log_lines, settings

from mesocosm.core.access import AccessSpec, validate_access
from mesocosm.env.services.app import create_app

AGENTS = ["agent-1", "agent-2", "agent-3"]


def access_settings(tmp_path, policy):
    st = settings(tmp_path, repos=("project", "private"))
    st.agent_ips["172.30.0.13"] = "agent-3"
    st.access = AccessSpec.model_validate(policy)
    return st


def test_access_references_and_bypass_conditions_are_validated():
    policy = AccessSpec.model_validate({"services": {"board": {"read": AGENTS}},
                                        "git": {"project": {"read": AGENTS}},
                                        "channels": {"general": {"read": AGENTS}},
                                        "direct_messages": {"agent-1": ["agent-2"]}})
    assert validate_access(policy, AGENTS, ["project"], ["general"]) == []
    assert "shared mount" in validate_access(policy, AGENTS, ["project"], ["general"], shared_directory=True)[0]
    errors = validate_access(policy, ["agent-1"], [], [])
    assert len(errors) == 3 and all("unknown" in error for error in errors)
    malformed = AccessSpec(direct_messages={"agent-1": ["agent-1", "agent-1"]})
    assert "unique other agents" in validate_access(malformed, AGENTS, [], [])[0]
    readonly = AccessSpec.model_validate({"services": {"pypi": {"write": ["agent-1"]}}})
    assert "no write operation" in validate_access(readonly, AGENTS, [], [])[0]
    for body in ({"services": {"invented": {"read": AGENTS}}}, {"allow_all": True},
                 {"services": {"board": {"read": ["agent-1", "agent-1"]}}}):
        with pytest.raises(ValidationError):
            AccessSpec.model_validate(body)


async def test_service_grants_are_default_deny_and_apply_to_actual_resource(tmp_path):
    st = access_settings(tmp_path, {"services": {"board": {"read": ["agent-2"], "write": ["agent-1"]}}})
    app = create_app(st)
    async with client(app, "172.30.0.11", "board") as writer, \
            client(app, "172.30.0.12", "wiki") as reader, client(app, "172.30.0.13", "services") as denied:
        assert (await writer.post("/api/posts", json={"body": "real shared content"})).status_code == 201
        assert (await writer.get("/api/posts")).status_code == 403
        # Existing handlers answer across hostnames. Permission follows the resource, not Host.
        assert (await reader.get("/api/posts/1")).json()["body"] == "real shared content"
        assert (await reader.post("/api/posts", json={"body": "unauthorized"})).status_code == 403
        assert (await denied.get("/api/posts/1")).status_code == 403
        assert (await denied.get("/simple/")).status_code == 403
        assert (await denied.get("/project.git/info/refs?service=git-upload-pack")).status_code == 403
    async with client(app, "10.9.9.9", "board") as unknown:
        assert (await unknown.get("/api/posts")).status_code == 403
    denials = [entry for entry in log_lines(st.log_path) if entry["op"] == "access_denied"]
    assert {entry["service"] for entry in denials} == {"board", "pypi", "git"}
    assert all(entry["status"] == 403 for entry in denials)


async def test_chat_direct_edges_channels_and_exposure_ids(tmp_path):
    st = access_settings(tmp_path, {"services": {"chat": {"read": AGENTS, "write": AGENTS}},
                                    "direct_messages": {"agent-1": ["agent-2"], "agent-2": ["agent-3"],
                                                        "agent-3": ["agent-1"]},
                                    "channels": {"general": {"write": ["agent-1"], "read": ["agent-2"]}}})
    st.chat = {"channels": ["general", "private"]}
    st.personas = dict(zip(AGENTS, ["alice", "bob", "carol"], strict=True))
    app = create_app(st)
    async with client(app, "172.30.0.11", "chat") as a1, client(app, "172.30.0.12", "chat") as a2, \
            client(app, "172.30.0.13", "chat") as a3:
        assert (await a1.post("/api/messages", json={"to": "bob", "text": "a to b"})).status_code == 201
        assert (await a2.post("/api/messages", json={"to": "carol", "text": "b to c"})).status_code == 201
        assert (await a3.post("/api/messages", json={"to": "alice", "text": "c to a"})).status_code == 201
        assert (await a1.post("/api/messages", json={"to": "carol", "text": "blocked edge"})).status_code == 403
        assert (await a1.post("/api/messages", json={"to": "#general", "text": "channel"})).status_code == 201
        assert (await a2.post("/api/messages", json={"to": "#general", "text": "blocked writer"})).status_code == 403
        assert [m["id"] for m in (await a2.get("/api/messages")).json()["messages"]] == [1, 2, 4]
        assert [m["id"] for m in (await a3.get("/api/messages")).json()["messages"]] == [2, 3]
        assert (await a3.get("/api/messages?channel=general")).status_code == 403
        assert "#general" not in (await a3.get("/")).text
        assert "#private" not in (await a2.get("/")).text
    read = next(entry for entry in log_lines(st.log_path)
                if entry["op"] == "read_list" and entry["agent"] == "agent-2")
    assert read["message_ids"] == [1, 2, 4]


async def test_mcp_cannot_bypass_underlying_board_permissions(tmp_path):
    st = access_settings(tmp_path, {"services": {"board": {"read": ["agent-2"], "write": ["agent-1"]}}})
    st.mcp = True
    st.service_limits = {"board": 2}
    app = create_app(st)
    payload = {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
               "params": {"name": "board_post", "arguments": {"body": "from MCP"}}}
    async with client(app, "172.30.0.11", "mcp") as writer, client(app, "172.30.0.12", "mcp") as reader:
        assert (await reader.post("/", json=payload)).status_code == 403
        assert (await writer.post("/", json=payload)).json()["result"]["isError"] is False
        payload["params"] = {"name": "board_read", "arguments": {"post_id": 1}}
        result = await reader.post("/", json=payload)
        assert "from MCP" in result.json()["result"]["content"][0]["text"]
        assert (await reader.get("/api/posts")).status_code == 429


async def test_cache_permissions_cannot_be_bypassed_by_reading_known_key(tmp_path):
    st = access_settings(tmp_path, {"services": {"cache": {"read": ["agent-2"], "write": ["agent-1"]}}})
    st.cache = {"writable": True, "list_recent": True}
    app = create_app(st)
    async with client(app, "172.30.0.11", "cache") as writer, client(app, "172.30.0.12", "cache") as reader, \
            client(app, "172.30.0.13", "cache") as denied:
        assert (await writer.put("/api/objects/key", content="content")).status_code == 201
        assert (await reader.get("/api/objects/key")).text == "content"
        assert (await reader.put("/api/objects/key", content="overwrite")).status_code == 403
        assert (await denied.get("/api/objects")).status_code == 403
        assert (await denied.get("/api/objects/key")).status_code == 403
    records = log_lines(st.log_path)
    write = next(entry for entry in records if entry["op"] == "write")
    read = next(entry for entry in records if entry["op"] == "read")
    assert read["object_version"] == write["object_version"]
    assert read["request_id"] != write["request_id"]
    assert len({entry["service_event_id"] for entry in records}) == len(records)


async def test_shared_request_caps_are_atomic_and_survive_restart(tmp_path):
    st = access_settings(tmp_path, {"services": {"board": {"read": AGENTS}}})
    st.service_limits = {"board": 2}
    app = create_app(st)
    async with client(app, "172.30.0.11", "board") as a1, client(app, "172.30.0.12", "board") as a2, \
            client(app, "172.30.0.13", "board") as a3:
        for agent in (a1, a2, a3):
            assert (await agent.get("/health")).json() == {"ok": True}
        assert (await a1.post("/api/posts", json={"body": "denied"})).status_code == 403
        responses = await asyncio.gather(a1.get("/api/posts"), a2.get("/api/posts"), a3.get("/api/posts"))
        assert sorted(response.status_code for response in responses) == [200, 200, 429]
    restarted = create_app(st)
    async with client(restarted, "172.30.0.11", "board") as agent, \
            client(restarted, "172.30.0.4", "services", api_key="test-control") as control:
        assert (await agent.get("/api/posts")).status_code == 429
        assert (await agent.post("/admin/epoch", json={"epoch": "reset-attempt"})).status_code == 404
        state = (await control.get("/admin/state")).json()
        assert state["service_usage"] == {"board": 2} and state["service_limits"] == {"board": 2}
        assert state["access"] == st.access.model_dump()
    assert len([entry for entry in log_lines(st.log_path) if entry["op"] == "quota_admitted"]) == 2
    records = log_lines(st.log_path)
    admission = next(entry for entry in records if entry["op"] == "quota_admitted")
    execution = next(entry for entry in records if entry["op"] == "read_list")
    assert admission["request_id"] == execution["request_id"]
    assert admission["service_event_id"] != execution["service_event_id"]
    async with client(restarted, "127.0.0.1", "services") as docker_health:
        assert (await docker_health.get("/health")).json() == {"ok": True}


@pytest.fixture
def readonly_git(tmp_path):
    st = access_settings(tmp_path, {"git": {"project": {"read": ["agent-1"]}}})
    yield from _serve(st)


def test_git_clone_works_and_real_push_is_denied(readonly_git, tmp_path):
    st, port = readonly_git
    work = tmp_path / "work"
    env = {"GIT_AUTHOR_NAME": "a", "GIT_AUTHOR_EMAIL": "a@x", "GIT_COMMITTER_NAME": "a", "GIT_COMMITTER_EMAIL": "a@x",
           "HOME": str(tmp_path), "PATH": "/usr/bin:/bin", "GIT_TERMINAL_PROMPT": "0"}
    url = f"http://127.0.0.1:{port}/project.git"
    subprocess.run(["git", "clone", "-q", url, str(work)], check=True, env=env, capture_output=True)
    (work / "hello.txt").write_text("hi\n")
    subprocess.run(["git", "-C", str(work), "add", "hello.txt"], check=True, env=env)
    subprocess.run(["git", "-C", str(work), "commit", "-q", "-m", "first"], check=True, env=env)
    push = subprocess.run(["git", "-C", str(work), "push", "-q", "origin", "HEAD:main"], env=env,
                          capture_output=True, text=True)
    assert push.returncode != 0 and "403" in push.stderr
    denials = [entry for entry in log_lines(st.log_path) if entry["op"] == "access_denied"]
    assert denials[-1]["operation"] == "write" and denials[-1]["repo"] == "project"


async def test_git_repository_list_and_query_cannot_bypass_write_acl(tmp_path):
    st = access_settings(tmp_path, {"git": {"project": {"read": ["agent-1"]}}})
    app = create_app(st)
    async with client(app, "172.30.0.11", "git") as agent:
        assert "project.git" in (await agent.get("/")).text
        assert "private.git" not in (await agent.get("/")).text
        assert (await agent.get("/private.git/info/refs?service=git-upload-pack")).status_code == 403
        assert (await agent.post("/project.git/git-receive-pack?service=git-upload-pack", content=b"0000")).status_code == 403
        assert (await agent.get("/project.git/info/refs?service=git-receive-pack")).status_code == 403
