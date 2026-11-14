"""Typing batteries: the trials that decide, lever by lever, which conditions a behavior needs.

A battery repeats the study baseline to measure run-to-run noise, then changes one setting at a time,
and removes two settings together where an interaction is to be tested. Every arm uses the same seeds,
so repetition ``i`` of an arm is paired with repetition ``i`` of the baseline.
"""

from __future__ import annotations

import math
import random
import re
from collections.abc import Sequence
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, JsonValue

from mesocosm.study.condition import LEVERS, Condition, Lever, StudyError, canonical
from mesocosm.study.substrate import Measure, Substrate, SubstrateInfo
from mesocosm.study.trial import TrialSpec

Origin = Literal["instructed", "cued", "spontaneous", "trained"]
MAX_REPETITIONS = 12  # the analysis enumerates every split of two arms; 12 per arm is about 2.7 million splits


class Arm(BaseModel):
    """A named condition: the study baseline with ``changes`` applied. The baseline arm has none.

    ``removal`` is true when every change sets a switch to its removal value.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str = Field(min_length=1)
    changes: dict[str, JsonValue] = Field(default_factory=dict)
    removal: bool = False


class BatteryPlan(BaseModel):
    """A frozen plan. ``origin`` states how the behavior arises in the baseline condition; a study must
    declare it, so that an instructed or cued behavior is never reported as spontaneous."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    substrate: SubstrateInfo
    measure: Measure
    origin: Origin
    baseline: Condition
    repetitions: int
    alpha: float
    arms: tuple[Arm, ...]
    factorials: tuple[tuple[str, str], ...]
    specs: tuple[TrialSpec, ...]


def minimum_p(first: int, second: int) -> float:
    """The smallest two-sided p-value an exact permutation test can return for arms of these sizes."""
    return 2 / math.comb(first + second, first) if first and second else 1.0


def _slug(value: JsonValue) -> str:
    return re.sub(r"[^a-z0-9]+", "-", canonical(value).lower()).strip("-") or "empty"


def plan_battery(substrate: Substrate, *, measure: str, origin: Origin,
                 baseline: dict[str, JsonValue] | None = None, levers: Sequence[Lever] = LEVERS,
                 factorials: Sequence[tuple[str, str]] = (), repetitions: int = 5, alpha: float = 0.05,
                 seed: int = 0) -> BatteryPlan:
    """Plan the baseline repetitions, one arm per alternative value of each tested setting, and one
    arm per factorial pair with both settings removed."""
    chosen = next((item for item in substrate.measures if item.name == measure), None)
    if chosen is None:
        raise StudyError(f"rejected_unsupported: the substrate has no measure {measure!r}")
    if not 0 < alpha < 1 or not 1 <= repetitions <= MAX_REPETITIONS:
        raise StudyError(f"alpha must lie in (0, 1) and repetitions in 1..{MAX_REPETITIONS}")
    if minimum_p(repetitions, repetitions) > alpha:
        raise StudyError(f"{repetitions} repetitions per arm cannot show an effect at alpha {alpha}: "
                         f"the smallest possible p-value is {minimum_p(repetitions, repetitions):.3f}")
    base = substrate.space.condition(baseline)
    arms = [Arm(name="baseline")]
    for setting in substrate.space.settings:
        if setting.lever not in levers:
            continue
        for value in setting.values:
            if canonical(value) != canonical(base.settings[setting.key]):
                arms.append(Arm(name=f"{setting.key}={_slug(value)}", changes={setting.key: value},
                                removal=setting.removes(value)))
    for first, second in factorials:
        removed: dict[str, JsonValue] = {}
        for key in (first, second):
            setting = substrate.space.setting(key)
            if setting.removal is None or setting.removes(base.settings[key]) or setting.lever not in levers:
                raise StudyError(f"rejected_unsupported: a factorial needs {key!r} to be present at baseline, "
                                 "removable and among the tested levers")
            removed[key] = setting.removal
        if len(removed) != 2:
            raise StudyError("a factorial crosses two different settings")
        arms.append(Arm(name="+".join(f"{key}={_slug(value)}" for key, value in removed.items()),
                        changes=removed, removal=True))
    if len({arm.name for arm in arms}) != len(arms):
        raise StudyError("two arms have the same name; setting values must differ in letters or digits")
    rng = random.Random(seed)
    seeds = [rng.getrandbits(32) for _ in range(repetitions)]
    specs = tuple(TrialSpec(trial_id=f"{arm.name}-r{repetition:02d}", arm=arm.name, repetition=repetition,
                            seed=seeds[repetition], condition=base.replace(arm.changes))
                  for arm in arms for repetition in range(repetitions))
    return BatteryPlan(substrate=substrate.info, measure=chosen, origin=origin, baseline=base,
                       repetitions=repetitions, alpha=alpha, arms=tuple(arms),
                       factorials=tuple((first, second) for first, second in factorials), specs=specs)
