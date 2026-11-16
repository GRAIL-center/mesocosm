"""A test is an instrument: repeated runs of two conditions, returned as an effect with its noise.

One test compares the study baseline with one changed condition. Both arms run the same seeds. The
baseline trials are named after their condition, so every test of a session reuses them and the
run-to-run noise is measured once. The result states the effect, the spread of the baseline, whether
the two arms are separated, whether the change reached a model input, and what the test cost.

Exploration tests are cheap and never enter a behavior card. A claim test needs enough repetitions for
its alpha, the held-out value of every partition switch, and seeds that no exploration trial used.
"""

from __future__ import annotations

import random
from collections.abc import Sequence
from statistics import fmean, stdev

from pydantic import BaseModel, ConfigDict, JsonValue

from mesocosm.study.battery import minimum_p
from mesocosm.study.card import Reach, Summary, permutation_p, reach, summarize
from mesocosm.study.condition import Condition, LeverSpace, StudyError, canonical
from mesocosm.study.substrate import Measure
from mesocosm.study.trial import Mode, TrialRecord, TrialSpec, TrialStore

MAX_TEST_REPETITIONS = 12


class Compute(BaseModel):
    """What trials cost. Token counts are ``None`` when a model server reported no usage."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    trials: int
    calls: int
    prompt_tokens: int | None
    completion_tokens: int | None


class TestPlan(BaseModel):
    __test__ = False  # not a pytest class
    model_config = ConfigDict(extra="forbid", frozen=True)

    measure: Measure
    mode: Mode
    alpha: float
    changes: dict[str, JsonValue]
    baseline: Condition
    variant: Condition
    specs: tuple[TrialSpec, ...]


class TestResult(BaseModel):
    """``separated`` is true when every variant trial lies outside the range of the baseline trials.
    ``shown`` is decided only for a claim: an exact permutation test at the plan's alpha."""

    __test__ = False
    model_config = ConfigDict(extra="forbid", frozen=True)

    mode: Mode
    measure: str
    changes: dict[str, JsonValue]
    stand_ins: dict[str, str]
    baseline: Summary
    variant: Summary
    effect: float | None
    noise_sd: float | None
    noise_range: float | None
    separated: bool | None
    p_value: float | None
    minimum_p: float
    shown: bool | None
    reach: Reach
    missing: tuple[str, ...]
    compute: Compute
    statement: str


def compute_of(records: Sequence[TrialRecord]) -> Compute:
    calls = [call for record in records if record.outcome for call in record.outcome.calls]

    def total(values: list[int | None]) -> int | None:
        return None if not values or any(value is None for value in values) else sum(v for v in values if v is not None)

    return Compute(trials=len(records), calls=len(calls), prompt_tokens=total([call.prompt_tokens for call in calls]),
                   completion_tokens=total([call.completion_tokens for call in calls]))


def trial_id(mode: Mode, condition: Condition, repetition: int) -> str:
    return f"{'x' if mode == 'exploration' else 'c'}-{condition.digest[:12]}-r{repetition:02d}"


def plan_test(space: LeverSpace, measures: Sequence[Measure], *, measure: str, changes: dict[str, JsonValue],
              baseline: dict[str, JsonValue] | None = None, repetitions: int = 3, seed: int = 0,
              mode: Mode = "exploration", alpha: float = 0.05) -> TestPlan:
    """Plan the baseline and the changed condition, ``repetitions`` trials each, on shared seeds.

    With no ``changes`` the plan holds the baseline repetitions only (a profile of the baseline)."""
    chosen = next((item for item in measures if item.name == measure), None)
    if chosen is None:
        raise StudyError(f"rejected_unsupported: the substrate has no measure {measure!r}")
    if not 0 < alpha < 1 or not 2 <= repetitions <= MAX_TEST_REPETITIONS:
        raise StudyError(f"alpha must lie in (0, 1) and repetitions in 2..{MAX_TEST_REPETITIONS}")
    base = space.condition(baseline)
    variant = base.replace(changes)
    space.validate_condition(variant)
    if changes and variant.digest == base.digest:
        raise StudyError("rejected_unsupported: the changes leave the baseline as it is")
    for setting in space.settings:
        if setting.held_out is None:
            continue
        held = {canonical(condition.settings[setting.key]) == canonical(setting.held_out)
                for condition in (base, variant)}
        if mode == "exploration" and True in held:
            raise StudyError(f"rejected_unsupported: the value {canonical(setting.held_out)} of {setting.key!r} "
                             "is held out for claims; explore on another value")
        if mode == "claim" and False in held:
            raise StudyError(f"rejected_unsupported: a claim runs on the held-out value of {setting.key!r} "
                             f"({canonical(setting.held_out)})")
    if mode == "claim" and minimum_p(repetitions, repetitions) > alpha:
        raise StudyError(f"rejected_unsupported: {repetitions} repetitions per arm cannot show an effect at alpha "
                         f"{alpha}; the smallest possible p-value is {minimum_p(repetitions, repetitions):.3f}")
    rng = random.Random(f"{mode}:{seed}")
    seeds = [rng.getrandbits(32) for _ in range(repetitions)]
    conditions = [base, variant] if changes else [base]
    specs = tuple(TrialSpec(trial_id=trial_id(mode, condition, repetition), arm=f"cond-{condition.digest[:12]}",
                            repetition=repetition, seed=seeds[repetition], condition=condition, mode=mode)
                  for condition in conditions for repetition in range(repetitions))
    return TestPlan(measure=chosen, mode=mode, alpha=alpha, changes=dict(changes), baseline=base, variant=variant,
                    specs=specs)


