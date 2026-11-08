"""Score saved AgentCollabBench runs with the official metrics.

Usage: python acb_score.py <runs_root> <scores.jsonl> [--judge-base-url URL --judge-model NAME] [--workers 8]
                           [--condition baseline]

<runs_root> holds <metric>/<condition>/<model>/<task_id>.json files written by acb_run.py. RTD and CLC are
programmatic. IDR and CPR use an LLM judge through the official LLMJudge; pass an OpenAI-compatible judge endpoint
(the paper's default judge is DeepSeek V4 Flash). Already scored (metric, condition, model, task) rows are skipped.
"""

from __future__ import annotations

import json
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from agentcollabbench.harness.evaluate import _compute_metric
from agentcollabbench.harness.provider import OllamaProvider


def arg(name: str, default: str | None = None) -> str | None:
    return sys.argv[sys.argv.index(name) + 1] if name in sys.argv else default


def main() -> None:
    root, out = Path(sys.argv[1]), Path(sys.argv[2])
    workers = int(arg("--workers", "8"))
    judge = None
    if arg("--judge-base-url"):
        judge = OllamaProvider(model=arg("--judge-model"), base_url=arg("--judge-base-url"), api_key="EMPTY",
                               max_tokens=4096)
    done = set()
    if out.exists():
        for line in out.read_text().splitlines():
            r = json.loads(line)
            done.add((r["metric"], r["condition"], r["model"], r["task_id"]))
    jobs = []
    for f in sorted(root.glob("*/*/*/*.json")):
        metric, cond, model = f.parts[-4], f.parts[-3], f.parts[-2]
        if (metric, cond, model, f.stem) in done:
            continue
        if metric in ("idr", "cpr") and judge is None:
            continue
        if arg("--condition") and cond != arg("--condition"):
            continue
        jobs.append((metric, cond, model, f))
    lock = threading.Lock()

    def one(job: tuple) -> None:
        metric, cond, model, f = job
        rec = json.loads(f.read_text())
        row = {"metric": metric, "condition": cond, "model": model, "task_id": f.stem}
        if "error" in rec:
            row["run_error"] = rec["error"]
        else:
            run = rec["run"]
            cfg = {"judge_provider": judge} if metric in ("idr", "cpr") else {}
            try:
                score, detailed = _compute_metric(metric, run["scenario"], run["output"], run["trace"], cfg, None)
                row["score"] = score
                row["detailed"] = detailed if isinstance(detailed, dict | list | str | int | float) else repr(detailed)
            except Exception as exc:
                row["score_error"] = repr(exc)
        with lock, out.open("a") as fh:
            fh.write(json.dumps(row, default=str) + "\n")

    with ThreadPoolExecutor(workers) as pool:
        list(pool.map(one, jobs))
    print(json.dumps({"scored": len(jobs)}))


if __name__ == "__main__":
    main()
