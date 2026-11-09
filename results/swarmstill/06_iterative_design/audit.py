"""Audit a designer session's tool use: every command / file access, flagging anything that leaves the workspace or
reaches the network. Usage: python audit.py codex <events.jsonl> <ws>   |   python audit.py claude <transcript.jsonl> <ws>
"""
import json
import re
import sys

SUSPECT = re.compile(r"\bssh\b|\bscp\b|\bcurl\b|\bwget\b|runpod|\.runpod|8007|8006|8004|38\.128|/private/tmp|scratchpad|"
                     r"acb_design/ws_|\.\./|~/|/Users/(?!seunghyun/acb_design/ws_)|http", re.I)


def codex(path, ws):
    rows = []
    for line in open(path):
        try:
            e = json.loads(line)
        except ValueError:
            continue
        item = e.get("item") or {}
        if e.get("type") == "item.completed" and item.get("type") == "command_execution":
            rows.append(("cmd", item.get("command", ""), item.get("exit_code")))
        elif e.get("type") == "item.completed" and item.get("type") in ("file_change", "mcp_tool_call", "web_search"):
            rows.append((item["type"], json.dumps(item)[:300], None))
    return rows


def claude(path, ws):
    rows = []
    for line in open(path):
        try:
            e = json.loads(line)
        except ValueError:
            continue
        msg = e.get("message") or {}
        for c in msg.get("content", []) if isinstance(msg.get("content"), list) else []:
            if c.get("type") == "tool_use":
                inp = c.get("input", {})
                rows.append((c.get("name"), inp.get("command") or inp.get("file_path") or inp.get("pattern")
                             or json.dumps(inp)[:300], None))
    return rows


def main():
    kind, path, ws = sys.argv[1:4]
    rows = (codex if kind == "codex" else claude)(path, ws)
    flagged = [r for r in rows if r[1] and SUSPECT.search(str(r[1]).replace(ws, "<WS>"))]
    print(f"{len(rows)} tool calls; {len(flagged)} flagged")
    for r in flagged:
        print("  FLAG", r[0], str(r[1])[:400])
    kinds = {}
    for r in rows:
        kinds[r[0]] = kinds.get(r[0], 0) + 1
    print("by tool:", kinds)


if __name__ == "__main__":
    main()
