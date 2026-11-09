"""Build loop_reference2.json from the operator's original-environment runs on the loop2 tasks (r1 of q8, q4, g4)."""
import json
import subprocess
from pathlib import Path

HOME = Path(__file__).resolve().parent
SSH = ["ssh", "-o", "BatchMode=yes", "-i", str(Path.home() / ".runpod/ssh/runpodctl-ssh-key"), "-p", "<POD_PORT>",
       "root@<POD_HOST>"]
GROUPS = ["branching_tree", "converging_dag", "custom_graph"]
out = {"topology": {}, "loop": {}}
for m in ("q8", "q4", "g4"):
    summary = json.loads(subprocess.run(SSH + [f"cat /workspace/design/runs/ref2/loop_{m}_r1/summary.json"],
                                        capture_output=True, text=True, check=True).stdout)
    errors = {t: v["error"] for t, v in summary.items() if v.get("error")}
    assert not errors, (m, errors)
    per = {t: v["score"] for t, v in sorted(summary.items())}
    out["topology"] = {t: v["topology"] for t, v in sorted(summary.items())}
    sig = {g: round(sum(100 * per[t] for t in per if out["topology"][t] == g) /
                    sum(1 for t in per if out["topology"][t] == g), 2) for g in GROUPS}
    out["loop"][m] = {"per_task": per, "signature": sig}
    print(m, sig)
(HOME / "loop_reference2.json").write_text(json.dumps(out, indent=1))
