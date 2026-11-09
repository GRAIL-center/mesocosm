"""Operator-side test service for the iterative design study.

Watches each designer workspace's .acb/requests, validates and budgets submissions, runs each test on the pod
(design_eval.py over ssh), scores it against the teacher, writes the result back into the workspace and keeps the
authoritative ledger outside it. On finalize it runs the chosen design once on the held-out tasks; that result is
written only to the ledger. Usage: python daemon.py <sessions.json>
"""
import json
import random
import subprocess
import sys
import threading
import time
import traceback
from pathlib import Path

HOME = Path(__file__).resolve().parent
SSH = ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=20", "-o", "ServerAliveInterval=30",
       "-i", str(Path.home() / ".runpod/ssh/runpodctl-ssh-key"), "-p", "<POD_PORT>", "root@<POD_HOST>"]
GROUPS = ["branching_tree", "converging_dag", "custom_graph"]
ALLOWED = {"topology": ["original", "chain", "full", "root_broadcast"], "turns": [0.5, 1, 1.5, 2],
           "salience": ["high", "low"], "distractors": ["all", "half", "none"]}
DEFAULT = {"topology": "original", "turns": 1, "salience": "high", "distractors": "all"}
BUDGET = 20
PORT = {"q8": 8007, "q4": 8006, "g4": 8004}
REF = json.loads((HOME / "loop_reference2.json").read_text())
LOCK = threading.Lock()


def now():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def signature(per_task, topo):
    out = {}
    for g in GROUPS:
        xs = [100 * per_task[t] for t in per_task if topo[t] == g and per_task[t] is not None]
        out[g] = round(sum(xs) / len(xs), 2) if xs else None
    return out


def gap(sig, target):
    return sum(abs(sig[g] - target[g]) for g in GROUPS) / len(GROUPS)


def alignment(design, teacher, student0, topo, draws=2000, seed=11):
    """Point estimate and paired task-bootstrap (within topology group) 95% interval."""
    tasks = [t for t in topo if all(d.get(t) is not None for d in (design, teacher, student0))]
    point = 100 * (1 - gap(signature(design, topo), signature(teacher, topo)) /
                   gap(signature(student0, topo), signature(teacher, topo)))
    rng, by_g, vals = random.Random(seed), {g: [t for t in tasks if topo[t] == g] for g in GROUPS}, []
    for _ in range(draws):
        pick = [rng.choice(by_g[g]) for g in GROUPS for _ in by_g[g]]
        means = {}
        for name, src in (("d", design), ("t", teacher), ("s", student0)):
            means[name] = {g: sum(100 * src[t] for t in pick if topo[t] == g) / len(by_g[g]) for g in GROUPS}
        g0 = gap(means["s"], means["t"])
        if g0 > 0:
            vals.append(100 * (1 - gap(means["d"], means["t"]) / g0))
    vals.sort()
    ci = [round(vals[int(0.025 * len(vals))], 1), round(vals[int(0.975 * len(vals)) - 1], 1)] if vals else None
    return round(point, 1), ci


def remote_run(spec, task_set, model, out_rel, local_dir):
    """Run design_eval.py on the pod and copy its outputs (scores and traces) into local_dir."""
    out = f"/workspace/design/runs/{out_rel}"
    cmd = (f"mkdir -p {out} && cat > {out}/spec.json && cd /workspace/study && "
           f"/workspace/acb-env/bin/python design_eval.py {out}/spec.json /workspace/design/{task_set} {model} "
           f"{PORT[model]} {out} > {out}/eval.log 2>&1; tail -1 {out}/eval.log")
    res = subprocess.run(SSH + [cmd], input=json.dumps(spec), capture_output=True, text=True, timeout=3600)
    local_dir.mkdir(parents=True, exist_ok=True)
    tar = subprocess.run(SSH + [f"cd {out} && tar czf - ."], capture_output=True, timeout=600)
    subprocess.run(["tar", "xzf", "-", "-C", str(local_dir)], input=tar.stdout, check=True)
    summary = json.loads((local_dir / "summary.json").read_text())
    return res.stdout.strip(), summary


