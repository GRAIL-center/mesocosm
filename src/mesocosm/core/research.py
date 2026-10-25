"""Explicit boundary between real research inputs and authored regression fixtures.

This rejects known platform fixture mechanisms. It cannot establish that arbitrary task
instructions or external services are authentic; input provenance remains a separate question.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterable

    from mesocosm.core.manifest import WorldManifest
    from mesocosm.integrations.tasks import Task


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


def research_task_errors(task: Task) -> list[str]:
    """Each source owns its format's fixture checks; common world controls stay above."""
    from mesocosm.integrations.registry import get_task_source

    errors = get_task_source(task.source).research_errors(task)
    if not isinstance(errors, list) or not all(isinstance(error, str) for error in errors):
        raise ResearchProfileError(f"task source {task.source!r} research_errors() must return a list of strings")
    if task.source != "harbor" and task.execution_kind == "harbor":
        from mesocosm.adapters.harbor.source import HarborSource

        # The native runner interprets these fixture directories regardless of the source's format.
        errors = [*errors, *HarborSource().research_errors(task)]
    return errors


def validate_research_tasks(manifest: WorldManifest, tasks: Iterable[Task]) -> None:
    """Recheck at execution, including tasks loaded from datasets or a persistent queue."""
    if manifest.profile == "fixture":
        return
    errors = research_profile_errors(manifest)
    for task in tasks:
        errors.extend(research_task_errors(task))
    if errors:
        raise ResearchProfileError("\n".join(errors))
