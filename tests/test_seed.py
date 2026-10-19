"""Seeding the world with a past (ADR-0039): prior board posts, prior chat and a real git history, loaded
once from a mounted seed directory when the matching store is empty. State, not scripted behaviour."""

import json
import subprocess

from test_services import client, settings

from mesocosm.env.services.app import create_app


def _write_seed(tmp_path):
    seed = tmp_path / "seed"
    (seed / "git" / "project").mkdir(parents=True)
    (seed / "board.jsonl").write_text(
        json.dumps({"author": "jkim", "title": "deploy runbook moved", "body": "The deploy steps live on the "
                    "wiki now, not in chat.", "ago_seconds": 172800,
                    "replies": [{"author": "mlee", "body": "thanks, updated my bookmarks", "ago_seconds": 171000}]})
        + "\n"
        + json.dumps({"author": "mlee", "title": "flaky test in parser", "body": "test_parse_dates is flaky on CI; "
                      "looking into it.", "ago_seconds": 86400}) + "\n", encoding="utf-8")
    (seed / "chat.jsonl").write_text(
        json.dumps({"channel": "general", "from": "jkim", "text": "morning", "ago_seconds": 90000}) + "\n"
        + json.dumps({"channel": "general", "from": "mlee", "text": "morning — standup at 10", "ago_seconds": 89900})
        + "\n"
        + json.dumps({"to": "jkim", "from": "mlee", "text": "can you review my branch?", "ago_seconds": 80000}) + "\n",
        encoding="utf-8")
    (seed / "git" / "project" / "README.md").write_text("# project\n\nInternal service.\n", encoding="utf-8")
    (seed / "git" / "project" / "main.py").write_text("print('ok')\n", encoding="utf-8")
    return seed


async def test_board_and_chat_start_with_a_past(tmp_path):
    st = settings(tmp_path)
    st.personas = {"agent-1": "jkim", "agent-2": "mlee"}
    st.chat = {"channels": ["general"], "max_chars": None, "max_messages": None}
    st.seed_dir = _write_seed(tmp_path)
    app = create_app(st)
    async with client(app, "172.30.0.11", "board") as board, client(app, "172.30.0.11", "chat") as chat:
        posts = (await board.get("/api/posts")).json()["posts"]
        assert [p["title"] for p in posts] == ["flaky test in parser", "deploy runbook moved"]  # newest first
        assert posts[0]["author"] == "mlee"
        full = (await board.get("/api/posts/1")).json()
        assert full["author"] == "jkim" and full["replies"][0]["author"] == "mlee"
        msgs = (await chat.get("/api/messages?channel=general")).json()["messages"]
        assert [m["from"] for m in msgs] == ["jkim", "mlee"] and "standup" in msgs[1]["text"]
        dms = (await chat.get("/api/messages")).json()["messages"]   # jkim's direct messages + channels
        assert any(m["from"] == "mlee" and m["to"] == "jkim" for m in dms)


async def test_git_repo_starts_with_real_history(tmp_path):
    st = settings(tmp_path)
    st.seed_dir = _write_seed(tmp_path)
    create_app(st)       # building the app creates and seeds the bare repo
    bare = st.data / "git" / "project.git"
    dst = tmp_path / "clone"
    subprocess.run(["git", "clone", "-q", str(bare), str(dst)], check=True)
    assert (dst / "README.md").read_text().startswith("# project")
    assert (dst / "main.py").is_file()
    log = subprocess.run(["git", "-C", str(dst), "log", "--format=%an|%s"], capture_output=True, text=True, check=True)
    assert log.stdout.strip() == "Platform Team|Initial import"


async def test_seed_is_loaded_once_not_on_every_restart(tmp_path):
    st = settings(tmp_path)
    st.personas = {"agent-1": "jkim"}
    st.seed_dir = _write_seed(tmp_path)
    create_app(st)                       # first start seeds two posts
    app2 = create_app(st)                # a restart over the same data dir must not seed again
    async with client(app2, "172.30.0.11", "board") as board:
        posts = (await board.get("/api/posts")).json()["posts"]
        assert len(posts) == 2
