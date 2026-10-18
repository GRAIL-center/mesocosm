"""The observer builds the live view from bundle events and gateway records. Fixtures follow the
shapes recorded on Colab (OpenCode 1.19.3 bus events, gateway chat_completion records) but are
synthetic: run logs are never committed."""

from mesocosm.observe import state as S
from mesocosm.observe.state import Observer, tool_input_summary, tool_phrase

RUN = "run_test"


def ev(kind, ts, agent_id=None, **data):
    return {"ts": ts, "run_id": RUN, "kind": kind, "agent_id": agent_id, "step": None, "request_id": None, "data": data}


def oc(etype, ts, sid, agent_id=None, **props):
    raw = {"id": f"evt_{ts}", "type": etype, "properties": {"sessionID": sid, **props}}
    return ev(f"opencode.{etype}", ts, agent_id, session_id=sid, summary={}, raw=raw)


def tool_part(sid, call_id, tool, status, inp=None, output=None, title=None):
    return {"part": {"id": f"prt_{call_id}", "sessionID": sid, "type": "tool", "tool": tool, "callID": call_id,
                     "state": {"status": status, "input": inp or {}, "output": output, "title": title}}}


def text_part(sid, part_id, text):
    return {"part": {"id": part_id, "sessionID": sid, "type": "text", "text": text}}


def gateway_record(ts, agent, rid, n_messages, tool_names=(), completion=20, reasoning=""):
    return {
        "ts": ts, "request_id": rid, "run": "lbl", "agent": agent, "path": "/v1/chat/completions", "method": "POST",
        "event": "chat_completion", "stream": True, "status": 200, "latency_ms": 900,
        "request": {"model": "mesocosm-3b", "messages": [{"role": "user", "content": "x"}] * n_messages,
                    "tools": [{"type": "function", "function": {"name": "bash"}}]},
        "request_summary": {"n_messages": n_messages, "n_tools": 1},
        "response": {"content": "ok", "reasoning": reasoning,
                     "tool_calls": [{"type": "function", "function": {"name": n, "arguments": "{}"}} for n in tool_names],
                     "finish_reason": "tool_calls" if tool_names else "stop",
                     "usage": {"prompt_tokens": 100, "completion_tokens": completion},
                     "prompt_token_ids": [1, 2, 3], "token_ids": [4, 5]},
    }


def run_with_subagent():
    """agent-1: root session busy -> bash tool -> subagent session via `task` -> text -> idle -> reward."""
    obs = Observer()
    t = 1000.0
    obs.apply_bundle_event(ev("harness.session", t, "agent-1", session_id="ses_root", task="hello-world"))
    obs.apply_bundle_event(oc("session.created", t, "ses_root", "agent-1",
                              info={"id": "ses_root", "title": "mesocosm spike", "time": {"created": 1}}))
    obs.apply_bundle_event(oc("session.status", t + 1, "ses_root", "agent-1", status={"type": "busy"}))
    obs.apply_gateway_record(gateway_record(t + 2, "agent-1", "req-1", 2, ("bash",)))
    obs.apply_bundle_event(oc("message.part.updated", t + 3, "ses_root", "agent-1",
                              **tool_part("ses_root", "call-1", "bash", "running", {"command": "git status --short"})))
    snap_running = obs.snapshot()
    obs.apply_bundle_event(oc("message.part.updated", t + 4, "ses_root", "agent-1",
                              **tool_part("ses_root", "call-1", "bash", "completed", {"command": "git status --short"},
                                          output="M hello.py\n", title="git status --short")))
    # the task tool spawns a child session; its events carry no agent mapping (the run manager
    # only knows the root session), so the observer inherits the agent from the parent
    obs.apply_bundle_event(oc("message.part.updated", t + 5, "ses_root", "agent-1",
                              **tool_part("ses_root", "call-2", "task", "running", {"description": "write the notes file"})))
    obs.apply_bundle_event(oc("session.created", t + 5.1, "ses_child", None,
                              info={"id": "ses_child", "parentID": "ses_root", "title": "write the notes file"}))
    obs.apply_bundle_event(oc("session.status", t + 5.2, "ses_child", None, status={"type": "busy"}))
    obs.apply_bundle_event(oc("message.part.updated", t + 6, "ses_child", None,
                              **tool_part("ses_child", "call-3", "write", "completed", {"filePath": "/work/NOTES.md"})))
    snap_child = obs.snapshot()
    obs.apply_bundle_event(oc("session.status", t + 7, "ses_child", None, status={"type": "idle"}))
    obs.apply_bundle_event(oc("message.part.updated", t + 8, "ses_root", "agent-1",
                              **tool_part("ses_root", "call-2", "task", "completed", {"description": "write the notes file"},
                                          output="done")))
    obs.apply_bundle_event(oc("message.part.updated", t + 9, "ses_root", "agent-1",
                              **text_part("ses_root", "prt_text", "The sum of integers from 1 to 100 is 5050.")))
    obs.apply_bundle_event(oc("session.status", t + 10, "ses_root", "agent-1", status={"type": "idle"}))
    obs.apply_bundle_event(ev("verifier.reward", t + 11, "agent-1", task="hello-world", reward=1.0))
    return obs, snap_running, snap_child


