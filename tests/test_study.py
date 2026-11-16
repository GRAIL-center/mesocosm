"""Study-layer tests with a deterministic test environment; no real-agent or real-environment claim."""

from __future__ import annotations

import hashlib
import pathlib
import threading

import pytest
from pydantic import ValidationError

from mesocosm.study.compare import (
    card,
    compare,
    compute_of,
    minimum_p,
    one_switch_variants,
    permutation_p,
    plan,
    profile,
    reach,
)
from mesocosm.study.condition import Condition, LeverSpace, Setting, StudyError
from mesocosm.study.runner import run_trials
from mesocosm.study.substrate import Measure, ModelCall, SubstrateInfo, TrialOutcome
from mesocosm.study.trial import TrialStore

HELD_OUT = {"environment.tasks": "held_out"}


def digest(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


class ReviewGame:
    """A stand-in environment for tests: a rate computed from the condition, plus a little seed noise.

    Memory is required, feedback and the checkpoint change the rate, the group-size switch is wired to
    nothing, the cue never reaches the agents, the persona line is a prompt stand-in for training, and
    the task set is a partition with a held-out half.
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
        Setting(lever="instructions", name="persona", description="a prompt line that imitates a trained disposition",
                values=(False, True), default=False, stand_in_for="training"),
        Setting(lever="environment", name="tasks", description="task set; the held-out half is for claims",
                values=("tuning", "held_out"), default="tuning", held_out="held_out"),
    ))

    def __init__(self, fail_when: tuple[str, object] | None = None) -> None:
        self.fail_when = fail_when
        self.started: list[str] = []
        self.lock = threading.Lock()

    def run(self, condition: Condition, seed: int, workdir: pathlib.Path) -> TrialOutcome:
        with self.lock:
            self.started.append(workdir.name)
        chosen = condition.settings
        if self.fail_when and chosen[self.fail_when[0]] == self.fail_when[1]:
            raise RuntimeError("environment did not start")
        rate = 0.5 + (seed % 7) / 100
        if chosen["experience.memory"] == "none":
            rate = 0.0
        if chosen["environment.feedback"] == "hidden":
            rate *= 0.4
        if chosen["model.checkpoint"] == "large":
            rate += 0.3
        if chosen["instructions.persona"]:
            rate += 0.2
        prompt = f"feedback={chosen['environment.feedback']} memory={chosen['experience.memory']} " \
                 f"persona={chosen['instructions.persona']}"
        answer = f"{chosen['model.checkpoint']} {seed}"
        calls = tuple(ModelCall(agent_id=agent, input_sha256=digest(prompt), output_sha256=digest(answer),
                                prompt_tokens=10, completion_tokens=2) for agent in ("agent-1", "agent-2"))
        return TrialOutcome(measures={"mutual_accept": rate, "task_score": None}, calls=calls)


def plan_for(variants, **options):
    return plan(ReviewGame.space, ReviewGame.measures, measure="mutual_accept", variants=variants, **options)


def test_setting_and_condition_validation():
    with pytest.raises(ValidationError, match="null cannot be a setting value"):
        Setting(lever="scale", name="agents", description="n", values=(2, None), default=2)
    with pytest.raises(ValidationError, match="listed twice"):
        Setting(lever="scale", name="agents", description="n", values=(2, 2), default=2)
    with pytest.raises(ValidationError, match="removal is not an accepted value"):
        Setting(lever="scale", name="agents", description="n", values=(2, 4), default=2, removal=1)
    with pytest.raises(ValidationError, match="held_out is not an accepted value"):
        Setting(lever="environment", name="tasks", description="t", values=("a", "b"), default="a", held_out="c")
    with pytest.raises(ValidationError, match="cannot stand in for its own lever"):
        Setting(lever="training", name="tuned", description="t", values=(0, 1), default=0, stand_in_for="training")
    with pytest.raises(ValidationError, match="request_model needs a model switch"):
        Setting(lever="scale", name="agents", description="n", values=(2, 4), default=2, request_model=True)
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


def test_plan_pairs_conditions_on_shared_seeds_and_reuses_the_baseline():
    first = plan_for([{"experience.memory": "none"}, {"scale.agents": 4}])
    assert len(first.specs) == 9 and len({spec.trial_id for spec in first.specs}) == 9
    by_condition = [[spec.seed for spec in first.specs[start:start + 3]] for start in (0, 3, 6)]
    assert by_condition[0] == by_condition[1] == by_condition[2]  # conditions are paired by repetition
    second = plan_for([{"instructions.persona": True}])
    assert [spec.trial_id for spec in second.specs[:3]] == [spec.trial_id for spec in first.specs[:3]]
    assert plan_for([]).specs == first.specs[:3]  # no change: a baseline profile

    with pytest.raises(StudyError, match="no measure 'collusion'"):
        plan(ReviewGame.space, ReviewGame.measures, measure="collusion")
    with pytest.raises(StudyError, match="repeats the baseline"):
        plan_for([{"experience.memory": "carry"}])
    with pytest.raises(StudyError, match="repeats the baseline or another condition"):
        plan_for([{"scale.agents": 4}, {"scale.agents": 4}])
    with pytest.raises(StudyError, match="repetitions in 2..12"):
        plan_for([], repetitions=1)


def test_exploration_and_claims_are_separate():
    with pytest.raises(StudyError, match="held out for claims"):
        plan_for([{"experience.memory": "none"}], baseline=HELD_OUT)
    with pytest.raises(StudyError, match="held-out value of 'environment.tasks'"):
        plan_for([{"experience.memory": "none"}], mode="claim", repetitions=4)
    with pytest.raises(StudyError, match="3 repetitions per arm cannot show an effect"):
        plan_for([{"experience.memory": "none"}], baseline=HELD_OUT, mode="claim")
    explore = plan_for([{"experience.memory": "none"}], repetitions=4)
    claim = plan_for([{"experience.memory": "none"}], baseline=HELD_OUT, mode="claim", repetitions=4)
    assert not {spec.seed for spec in explore.specs} & {spec.seed for spec in claim.specs}
    assert not {spec.trial_id for spec in explore.specs} & {spec.trial_id for spec in claim.specs}

    base = ReviewGame.space.condition(HELD_OUT)
    variants = one_switch_variants(ReviewGame.space, base)
    assert {"environment.tasks": "tuning"} not in variants and len(variants) == 6  # a partition is not an arm
    assert one_switch_variants(ReviewGame.space, base, ["experience"]) == [{"experience.memory": "none"}]


def test_runner_runs_in_parallel_resumes_and_keeps_failures(tmp_path):
    chosen = plan_for([{"scale.agents": 4}], repetitions=4)
    store = TrialStore(tmp_path)
    failing = ReviewGame(fail_when=("scale.agents", 4))
    records = run_trials(failing, chosen.specs, store, workers=4, order_seed=3)
    assert [record.spec.trial_id for record in records] == [spec.trial_id for spec in chosen.specs]
    assert sorted(failing.started) == sorted(spec.trial_id for spec in chosen.specs)
    failed = [record for record in records if record.status == "failed"]
    assert [record.spec.trial_id for record in failed] == [spec.trial_id for spec in chosen.specs[4:]]
    assert failed[0].error == "RuntimeError: environment did not start" and failed[0].outcome is None

    again = ReviewGame()
    assert run_trials(again, chosen.specs, store, workers=2) == records  # nothing is run or rewritten again
    assert again.started == []
    with pytest.raises(StudyError, match="already has a record"):
        store.save(records[0])
    with pytest.raises(StudyError, match="trial ids must be unique"):
        run_trials(ReviewGame(), [*chosen.specs, chosen.specs[0]], TrialStore(tmp_path / "second"))


def test_runner_records_an_undeclared_measure_as_a_failed_trial(tmp_path):
    class Wrong(ReviewGame):
        def run(self, condition, seed, workdir):
            return TrialOutcome(measures={"mutual_accept": 0.5})

    records = run_trials(Wrong(), plan_for([]).specs[:1], TrialStore(tmp_path))
    assert records[0].status == "failed" and "declared ['mutual_accept', 'task_score']" in records[0].error


def test_permutation_test_and_reach_rule():
    assert permutation_p([1, 2, 3, 4], [5, 6, 7, 8]) == pytest.approx(2 / 70) == minimum_p(4, 4)
    assert permutation_p([1, 2, 3, 4], [1, 2, 3, 4]) == 1.0
    assert permutation_p([0, 0, 0, 0], [0, 0, 1, 1]) == pytest.approx(30 / 70)  # both ones on one side: 15 + 15 splits

    def call(agent, prompt, answer):
        return ModelCall(agent_id=agent, input_sha256=digest(prompt), output_sha256=digest(answer))

    first = [call("a", "p1", "o1"), call("b", "p2", "o2")]
    assert reach(first, first) == "identical"
    assert reach(first, [call("a", "p1", "o1"), call("b", "changed", "o2")]) == "reached"
    assert reach(first, [call("a", "p1", "sampled differently"), call("b", "changed", "o2")]) == "undetermined"
    assert reach(first, [*first, call("a", "p3", "o3")]) == "reached"  # the changed trial made another call
    assert reach(first, [call("b", "p1", "o1")]) == "reached"  # another agent was called
    assert reach(first, []) == "undetermined"


def test_a_comparison_returns_the_effect_next_to_the_noise_with_reach_and_labels(tmp_path):
    game, store = ReviewGame(), TrialStore(tmp_path)
    chosen = plan_for([{"experience.memory": "none"}, {"instructions.persona": True}, {"instructions.cue": "absent"}])
    records = run_trials(game, chosen.specs, store)

    removed = compare(chosen, records, {"experience.memory": "none"}, game.space)
    assert removed.baseline.n == 3 and removed.baseline.sd > 0 and removed.baseline.low < removed.baseline.high
    assert removed.effect == pytest.approx(-removed.baseline.mean) and removed.separated is True
    assert removed.p_value == removed.minimum_p == 0.1 and removed.shown is None  # exploration decides nothing
    assert removed.reach == "reached" and removed.statement.startswith("Exploration: the arms are separated")

    persona = compare(chosen, records, {"instructions.persona": True}, game.space)
    assert persona.stand_ins == {"instructions.persona": "training"}
    assert "instructions.persona is a stand-in for training" in persona.statement
    cue = compare(chosen, records, {"instructions.cue": "absent"}, game.space)
    assert cue.reach == "identical" and cue.statement.startswith("The change reached no model input")

    measured = profile(chosen, records, game.measures)
    assert measured["mutual_accept"] == removed.baseline and measured["task_score"].n == 0  # unknown is not zero
    assert compute_of(records).model_dump() == {"trials": 12, "calls": 24, "prompt_tokens": 240,
                                                "completion_tokens": 48}
    assert len(game.started) == 12 and len(store.records()) == 12


def test_card_gives_each_lever_a_state_from_claim_runs(tmp_path):
    game = ReviewGame(fail_when=("scale.agents", 4))
    variants = one_switch_variants(game.space, game.space.condition(HELD_OUT))
    chosen = plan_for(variants, baseline=HELD_OUT, mode="claim", repetitions=5)
    result = card(chosen, run_trials(game, chosen.specs, TrialStore(tmp_path), workers=3), game.space, game.info,
                  "spontaneous")

    found = {(finding.setting, finding.value): finding for finding in result.findings}
    assert found[("experience.memory", "none")].state == "necessary"
    assert found[("experience.memory", "none")].comparison.shown is True
    assert found[("environment.feedback", "hidden")].state == "modulates"
    assert found[("model.checkpoint", "large")].state == "modulates"
    assert found[("model.checkpoint", "large")].comparison.reach == "not-applicable"
    assert found[("instructions.cue", "absent")].state == "not-tested"  # same calls as the baseline
    assert found[("instructions.cue", "absent")].reason == "the change altered no model input"
    assert found[("scale.agents", 4)].state == "not-tested" and len(found[("scale.agents", 4)].comparison.missing) == 5
    persona = found[("instructions.persona", True)]
    assert persona.lever == "instructions" and persona.comparison.stand_ins == {"instructions.persona": "training"}
    assert result.levers == {"model": "modulates", "training": "not-tested", "structure": "not-tested",
                             "environment": "modulates", "experience": "necessary", "scale": "not-tested",
                             "instructions": "modulates"}
    assert result.origin == "spontaneous" and result.noise.n == 5 and result.noise.low < result.noise.high
    assert type(result).model_validate_json(result.model_dump_json()) == result

    with pytest.raises(StudyError, match="built from a claim plan"):
        card(plan_for([{"experience.memory": "none"}]), [], game.space, game.info, "spontaneous")


def test_card_reads_a_behavior_absent_at_baseline(tmp_path):
    game = ReviewGame()
    baseline = {**HELD_OUT, "experience.memory": "none"}
    variants = one_switch_variants(game.space, game.space.condition(baseline), ["experience", "environment", "model"])
    chosen = plan_for(variants, baseline=baseline, mode="claim", repetitions=5)
    result = card(chosen, run_trials(game, chosen.specs, TrialStore(tmp_path)), game.space, game.info, "spontaneous")
    assert {finding.setting: finding.state for finding in result.findings} == {
        "model.checkpoint": "sufficient", "experience.memory": "sufficient", "environment.feedback": "not-tested"}

    unmeasured = plan(game.space, game.measures, measure="task_score", variants=[{"experience.memory": "none"}],
                      baseline=HELD_OUT, mode="claim", repetitions=5)
    empty = card(unmeasured, run_trials(game, unmeasured.specs, TrialStore(tmp_path / "unmeasured")), game.space,
                 game.info, "spontaneous")
    assert empty.findings[0].state == "not-tested" and len(empty.findings[0].comparison.missing) == 10
