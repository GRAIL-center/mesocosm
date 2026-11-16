"""Plumbing tests for agent-built environments, the recording gateway link, tests as instruments,
diagnosis and the study commands. Inference is a scripted local server: no real-model claim."""

from __future__ import annotations

import hashlib
import json
import pathlib
import random
import re
import sys
import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from typer.testing import CliRunner

from mesocosm.cli.main import app
from mesocosm.study.battery import minimum_p, plan_battery
from mesocosm.study.brief import Reference
from mesocosm.study.card import build_card
from mesocosm.study.command import load_substrate
from mesocosm.study.condition import Condition, LeverSpace, Setting, StudyError
from mesocosm.study.diagnose import breakdown, read_events
from mesocosm.study.gateway_link import GatewayLink
from mesocosm.study.instrument import check_budget, check_claim_seeds, evaluate_test, plan_test
from mesocosm.study.runner import run_trials
from mesocosm.study.substrate import Measure, ModelCall, SubstrateInfo, TrialOutcome
from mesocosm.study.trial import TrialSpec, TrialStore

EXAMPLES = pathlib.Path(__file__).parents[1] / "examples" / "study"
TEMPLATE = EXAMPLES / "naming-game" / "substrate.json"
MODEL = "Qwen/Qwen3-4B-Instruct-2507"
runner = CliRunner()


class _Upstream(BaseHTTPRequestHandler):
    """Scripted inference: pick the alphabetically first name the prompt's history shows, else a name
    drawn from the request's sampling seed."""

    authorization: list[str] = []

    def do_POST(self) -> None:  # noqa: N802 (http.server's name)
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        type(self).authorization.append(self.headers.get("Authorization", ""))
        user = body["messages"][-1]["content"]
        names = re.search(r"Names: (.*)", user)
        seen = re.findall(r"picked ([A-Z])\b", user)
        reply = "ok" if names is None else min(seen) if seen else \
            random.Random(body.get("seed")).choice(sorted(names.group(1).split(", ")))
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

    def log_message(self, *_: object) -> None:
        pass


@pytest.fixture(scope="module")
def upstream() -> Iterator[str]:
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Upstream)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()


@pytest.fixture(scope="module")
def naming_run(tmp_path_factory: pytest.TempPathFactory, upstream: str):
    """One exploration test on the template: history shown against history removed, three runs each."""
    described = load_substrate(TEMPLATE)
    plan = plan_test(described.space, described.measures, measure="final_match_rate",
                     changes={"experience.history": 0}, baseline={"scale.agents": 4}, repetitions=3)
    store = TrialStore(tmp_path_factory.mktemp("naming"))
    with GatewayLink(store.root / "gateway", {spec.trial_id: described.manifest.agents for spec in plan.specs},
                     upstream=upstream) as link:
        records = run_trials(load_substrate(TEMPLATE, link), plan.specs, store)
    return described, plan, store, records, evaluate_test(plan, records, described.space, store)


def test_manifest_declares_the_environment_and_a_bad_one_is_refused(tmp_path: pathlib.Path) -> None:
    described = load_substrate(TEMPLATE)
    assert described.info.kind == "reconstruction"
    assert described.space.setting("environment.name_pool").held_out == "b"
    assert described.space.setting("model.checkpoint").request_model
    bad = tmp_path / "substrate.json"
    bad.write_text(json.dumps({"name": "x", "version": "1", "kind": "original", "source": "s", "command": ["true"],
                               "settings": []}), encoding="utf-8")
    with pytest.raises(StudyError, match="rejected_unsupported.*measures"):
        load_substrate(bad)


def test_every_model_call_of_a_trial_is_recorded_through_the_gateway(naming_run) -> None:
    _, plan, _, records, _ = naming_run
    assert [record.error for record in records if record.status == "failed"] == []
    for record in records:
        calls = record.outcome.calls
        assert len(calls) == 6 * 2 * 2  # rounds x interactions per round x picks
        assert {call.model for call in calls} == {MODEL}
        assert {call.agent_id for call in calls} <= {"a01", "a02", "a03", "a04"}
        assert all(call.prompt_tokens and call.completion_tokens == 1 for call in calls)
    assert len(records) == len(plan.specs) == 6


