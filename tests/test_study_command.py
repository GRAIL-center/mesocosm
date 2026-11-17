"""Plumbing tests for agent-built environments, the recording gateway link and the study commands.
Inference is a scripted local server: no real-model claim."""

from __future__ import annotations

import json
import pathlib
import random
import re
import sys
import threading
import time
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from typer.testing import CliRunner

from mesocosm.cli.main import app
from mesocosm.study import command
from mesocosm.study.command import load_substrate
from mesocosm.study.compare import compare, compute_of, plan
from mesocosm.study.condition import StudyError
from mesocosm.study.gateway_link import GatewayLink
from mesocosm.study.runner import run_trials
from mesocosm.study.trial import TrialSpec, TrialStore

SKILL = pathlib.Path(__file__).parents[1] / "skills" / "behavior-study"
TEMPLATE = SKILL / "template" / "substrate.json"
MODEL = "Qwen/Qwen3-4B-Instruct-2507"
runner = CliRunner()


class _Upstream(BaseHTTPRequestHandler):
    """Scripted inference: pick the alphabetically first name the prompt's history shows, else a name
    drawn from the request's sampling seed."""

    authorization: list[str] = []
    in_flight = most_in_flight = 0
    gauge = threading.Lock()

    def do_POST(self) -> None:  # noqa: N802 (http.server's name)
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        type(self).authorization.append(self.headers.get("Authorization", ""))
        user = body["messages"][-1]["content"]
        if user == "slow":  # hold the request open so that overlapping requests can be counted
            with self.gauge:
                type(self).in_flight += 1
                type(self).most_in_flight = max(self.most_in_flight, self.in_flight)
            time.sleep(0.3)
            with self.gauge:
                type(self).in_flight -= 1
        names = re.search(r"Names: (.*)", user)
        seen = re.findall(r"picked ([A-Z])\b", user)
        reply = "ok" if names is None else min(seen) if seen else \
            random.Random(body.get("seed")).choice(sorted(names.group(1).split(", ")))
        if "Pick: <name>" in body["messages"][0]["content"]:
            reply = f"Earlier rounds showed K and {reply}, so I stay with one. Pick: {reply}"
        payload = json.dumps({
            "id": "c", "object": "chat.completion", "model": body["model"],
            "choices": [{"index": 0, "message": {"role": "assistant", "content": reply}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": len(user.split()), "completion_tokens": 1,
                      "total_tokens": len(user.split()) + 1}}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def do_GET(self) -> None:  # noqa: N802 (http.server's name); the readiness check asks for the model list
        payload = b'{"object": "list", "data": []}'
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, *_: object) -> None:
        pass


@pytest.fixture(scope="module")
def upstream() -> Iterator[str]:
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Upstream)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()


def test_a_bad_manifest_is_refused_with_its_location(tmp_path: pathlib.Path) -> None:
    described = load_substrate(TEMPLATE)
    assert described.info.kind == "reconstruction"
    assert described.space.setting("environment.name_pool").held_out == "b"
    bad = tmp_path / "substrate.json"
    bad.write_text(json.dumps({"name": "x", "version": "1", "kind": "original", "source": "s", "command": ["true"],
                               "settings": []}), encoding="utf-8")
    with pytest.raises(StudyError, match="rejected_unsupported.*measures"):
        load_substrate(bad)


def test_the_template_runs_through_the_gateway_and_every_model_call_is_recorded(tmp_path: pathlib.Path,
                                                                                upstream: str) -> None:
    described = load_substrate(TEMPLATE)
    chosen = plan(described.space, described.measures, measure="final_match_rate",
                  variants=[{"experience.history": 0}], baseline={"scale.agents": 4})
    store = TrialStore(tmp_path)
    with GatewayLink(tmp_path / "gateway", {spec.trial_id: described.manifest.agents for spec in chosen.specs},
                     upstream=upstream) as link:
        records = run_trials(load_substrate(TEMPLATE, link), chosen.specs, store)
    assert [record.error for record in records if record.status == "failed"] == []
    for record in records:
        calls = record.outcome.calls
        assert len(calls) == 6 * 2 * 2  # rounds x interactions per round x picks
        assert {call.model for call in calls} == {MODEL}
        assert {call.agent_id for call in calls} <= {"a01", "a02", "a03", "a04"}
        assert all(call.prompt_tokens and call.completion_tokens == 1 for call in calls)
        events = (store.workdir(record.spec.trial_id) / "events.jsonl").read_text(encoding="utf-8").splitlines()
        assert len(events) == 12 and set(json.loads(events[0])) == {"round", "agents", "choices", "match"}

    removed = compare(chosen, records, {"experience.history": 0}, described.space)
    assert removed.effect < -0.4 and removed.separated is True and removed.reach == "reached"
    assert compute_of(records).calls == 144 and compute_of(records).prompt_tokens > 0


