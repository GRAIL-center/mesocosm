"""Claim 1-1: how a scripted peer shifts the same model's acceptance. Paired by sequence_id, bootstrap 2000.

Usage: python peer_contrasts.py <parquet_dir> <out_json>
2x2 design (Gemini 3.1 Flash-Lite as Alice; Bob scripted, full feedback):
  peer message type: raw-prefix (first raw-log chunks) vs summary;  peer verdict: accept vs reject.
"""
import json
import pathlib
import random
import sys

import pyarrow.parquet as pq

d, out = pathlib.Path(sys.argv[1]), pathlib.Path(sys.argv[2])
P = "controlled_peer__gemini-3-1-flash-lite__"


def per_seq(cond):
    runs = {}
    for r in pq.read_table(d / f"{P}{cond}--full-feedback.parquet").to_pylist():
        if not r["is_warmup"]:
            runs.setdefault(r["sequence_id"], []).append((r["alice_verdict"] or "").lower() == "accept")
    return {s: sum(v) / len(v) for s, v in runs.items()}


cells = {c: per_seq(c) for c in ("raw-prefix--accept", "raw-prefix--reject", "summary--accept", "summary--reject")}
rng = random.Random(11)


def contrast(a, b):
    seqs = sorted(set(cells[a]) & set(cells[b]))
    diffs = [cells[a][s] - cells[b][s] for s in seqs]
    n = len(diffs)
    st = sorted(sum(diffs[rng.randrange(n)] for _ in range(n)) / n for _ in range(2000))
    return {"a": a, "b": b, "delta_pts": 100 * sum(diffs) / n, "ci": [100 * st[50], 100 * st[1949]], "n_pairs": n}


res = {"cells": {c: 100 * sum(v.values()) / len(v) for c, v in cells.items()},
       "peer_verdict_effect": [contrast("raw-prefix--accept", "raw-prefix--reject"),
                               contrast("summary--accept", "summary--reject")],
       "peer_message_effect": [contrast("summary--accept", "raw-prefix--accept"),
                               contrast("summary--reject", "raw-prefix--reject")],
       "paper_headline": contrast("summary--accept", "raw-prefix--reject")}
out.write_text(json.dumps(res, indent=1))
print(json.dumps(res, indent=1))
