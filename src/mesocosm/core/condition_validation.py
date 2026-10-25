"""Reject declared treatments whose enforcement component is inactive.

Defaults remain valid in serialized manifests. Nondefault settings must not silently
describe an intervention that cannot happen. Keep this separate from model parsing so
the existing runtime/manifest validation boundary remains explicit.
"""

from __future__ import annotations

import math
from typing import TYPE_CHECKING

from pydantic import BaseModel

if TYPE_CHECKING:
    from mesocosm.core.manifest import WorldManifest


def _nondefault_fields(model: BaseModel) -> list[str]:
    defaults = type(model)().model_dump(mode="python")
    return sorted(key for key, value in model.model_dump(mode="python").items()
                  if key != "enabled" and value != defaults[key])


def _exposure_seconds(value: object) -> bool:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return False
    try:
        return math.isfinite(value) and value >= 0
    except OverflowError:
        return False


def inactive_condition_errors(world: WorldManifest) -> list[str]:
    """Return unsupported dormant controls and malformed exposure conditions."""
    errors = []
    for prefix, settings in (
        ("services.chat", world.services.chat),
        ("judge.monitor", world.judge.monitor),
        ("internet", world.internet),
        ("services.peer_review", world.services.peer_review),
    ):
        if not settings.enabled:
            changed = _nondefault_fields(settings)
            if changed:
                fields = ", ".join(f"{prefix}.{key}" for key in changed)
                errors.append(f"rejected_unsupported: {fields} require {prefix}.enabled=true")
    if world.mode != "persistent":
        changed = _nondefault_fields(world.stream)
        if changed:
            fields = ", ".join(f"stream.{key}" for key in changed)
            errors.append(f"rejected_unsupported: {fields} require mode: persistent")
    if "exposure" in world.conditions:
        exposure = world.conditions["exposure"]
        wiki = exposure.get("wiki") if isinstance(exposure, dict) else None
        if (not isinstance(exposure, dict) or set(exposure) != {"wiki"}
                or not isinstance(wiki, dict)
                or any(not isinstance(slug, str) or not slug.strip() or not _exposure_seconds(seconds)
                       for slug, seconds in wiki.items())):
            errors.append("rejected_unsupported: conditions.exposure: expected {wiki: {<page slug>: "
                          "<finite nonnegative seconds after start>}} and no other keys")
    return errors
