"""Operator-side service for an AgentCollabBench S/E design session (RTD, CPR or IDR; student Qwen3-4B).
Same request protocol as the collusion service (the designer's ./swarmstill tool). Runs each test on the ACB pod;
RTD is scored there, CPR/IDR are judge-scored on pod1 (the judge key stays on pod1).
Usage: python daemon.py <session> <workspace> <metric> [budget]"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
import traceback
from pathlib import Path

HOME = Path(__file__).resolve().parent
# Hosts from the environment: SWARMSTILL_ACB_HOST/PORT (AgentCollabBench runs and the student server) and
# SWARMSTILL_JUDGE_HOST/PORT (the machine with the LLM-judge endpoint at 127.0.0.1:8100); SWARMSTILL_SSH_KEY optional.
KEY = ["-o", "BatchMode=yes", "-o", "ConnectTimeout=20", "-o", "ServerAliveInterval=30",
       *(["-i", os.environ["SWARMSTILL_SSH_KEY"]] if os.environ.get("SWARMSTILL_SSH_KEY") else [])]
ACB = ["ssh", *KEY, "-p", os.environ.get("SWARMSTILL_ACB_PORT", "22"), os.environ.get("SWARMSTILL_ACB_HOST", "")]
POD1 = ["ssh", *KEY, "-p", os.environ.get("SWARMSTILL_JUDGE_PORT", "22"), os.environ.get("SWARMSTILL_JUDGE_HOST", "")]
FACTORS = {  # designer-facing name -> (axis, acb_eval spec key, domain, default, meaning)
    "S.topology": ("topology", ["original", "chain", "full", "root_broadcast"], "original",
                   "who receives from whom: the task's own graph, a chain in speaking order, everyone hears "
                   "everyone, or the original graph plus root -> every agent"),
    "S.turns": ("turns", [0.5, 1, 1.5, 2], 1, "conversation length: speaking-order length multiplier"),
    "E.constraint_salience": ("salience", ["high", "low"], "high",
                              "how the tagged constraint is formatted (high: **CRITICAL CONSTRAINT [ID]: ...**)"),
    "E.distractor_load": ("distractors", ["all", "half", "none"], "all",
                          "how many of the task's distractor lines accompany the constraint"),
    "E.false_fact_carrier_position": ("seed_position", ["original", "first", "middle", "last"], "original",
                                      "which agent in the speaking order is given the false fact"),
    "E.peer_pressure_messages": ("pressure", ["all", "half", "first"], "all",
                                 "how many of the task's own peer-pressure messages are delivered"),
}
BY_METRIC = {"rtd": ["S.topology", "S.turns", "E.constraint_salience", "E.distractor_load"],
             "cpr": ["S.topology", "S.turns", "E.false_fact_carrier_position"],
             "idr": ["S.topology", "S.turns", "E.peer_pressure_messages"]}
TASKS = {"rtd": "/workspace/design/loop2", "cpr": "/workspace/design/cpr", "idr": "/workspace/design/idr"}


def now():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def signature(per, topo):
    out = {}
    for g in sorted(set(topo.values())):
        xs = [100 * per[t] for t in per if topo.get(t) == g and per[t] is not None]
        out[g] = round(sum(xs) / len(xs), 1) if xs else None
    return out


def gap(a, b):
    gs = [g for g in a if a.get(g) is not None and b.get(g) is not None]
    return sum(abs(a[g] - b[g]) for g in gs) / len(gs)


class Session:
    def __init__(self, name, ws, metric, budget):
        self.name, self.ws, self.metric, self.budget = name, Path(ws), metric, budget
        self.sw = self.ws / ".sw"
        for d in ("requests", "responses", "results"):
            (self.sw / d).mkdir(parents=True, exist_ok=True)
        ref = json.loads((HOME / f"ref_{metric}.json").read_text())
        self.topo, self.teacher, self.student0 = ref["topology"], ref["teacher"], ref["student"]
        self.t_sig, self.s0_sig = signature(self.teacher, self.topo), signature(self.student0, self.topo)
        self.gap0 = gap(self.s0_sig, self.t_sig)
        self.ledger = HOME / "ledger" / f"{name}.jsonl"
        self.ledger.parent.mkdir(exist_ok=True)
        self.tests, self.running, self.final = {}, None, None

    def log(self, **e):
        with self.ledger.open("a") as fh:
            fh.write(json.dumps(dict(e, at=now()), ensure_ascii=False, default=str) + "\n")

    def write(self, path, obj):
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(obj, indent=1, ensure_ascii=False, default=str))
        tmp.rename(path)

    def respond(self, rid, obj):
        self.write(self.sw / "responses" / f"{rid}.json", obj)

    def used(self):
        return sum(1 for r in self.tests.values() if not r.get("refunded"))

    def check(self, spec):
        if not isinstance(spec, dict) or not isinstance(spec.get("changes"), list):
            return None, "the spec must be {\"changes\": [{\"factor\": ..., \"value\": ...}, ...]}"
        allowed, eval_spec, seen = BY_METRIC[self.metric], {}, set()
        for ch in spec["changes"]:
            if not isinstance(ch, dict) or set(ch) != {"factor", "value"}:
                return None, "each change must be exactly {\"factor\": ..., \"value\": ...}"
            f, v = ch["factor"], ch["value"]
            if f not in allowed:
                return None, f"unknown factor {f!r}; allowed: {allowed}"
            if f in seen:
                return None, f"duplicate factor {f}"
            key, dom, default, _ = FACTORS[f]
            if v not in dom or type(v) is not type(default):
                return None, f"{f}={v!r} is outside its domain {dom}"
            seen.add(f)
            eval_spec[key] = v
        return eval_spec, None

    def handle(self, rid, req):
        cmd = req.get("cmd")
        if self.final:
            return self.respond(rid, {"error": f"the session is complete (final design {self.final})"})
        if cmd == "history":
            keys = ("test_id", "status", "changes", "prediction", "signature", "distance", "alignment")
            return self.respond(rid, {"tests_used": self.used(), "tests_left": self.budget - self.used(),
                                      "tests": [{k: r[k] for k in keys if k in r} for r in self.tests.values()]})
        if cmd == "check":
            spec, err = self.check(req.get("spec"))
            return self.respond(rid, {"error": err} if err else {"valid": True, "effective_spec": spec})
        if cmd == "run":
            body = req.get("body") or {}
            spec, err = self.check({"changes": body.get("changes")})
            if err:
                return self.respond(rid, {"error": err})
            if self.used() >= self.budget:
                return self.respond(rid, {"error": f"no tests left ({self.budget} used); submit one of your tests"})
            if self.running:
                return self.respond(rid, {"error": f"test {self.running} is still running; wait for it first"})
            tid = f"t{len(self.tests) + 1:02d}"
            rec = {"test_id": tid, "status": "running", "changes": body["changes"], "effective_spec": spec,
                   "prediction": body.get("prediction"), "rationale": body.get("rationale"), "submitted_at": now()}
            self.tests[tid], self.running = rec, tid
            self.write(self.sw / "results" / f"{tid}.json", rec)
            self.log(event="run", **rec)
            threading.Thread(target=self.run_test, args=(tid, spec), daemon=True).start()
            return self.respond(rid, {"test_id": tid, "status": "running", "tests_left": self.budget - self.used(),
                                      "next": f"./swarmstill wait {tid} 600"})
        if cmd == "submit":
            tid = req.get("test_id")
            if tid not in self.tests or self.tests[tid].get("status") != "done":
                return self.respond(rid, {"error": f"{tid} is not a finished test"})
            self.final = tid
            self.log(event="submit", test_id=tid, changes=self.tests[tid]["changes"])
            return self.respond(rid, {"submitted": tid, "message": "Final design recorded. The session is complete."})
        return self.respond(rid, {"error": f"unknown command {cmd!r}"})

    def per_task_scores(self, tid, local):
        if self.metric == "rtd":
            summ = json.loads((local / "summary.json").read_text())
            return {t: v.get("score") for t, v in summ.items()}
        # judge scoring on pod1: copy runs into acb_score's layout, score with retries, read back
        root = f"/workspace/acbd_score/{self.name}/{tid}"
        tar = subprocess.run(["tar", "czf", "-", "-C", str(local), "."], capture_output=True, check=True)
        subprocess.run(POD1 + [f"rm -rf {root}; mkdir -p {root}/{self.metric}/{tid}/qwen3-4b && "
                               f"tar xzf - -C {root}/{self.metric}/{tid}/qwen3-4b && rm -f "
                               f"{root}/{self.metric}/{tid}/qwen3-4b/summary.json"],
                       input=tar.stdout, check=True, timeout=600)
        score_cmd = (f"cd /workspace && for p in 1 2 3; do /workspace/acb-env/bin/python study/acb_score.py {root} "
                     f"{root}/scores.jsonl --judge-base-url http://127.0.0.1:8100/v1 --judge-model deepseek-v4-flash "
                     f"--workers 8 > /dev/null 2>&1; n=$(grep -c score_error {root}/scores.jsonl); [ \"$n\" = 0 ] && "
                     f"break; grep -v score_error {root}/scores.jsonl > {root}/s.tmp; mv {root}/s.tmp "
                     f"{root}/scores.jsonl; done; cat {root}/scores.jsonl")
        out = subprocess.run(POD1 + [score_cmd], capture_output=True, text=True, timeout=3600).stdout
        rows = [json.loads(x) for x in out.splitlines() if x.strip()]
        return {r["task_id"]: r.get("score") for r in rows}

    def run_test(self, tid, spec):
        rec = self.tests[tid]
        out = f"/workspace/design/runs/{self.name}/{tid}"
        try:
            cmd = (f"mkdir -p {out} && cat > {out}/spec.json && cd /workspace/study && /workspace/acb-env/bin/python "
                   f"acb_eval.py {self.metric} {out}/spec.json {TASKS[self.metric]} qwen3-4b "
                   f"http://127.0.0.1:8000/v1 {out} > {out}/eval.log 2>&1; tail -1 {out}/eval.log")
            subprocess.run(ACB + [cmd], input=json.dumps(spec), capture_output=True, text=True, timeout=3 * 3600)
            local = HOME / "runs" / self.name / tid
            local.mkdir(parents=True, exist_ok=True)
            tar = subprocess.run(ACB + [f"cd {out} && tar czf - --exclude=spec.json --exclude=eval.log ."],
                                 capture_output=True, timeout=600)
            subprocess.run(["tar", "xzf", "-", "-C", str(local)], input=tar.stdout, check=True)
            per = self.per_task_scores(tid, local)
            sig = signature(per, self.topo)
            d = gap(sig, self.t_sig)
            dest = self.ws / "results" / tid
            dest.mkdir(parents=True, exist_ok=True)
            for f in local.glob("TASK-*.json"):
                (dest / f.name).write_bytes(f.read_bytes())
            rec.update(status="done", finished_at=now(), signature=sig, target_signature=self.t_sig,
                       distance=round(d, 1), alignment=round(100 * (1 - d / self.gap0), 1),
                       per_task=[{"task_id": t, "topology": self.topo.get(t), "student": per.get(t),
                                  "target": self.teacher.get(t)} for t in sorted(per)],
                       missing_scores=sorted(t for t in self.topo if per.get(t) is None),
                       full_runs=f"results/{tid}/", tests_used=self.used(), tests_left=self.budget - self.used())
            self.log(event="done", **{k: v for k, v in rec.items() if k != "per_task"}, per_task_scores=per)
        except Exception as exc:
            rec.update(status="failed", refunded=True, finished_at=now(),
                       error="the run failed on the test machine; this test was not counted")
            self.log(event="platform_failure", test_id=tid, error=repr(exc)[:2000], tb=traceback.format_exc()[-2000:])
        self.write(self.sw / "results" / f"{tid}.json", rec)
        self.running = None

    def step(self):
        for req in sorted((self.sw / "requests").glob("*.json")):
            try:
                body = json.loads(req.read_text())
            except ValueError:
                body = {}
            req.unlink()
            try:
                self.handle(req.stem, body)
            except Exception:
                self.log(event="daemon_error", tb=traceback.format_exc()[-2000:])
                self.respond(req.stem, {"error": "internal error; try again"})


def main():
    if not ACB[-1] or not POD1[-1]:
        raise SystemExit("set SWARMSTILL_ACB_HOST and SWARMSTILL_JUDGE_HOST (plus ports and SWARMSTILL_SSH_KEY)")
    s = Session(sys.argv[1], sys.argv[2], sys.argv[3], int(sys.argv[4]) if len(sys.argv) > 4 else 5)
    s.log(event="daemon_start", ws=str(s.ws), metric=s.metric, budget=s.budget, gap0=s.gap0,
          target=s.t_sig, student0=s.s0_sig)
    while True:
        s.step()
        time.sleep(1)


if __name__ == "__main__":
    main()