def test_tree_status_and_summaries_follow_the_events():
    obs, snap_running, snap_child = run_with_subagent()
    a_running = snap_running["agents"][0]
    assert a_running["status"] == "tool"
    assert a_running["summary"] == "Running git status --short" and a_running["summary_source"] == "rules"

    child = snap_child["sessions"]["ses_child"]
    assert child["parent_id"] == "ses_root" and child["agent_id"] == "agent-1"
    assert snap_child["sessions"]["ses_root"]["children"] == ["ses_child"]
    assert snap_child["agents"][0]["summary"] == "Subagent: Waiting for the model"
    assert snap_child["sessions"]["ses_child"]["summary"] == "Waiting for the model"

    final = obs.snapshot()
    agent = final["agents"][0]
    assert agent["status"] == "done" and agent["requests"] == 1 and agent["completion_tokens"] == 20
    assert agent["rewards"] == [{"task": "hello-world", "reward": 1.0, "ts": 1011.0}]
    assert final["sessions"]["ses_root"]["summary"] == "The sum of integers from 1 to 100 is 5050."
    assert agent["summary"] == "Done: the sum of integers from 1 to 100 is 5050."
    obs.sessions["ses_root"].model_summary = (obs.sessions["ses_root"].signature(), "Done: wrote the two files")
    assert obs.snapshot()["agents"][0]["summary"] == "Done: wrote the two files"   # no double prefix
    assert final["sessions"]["ses_root"]["tool_calls"] == 2 and final["sessions"]["ses_child"]["tool_calls"] == 1
    assert final["started_ts"] == 1000.0 and final["last_ts"] == 1011.0


def test_details_join_tools_text_and_requests():
    obs, _, _ = run_with_subagent()
    d = obs.session_details("ses_root")
    kinds = [(i["kind"], i.get("tool") or i.get("text", "")[:10]) for i in d["items"]]
    assert kinds == [("tool", "bash"), ("tool", "task"), ("text", "The sum of")]
    assert d["items"][0]["output"] == "M hello.py" and d["items"][0]["status"] == "completed"
    assert d["requests"][0]["request_id"] == "req-1" and d["requests"][0]["session_hint"] == "ses_root"
    assert d["requests"][0]["tool_calls"] == ["bash"] and d["requests"][0]["has_token_ids"]
    assert obs.request_record("agent-1", "req-1")["request"]["messages"][0]["role"] == "user"
    assert obs.request_record("agent-1", "nope") is None
    assert obs.session_details("nope") is None


