"""Run the branch's `ec.py mask` without storing raw traces on disk.

Usage: python mask_stream.py <ec.py dir> <designer_dir> <operator_dir> [n]

Traces are streamed from the Hugging Face release and gunzipped in memory. Selection is ec.py's own
seeded, outcome-blind sample over the 50 released runs of the baseline condition.
"""
import argparse
import gzip
import hashlib
import io
import json
import pathlib
import sys
import urllib.request

sys.path.insert(0, sys.argv[1])
import ec  # noqa: E402

BASE = "https://huggingface.co/datasets/SALT-NLP/agent-collusion/resolve/main/"
SHA = {}


def fake_files(data, condition):
    folder = ec.condition_dir(data, condition)
    return [folder / f"rep{i:03d}.json.gz" for i in range(1, 51)]


def stream_trace(path):
    url = BASE + path.relative_to(pathlib.Path("DATA")).as_posix()
    with urllib.request.urlopen(url, timeout=120) as resp:
        raw = resp.read()
    SHA[path.name] = hashlib.sha256(raw).hexdigest()
    run = json.load(io.TextIOWrapper(gzip.GzipFile(fileobj=io.BytesIO(raw)), encoding="utf-8"))
    print("streamed", path.name, len(raw) // 1024, "KiB", file=sys.stderr)
    return run


ec.trace_files = fake_files
ec.load_trace = stream_trace
ec.sha256_file = lambda p: SHA.get(p.name, "")

n = int(sys.argv[4]) if len(sys.argv) > 4 else 20
args = argparse.Namespace(data=pathlib.Path("DATA"), condition=ec.DEFAULT_BASELINE, n=n, seed=20261107,
                          designer=pathlib.Path(sys.argv[2]), operator=pathlib.Path(sys.argv[3]))
print(json.dumps(ec.cmd_mask(args), indent=1))
