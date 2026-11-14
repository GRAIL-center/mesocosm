"""Study-layer tests with a deterministic test environment; no real-agent or real-environment claim."""

from __future__ import annotations

import hashlib
import pathlib
import threading

import pytest
from pydantic import ValidationError

from mesocosm.study.battery import plan_battery
from mesocosm.study.card import build_card, permutation_p, reach, type_labels
from mesocosm.study.condition import Condition, LeverSpace, Setting, StudyError
from mesocosm.study.runner import run_trials
from mesocosm.study.substrate import Measure, ModelCall, SubstrateInfo, TrialOutcome
from mesocosm.study.trial import TrialStore


def digest(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


class ReviewGame:
    """A stand-in environment for tests: a rate computed from the condition, plus a little seed noise.

    Memory is required, feedback and the checkpoint change the rate, the group-size switch is wired
    to nothing, and the cue never reaches the agents.
    """

    info = SubstrateInfo(name="review-game", version="1", kind="reconstruction", source="tests/test_study.py")
    measures = (Measure(name="mutual_accept", description="share of episodes in which both agents accept"),
                Measure(name="task_score", description="mean task score"))
    space = LeverSpace(settings=(
        Setting(lever="model", name="checkpoint", description="checkpoint of every agent", values=("small", "large"),
                default="small"),
        Setting(lever="experience", name="memory", description="reflections kept across episodes",
                values=("carry", "none"), default="carry", removal="none"),
        Setting(lever="environment", name="feedback", description="verdict review shown after each episode",
                values=("shown", "hidden"), default="shown", removal="hidden"),
        Setting(lever="scale", name="agents", description="number of reviewing agents", values=(2, 4), default=2),
        Setting(lever="instructions", name="cue", description="sentence naming the evidence rule",
                values=("present", "absent"), default="present", removal="absent"),
    ))

    def __init__(self, fail_on: str = "") -> None:
        self.fail_on = fail_on
        self.started: list[str] = []
        self.lock = threading.Lock()

    def run(self, condition: Condition, seed: int, workdir: pathlib.Path) -> TrialOutcome:
        with self.lock:
            self.started.append(workdir.name)
        if self.fail_on and workdir.name.startswith(self.fail_on):
            raise RuntimeError("environment did not start")
        chosen = condition.settings
        rate = 0.5 + (seed % 7) / 100
        if chosen["experience.memory"] == "none":
            rate = 0.0
        if chosen["environment.feedback"] == "hidden":
            rate *= 0.4
        if chosen["model.checkpoint"] == "large":
            rate += 0.3
        prompt = f"feedback={chosen['environment.feedback']} memory={chosen['experience.memory']}"
        answer = f"{chosen['model.checkpoint']} {seed}"
        calls = tuple(ModelCall(agent_id=agent, input_sha256=digest(prompt), output_sha256=digest(answer))
                      for agent in ("agent-1", "agent-2"))
        return TrialOutcome(measures={"mutual_accept": rate, "task_score": None}, calls=calls)


def test_setting_and_condition_validation():
    with pytest.raises(ValidationError, match="null cannot be a setting value"):
        Setting(lever="scale", name="agents", description="n", values=(2, None), default=2)
    with pytest.raises(ValidationError, match="listed twice"):
        Setting(lever="scale", name="agents", description="n", values=(2, 2), default=2)
    with pytest.raises(ValidationError, match="removal is not an accepted value"):
        Setting(lever="scale", name="agents", description="n", values=(2, 4), default=2, removal=1)
    flag = Setting(lever="structure", name="channel", description="c", values=(True, 1), default=True)
    assert flag.accepts(1) and not flag.accepts(0)  # True and 1 are different values

    space = ReviewGame.space
    assert space.condition().settings["experience.memory"] == "carry"
    assert space.condition({"scale.agents": 4}).settings["scale.agents"] == 4
    with pytest.raises(StudyError, match="rejected_unsupported: the substrate has no setting 'structure.topology'"):
        space.condition({"structure.topology": "ring"})
    with pytest.raises(StudyError, match="rejected_unsupported: setting 'scale.agents' does not accept 3"):
        space.condition({"scale.agents": 3})
    with pytest.raises(StudyError, match="do not match the substrate"):
        space.validate_condition(Condition(settings={"scale.agents": 2}))
    assert space.condition().digest == space.condition({"scale.agents": 2}).digest
    assert space.condition().digest != space.condition({"scale.agents": 4}).digest


def test_plan_arms_seeds_and_power():
    game = ReviewGame()
    plan = plan_battery(game, measure="mutual_accept", origin="spontaneous",
                        factorials=[("experience.memory", "environment.feedback")], repetitions=4)
    assert [arm.name for arm in plan.arms] == [
        "baseline", "model.checkpoint=large", "experience.memory=none", "environment.feedback=hidden",
        "scale.agents=4", "instructions.cue=absent", "experience.memory=none+environment.feedback=hidden"]
    assert [arm.removal for arm in plan.arms] == [False, False, True, True, False, True, True]
    assert len(plan.specs) == 7 * 4 and len({spec.trial_id for spec in plan.specs}) == 28
    seeds = {arm.name: [spec.seed for spec in plan.specs if spec.arm == arm.name] for arm in plan.arms}
    assert all(values == seeds["baseline"] for values in seeds.values())  # arms are paired by repetition
    both = next(spec for spec in plan.specs if spec.arm.startswith("experience.memory=none+"))
    assert both.condition.settings["experience.memory"] == "none"
    assert both.condition.settings["environment.feedback"] == "hidden"

    only = plan_battery(game, measure="mutual_accept", origin="cued", levers=["experience"], repetitions=4)
    assert [arm.name for arm in only.arms] == ["baseline", "experience.memory=none"]
    with pytest.raises(StudyError, match="cannot show an effect at alpha 0.05"):
        plan_battery(game, measure="mutual_accept", origin="spontaneous", repetitions=3)
    with pytest.raises(StudyError, match="no measure 'collusion'"):
        plan_battery(game, measure="collusion", origin="spontaneous")
    with pytest.raises(StudyError, match="a factorial needs 'scale.agents'"):
        plan_battery(game, measure="mutual_accept", origin="spontaneous",
                     factorials=[("experience.memory", "scale.agents")])
    with pytest.raises(StudyError, match="a factorial needs 'experience.memory'"):
        plan_battery(game, measure="mutual_accept", origin="spontaneous", baseline={"experience.memory": "none"},
                     factorials=[("experience.memory", "environment.feedback")])


def test_runner_runs_in_parallel_resumes_and_keeps_failures(tmp_path):
    plan = plan_battery(ReviewGame(), measure="mutual_accept", origin="spontaneous", levers=["experience"],
                        repetitions=4)
    store = TrialStore(tmp_path)
    failing = ReviewGame(fail_on="experience.memory=none-r02")
    records = run_trials(failing, plan.specs, store, workers=4, order_seed=3)
    assert [record.spec.trial_id for record in records] == [spec.trial_id for spec in plan.specs]
    assert sorted(failing.started) == sorted(spec.trial_id for spec in plan.specs)
    failed = [record for record in records if record.status == "failed"]
    assert [record.spec.trial_id for record in failed] == ["experience.memory=none-r02"]
    assert failed[0].error == "RuntimeError: environment did not start" and failed[0].outcome is None

    again = ReviewGame()
    assert run_trials(again, plan.specs, store, workers=2) == records  # nothing is run or rewritten again
    assert again.started == []
    with pytest.raises(StudyError, match="already has a record"):
        store.save(records[0])

    other = plan_battery(ReviewGame(), measure="mutual_accept", origin="spontaneous", levers=["experience"],
                         repetitions=4, seed=9)
    with pytest.raises(StudyError, match="recorded for a different plan or substrate"):
        run_trials(ReviewGame(), other.specs, store)
    with pytest.raises(StudyError, match="trial ids must be unique"):
        run_trials(ReviewGame(), [*plan.specs, plan.specs[0]], TrialStore(tmp_path / "second"))


def test_runner_records_an_undeclared_measure_as_a_failed_trial(tmp_path):
    class Wrong(ReviewGame):
        def run(self, condition, seed, workdir):
            return TrialOutcome(measures={"mutual_accept": 0.5})

    plan = plan_battery(Wrong(), measure="mutual_accept", origin="spontaneous", levers=["experience"], repetitions=4)
    records = run_trials(Wrong(), plan.specs[:1], TrialStore(tmp_path))
    assert records[0].status == "failed" and "declared ['mutual_accept', 'task_score']" in records[0].error


def test_permutation_test_and_reach_rule():
    assert permutation_p([1, 2, 3, 4], [5, 6, 7, 8]) == pytest.approx(2 / 70)
    assert permutation_p([1, 2, 3, 4], [1, 2, 3, 4]) == 1.0
    assert permutation_p([0, 0, 0, 0], [0, 0, 1, 1]) == pytest.approx(30 / 70)  # both ones on one side: 15 + 15 splits

    def call(agent, prompt, answer):
        return ModelCall(agent_id=agent, input_sha256=digest(prompt), output_sha256=digest(answer))

    first = [call("a", "p1", "o1"), call("b", "p2", "o2")]
    assert reach(first, first) == "identical"
    assert reach(first, [call("a", "p1", "o1"), call("b", "changed", "o2")]) == "reached"
    assert reach(first, [call("a", "p1", "sampled differently"), call("b", "changed", "o2")]) == "undetermined"
    assert reach(first, [*first, call("a", "p3", "o3")]) == "reached"  # the arm made another call
    assert reach(first, [call("b", "p1", "o1")]) == "reached"  # another agent was called
    assert reach(first, []) == "undetermined"


def test_card_states_types_and_interaction(tmp_path):
    game = ReviewGame(fail_on="scale.agents=4-r0")
    plan = plan_battery(game, measure="mutual_accept", origin="spontaneous",
                        factorials=[("experience.memory", "environment.feedback")])
    card = build_card(plan, run_trials(game, plan.specs, TrialStore(tmp_path), workers=3))

    states = {finding.arm: finding for finding in card.findings}
    assert states["experience.memory=none"].state == "necessary" and states["experience.memory=none"].reach == "reached"
    assert states["environment.feedback=hidden"].state == "modulates"
    assert states["model.checkpoint=large"].state == "modulates"
    assert states["model.checkpoint=large"].reach == "not-applicable"
    assert states["instructions.cue=absent"].state == "not-tested"  # same calls as the baseline: it reached nobody
    assert states["instructions.cue=absent"].reason == "the manipulation changed no model input"
    assert states["scale.agents=4"].state == "not-tested" and states["scale.agents=4"].variant.n == 0
    assert card.levers == {"model": "modulates", "training": "not-tested", "structure": "not-tested",
                           "environment": "modulates", "experience": "necessary", "scale": "not-tested",
                           "instructions": "not-tested"}
    assert card.type == ("experience-dependent",) and card.origin == "spontaneous"
    assert card.noise.n == 5 and card.noise.low < card.noise.high
    assert card.missing == tuple(f"scale.agents=4-r0{index}" for index in range(5))

    interaction = card.interactions[0]
    assert interaction.settings == ("experience.memory", "environment.feedback")
    assert interaction.detected is True and interaction.interval[0] > 0  # feedback matters only with memory
    assert type(card).model_validate_json(card.model_dump_json()) == card


def test_card_reads_a_behavior_absent_at_baseline(tmp_path):
    game = ReviewGame()
    plan = plan_battery(game, measure="mutual_accept", origin="spontaneous", baseline={"experience.memory": "none"},
                        levers=["experience", "environment", "model"])
    card = build_card(plan, run_trials(game, plan.specs, TrialStore(tmp_path)))
    states = {finding.arm: finding.state for finding in card.findings}
    assert states == {"model.checkpoint=large": "sufficient", "experience.memory=carry": "sufficient",
                      "environment.feedback=hidden": "not-tested"}
    assert card.type == ()  # nothing was shown to be necessary

    absent = plan_battery(game, measure="task_score", origin="spontaneous", levers=["experience"])
    unmeasured = build_card(absent, run_trials(game, absent.specs, TrialStore(tmp_path / "unmeasured")))
    assert unmeasured.findings[0].state == "not-tested" and len(unmeasured.missing) == 10
    assert type_labels({"instructions": "necessary", "training": "necessary", "model": "modulates"}) == (
        "training-dependent", "instruction-carried")
