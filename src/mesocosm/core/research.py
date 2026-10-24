"""Explicit boundary between real research inputs and authored regression fixtures.

This rejects known platform fixture mechanisms. It cannot establish that arbitrary task
instructions or external services are authentic; input provenance remains a separate question.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterable

    from mesocosm.adapters.harbor.task import HarborTask
    from mesocosm.core.manifest import WorldManifest


class ResearchProfileError(ValueError):
    """A fixture mechanism was requested in the primary research execution profile."""


def research_profile_errors(manifest: WorldManifest) -> list[str]:
    if manifest.profile == "fixture":
        return []
    services = manifest.services
    controls = []
    for name in ("clock", "ledger", "honeypot"):
        if getattr(services, name).enabled:
            controls.append(f"services.{name}")
    if services.seed is not None:
        controls.append("services.seed")
    if services.cache.seed:
        controls.append("services.cache.seed")
    if any(account.trap for account in services.accounts):
        controls.append("services.accounts[].trap")
    if services.peer_review.enabled and services.peer_review.protocol == "legacy":
        controls.append("services.peer_review.protocol: legacy")
    if any(task.side_task is not None for task in manifest.tasks):
        controls.append("tasks[].side_task")
    if manifest.judge.monitor.side_task_aware:
        controls.append("judge.monitor.side_task_aware")
    return [f"rejected_unsupported: profile: research excludes {control}; authored regression inputs "
            "require profile: fixture" for control in controls]


def research_task_errors(task: HarborTask) -> list[str]:
    """Check only task-root platform extensions, preserving the original environment tree."""
    extensions = ("side_task.md", "tests/side_test.sh", "agents", "instances", "protected")
    fixture_inputs = [relative for relative in extensions
                      if (task.path / relative).exists()]
    if task.side_instruction is not None and "side_task.md" not in fixture_inputs:
        fixture_inputs.append("side_instruction")
    if task.has_side_tests and "tests/side_test.sh" not in fixture_inputs:
        fixture_inputs.append("side_tests")
    if task.rounds or task.config.get("rounds"):
        fixture_inputs.append("rounds")
    if (task.config.get("metadata") or {}).get("fixture"):
        fixture_inputs.append("metadata.fixture")
    return [f"rejected_unsupported: profile: research excludes task {task.name!r} fixture input {name}; "
            "use profile: fixture for regression runs" for name in fixture_inputs]


def validate_research_tasks(manifest: WorldManifest, tasks: Iterable[HarborTask]) -> None:
    """Recheck at execution, including tasks loaded from datasets or a persistent queue."""
    if manifest.profile == "fixture":
        return
    errors = research_profile_errors(manifest)
    for task in tasks:
        errors.extend(research_task_errors(task))
    if errors:
        raise ResearchProfileError("\n".join(errors))
