"""Write the 20 IDR task files (unchanged JSON from the AgentCollabBench train split) into tasks/loop and tasks/heldout.
Usage: python3 extract_tasks.py <train.jsonl> <split.json> <out_dir>"""
from __future__ import annotations

import json
import sys
from pathlib import Path

from prepare import load_tasks

train, split, out = Path(sys.argv[1]), json.loads(Path(sys.argv[2]).read_text()), Path(sys.argv[3])
tasks = load_tasks(train, {t for v in split.values() for t in v})
for name, ids in split.items():
    d = out / name
    d.mkdir(parents=True, exist_ok=True)
    for t in ids:
        (d / f"{t}.json").write_text(json.dumps(tasks[t], indent=1))
print({k: len(v) for k, v in split.items()}, "missing:", sorted({t for v in split.values() for t in v} - set(tasks)))
