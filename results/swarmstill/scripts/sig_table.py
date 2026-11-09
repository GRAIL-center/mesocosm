"""Markdown table of harness signatures. Usage: python sig_table.py <signature.json> <flags_dir> <out.md> [rounds...]"""
import json, pathlib, sys
sig = json.loads(pathlib.Path(sys.argv[1]).read_text()); fl = pathlib.Path(sys.argv[2]); rounds = sys.argv[4:] or ["ref", "r1", "r2", "r3"]
NAME = {"main": "Qwen3-4B, original settings", "mem0": "Qwen3-4B, original, no memory"}
def short(name):
    p = fl / f"{name}.flags"
    if not p.exists(): return ""
    t = p.read_text().split(); keep = []
    i = 0
    while i < len(t):
        a = t[i]; b = t[i + 1] if i + 1 < len(t) and not t[i + 1].startswith("--") else None
        if a in ("--alice-temperature", "--bob-cross-episode-memory-length", "--char-limit", "--max-rounds", "--reward", "--reflection", "--verdict-review", "--bob-temperature"): pass
        elif a == "--task-sequence-record": keep.append("3 warm-up episodes")
        else: keep.append(a.lstrip("-").replace("alice-", "") + (f" {b}" if b else ""))
        i += 2 if b else 1
    return "; ".join(keep)
rows = ["| Harness | Round | Changes from original | n | Episodes | Ep. 1 | Change/ep. | Overall (teacher) | Own task |", "|---|---|---|---|---|---|---|---|---|"]
for k, s in sorted(sig.items(), key=lambda x: (rounds.index(x[1]["round"]) if x[1]["round"] in rounds else 9, x[1]["distance"])):
    if s["round"] not in rounds: continue
    rows.append(f"| {NAME.get(k, k)} | {s['round']} | {short(k)} | {s['n_traj']} | 1–{s['K']} | {s['ep1']:.0f}% | {s['slope']:+.1f} | "
                f"{s['rate']:.0f}% ({s['target_rate_same_window']:.0f}%) | {(s['own_task'] or 0):.0f}% |")
pathlib.Path(sys.argv[3]).write_text("\n".join(rows) + "\n\nTeacher (Gemini 3.1 Flash-Lite): 0% in episode 1, +8.9 points per episode over 1–10, 42.4% overall.\n")
print("\n".join(rows))
