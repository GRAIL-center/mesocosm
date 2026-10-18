"""Every command prints one JSON object with --json and exits non-zero on a rejected or failed result."""

import json

from typer.testing import CliRunner

from mesocosm.cli.main import app
from mesocosm.core.records import RunBundle

runner = CliRunner()


def jrun(*args):
    result = runner.invoke(app, [*args, "--json"])
    lines = [ln for ln in result.stdout.splitlines() if ln.strip()]
    assert len(lines) == 1, result.stdout
    return result.exit_code, json.loads(lines[0])


def flagged_bundle(tmp_path):
    b = RunBundle.create(tmp_path / "runs", "run_f")
    b.write_manifest({"gateway": {"url": "http://127.0.0.1:9"}}, "d")
    b._append("events.jsonl", {"ts": 10.0, "run_id": "run_f", "kind": "harness.session", "agent_id": "agent-1",
                               "data": {"session_id": "s", "task": "t"}})
    b._append("events.jsonl", {"ts": 70.0, "run_id": "run_f", "kind": "verifier.reward", "agent_id": "agent-1",
                               "data": {"task": "t", "reward": 1.0}})
    b.record_flag({"flag_id": "flag_a", "run_id": "run_f", "agent_id": "agent-1", "kind": "impersonation",
                   "summary": "wrote to board as 'agent-2'", "detector": "state_check", "detector_version": "0.1"})
    b.write_summary({"status": "ok", "rewards": {"agent-1/t": 1.0}})
    return b.root


def test_flags_list_review_and_report(tmp_path):
    root = flagged_bundle(tmp_path)
    code, listed = jrun("flags", "list", str(root))
    assert code == 0 and listed["counts"] == {"impersonation": 1} and listed["unreviewed"] == 1
    assert listed["flags"][0]["verdict"] is None
    code, verdict = jrun("flags", "review", str(root), "flag_a", "--verdict", "confirmed", "--reviewer", "sy",
                         "--note", "it posted as the other agent")
    assert code == 0 and verdict["verdict"] == "confirmed" and verdict["kind"] == "impersonation"
    code, listed = jrun("flags", "list", str(root))
    assert listed["flags"][0]["verdict"]["reviewer"] == "sy" and listed["unreviewed"] == 0
    code, bad = jrun("flags", "review", str(root), "flag_a", "--verdict", "maybe")
    assert code == 1 and "verdict must be" in bad["error"]
    code, missing = jrun("flags", "review", str(root), "flag_zz", "--verdict", "rejected")
    assert code == 1 and "no flag" in missing["error"]
    code, rep = jrun("report", str(root), "--gpu-hour-cost", "2.0")
    assert code == 0 and rep["agent_hours"] == round(60 / 3600, 6) and rep["flags"] == {"impersonation": 1}
    assert rep["verdicts"] == {"confirmed": 1} and rep["cost"]["per_agent_hour"] == round(rep["cost"]["run_cost"] / rep["agent_hours"], 4)


def test_conditions_set_rejects_what_cannot_change_mid_run(tmp_path):
    root = flagged_bundle(tmp_path)
    code, receipt = jrun("conditions", "set", str(root), "agent-1", "exposure", '{"wiki": {"x": 1}}')
    assert code == 1 and receipt["result"] == "rejected_unsupported" and receipt["enforced_at"]["component"] == "control"
    assert RunBundle.open(root).receipts()[-1]["result"] == "rejected_unsupported"


def test_scenario_report_reads_a_scenario_directory(tmp_path):
    out = tmp_path / "scn"
    out.mkdir()
    (out / "scenario.json").write_text(json.dumps({
        "name": "s", "stages": [{"index": 1, "name": "a", "runs": [{"status": "ok", "agent_hours": 0.5}], "flags": {}},
                                {"index": 2, "name": "b", "runs": [{"status": "ok", "agent_hours": 0.5}],
                                 "flags": {"trap_credential": 1}}],
        "minimum_elicitation_stage": {"trap_credential": 2}, "stopped_at": None}))
    code, table = jrun("scenario", "report", str(out))
    assert code == 0 and table["stages"][1]["flags_per_agent_hour"] == 2.0 and table["minimum_elicitation_stage"] == {"trap_credential": 2}
