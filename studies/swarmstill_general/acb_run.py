"""Run AgentCollabBench tasks with the official runner against an OpenAI-compatible model server; save traces.

Usage: python acb_run.py <condition_dir> <metric> <model> <base_url> <out_dir> [--workers 16] [--max-tokens 2048]

Each task is executed by agentcollabbench.harness.runner.TaskRunner exactly as the official evaluator does, with the
official OpenAI-compatible provider. The RunResult (scenario, output, trace) is written to <out_dir>/<task_id>.json.
Existing outputs are skipped, so the command can be re-run to resume. Scoring is a separate step (acb_score.py).
"""

from __future__ import annotations

import json
import sys
import time
import traceback
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from agentcollabbench.harness.provider import OllamaProvider
from agentcollabbench.harness.runner import TaskRunner
from agentcollabbench.harness.schema import validate_task


def main() -> None:
    cond_dir, metric, model, base_url, out = Path(sys.argv[1]), sys.argv[2], sys.argv[3], sys.argv[4], Path(sys.argv[5])
    workers = int(sys.argv[sys.argv.index("--workers") + 1]) if "--workers" in sys.argv else 16
    max_tokens = int(sys.argv[sys.argv.index("--max-tokens") + 1]) if "--max-tokens" in sys.argv else 2048
    out.mkdir(parents=True, exist_ok=True)
    provider = OllamaProvider(model=model, base_url=base_url, api_key="EMPTY", max_tokens=max_tokens)

    def one(path: Path) -> str:
        dest = out / path.name
        if dest.exists():
            return "skip"
        task = json.loads(path.read_text())
        t0 = time.time()
        try:
            validate_task(task)
            result = TaskRunner(provider=provider, metric=metric).run_task(task).to_dict()
            rec = {"task_id": task["task_id"], "metric": metric, "model": model, "seconds": time.time() - t0,
                   "run": result}
        except Exception as exc:  # recorded, never silently dropped
            rec = {"task_id": task.get("task_id"), "metric": metric, "model": model, "error": repr(exc),
                   "traceback": traceback.format_exc()[-2000:]}
        dest.write_text(json.dumps(rec))
        return "error" if "error" in rec else "ok"

    paths = sorted(cond_dir.glob("*.json"))
    with ThreadPoolExecutor(workers) as pool:
        status = list(pool.map(one, paths))
    print(json.dumps({"condition": str(cond_dir), "model": model, "ok": status.count("ok"),
                      "error": status.count("error"), "skip": status.count("skip")}))


if __name__ == "__main__":
    main()
