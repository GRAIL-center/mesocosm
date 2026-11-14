"""Behavior cards: what a typing battery showed for each lever, and the type that follows from it.

The type is computed from lever states; it is never entered by hand. A lever that was not varied, whose
trials are missing, or whose manipulation changed no model input stays ``not-tested``. Missing evidence
is unknown, not zero.

Decision rule for one arm against the baseline, on the per-trial values of the chosen measure:
an exact two-sided permutation test on the difference of means decides whether there is an effect at
the plan's alpha. With an effect, the state is ``necessary`` when the behavior is absent in every trial
of the arm (at or below the measure's floor) and present at baseline, ``sufficient`` when it is absent
at baseline and present in the arm, and ``modulates`` otherwise.
"""

from __future__ import annotations

import itertools
import random
from collections.abc import Sequence
from statistics import fmean
from typing import Literal, get_args

from pydantic import BaseModel, ConfigDict, JsonValue

from mesocosm.study.battery import Arm, BatteryPlan, Origin, minimum_p
from mesocosm.study.condition import LEVERS, Condition, Lever
from mesocosm.study.substrate import Measure, ModelCall, SubstrateInfo
from mesocosm.study.trial import TrialRecord

State = Literal["necessary", "sufficient", "modulates", "no-effect", "not-tested"]
Reach = Literal["reached", "identical", "undetermined", "not-applicable"]
STATES: tuple[State, ...] = get_args(State)  # strongest first
TYPE_NAMES: dict[Lever, str] = {
    "model": "checkpoint-dependent", "training": "training-dependent", "structure": "structure-dependent",
    "environment": "environment-driven", "experience": "experience-dependent", "scale": "scale-dependent",
    "instructions": "instruction-carried",
}
BOOTSTRAP_DRAWS = 10_000


