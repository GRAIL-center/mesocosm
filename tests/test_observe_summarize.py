"""The summarizer asks a chat endpoint for one plain line per changed session, caches by activity
signature, persists to a derived file next to the bundle, and never breaks the view on failure."""

import json
import time

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from test_observe_state import ev, oc, text_part, tool_part

from mesocosm.observe.state import Observer
from mesocosm.observe.summarize import Summarizer, clean, context_for, signature


def fake_llm(replies: list[str], seen: list[dict]) -> FastAPI:
    app = FastAPI()

    @app.post("/v1/chat/completions")
    async def chat(request: Request):
        seen.append(await request.json())
        text = replies.pop(0) if replies else "Still working"
        return {"choices": [{"message": {"role": "assistant", "content": text}}]}

    return app


def observer_with_tool():
    obs = Observer()
    obs.apply_bundle_event(ev("harness.session", 1.0, "agent-1", session_id="s", task="fix-git"))
    obs.apply_bundle_event(oc("session.status", 2.0, "s", "agent-1", status={"type": "busy"}))
    obs.apply_bundle_event(oc("message.part.updated", 3.0, "s", "agent-1",
                              **tool_part("s", "c1", "bash", "running", {"command": "git reflog | head"})))
    return obs


async def test_summarize_replaces_the_rule_line_and_caches_by_signature(tmp_path):
    seen = []
    app = fake_llm(['"Searching the git history for lost work."'], seen)
    client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://llm")
    derived = tmp_path / "run.observe" / "summaries.jsonl"
    sm = Summarizer("http://llm/v1", "fake", "key", "en", derived_path=derived, client=client)
    obs = observer_with_tool()
    node = obs.sessions["s"]
    assert node.summary() == "Running git reflog | head" and node.summary_source() == "rules"
    due = sm.due(obs, now=100.0)
    assert [n.session_id for n in due] == ["s"]
    assert await sm.summarize(node, "fix-git") == "Searching the git history for lost work"
    assert node.summary() == "Searching the git history for lost work" and node.summary_source() == "model"
    assert obs.snapshot()["agents"][0]["summary"] == "Searching the git history for lost work"
    assert obs.snapshot()["agents"][0]["summary_source"] == "model"
    prompt = seen[0]["messages"]
    assert prompt[0]["role"] == "system" and "at most 8 words" in prompt[0]["content"]
    assert "Task given to the agent: fix-git" in prompt[1]["content"] and "bash git reflog | head [running]" in prompt[1]["content"]
    assert seen[0]["model"] == "fake" and client.headers["authorization"] == "Bearer key"
    # same activity -> nothing due; new activity -> due again; back to a seen signature -> served from cache
    later = time.time() + 100          # summarize() stamps the wall clock; move past the per-node interval
    assert sm.due(obs, now=later) == []
    obs.apply_bundle_event(oc("message.part.updated", 4.0, "s", "agent-1", **text_part("s", "p", "Found it on a dangling commit.")))
    assert node.summary_source() == "rules" and [n.session_id for n in sm.due(obs, now=later)] == ["s"]
    rec = json.loads(derived.read_text().splitlines()[0])
    assert rec["session_id"] == "s" and rec["text"] == "Searching the git history for lost work" and rec["model"] == "fake"
    # a fresh summarizer loads the derived file and applies cached lines without calling the model
    obs2 = observer_with_tool()
    sm2 = Summarizer("http://llm/v1", "fake", derived_path=derived, client=client)
    sm2.apply_cached(obs2)
    assert obs2.sessions["s"].summary() == "Searching the git history for lost work" and sm2.calls == 0
    await client.aclose()


async def test_failures_leave_the_rule_line_in_place():
    app = FastAPI()

    @app.post("/v1/chat/completions")
    async def chat(request: Request):
        return JSONResponse({"error": "boom"}, status_code=500)

    client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://llm")
    sm = Summarizer("http://llm/v1", "fake", client=client)
    obs = observer_with_tool()
    assert await sm.summarize(obs.sessions["s"], "fix-git") is None
    assert obs.sessions["s"].summary() == "Running git reflog | head" and sm.failures == 1 and "500" in sm.last_error
    assert sm.stats()["calls"] == 0
    await client.aclose()


def test_due_respects_the_per_node_interval_and_idle_nodes():
    sm = Summarizer("http://llm/v1", "fake", client=httpx.AsyncClient())
    obs = observer_with_tool()
    sm.last_call["s"] = 100.0
    assert sm.due(obs, now=101.0) == []           # called 1 s ago
    assert [n.session_id for n in sm.due(obs, now=103.0)] == ["s"]
    quiet = Observer()
    quiet.apply_bundle_event(oc("session.status", 1.0, "q", "agent-2", status={"type": "idle"}))
    assert sm.due(quiet, now=500.0) == []         # nothing happened yet: the rule line says it all


def test_clean_and_signature():
    assert clean(' "Writing the notes file." \nsecond line') == "Writing the notes file"
    assert clean("one two three four five six seven eight nine ten eleven twelve thirteen fourteen") .endswith("…")
    assert clean("") == ""
    obs = observer_with_tool()
    s1 = signature(obs.sessions["s"])
    obs.apply_bundle_event(oc("message.part.updated", 4.0, "s", "agent-1",
                              **tool_part("s", "c1", "bash", "completed", {"command": "git reflog | head"}, "abc")))
    assert signature(obs.sessions["s"]) != s1
    assert "oldest first" in context_for(obs.sessions["s"], "fix-git")