def test_a_test_returns_the_effect_with_the_noise_the_reach_and_the_cost(naming_run) -> None:
    _, _, _, _, result = naming_run
    assert result.mode == "exploration" and result.shown is None
    assert result.baseline.n == result.variant.n == 3
    assert result.effect is not None and result.effect < -0.4
    assert result.noise_range is not None and result.noise_sd is not None
    assert result.separated is True and result.statement.startswith("Exploration: the arms are separated")
    assert result.reach == "reached"
    assert result.compute.trials == 6 and result.compute.calls == 144 and result.compute.prompt_tokens > 0


def test_events_are_read_in_pages_and_broken_down_by_step(naming_run) -> None:
    _, plan, store, records, _ = naming_run
    page = read_events(store, plan.specs[0].trial_id, limit=5)
    assert page["total"] == 12 and len(page["events"]) == 5 and page["next_offset"] == 5
    assert read_events(store, plan.specs[0].trial_id, offset=10)["next_offset"] is None
    assert read_events(store, plan.specs[0].trial_id, kind="verdict")["total"] == 0
    with pytest.raises(StudyError, match="no trial"):
        read_events(store, "absent")
    table = breakdown(records, kind="interaction", field="match")["table"]
    shown, removed = plan.baseline.digest[:12], plan.variant.digest[:12]
    assert list(table[shown]) == ["0", "1", "2", "3", "4", "5"] and table[shown]["5"]["n"] == 6
    assert table[shown]["5"]["mean"] > table[shown]["0"]["mean"]    # matches rise over rounds with history
    assert table[removed]["5"]["mean"] < table[shown]["5"]["mean"]
    assert breakdown(records, kind="interaction", field="choice")["skipped"] == 72  # a name is not a number


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
    specs = [TrialSpec(trial_id=name, arm=name, repetition=0, seed=1, mode="exploration",
                       condition=space.condition(changes))
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


class Review:
    """A test environment without inference: memory is required, the persona line is a prompt stand-in
    for training, and the task set is a partition with a held-out half."""

    info = SubstrateInfo(name="review", version="1", kind="reconstruction", source="tests/test_study_command.py")
    measures = (Measure(name="accept", description="share of episodes with mutual acceptance"),)
    space = LeverSpace(settings=(
        Setting(lever="experience", name="memory", description="reflections kept across episodes",
                values=("carry", "none"), default="carry", removal="none"),
        Setting(lever="instructions", name="persona", description="a prompt line that imitates a trained disposition",
                values=(False, True), default=False, stand_in_for="training"),
        Setting(lever="environment", name="tasks", description="task set; the held-out half is for claims",
                values=("tuning", "held_out"), default="tuning", held_out="held_out"),
    ))

    def __init__(self, tokens: int | None = 10) -> None:
        self.tokens, self.started = tokens, 0

    def run(self, condition: Condition, seed: int, workdir: pathlib.Path) -> TrialOutcome:
        self.started += 1
        rate = 0.6 + seed % 5 / 100 + (0.2 if condition.settings["instructions.persona"] else 0.0)
        rate = 0.0 if condition.settings["experience.memory"] == "none" else rate
        sha = hashlib.sha256(json.dumps(condition.settings, sort_keys=True).encode()).hexdigest()
        call = ModelCall(agent_id="a", input_sha256=sha, output_sha256=sha, prompt_tokens=self.tokens,
                         completion_tokens=2 if self.tokens is not None else None)
        return TrialOutcome(measures={"accept": rate}, calls=(call,) * 3)


def test_exploration_is_cheap_and_a_claim_needs_held_out_values_fresh_seeds_and_enough_runs(
        tmp_path: pathlib.Path) -> None:
    review, store = Review(), TrialStore(tmp_path)
    explore = plan_test(review.space, review.measures, measure="accept", changes={"experience.memory": "none"})
    result = evaluate_test(explore, run_trials(review, explore.specs, store), review.space, store)
    assert result.separated is True and result.shown is None and result.p_value == minimum_p(3, 3) == 0.1

    with pytest.raises(StudyError, match="held out for claims"):
        plan_test(review.space, review.measures, measure="accept", changes={"experience.memory": "none"},
                  baseline={"environment.tasks": "held_out"})
    with pytest.raises(StudyError, match="held-out value of 'environment.tasks'"):
        plan_test(review.space, review.measures, measure="accept", changes={"experience.memory": "none"},
                  mode="claim", repetitions=4)
    with pytest.raises(StudyError, match="3 repetitions per arm cannot show an effect"):
        plan_test(review.space, review.measures, measure="accept", changes={"experience.memory": "none"},
                  baseline={"environment.tasks": "held_out"}, mode="claim")

    claim = plan_test(review.space, review.measures, measure="accept", changes={"experience.memory": "none"},
                      baseline={"environment.tasks": "held_out"}, mode="claim", repetitions=4)
    check_claim_seeds(claim.specs, store)
    confirmed = evaluate_test(claim, run_trials(review, claim.specs, store), review.space, store)
    assert confirmed.shown is True and confirmed.statement == "Claim: the effect is shown at the chosen alpha."
    reused = [claim.specs[0].model_copy(update={"seed": explore.specs[0].seed})]
    with pytest.raises(StudyError, match="a claim needs fresh seeds"):
        check_claim_seeds(reused, store)

    battery = plan_battery(review, measure="accept", origin="spontaneous", repetitions=4,
                           baseline={"environment.tasks": "held_out"})
    with pytest.raises(StudyError, match="exploration trials cannot enter a behavior card"):
        build_card(battery, store.records())


def test_a_prompt_stand_in_is_labeled_and_recorded_under_instructions(tmp_path: pathlib.Path) -> None:
    review, store = Review(), TrialStore(tmp_path)
    plan = plan_test(review.space, review.measures, measure="accept", changes={"instructions.persona": True})
    result = evaluate_test(plan, run_trials(review, plan.specs, store), review.space, store)
    assert result.stand_ins == {"instructions.persona": "training"}
    assert "instructions.persona is a stand-in for training" in result.statement

    battery = plan_battery(review, measure="accept", origin="cued", repetitions=4,
                           baseline={"environment.tasks": "held_out"})
    card = build_card(battery, run_trials(review, battery.specs, TrialStore(tmp_path / "claim")))
    finding = next(item for item in card.findings if item.setting == "instructions.persona")
    assert finding.lever == "instructions" and finding.stand_in_for == "training"
    assert card.levers["training"] == "not-tested" and card.levers["experience"] == "necessary"
    assert all(item.setting != "environment.tasks" for item in card.findings)  # a partition, not an arm


def test_the_budget_is_compute_and_baseline_runs_are_shared_between_tests(tmp_path: pathlib.Path) -> None:
    review, store = Review(), TrialStore(tmp_path)
    first = plan_test(review.space, review.measures, measure="accept", changes={"experience.memory": "none"})
    run_trials(review, first.specs, store)                          # 6 trials, 18 calls, 216 tokens
    second = plan_test(review.space, review.measures, measure="accept", changes={"instructions.persona": True})
    check_budget(second.specs, store, max_calls=27, max_tokens=324)  # 3 pending trials: 9 calls, 108 tokens
    with pytest.raises(StudyError, match="rejected_budget: 18 model calls spent of 26; 3 pending"):
        check_budget(second.specs, store, max_calls=26)
    with pytest.raises(StudyError, match="rejected_budget: 216 tokens spent of 300"):
        check_budget(second.specs, store, max_tokens=300)
    run_trials(review, second.specs, store)
    assert review.started == 9                                      # the baseline ran once for both tests

    silent, other = Review(tokens=None), TrialStore(tmp_path / "silent")
    run_trials(silent, first.specs, other)
    with pytest.raises(StudyError, match="rejected_budget: a budget in tokens needs usage"):
        check_budget(second.specs, other, max_tokens=1000)


def _json(args: list[str], code: int = 0) -> dict:
    result = runner.invoke(app, ["study", *args, "--json"])
    assert result.exit_code == code, result.output
    return json.loads(result.stdout)


def test_study_commands_print_one_json_object(tmp_path: pathlib.Path, upstream: str) -> None:
    root, served = ["--root", str(tmp_path)], ["--upstream", upstream, "--baseline", "scale.agents=4"]
    described = _json(["describe", str(TEMPLATE)])
    assert described["levers_without_a_switch"] == ["training", "structure"]
    assert {setting["key"] for setting in described["settings"]} >= {"model.checkpoint", "experience.history"}

    brief = _json(["brief", str(TEMPLATE), *root, *served, "--across", "experience.history",
                   "--reference", str(EXAMPLES / "library" / "naming-conventions.json")])
    assert brief["reference"]["conditions"][0]["passage"] and set(brief["profile"]) == {
        "experience.history=0", "experience.history=5"}
    assert brief["profile"]["experience.history=5"]["final_match_rate"]["n"] == 3

    test = _json(["test", str(TEMPLATE), *root, *served, "--measure", "final_match_rate",
                  "--set", "experience.history=0"])
    assert test["effect"] < -0.4 and test["reach"] == "reached" and test["compute"]["calls"] == 144
    assert test["study_compute"]["calls"] == 144    # the brief's six trials were reused, none ran twice
    hint = _json(["test", str(TEMPLATE), *root, *served, "--measure", "final_match_rate",
                  "--set", "instructions.coordination_hint=true"])
    assert hint["reach"] == "reached" and hint["separated"] is False and "within run-to-run noise" in hint["statement"]

    events = _json(["events", test["trials"][0], *root, "--limit", "3"])
    assert events["total"] == 12 and events["next_offset"] == 3
    assert _json(["breakdown", *root, "--kind", "interaction", "--field", "match"])["skipped"] == 0

    claim = _json(["test", str(TEMPLATE), *root, *served, "--measure", "final_match_rate", "--claim",
                   "--repetitions", "4", "--set", "experience.history=0"])
    assert claim["mode"] == "claim" and claim["baseline_condition"]["environment.name_pool"] == "b"
    assert claim["shown"] is True
    card = _json(["card", str(TEMPLATE), *root, *served, "--measure", "final_match_rate", "--origin", "spontaneous",
                  "--lever", "experience", "--repetitions", "4"])["card"]
    assert card["levers"]["experience"] in ("necessary", "modulates") and card["levers"]["model"] == "not-tested"
    assert card["findings"][0]["reach"] == "reached" and card["origin"] == "spontaneous"

    assert _json(["test", str(TEMPLATE), *root, "--measure", "final_match_rate", "--set", "experience.history=7"],
                 code=1)["status"] == "rejected_unsupported"
    assert _json(["test", str(TEMPLATE), *root, *served, "--measure", "final_match_rate", "--max-calls", "10",
                  "--set", "environment.rounds=12"], code=1)["status"] == "rejected_budget"


def test_library_references_are_valid() -> None:
    for path in sorted((EXAMPLES / "library").glob("*.json")):
        reference = Reference.model_validate_json(path.read_text(encoding="utf-8"))
        assert reference.sources and reference.conditions and reference.reported


def test_the_template_environment_uses_the_standard_library_only() -> None:
    source = (EXAMPLES / "naming-game" / "naming_game.py").read_text(encoding="utf-8")
    imported = {line.split()[1].split(".")[0] for line in source.splitlines() if line.startswith(("import ", "from "))}
    assert imported <= set(sys.stdlib_module_names) | {"__future__"}