def check_budget(specs: Sequence[TrialSpec], store: TrialStore, *, max_calls: int | None = None,
                 max_tokens: int | None = None) -> None:
    """Refuse trials that the remaining compute cannot pay for. The cost of a pending trial is estimated
    from the mean cost of the trials already recorded; with no record yet, only spent budgets stop it."""
    records = store.records()
    spent = compute_of(records)
    pending = sum(1 for spec in specs if store.load(spec.trial_id) is None)
    tokens = None if spent.prompt_tokens is None or spent.completion_tokens is None \
        else spent.prompt_tokens + spent.completion_tokens
    for name, limit, used in (("model calls", max_calls, spent.calls), ("tokens", max_tokens, tokens)):
        if limit is None:
            continue
        if used is None:
            raise StudyError(f"rejected_budget: a budget in {name} needs usage that the model server did not report")
        estimate = used / len(records) * pending if records else 0.0
        if used >= limit or used + estimate > limit:
            raise StudyError(f"rejected_budget: {used} {name} spent of {limit}; {pending} pending trials need "
                             f"about {estimate:.0f} more")


def check_claim_seeds(specs: Sequence[TrialSpec], store: TrialStore) -> None:
    """A claim is confirmed on seeds that exploration did not see."""
    explored = {record.spec.seed for record in store.records() if record.spec.mode == "exploration"}
    reused = explored & {spec.seed for spec in specs if spec.mode == "claim"}
    if reused:
        raise StudyError(f"rejected_unsupported: a claim needs fresh seeds; {len(reused)} were used in exploration")


def evaluate_test(plan: TestPlan, records: Sequence[TrialRecord], space: LeverSpace, store: TrialStore) -> TestResult:
    """Summarize the recorded trials of a test. A trial without a measured value is a missing observation."""
    by_id = {record.spec.trial_id: record for record in records}
    values: dict[str, dict[int, float]] = {plan.baseline.digest: {}, plan.variant.digest: {}}
    calls: dict[str, dict[int, tuple]] = {plan.baseline.digest: {}, plan.variant.digest: {}}
    missing, used = [], []
    for spec in plan.specs:
        record = by_id.get(spec.trial_id)
        value = record.outcome.measures.get(plan.measure.name) if record and record.outcome else None
        if record is not None:
            used.append(record)
        if record is None or record.outcome is None or value is None:
            missing.append(spec.trial_id)
            continue
        values[spec.condition.digest][spec.repetition] = value
        calls[spec.condition.digest][spec.repetition] = record.outcome.calls
    base = list(values[plan.baseline.digest].values())
    variant = list(values[plan.variant.digest].values()) if plan.changes else []
    effect = fmean(variant) - fmean(base) if base and variant else None
    separated = (min(variant) > max(base) or max(variant) < min(base)) if base and variant else None
    lowest = minimum_p(len(base), len(variant))
    p_value = permutation_p(base, variant) if len(base) >= 2 and len(variant) >= 2 else None
    shown = None
    if plan.mode == "claim" and p_value is not None and lowest <= plan.alpha:
        shown = p_value <= plan.alpha
    pairs = sorted(set(calls[plan.baseline.digest]) & set(calls[plan.variant.digest])) if plan.changes else []
    reached = _reach(plan, [reach(calls[plan.baseline.digest][i], calls[plan.variant.digest][i]) for i in pairs])
    stand_ins = {key: str(space.setting(key).stand_in_for) for key in plan.changes if space.setting(key).stand_in_for}
    return TestResult(
        mode=plan.mode, measure=plan.measure.name, changes=plan.changes, stand_ins=stand_ins,
        baseline=summarize(base), variant=summarize(variant), effect=effect,
        noise_sd=stdev(base) if len(base) >= 2 else None, noise_range=max(base) - min(base) if base else None,
        separated=separated, p_value=p_value, minimum_p=lowest, shown=shown, reach=reached, missing=tuple(missing),
        compute=compute_of(used), statement=_statement(plan, effect, separated, shown, reached, stand_ins))


def _reach(plan: TestPlan, results: list[Reach]) -> Reach:
    if not plan.changes or all(key.split(".")[0] in ("model", "training") for key in plan.changes):
        return "not-applicable"  # these act on the policy that answers, not on its input
    if "reached" in results:
        return "reached"
    return "identical" if results and all(result == "identical" for result in results) else "undetermined"


def _statement(plan: TestPlan, effect: float | None, separated: bool | None, shown: bool | None, reached: Reach,
               stand_ins: dict[str, str]) -> str:
    if not plan.changes:
        return "Baseline repetitions only: this is the run-to-run noise of the unchanged condition."
    if effect is None:
        return "Not enough measured trials to compare the two conditions."
    if reached == "identical":
        return "The change reached no model input: the two conditions sent identical requests."
    parts = []
    if plan.mode == "exploration":
        parts.append("Exploration: the arms are separated, worth confirming as a claim." if separated
                     else "Exploration: the arms overlap, so this difference is within run-to-run noise.")
    elif shown is None:
        parts.append("Claim not decided: too few measured trials for the chosen alpha.")
    else:
        parts.append("Claim: the effect is shown at the chosen alpha." if shown
                     else "Claim: no effect is shown at the chosen alpha.")
    if reached == "undetermined":
        parts.append("Whether the change reached a model input could not be determined.")
    for key, lever in stand_ins.items():
        parts.append(f"{key} is a stand-in for {lever}; it is recorded under its own lever.")
    return " ".join(parts)
