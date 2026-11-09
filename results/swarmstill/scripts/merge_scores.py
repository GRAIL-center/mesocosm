"""Merge score files, keeping the FIRST row per (metric, condition, model, task_id).
Usage: python merge_scores.py <out.jsonl> <first.jsonl> [more.jsonl ...]
The first file holds the original baseline scores the designers saw, so they win over any later duplicate.
"""
import json
import sys

out, files = sys.argv[1], sys.argv[2:]
seen, rows, dups = set(), [], 0
for f in files:
    for line in open(f):
        r = json.loads(line)
        k = (r["metric"], r["condition"], r["model"], r["task_id"])
        if k in seen:
            dups += 1
            continue
        seen.add(k)
        rows.append(line if line.endswith("\n") else line + "\n")
open(out, "w").writelines(rows)
print(json.dumps({"rows": len(rows), "duplicates_dropped": dups}))
