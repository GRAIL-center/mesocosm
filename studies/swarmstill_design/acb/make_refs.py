"""Measure the student (Qwen3-4B-Instruct-2507) in the original environment for one metric and write ref_<metric>.json
with the Qwen3-8B target scores. Usage: python make_refs.py <metric> [run_label]"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

HOME = Path(__file__).resolve().parent
sys.path.insert(0, str(HOME))
import daemon  # noqa: E402

metric = sys.argv[1]
label = sys.argv[2] if len(sys.argv) > 2 else "orig_r1"
out = f"/workspace/design/runs/ref/{metric}_{label}"
subprocess.run(daemon.ACB + [f"mkdir -p {out} && echo '{{}}' > {out}/spec.json && cd /workspace/study && "
                             f"/workspace/acb-env/bin/python acb_eval.py {metric} {out}/spec.json "
                             f"{daemon.TASKS[metric]} qwen3-4b http://127.0.0.1:8000/v1 {out} > {out}/eval.log 2>&1"],
               check=True, timeout=3 * 3600)
local = HOME / "runs" / "ref" / f"{metric}_{label}"
local.mkdir(parents=True, exist_ok=True)
tar = subprocess.run(daemon.ACB + [f"cd {out} && tar czf - --exclude=spec.json --exclude=eval.log ."],
                     capture_output=True, timeout=600)
subprocess.run(["tar", "xzf", "-", "-C", str(local)], input=tar.stdout, check=True)
fake = daemon.Session.__new__(daemon.Session)
fake.metric, fake.name = metric, f"ref_{metric}_{label}"
student = daemon.Session.per_task_scores(fake, label, local)
summary = json.loads((local / "summary.json").read_text())
topo = {t: v["topology"] for t, v in summary.items()}
teacher = json.loads((HOME / "teacher_scores.json").read_text())[metric]["teacher"]
path = HOME / f"ref_{metric}.json"
ref = json.loads(path.read_text()) if path.exists() else {}
ref.update(topology=topo, teacher=teacher)
ref["student" if label == "orig_r1" else f"student_{label}"] = student
path.write_text(json.dumps(ref, indent=1))
print(metric, label, "student", daemon.signature(student, topo), "teacher", daemon.signature(teacher, topo),
      "missing", [t for t in topo if student.get(t) is None])