SCRIPT = """
import json, os, urllib.request
condition = json.load(open(os.environ["TRIAL_CONDITION"]))
models = condition["model.checkpoint"]
models = ["another-model"] if condition["environment.wrong_model"] else models if isinstance(models, list) else [models]
for model in models:
    body = json.dumps({"model": model, "messages": [{"role": "user", "content": "hello"}]}).encode()
    urllib.request.urlopen(urllib.request.Request(
        os.environ["OPENAI_BASE_URL"] + "/chat/completions", data=body,
        headers={"Content-Type": "application/json", "Authorization": "Bearer " + os.environ["OPENAI_API_KEY"]})).read()
json.dump({"measures": {"score": float(len(models))}, "note": os.environ.get("SERVER_KEY", "withheld")},
          open(os.environ["TRIAL_OUTCOME"], "w"))
"""


def test_a_model_switch_is_enforced_by_the_recorded_requests(tmp_path: pathlib.Path, upstream: str,
                                                             monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / "run.py").write_text(SCRIPT, encoding="utf-8")
    manifest = tmp_path / "substrate.json"
    manifest.write_text(json.dumps({
        "name": "model-check", "version": "1", "kind": "reconstruction", "source": "tests", "command": [
            "{python}", "run.py"],
        "settings": [
            {"lever": "model", "name": "checkpoint", "description": "ids the trial requests",
             "values": ["small", ["small", "large"]], "default": "small", "request_model": True},
            {"lever": "environment", "name": "wrong_model", "description": "request an undeclared id",
             "values": [False, True], "default": False}],
        "measures": [{"name": "score", "description": "number of ids requested"}]}), encoding="utf-8")
    space = load_substrate(manifest).space
    specs = [TrialSpec(trial_id=name, repetition=0, seed=1, mode="exploration", condition=space.condition(changes))
             for name, changes in (("mixed", {"model.checkpoint": ["small", "large"]}),
                                   ("undeclared", {"environment.wrong_model": True}))]
    monkeypatch.setenv("SERVER_KEY", "server-secret")
    store = TrialStore(tmp_path / "study")
    with GatewayLink(store.root / "gateway", {spec.trial_id: () for spec in specs}, upstream=upstream,
                     upstream_key="server-secret") as link:
        mixed, undeclared = run_trials(load_substrate(manifest, link, withheld=["SERVER_KEY"]), specs, store)
    assert mixed.status == "completed" and [call.model for call in mixed.outcome.calls] == ["small", "large"]
    assert mixed.outcome.note == "withheld"                      # the command never held the server's key
    assert _Upstream.authorization[-1] == "Bearer server-secret"  # the gateway presented it
    assert undeclared.status == "failed" and "requests named models ['another-model']" in undeclared.error


def test_trials_of_one_command_reach_the_model_server_at_the_same_time(tmp_path: pathlib.Path, upstream: str) -> None:
    (tmp_path / "run.py").write_text(SCRIPT.replace('"hello"', '"slow"'), encoding="utf-8")
    manifest = tmp_path / "substrate.json"
    manifest.write_text(json.dumps({
        "name": "slow", "version": "1", "kind": "reconstruction", "source": "tests", "command": ["{python}", "run.py"],
        "settings": [
            {"lever": "model", "name": "checkpoint", "description": "id", "values": ["small"], "default": "small"},
            {"lever": "environment", "name": "wrong_model", "description": "unused", "values": [False], "default": False}],
        "measures": [{"name": "score", "description": "number of ids requested"}]}), encoding="utf-8")
    space = load_substrate(manifest).space
    specs = [TrialSpec(trial_id=f"t{index}", repetition=index, seed=index, mode="exploration",
                       condition=space.condition()) for index in range(3)]
    _Upstream.most_in_flight = 0
    with GatewayLink(tmp_path / "gateway", {spec.trial_id: () for spec in specs}, upstream=upstream) as link:
        records = run_trials(load_substrate(manifest, link), specs, TrialStore(tmp_path / "study"), workers=3)
    assert [record.status for record in records] == ["completed"] * 3
    assert _Upstream.most_in_flight >= 2  # the gateway did not queue the trials behind one another
    assert {call.agent_id for record in records for call in record.outcome.calls} == {"environment"}