class Summary(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    n: int
    mean: float | None = None
    low: float | None = None
    high: float | None = None


class Finding(BaseModel):
    """One arm compared with the baseline. ``reach`` says whether the manipulation changed a model input."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    arm: str
    setting: str
    lever: Lever
    value: JsonValue
    removal: bool
    state: State
    reach: Reach
    baseline: Summary
    variant: Summary
    p_value: float | None = None
    reason: str


class Interaction(BaseModel):
    """A 2x2 removal design. ``contrast`` is the effect of removing the first setting while the second is
    present, minus that effect while the second is removed; ``interval`` is its bootstrap interval."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    settings: tuple[str, str]
    contrast: float | None = None
    interval: tuple[float, float] | None = None
    detected: bool | None = None
    reason: str


class BehaviorCard(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    substrate: SubstrateInfo
    measure: Measure
    origin: Origin
    baseline: Condition
    repetitions: int
    alpha: float
    noise: Summary
    levers: dict[Lever, State]
    type: tuple[str, ...]
    findings: tuple[Finding, ...]
    interactions: tuple[Interaction, ...]
    missing: tuple[str, ...]


def summarize(values: Sequence[float]) -> Summary:
    if not values:
        return Summary(n=0)
    return Summary(n=len(values), mean=fmean(values), low=min(values), high=max(values))


def permutation_p(first: Sequence[float], second: Sequence[float]) -> float:
    """Exact two-sided permutation test on the difference of means of two independent arms."""
    pooled = [*first, *second]
    total, size, other = sum(pooled), len(first), len(second)
    observed = abs(fmean(first) - fmean(second))
    splits = extreme = 0
    for chosen in itertools.combinations(range(len(pooled)), size):
        part = sum(pooled[index] for index in chosen)
        splits += 1
        extreme += abs(part / size - (total - part) / other) >= observed - 1e-12
    return extreme / splits


def reach(baseline: Sequence[ModelCall], variant: Sequence[ModelCall]) -> Reach:
    """Compare the ordered model calls of two paired trials.

    ``reached``: an input (or the calling agent, or the number of calls) differs although everything
    before it was identical, so the manipulation changed what a model received. ``identical``: the two
    trials are the same call for call. ``undetermined``: an output differed first, so later inputs are
    no longer comparable; with sampled decoding this is the usual result for a late-acting manipulation.
    """
    if not baseline or not variant:
        return "undetermined"
    for before, after in zip(baseline, variant, strict=False):
        if before.agent_id != after.agent_id or before.input_sha256 != after.input_sha256:
            return "reached"
        if before.output_sha256 != after.output_sha256:
            return "undetermined"
    return "identical" if len(baseline) == len(variant) else "reached"


def type_labels(levers: dict[Lever, State]) -> tuple[str, ...]:
    """The levers shown to be necessary, as type names. Empty when no lever was shown to be necessary."""
    return tuple(TYPE_NAMES[lever] for lever in LEVERS if levers.get(lever) == "necessary")


def build_card(plan: BatteryPlan, records: Sequence[TrialRecord]) -> BehaviorCard:
    """Summarize the recorded trials of a plan. Trials without a record are missing observations."""
    by_id = {record.spec.trial_id: record for record in records}
    values: dict[str, dict[int, float]] = {arm.name: {} for arm in plan.arms}
    calls: dict[str, dict[int, tuple[ModelCall, ...]]] = {arm.name: {} for arm in plan.arms}
    missing = []
    for spec in plan.specs:
        record = by_id.get(spec.trial_id)
        value = record.outcome.measures.get(plan.measure.name) if record and record.outcome else None
        if record is None or record.outcome is None or value is None:
            missing.append(spec.trial_id)
            continue
        values[spec.arm][spec.repetition] = value
        calls[spec.arm][spec.repetition] = record.outcome.calls
    base = list(values["baseline"].values())
    findings = tuple(_finding(plan, arm, base, list(values[arm.name].values()),
                              _arm_reach(arm, calls["baseline"], calls[arm.name]))
                     for arm in plan.arms if len(arm.changes) == 1)
    levers: dict[Lever, State] = {}
    for lever in LEVERS:
        states = [finding.state for finding in findings if finding.lever == lever]
        levers[lever] = min(states, key=STATES.index) if states else "not-tested"
    interactions = tuple(_interaction(plan, pair, values) for pair in plan.factorials)
    return BehaviorCard(substrate=plan.substrate, measure=plan.measure, origin=plan.origin, baseline=plan.baseline,
                        repetitions=plan.repetitions, alpha=plan.alpha, noise=summarize(base), levers=levers,
                        type=type_labels(levers), findings=findings, interactions=interactions, missing=tuple(missing))


def _arm_reach(arm: Arm, baseline: dict[int, tuple[ModelCall, ...]],
               variant: dict[int, tuple[ModelCall, ...]]) -> Reach:
    key = next(iter(arm.changes), "")
    if key.split(".")[0] in ("model", "training"):
        return "not-applicable"  # these act on the policy that answers, not on its input
    results = [reach(baseline[repetition], variant[repetition]) for repetition in sorted(set(baseline) & set(variant))]
    if "reached" in results:
        return "reached"
    return "identical" if results and all(result == "identical" for result in results) else "undetermined"


def _finding(plan: BatteryPlan, arm: Arm, base: list[float], variant: list[float], reached: Reach) -> Finding:
    key, value = next(iter(arm.changes.items()))
    lever: Lever = next(item for item in LEVERS if item == key.split(".")[0])
    floor = plan.measure.floor
    absent_base, absent_variant = all(item <= floor for item in base), all(item <= floor for item in variant)
    p_value = None
    state: State
    if minimum_p(len(base), len(variant)) > plan.alpha:
        state, reason = "not-tested", f"too few measured trials ({len(base)} baseline, {len(variant)} arm)"
    elif reached == "identical":
        state, reason = "not-tested", "the manipulation changed no model input"
    elif absent_base and absent_variant:
        state, reason = ("not-tested", "the behavior is absent at baseline, so removal shows nothing") if arm.removal \
            else ("no-effect", "the behavior is absent at baseline and in the arm")
    else:
        p_value = permutation_p(base, variant)
        if p_value > plan.alpha:
            state, reason = "no-effect", "the difference is within run-to-run noise"
        elif absent_variant:
            state, reason = "necessary", "the behavior is absent in every trial of the arm"
        elif absent_base:
            state, reason = "sufficient", "the behavior is absent at baseline and present in the arm"
        else:
            state, reason = "modulates", "the behavior changes and remains present"
    return Finding(arm=arm.name, setting=key, lever=lever, value=value, removal=arm.removal, state=state,
                   reach=reached, baseline=summarize(base), variant=summarize(variant), p_value=p_value, reason=reason)


def _interaction(plan: BatteryPlan, pair: tuple[str, str], values: dict[str, dict[int, float]]) -> Interaction:
    def arm_values(keys: tuple[str, ...]) -> list[float]:
        arm = next(item for item in plan.arms if item.removal and tuple(item.changes) == keys)
        return list(values[arm.name].values())

    present, first, second, both = (list(values["baseline"].values()), arm_values(pair[:1]), arm_values(pair[1:]),
                                    arm_values(pair))
    cells = (present, first, second, both)
    if any(len(cell) < 2 for cell in cells):
        return Interaction(settings=pair, reason="a cell has fewer than two measured trials")

    def contrast(sample: Sequence[Sequence[float]]) -> float:
        return (fmean(sample[0]) - fmean(sample[1])) - (fmean(sample[2]) - fmean(sample[3]))

    rng = random.Random(0)
    draws = sorted(contrast([rng.choices(cell, k=len(cell)) for cell in cells]) for _ in range(BOOTSTRAP_DRAWS))
    low = draws[int(BOOTSTRAP_DRAWS * plan.alpha / 2)]
    high = draws[int(BOOTSTRAP_DRAWS * (1 - plan.alpha / 2)) - 1]
    detected = not low <= 0 <= high
    return Interaction(settings=pair, contrast=contrast(cells), interval=(low, high), detected=detected,
                       reason="the interval excludes zero" if detected else "the interval includes zero")