def test_budget_exhaustion_and_errors_surface_on_the_agent():
    obs = Observer()
    obs.apply_bundle_event(ev("harness.session", 1.0, "agent-2", session_id="s", task="t"))
    obs.apply_bundle_event(oc("session.status", 2.0, "s", "agent-2", status={"type": "busy"}))
    assert obs.snapshot()["agents"][0]["status"] == "busy"
    assert obs.snapshot()["agents"][0]["summary"] == "Waiting for the model"
    obs.apply_gateway_record({"ts": 3.0, "request_id": "r", "agent": "agent-2", "event": "budget_exhausted",
                              "dimension": "completion_tokens", "request": {}})
    a = obs.snapshot()["agents"][0]
    assert a["status"] == "blocked" and a["summary"] == "Stopped: budget used up (completion_tokens)"
    obs2 = Observer()
    obs2.apply_bundle_event(oc("session.error", 1.0, "s", "agent-3",
                               error={"name": "ProviderError", "data": {"message": "429 budget"}}))
    assert obs2.snapshot()["agents"][0]["status"] == "error"
    assert obs2.snapshot()["sessions"]["s"]["summary"] == "Stopped: 429 budget"


def test_receipts_and_duplicate_gateway_copies_are_handled():
    obs = Observer()
    obs.apply_receipt({"op": "conditions.set", "requested": {"agent_id": "agent-1", "completion_tokens": 500},
                       "result": "applied", "enforced_at": {"component": "gateway", "wallclock": 5.0}})
    assert obs.snapshot()["agents"][0]["budget"] == {"completion_tokens": 500, "receipt": "applied"}
    rec = gateway_record(6.0, "agent-1", "req-9", 1)
    obs.apply_gateway_record(rec)
    obs.apply_gateway_record(rec)   # the end-of-run copy inside the bundle must not double count
    assert obs.snapshot()["agents"][0]["requests"] == 1


def test_memory_is_bounded():
    obs = Observer()
    obs.apply_bundle_event(ev("harness.session", 0.0, "a", session_id="s", task="t"))
    for i in range(S.TIMELINE_MAX + 500):
        obs.apply_bundle_event(oc("message.part.updated", float(i), "s", "a", **text_part("s", f"p{i}", f"text {i}")))
    for i in range(S.TOOLS_MAX + 100):
        obs.apply_bundle_event(oc("message.part.updated", float(i), "s", "a",
                                  **tool_part("s", f"c{i}", "bash", "completed", {"command": str(i)})))
    for i in range(S.REQUEST_BODIES_MAX + 20):
        obs.apply_gateway_record(gateway_record(float(i), "a", f"r{i}", 1))
    node = obs.sessions["s"]
    assert len(node.items) == S.TIMELINE_MAX and len(node.tools) == S.TOOLS_MAX
    assert node.text_parts_total == S.TIMELINE_MAX + 500 and node.tool_calls_total == S.TOOLS_MAX + 100
    agent = obs.agents["a"]
    assert len(agent.request_bodies) == S.REQUEST_BODIES_MAX and agent.requests_total == S.REQUEST_BODIES_MAX + 20
    assert obs.request_record("a", "r0") is None and obs.request_record("a", f"r{S.REQUEST_BODIES_MAX + 19}") is not None
    for i in range(S.SESSIONS_MAX + 50):
        obs.apply_bundle_event(oc("session.status", float(i), f"ses{i}", "a", status={"type": "idle"}))
    assert len(obs.sessions) == S.SESSIONS_MAX


def test_tool_input_summaries():
    assert tool_input_summary("bash", {"command": "ls -la /work"}) == "ls -la /work"
    assert tool_input_summary("write", {"filePath": "/work/NOTES.md"}) == "NOTES.md"
    assert tool_input_summary("grep", {"pattern": "TODO"}) == "TODO"
    assert tool_input_summary("task", {"description": "x" * 100}).endswith("…")
    assert tool_input_summary("todowrite", {"todos": [1, 2]}) == "2 todos"
    assert tool_input_summary("mystery", {"a": 1, "b": "hello"}) == "hello"
    assert tool_input_summary("bash", None) == ""


