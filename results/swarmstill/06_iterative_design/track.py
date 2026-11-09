"""Print each session's test trajectory from the operator ledger. Usage: python track.py [session ...]"""
import json
import sys
from pathlib import Path

HOME = Path(__file__).resolve().parent
ABBR = {"topology": {"original": "orig", "chain": "chain", "full": "full", "root_broadcast": "rootB"},
        "salience": {"high": "hi", "low": "lo"}, "distractors": {"all": "dAll", "half": "dHalf", "none": "dNone"}}


def short(spec):
    return "%s x%s %s %s" % (ABBR["topology"][spec["topology"]], spec["turns"], ABBR["salience"][spec["salience"]],
                             ABBR["distractors"][spec["distractors"]])


def main():
    names = sys.argv[1:] or sorted(p.stem for p in (HOME / "ledger").glob("*.jsonl"))
    for name in names:
        events = [json.loads(x) for x in (HOME / "ledger" / f"{name}.jsonl").read_text().splitlines()]
        print(f"== {name}")
        for e in events:
            if e["event"] == "done":
                s, p = e["student_signature"], e["prediction"]
                print(f"  {e['test_id']} {short(e['spec']):24s} sig {s['branching_tree']:5.1f} {s['converging_dag']:5.1f} "
                      f"{s['custom_graph']:5.1f} | pred {p['branching_tree']:5.1f} {p['converging_dag']:5.1f} "
                      f"{p['custom_graph']:5.1f} | align {e['alignment']:6.1f} {e['alignment_ci95']} "
                      f"| pred err {e['prediction_error']:5.1f} | {e['submitted_at'][11:16]}-{e['finished_at'][11:16]}")
            elif e["event"] in ("submit", "rejected", "platform_failure", "finalize", "heldout", "heldout_failure",
                                "daemon_error"):
                extra = e.get("test_id", "") if e["event"] != "rejected" else e.get("reason", "")[:80]
                if e["event"] == "submit":
                    extra = f"{e['test_id']} {short(e['spec'])}"
                print(f"  [{e['at'][11:16]}] {e['event']} {extra}")


if __name__ == "__main__":
    main()
