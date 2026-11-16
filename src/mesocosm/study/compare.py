"""Comparisons between a baseline and changed conditions, over repeated runs.

A plan holds the baseline and any number of changed conditions. Each runs ``repetitions`` times on shared
seeds, so repetition ``i`` of a changed condition is paired with repetition ``i`` of the baseline. Trials
are named after their condition, so every plan of a study reuses the baseline runs it already has.

``compare`` returns one changed condition against the baseline: the effect next to the spread of the
baseline, an exact two-sided permutation test, and whether the change reached a model input. ``card``
reads a claim plan with one changed switch per condition and gives each lever a state. A lever that was
not varied, whose trials are missing, or whose change altered no model input stays ``not-tested``.

Exploration is cheap and never enters a card. A claim needs the held-out value of every partition switch
and enough repetitions for its alpha. Its seeds come from a separate stream, so a claim never reuses a
seed that exploration saw.
"""

from __future__ import annotations

import itertools
import math
import random
from collections.abc import Sequence
from statistics import fmean, stdev
from typing import Literal, get_args

from pydantic import BaseModel, ConfigDict, JsonValue

from mesocosm.study.condition import LEVERS, Condition, Lever, LeverSpace, StudyError, canonical
from mesocosm.study.substrate import Measure, ModelCall, SubstrateInfo
from mesocosm.study.trial import Mode, TrialRecord, TrialSpec

Origin = Literal["instructed", "cued", "spontaneous", "trained"]
State = Literal["necessary", "sufficient", "modulates", "no-effect", "not-tested"]
Reach = Literal["reached", "identical", "undetermined", "not-applicable"]
STATES: tuple[State, ...] = get_args(State)  # strongest first
MAX_REPETITIONS = 12  # the exact test enumerates every split of two arms; 12 per arm is about 2.7 million


class Summary(BaseModel):
    """The measured values of one condition. ``sd`` of the baseline is the run-to-run noise."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    n: int
    mean: float | None = None
    low: float | None = None
    high: float | None = None
    sd: float | None = None


class Compute(BaseModel):
    """What trials cost. Token counts are ``None`` when a model server reported no usage."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    trials: int
    calls: int
    prompt_tokens: int | None
    completion_tokens: int | None