def test_tool_phrases_are_plain_verbs():
    assert tool_phrase("bash", "git status") == "Running git status"
    assert tool_phrase("write", "NOTES.md") == "Writing NOTES.md"
    assert tool_phrase("task", "write the regex") == "Delegating: write the regex"
    assert tool_phrase("todowrite", "3 todos") == "Updating the plan"
    assert tool_phrase("bash", "") == "Running"
    assert tool_phrase("mystery", "x") == "Using mystery x"


def test_manifest_limits_gateway_callers_and_summary_sets_status():
    obs = Observer()
    obs.apply_manifest({"digest": "x", "manifest": {"agents": [{"id": "agent-1"}, {"id": "agent-2"}]}})
    assert sorted(obs.agents) == ["agent-1", "agent-2"]
    obs.apply_gateway_record(gateway_record(1.0, "curl", "r-curl", 1))
    obs.apply_gateway_record(gateway_record(2.0, "agent-1", "r-1", 1))
    snap = obs.snapshot()
    assert "curl" not in obs.agents and snap["ignored_records"] == 1
    assert next(a for a in snap["agents"] if a["agent_id"] == "agent-1")["requests"] == 1
    obs.apply_summary({"status": "ok", "rewards": {}})
    assert obs.snapshot()["status"] == "ok"


def test_service_records_join_reads_to_writes_as_interactions():
    obs = Observer()
    obs.apply_manifest({"manifest": {"agents": [{"id": "agent-1"}, {"id": "agent-2"}]}})
    obs.apply_bundle_event(ev("harness.session", 1.0, "agent-1", session_id="s1", task="t"))
    obs.apply_bundle_event(oc("session.status", 1.5, "s1", "agent-1", status={"type": "busy"}))
    obs.apply_service_record({"ts": 2.0, "service": "board", "op": "write", "agent": "agent-1", "post_id": 1,
                              "sha256": "abc", "summary": "sum is 5050"})
    obs.apply_service_record({"ts": 3.0, "service": "board", "op": "read", "agent": "agent-2", "post_id": 1, "sha256": "abc"})
    obs.apply_service_record({"ts": 3.5, "service": "board", "op": "read", "agent": "agent-1", "post_id": 1, "sha256": "abc"})
    obs.apply_service_record({"ts": 4.0, "service": "git", "op": "write", "agent": "curl", "repo": "project"})
    snap = obs.snapshot()
    assert snap["interactions"] == [{"ts": 3.0, "from": "agent-1", "to": "agent-2", "service": "board", "key": "1", "sha256": "abc"}]
    assert snap["service_records"] == 4 and snap["ignored_records"] == 1
    items = obs.session_details("s1")["items"]
    assert items[0]["kind"] == "service" and items[0]["op"] == "write" and items[0]["summary"] == "sum is 5050"
    # the end-of-run copy inside the bundle feeds the same path when no live log is tailed
    obs2 = Observer()
    obs2.apply_bundle_event(ev("service.write", 2.0, "agent-1", service="board", post_id=7, sha256="x", summary="hi"))
    obs2.apply_bundle_event(ev("service.read", 3.0, "agent-2", service="board", post_id=7, sha256="x"))
    assert obs2.snapshot()["interactions"][0]["to"] == "agent-2" and obs2.snapshot()["service_records"] == 2


def test_run_level_conditions_do_not_create_an_agent():
    obs = Observer()
    obs.apply_bundle_event(ev("conditions.set", 1.0, None, condition="channel:pastebin", enabled=False, result="applied"))
    obs.apply_bundle_event(ev("conditions.set", 1.1, "agent-1", condition="budget", result="applied"))
    snap = obs.snapshot()
    assert [a["agent_id"] for a in snap["agents"]] == ["agent-1"]
    assert snap["run_conditions"] == {"channel:pastebin": {"enabled": False, "result": "applied"}}