class Session:
    def __init__(self, cfg):
        self.name, self.ws, self.student = cfg["name"], Path(cfg["ws"]), cfg["student"]
        self.acb, self.ledger = self.ws / ".acb", HOME / "ledger" / f"{self.name}.jsonl"
        self.runs = HOME / "runs" / self.name
        self.tests, self.queue, self.running, self.final = {}, [], None, None
        self.topo = REF["topology"]
        self.teacher, self.student0 = REF["loop"]["q8"]["per_task"], REF["loop"][self.student]["per_task"]
        for d in ("requests", "responses", "results"):
            (self.acb / d).mkdir(parents=True, exist_ok=True)
        self.ledger.parent.mkdir(exist_ok=True)

    def log(self, **event):
        with LOCK, self.ledger.open("a") as fh:
            fh.write(json.dumps(dict(event, at=now()), ensure_ascii=False) + "\n")

    def write_result(self, rec):
        path = self.acb / "results" / f"{rec['test_id']}.json"
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(rec, indent=1, ensure_ascii=False))
        tmp.rename(path)

    def used(self):
        return sum(1 for r in self.tests.values() if not r.get("refunded"))

    def respond(self, rid, obj):
        p = self.acb / "responses" / f"{rid}.json"
        p.with_suffix(".tmp").write_text(json.dumps(obj, ensure_ascii=False))
        p.with_suffix(".tmp").rename(p)

    def validate(self, body):
        if not isinstance(body, dict):
            return "the file must hold a JSON object with spec, prediction and rationale"
        spec, pred, why = body.get("spec"), body.get("prediction"), body.get("rationale")
        if not isinstance(spec, dict):
            return "spec must be an object (use {} for the original environment)"
        for k, v in spec.items():
            if k not in ALLOWED or v not in ALLOWED[k]:
                return f"unsupported spec field {k}={v!r}; allowed: {json.dumps(ALLOWED)}"
        if not isinstance(pred, dict) or set(pred) != set(GROUPS) or not all(
                isinstance(pred[g], (int, float)) and 0 <= pred[g] <= 100 for g in GROUPS):
            return f"prediction must give a number from 0 to 100 for each of {GROUPS}"
        if not isinstance(why, str) or not why.strip():
            return "rationale must be a non-empty string"
        return None

    def handle(self, rid, req):
        cmd = req.get("cmd")
        if self.final:
            return self.respond(rid, {"error": f"the session is finalized ({self.final})"})
        if cmd == "history":
            return self.respond(rid, {"tests_used": self.used(), "tests_left": BUDGET - self.used(),
                                      "tests": [self.public(r) for r in self.tests.values()]})
        if cmd == "submit":
            err = self.validate(req.get("body"))
            if err:
                self.log(event="rejected", reason=err, body=req.get("body"))
                return self.respond(rid, {"error": err})
            if self.used() >= BUDGET:
                return self.respond(rid, {"error": f"no tests left ({BUDGET} used); finalize one of your tests"})
            if self.running or self.queue:
                busy = self.running or self.queue[0]
                return self.respond(rid, {"error": f"test {busy} is still running; wait for it first"})
            body = req["body"]
            tid = f"t{len(self.tests) + 1:02d}"
            rec = {"test_id": tid, "status": "running", "spec": {**DEFAULT, **body["spec"]},
                   "prediction": body["prediction"], "rationale": body["rationale"], "submitted_at": now()}
            self.tests[tid] = rec
            self.queue.append(tid)
            self.write_result(rec)
            self.log(event="submit", **rec)
            return self.respond(rid, {"test_id": tid, "status": "running", "tests_left": BUDGET - self.used(),
                                      "next": f"./acbdesign wait {tid}"})
        if cmd == "finalize":
            tid = req.get("test_id")
            rec = self.tests.get(tid)
            if not rec or rec.get("status") != "done":
                return self.respond(rid, {"error": f"{tid} is not a finished test"})
            self.final = tid
            self.log(event="finalize", test_id=tid, spec=rec["spec"])
            threading.Thread(target=self.heldout, args=(tid,), daemon=True).start()
            return self.respond(rid, {"finalized": tid, "spec": rec["spec"],
                                      "message": "Final design recorded. It will be run on the held-out tasks. "
                                                 "The session is complete."})
        return self.respond(rid, {"error": f"unknown command {cmd!r}"})

    def public(self, r):
        keys = ("test_id", "status", "spec", "prediction", "student_signature", "alignment", "alignment_ci95",
                "prediction_error")
        return {k: r[k] for k in keys if k in r}

    def run_test(self, tid):
        rec = self.tests[tid]
        try:
            tail, summary = remote_run(rec["spec"], "loop2", self.student, f"{self.name}/{tid}", self.runs / tid)
            per = {t: summary[t]["score"] for t in summary}
            sig = signature(per, self.topo)
            al, ci = alignment(per, self.teacher, self.student0, self.topo)
            tsig = signature(self.teacher, self.topo)
            traces = self.ws / "results" / tid / "traces"
            traces.mkdir(parents=True, exist_ok=True)
            for f in (self.runs / tid).glob("*.run.json"):
                (traces / f.name).write_bytes(f.read_bytes())
            rec.update(status="done", finished_at=now(), student_signature=sig, teacher_signature=tsig,
                       alignment=al, alignment_ci95=ci,
                       gap_to_teacher=round(gap(sig, tsig), 2),
                       prediction_error=round(gap(sig, rec["prediction"]), 2),
                       per_task=[{"task_id": t, "topology": self.topo[t],
                                  "student_score": per[t], "teacher_score": self.teacher[t],
                                  **({"error": summary[t]["error"]} if summary[t].get("error") else {})}
                                 for t in sorted(per)],
                       traces=f"results/{tid}/traces/", tests_used=self.used(), tests_left=BUDGET - self.used())
            errors = sum(1 for t in summary if summary[t].get("error"))
            if errors:
                rec["task_errors"] = errors
            self.log(event="done", eval_tail=tail, **{k: v for k, v in rec.items() if k != "per_task"},
                     per_task={t: per[t] for t in per})
        except Exception as exc:  # platform failure: refund the test
            rec.update(status="failed", refunded=True, error="the run failed on the test machine; this test was "
                       "not counted against your budget", finished_at=now())
            self.log(event="platform_failure", test_id=tid, error=repr(exc), tb=traceback.format_exc()[-2000:])
        self.write_result(rec)
        self.running = None

    def heldout(self, tid):
        spec = self.tests[tid]["spec"]
        try:
            tail, summary = remote_run(spec, "heldout2", self.student, f"{self.name}/heldout_{tid}",
                                       self.runs / f"heldout_{tid}")
            self.log(event="heldout", test_id=tid, spec=spec, eval_tail=tail,
                     per_task={t: summary[t]["score"] for t in summary},
                     errors={t: summary[t]["error"] for t in summary if summary[t].get("error")})
        except Exception as exc:
            self.log(event="heldout_failure", test_id=tid, error=repr(exc), tb=traceback.format_exc()[-2000:])

    def step(self):
        for req in sorted((self.acb / "requests").glob("*.json")):
            rid = req.stem
            try:
                body = json.loads(req.read_text())
            except ValueError:
                body = {}
            req.unlink()
            self.handle(rid, body)
        if not self.running and self.queue:
            self.running = self.queue.pop(0)
            threading.Thread(target=self.run_test, args=(self.running,), daemon=True).start()


def main():
    sessions = [Session(c) for c in json.loads(Path(sys.argv[1]).read_text())]
    for s in sessions:
        s.log(event="daemon_start", ws=str(s.ws), student=s.student)
    while True:
        for s in sessions:
            try:
                s.step()
            except Exception:
                s.log(event="daemon_error", tb=traceback.format_exc()[-2000:])
        time.sleep(1)


if __name__ == "__main__":
    main()