PROBE = """
import json, os, socket, urllib.request
facts = {{"internet": 0, "host_file": os.path.exists({secret!r}), "other_trial": os.path.exists({other!r}),
         "server_key": "SERVER_KEY" in os.environ, "root": os.getuid() == 0, "manifest_writable": 0}}
try:
    socket.create_connection(("1.1.1.1", 443), timeout=3); facts["internet"] = 1
except OSError:
    pass
try:
    open("planted.txt", "w").close(); facts["manifest_writable"] = 1
except OSError:
    pass
body = json.dumps({{"model": "small", "messages": [{{"role": "user", "content": "hello"}}]}}).encode()
reply = urllib.request.urlopen(urllib.request.Request(
    os.environ["OPENAI_BASE_URL"] + "/chat/completions", data=body,
    headers={{"Content-Type": "application/json", "Authorization": "Bearer " + os.environ["OPENAI_API_KEY"]}})).read()
json.dump({{"measures": {{"leaks": float(sum(map(int, facts.values()))), "gateway": float(b"ok" in reply)}},
           "note": json.dumps(facts)}}, open(os.environ["TRIAL_OUTCOME"], "w"))
"""


def _contained_manifest(tmp_path: pathlib.Path, store: TrialStore) -> pathlib.Path:
    secret = tmp_path / "host-secret.txt"
    secret.write_text("outside the environment", encoding="utf-8")
    other = store.workdir("other-trial") / "record.json"
    other.write_text("{}", encoding="utf-8")
    directory = tmp_path / "environment"
    directory.mkdir()
    (directory / "run.py").write_text(PROBE.format(secret=str(secret), other=str(other)), encoding="utf-8")
    manifest = directory / "substrate.json"
    manifest.write_text(json.dumps({
        "name": "contained-probe", "version": "1", "kind": "reconstruction", "source": "tests", "contained": True,
        "command": ["{python}", "run.py"],
        "settings": [{"lever": "model", "name": "checkpoint", "description": "id the trial requests",
                      "values": ["small"], "default": "small", "request_model": True}],
        "measures": [{"name": "leaks", "description": "host things the trial could reach"},
                     {"name": "gateway", "description": "the model call succeeded"}]}), encoding="utf-8")
    return manifest


@pytest.mark.skipif(command._sandbox() is not None, reason="this host cannot start a bubblewrap sandbox")
def test_a_contained_trial_reaches_only_the_gateway_and_its_own_directory(tmp_path: pathlib.Path, upstream: str,
                                                                          monkeypatch: pytest.MonkeyPatch) -> None:
    store = TrialStore(tmp_path / "study")
    manifest = _contained_manifest(tmp_path, store)
    spec = TrialSpec(trial_id="contained", repetition=0, seed=1, mode="exploration",
                     condition=load_substrate(manifest).space.condition())
    monkeypatch.setenv("SERVER_KEY", "server-secret")
    with GatewayLink(store.root / "gateway", {spec.trial_id: ()}, upstream=upstream) as link:
        (record,) = run_trials(load_substrate(manifest, link), [spec], store)
    assert record.status == "completed", record.error
    assert record.outcome.measures == {"leaks": 0.0, "gateway": 1.0}, record.outcome.note
    assert [call.model for call in record.outcome.calls] == ["small"]  # the only way out was recorded
    assert not (manifest.parent / "planted.txt").exists()