class Plan(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    measure: Measure
    mode: Mode
    alpha: float
    repetitions: int
    baseline: Condition
    variants: tuple[dict[str, JsonValue], ...]
    specs: tuple[TrialSpec, ...]


class Comparison(BaseModel):
    """One changed condition against the baseline. ``separated``: every changed run lies outside the range
    of the baseline runs. ``shown`` is decided for a claim only."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    changes: dict[str, JsonValue]
    stand_ins: dict[str, Lever]
    baseline: Summary
    variant: Summary
    effect: float | None
    separated: bool | None
    p_value: float | None
    minimum_p: float
    shown: bool | None
    reach: Reach
    missing: tuple[str, ...]
    statement: str


class Finding(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    setting: str
    lever: Lever
    value: JsonValue
    state: State
    reason: str
    comparison: Comparison


class Card(BaseModel):
    """What a claim plan showed for each lever. ``origin`` states how the behavior arises at baseline; a
    study declares it, so that an instructed or cued behavior is never reported as spontaneous."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    substrate: SubstrateInfo
    measure: Measure
    origin: Origin
    baseline: Condition
    repetitions: int
    alpha: float
    noise: Summary
    levers: dict[Lever, State]
    findings: tuple[Finding, ...]
    compute: Compute


def summarize(values: Sequence[float]) -> Summary:
    if not values:
        return Summary(n=0)
    return Summary(n=len(values), mean=fmean(values), low=min(values), high=max(values),
                   sd=stdev(values) if len(values) >= 2 else None)


def minimum_p(first: int, second: int) -> float:
    """The smallest two-sided p-value an exact permutation test can return for arms of these sizes."""
    return 2 / math.comb(first + second, first) if first and second else 1.0


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
    before it was identical, so the change altered what a model received. ``identical``: the two trials
    are the same call for call. ``undetermined``: an output differed first, so later inputs are no longer
    comparable; this is the usual result when requests carry no sampling seed.
    """
    if not baseline or not variant:
        return "undetermined"
    for before, after in zip(baseline, variant, strict=False):
        if before.agent_id != after.agent_id or before.input_sha256 != after.input_sha256:
            return "reached"
        if before.output_sha256 != after.output_sha256:
            return "undetermined"
    return "identical" if len(baseline) == len(variant) else "reached"


def compute_of(records: Sequence[TrialRecord]) -> Compute:
    calls = [call for record in records if record.outcome for call in record.outcome.calls]

    def total(values: list[int | None]) -> int | None:
        return None if not values or any(value is None for value in values) else sum(v for v in values if v is not None)

    return Compute(trials=len(records), calls=len(calls), prompt_tokens=total([call.prompt_tokens for call in calls]),
                   completion_tokens=total([call.completion_tokens for call in calls]))


def trial_id(mode: Mode, condition: Condition, repetition: int) -> str:
    return f"{'x' if mode == 'exploration' else 'c'}-{condition.digest[:12]}-r{repetition:02d}"


def one_switch_variants(space: LeverSpace, baseline: Condition, levers: Sequence[Lever] = LEVERS) -> list[
        dict[str, JsonValue]]:
    """One changed condition per alternative value of each tested switch. Partition switches are skipped."""
    return [{setting.key: value} for setting in space.settings
            if setting.held_out is None and setting.lever in levers
            for value in setting.values if canonical(value) != canonical(baseline.settings[setting.key])]


def plan(space: LeverSpace, measures: Sequence[Measure], *, measure: str,
         variants: Sequence[dict[str, JsonValue]] = (), baseline: dict[str, JsonValue] | None = None,
         repetitions: int = 3, seed: int = 0, mode: Mode = "exploration", alpha: float = 0.05) -> Plan:
    """Plan the baseline and each changed condition. With no variants the plan is a baseline profile."""
    chosen = next((item for item in measures if item.name == measure), None)
    if chosen is None:
        raise StudyError(f"rejected_unsupported: the substrate has no measure {measure!r}")
    if not 0 < alpha < 1 or not 2 <= repetitions <= MAX_REPETITIONS:
        raise StudyError(f"rejected_unsupported: alpha must lie in (0, 1) and repetitions in 2..{MAX_REPETITIONS}")
    base = space.condition(baseline)
    conditions = [base]
    for changes in variants:
        changed = base.replace(changes)
        space.validate_condition(changed)
        if not changes or changed.digest in {condition.digest for condition in conditions}:
            raise StudyError(f"rejected_unsupported: {canonical(changes)} repeats the baseline or another condition")
        conditions.append(changed)
    for setting in space.settings:
        if setting.held_out is None:
            continue
        held = {canonical(condition.settings[setting.key]) == canonical(setting.held_out) for condition in conditions}
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
    specs = tuple(TrialSpec(trial_id=trial_id(mode, condition, repetition), repetition=repetition,
                            seed=seeds[repetition], condition=condition, mode=mode)
                  for condition in conditions for repetition in range(repetitions))
    return Plan(measure=chosen, mode=mode, alpha=alpha, repetitions=repetitions, baseline=base,
                variants=tuple(dict(changes) for changes in variants), specs=specs)


def profile(plan: Plan, records: Sequence[TrialRecord], measures: Sequence[Measure]) -> dict[str, Summary]:
    """Every declared measure over the baseline runs of a plan."""
    wanted = {spec.trial_id for spec in plan.specs if spec.condition.digest == plan.baseline.digest}
    done = [record.outcome for record in records if record.spec.trial_id in wanted and record.outcome is not None]
    return {measure.name: summarize([value for outcome in done
                                     for value in [outcome.measures.get(measure.name)] if value is not None])
            for measure in measures}


def compare(plan: Plan, records: Sequence[TrialRecord], changes: dict[str, JsonValue], space: LeverSpace) -> Comparison:
    """One changed condition of a plan against its baseline. A trial without a value is a missing observation."""
    by_id = {record.spec.trial_id: record for record in records}
    variant = plan.baseline.replace(changes)
    values: dict[str, dict[int, float]] = {plan.baseline.digest: {}, variant.digest: {}}
    calls: dict[str, dict[int, tuple[ModelCall, ...]]] = {plan.baseline.digest: {}, variant.digest: {}}
    missing = []
    for spec in plan.specs:
        if spec.condition.digest not in values:
            continue
        record = by_id.get(spec.trial_id)
        value = record.outcome.measures.get(plan.measure.name) if record and record.outcome else None
        if record is None or record.outcome is None or value is None:
            missing.append(spec.trial_id)
            continue
        values[spec.condition.digest][spec.repetition] = value
        calls[spec.condition.digest][spec.repetition] = record.outcome.calls
    base, changed = list(values[plan.baseline.digest].values()), list(values[variant.digest].values())
    effect = fmean(changed) - fmean(base) if base and changed else None
    separated = (min(changed) > max(base) or max(changed) < min(base)) if base and changed else None
    lowest = minimum_p(len(base), len(changed))
    p_value = permutation_p(base, changed) if len(base) >= 2 and len(changed) >= 2 else None
    shown = p_value <= plan.alpha if plan.mode == "claim" and p_value is not None and lowest <= plan.alpha else None
    pairs = sorted(set(calls[plan.baseline.digest]) & set(calls[variant.digest]))
    reached = _reach(changes, [reach(calls[plan.baseline.digest][i], calls[variant.digest][i]) for i in pairs])
    stand_ins = {key: lever for key in changes if (lever := space.setting(key).stand_in_for) is not None}
    return Comparison(changes=changes, stand_ins=stand_ins, baseline=summarize(base), variant=summarize(changed),
                      effect=effect, separated=separated, p_value=p_value, minimum_p=lowest, shown=shown,
                      reach=reached, missing=tuple(missing),
                      statement=_statement(plan.mode, effect, separated, shown, reached, stand_ins))


def card(plan: Plan, records: Sequence[TrialRecord], space: LeverSpace, substrate: SubstrateInfo,
         origin: Origin) -> Card:
    """The state of each lever, from a claim plan whose changed conditions each change one switch."""
    if plan.mode != "claim" or any(len(changes) != 1 for changes in plan.variants):
        raise StudyError("rejected_unsupported: a behavior card is built from a claim plan that changes one switch "
                         "per condition")
    findings = []
    for changes in plan.variants:
        (key, value), = changes.items()
        setting = space.setting(key)
        comparison = compare(plan, records, changes, space)
        state, reason = _state(comparison, plan.measure.floor, setting.removes(value), plan.alpha)
        findings.append(Finding(setting=key, lever=setting.lever, value=value, state=state, reason=reason,
                                comparison=comparison))
    levers: dict[Lever, State] = {}
    for lever in LEVERS:
        states = [finding.state for finding in findings if finding.lever == lever]
        levers[lever] = min(states, key=STATES.index) if states else "not-tested"
    wanted = {spec.trial_id for spec in plan.specs}
    return Card(substrate=substrate, measure=plan.measure, origin=origin, baseline=plan.baseline,
                repetitions=plan.repetitions, alpha=plan.alpha, noise=profile(plan, records, [plan.measure])[
                    plan.measure.name], levers=levers, findings=tuple(findings),
                compute=compute_of([record for record in records if record.spec.trial_id in wanted]))


def _reach(changes: dict[str, JsonValue], results: list[Reach]) -> Reach:
    if all(key.split(".")[0] in ("model", "training") for key in changes):
        return "not-applicable"  # these act on the policy that answers, not on its input
    if "reached" in results:
        return "reached"
    return "identical" if results and all(result == "identical" for result in results) else "undetermined"


def _state(comparison: Comparison, floor: float, removal: bool, alpha: float) -> tuple[State, str]:
    base, changed = comparison.baseline, comparison.variant
    if comparison.minimum_p > alpha or comparison.p_value is None or base.high is None or changed.high is None:
        return "not-tested", f"too few measured trials ({base.n} baseline, {changed.n} changed)"
    if comparison.reach == "identical":
        return "not-tested", "the change altered no model input"
    absent_base, absent_changed = base.high <= floor, changed.high <= floor
    if absent_base and absent_changed:
        return ("not-tested", "the behavior is absent at baseline, so removal shows nothing") if removal \
            else ("no-effect", "the behavior is absent at baseline and in the changed condition")
    if comparison.p_value > alpha:
        return "no-effect", "the difference is within run-to-run noise"
    if absent_changed:
        return "necessary", "the behavior is absent in every trial of the changed condition"
    if absent_base:
        return "sufficient", "the behavior is absent at baseline and present in the changed condition"
    return "modulates", "the behavior changes and remains present"


def _statement(mode: Mode, effect: float | None, separated: bool | None, shown: bool | None, reached: Reach,
               stand_ins: dict[str, Lever]) -> str:
    if effect is None:
        return "Not enough measured trials to compare the two conditions."
    if reached == "identical":
        return "The change reached no model input: the two conditions sent identical requests."
    if mode == "exploration":
        parts = ["Exploration: the arms are separated, worth confirming as a claim." if separated
                 else "Exploration: the arms overlap, so this difference is within run-to-run noise."]
    elif shown is None:
        parts = ["Claim not decided: too few measured trials for the chosen alpha."]
    else:
        parts = ["Claim: the effect is shown at the chosen alpha." if shown
                 else "Claim: no effect is shown at the chosen alpha."]
    if reached == "undetermined":
        parts.append("Whether the change reached a model input could not be determined.")
    parts.extend(f"{key} is a stand-in for {lever}; it is recorded under its own lever."
                 for key, lever in stand_ins.items())
    return " ".join(parts)
