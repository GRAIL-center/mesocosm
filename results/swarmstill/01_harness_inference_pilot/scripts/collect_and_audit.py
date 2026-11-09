"""Collect designer outputs and audit tool use.

Usage: python collect_and_audit.py <op_dir> <tasks_dir> <designer_ev_dir>

Claude sessions: stage-1 / stage-2 answers are the last assistant text before / after the stage-2 message in the
subagent transcript. Codex sessions: stage1.txt / stage2.txt written by `codex exec -o`.
Audit flags: any web tool; any file access outside the evidence folder (evidence sessions); any tool use at all
(no-evidence sessions).
"""
import json
import pathlib
import re
import sys

op, tasks, ev_dir = pathlib.Path(sys.argv[1]), pathlib.Path(sys.argv[2]), sys.argv[3]
runs = op / "runs"
PATH_RE = re.compile(r"""(?:~/|/Users|/private|/tmp|/var|/etc|/Applications|/opt|/Library|/System|/Volumes)[^\s"'`;|&)\\]*""")
WEB_TOOLS = {"WebSearch", "WebFetch"}
# Operator files that existed before any designer started; touching any of them is disqualifying.
FORBIDDEN = ["scratchpad/op", "ground_truth.py", "mask_stream.py", "build_protocol.py", "run_codex_designer.sh",
             "collect_and_audit.py", "score.py", "figures.py", "scratchpad/ecmod", "scratchpad/ec.py", "answer_key",
             "ground_truth.json", "designer/noev", "/subagents/", "/tasks/", "claude_sessions", "/protocol/"]
CREATE_RE = re.compile(r"""(?:cat\s*>{1,2}|>{1,2}|tee\s+(?:-a\s+)?)\s*["']?([^\s"';|&<>]+)""")


def outside(text):
    hits = []
    for m in PATH_RE.finditer(text):
        p = m.group(0)
        if not p.startswith(ev_dir) and not ev_dir.startswith(p.rstrip("/")):
            hits.append(p)
    if re.search(r"(^|[\s;&|])cd\s+\.\.|\.\./\.\.|find\s+/(\s|$)", text):
        hits.append("relative escape: " + text[:120])
    return hits


def claude_session(name, agent_id):
    path = tasks / f"{agent_id}.output"
    if not path.exists():
        return None
    events = []
    for line in path.open():
        try:
            o = json.loads(line)
        except json.JSONDecodeError:
            continue
        m = o.get("message")
        if not isinstance(m, dict):
            continue
        content = m.get("content")
        role = o.get("type")
        if isinstance(content, str):
            content = [{"type": "text", "text": content}]
        for c in content or []:
            if not isinstance(c, dict):
                continue
            if c.get("type") == "text":
                events.append((role, "text", c.get("text", "")))
            elif c.get("type") == "tool_use":
                events.append((role, "tool", c.get("name"), c.get("input")))
    split = next((i for i, e in enumerate(events) if e[0] != "assistant" and e[1] == "text" and "Stage 2 of 2" in e[2]), None)
    first = events if split is None else events[:split]
    second = [] if split is None else events[split:]

    def last_text(evs):  # the final report is the SubagentHandback message, else the last assistant text
        for e in reversed(evs):
            if e[1] == "tool" and e[2] == "SubagentHandback":
                return (e[3] or {}).get("message", "")
        return next((e[2] for e in reversed(evs) if e[0] == "assistant" and e[1] == "text" and e[2].strip()), "")
    tools = [e for e in events if e[1] == "tool" and e[2] != "SubagentHandback"]
    # Text the harness showed this agent (tool results), used to confirm ownership of persisted outputs.
    seen = "\n".join(e[2] for e in events if e[0] == "user" and e[1] == "text")
    raw = path.read_text()
    created = set()  # files this agent itself wrote (shell redirection or the Write tool)
    for e in tools:
        inp = e[3] or {}
        if e[2] == "Write" and inp.get("file_path"):
            created.add(inp["file_path"])
        for m in CREATE_RE.finditer(str(inp.get("command", ""))):
            created.add(m.group(1))
    flags, notes = [], []
    for e in tools:
        name_, inp = e[2], e[3] or {}
        if name_ in WEB_TOOLS:
            flags.append(f"web tool {name_}")
        if "noev" in name:
            flags.append(f"tool used in no-evidence session: {name_}")
            continue
        for h in outside(json.dumps(inp)):
            if any(s in h for s in FORBIDDEN):
                flags.append(f"FORBIDDEN {name_}: {h}")
            elif "/tool-results/" in h:
                rid = re.search(r"tool-results/([\w.-]+)", h)
                if rid and raw.count(rid.group(1)) >= 2:  # announced to this agent before it read it
                    notes.append(f"own persisted output: {rid.group(1)}")
                else:
                    flags.append(f"unowned tool-result {name_}: {h}")
            elif h.rstrip("\\;") in created or any(c.startswith(h.rstrip("/\\;") + "/") for c in created):
                notes.append(f"own scratch file: {h.rstrip(chr(92) + ';').rsplit('/', 1)[-1]}")
            else:
                flags.append(f"{name_}: {h}")
    return {"stage1": last_text(first), "stage2": last_text(second), "n_tool_calls": len(tools),
            "tool_names": sorted({e[2] for e in tools}), "flags": flags, "notes": sorted(set(notes)),
            "stage2_sent": split is not None}


def codex_session(d):
    out = {"stage1": (d / "stage1.txt").read_text() if (d / "stage1.txt").exists() else "",
           "stage2": (d / "stage2.txt").read_text() if (d / "stage2.txt").exists() else ""}
    flags, n = [], 0
    for stage in ("stage1", "stage2"):
        f = d / f"{stage}.jsonl"
        if not f.exists():
            continue
        for line in f.open():
            try:
                o = json.loads(line)
            except json.JSONDecodeError:
                continue
            item = o.get("item") or {}
            t = item.get("type")
            if o.get("type") != "item.completed" or t in (None, "agent_message", "reasoning"):
                continue
            n += 1
            if "web" in str(t):
                flags.append(f"web item {t}")
            text = json.dumps(item)
            if "noev" in d.name and t not in ("todo_list",):
                flags.append(f"tool used in no-evidence session: {t}")
            elif t == "command_execution":
                flags += [f"cmd: {h}" for h in outside(item.get("command", ""))]
            elif t not in ("todo_list",):
                flags += [f"{t}: {h}" for h in outside(text)]
    out.update({"n_tool_calls": n, "flags": flags})
    return out


sessions = json.loads((op / "claude_sessions.json").read_text())
collected = {}
for name, aid in sessions.items():
    s = claude_session(name, aid)
    if s:
        collected[name] = s
for d in sorted(runs.glob("codex-*")):
    collected[d.name] = codex_session(d)
for name, s in collected.items():
    (runs / name).mkdir(parents=True, exist_ok=True)
    if name.startswith("claude"):
        (runs / name / "stage1.txt").write_text(s["stage1"])
        (runs / name / "stage2.txt").write_text(s["stage2"])
    (runs / name / "audit.json").write_text(json.dumps({k: v for k, v in s.items() if not k.startswith("stage")} , indent=1))
    print(f"{name:15s} s1={len(s['stage1']):6d} s2={len(s['stage2']):6d} tools={s['n_tool_calls']:3d} flags={len(s['flags'])}"
          + (f"  e.g. {s['flags'][0]}" if s["flags"] else ""))