def test_trials_do_not_start_when_containment_or_the_model_server_is_missing(
        tmp_path: pathlib.Path, upstream: str, monkeypatch: pytest.MonkeyPatch) -> None:
    store = TrialStore(tmp_path / "study")
    manifest = _contained_manifest(tmp_path, store)
    with pytest.raises(StudyError, match="rejected_unsupported: the model server at http://127.0.0.1:9 does not"):
        with GatewayLink(tmp_path / "gateway", {"t": ()}, upstream="http://127.0.0.1:9"):
            pass
    monkeypatch.setattr(command.shutil, "which", lambda _: None)
    command._sandbox.cache_clear()
    try:
        with pytest.raises(StudyError, match="must run contained and bubblewrap .bwrap. is not installed"):
            load_substrate(manifest).preflight()
        refused = _json(["test", str(manifest), "--root", str(tmp_path / "cli"), "--upstream", upstream], code=1)
    finally:
        command._sandbox.cache_clear()
    assert refused["status"] == "rejected_unsupported" and not (tmp_path / "cli" / "trials").exists()


def _json(args: list[str], code: int = 0) -> dict:
    result = runner.invoke(app, ["study", *args, "--json"])
    assert result.exit_code == code, result.output
    return json.loads(result.stdout)


def test_study_commands_print_one_json_object(tmp_path: pathlib.Path, upstream: str) -> None:
    run = [str(TEMPLATE), "--root", str(tmp_path), "--upstream", upstream, "--baseline", "scale.agents=4"]
    described = _json(["describe", str(TEMPLATE)])
    assert described["levers_without_a_switch"] == ["training", "structure"]
    assert {setting["key"] for setting in described["settings"]} >= {"model.checkpoint", "experience.history"}

    baseline = _json(["test", *run])
    assert baseline["comparison"] is None and baseline["compute"]["calls"] == 72
    assert baseline["baseline_profile"]["final_match_rate"]["n"] == 3 and "sd" in baseline["baseline_profile"]["consensus"]

    test = _json(["test", *run, "--measure", "final_match_rate", "--set", "experience.history=0"])
    assert test["comparison"]["effect"] < -0.4 and test["comparison"]["reach"] == "reached"
    assert test["compute"]["calls"] == test["study_compute"]["calls"] == 144  # the baseline runs were reused
    hint = _json(["test", *run, "--measure", "final_match_rate", "--set", "instructions.coordination_hint=true"])
    assert hint["comparison"]["reach"] == "reached" and hint["comparison"]["separated"] is False
    assert "within run-to-run noise" in hint["comparison"]["statement"]

    reasoned = _json(["test", *run, "--measure", "valid_rate", "--set", "instructions.reasoning=true"])
    assert reasoned["comparison"]["variant"]["mean"] == 1.0  # the stated pick is read, not letters in the reasoning
    assert reasoned["comparison"]["reach"] == "reached"

    claim = _json(["test", *run, "--measure", "final_match_rate", "--claim", "--repetitions", "4",
                   "--set", "experience.history=0"])
    assert claim["mode"] == "claim" and claim["baseline_condition"]["environment.name_pool"] == "b"
    assert claim["comparison"]["shown"] is True
    card = _json(["card", *run, "--measure", "final_match_rate", "--origin", "spontaneous", "--lever", "experience",
                  "--repetitions", "4"])["card"]
    assert card["levers"]["experience"] in ("necessary", "modulates") and card["levers"]["model"] == "not-tested"
    assert card["findings"][0]["comparison"]["reach"] == "reached" and card["origin"] == "spontaneous"

    assert _json(["test", str(TEMPLATE), "--root", str(tmp_path), "--measure", "final_match_rate",
                  "--set", "experience.history=7"], code=1)["status"] == "rejected_unsupported"
    assert _json(["test", str(TEMPLATE), "--root", str(tmp_path), "--set", "experience.history=0"],
                 code=1)["error"] == "rejected_unsupported: --set needs --measure"


def test_the_skill_is_self_contained() -> None:
    source = (SKILL / "template" / "naming_game.py").read_text(encoding="utf-8")
    imported = {line.split()[1].split(".")[0] for line in source.splitlines() if line.startswith(("import ", "from "))}
    assert imported <= set(sys.stdlib_module_names) | {"__future__"}
    for page in SKILL.rglob("*.md"):
        for target in re.findall(r"\]\(([^)#]+)\)", page.read_text(encoding="utf-8")):
            assert target.startswith("http") or (page.parent / target).exists(), f"{page.name} links to {target}"
