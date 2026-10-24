"""Generic episode incentives; independent task measurements remain immutable."""
from __future__ import annotations

import asyncio
import json
import math
from typing import Any

from mesocosm.adapters.harbor.task import HarborTask
from mesocosm.core.control import stop_requested
from mesocosm.core.manifest import RewardSpec, WorldManifest, reviewers
from mesocosm.core.records import Event, RunBundle
from mesocosm.env.services.client import ServicesAdmin


def allocate(raw: dict[str, float | None], spec: RewardSpec) -> dict[str, float | None]:
    """Missing measurements are not zero; an incomplete team has no team reward."""
    values = list(raw.values())
    if any(v is not None and not math.isfinite(v) for v in values):
        raise ValueError("rewards must be finite")
    mean = sum(v for v in values if v is not None) / len(values) if values and None not in values else None
    if spec.allocation == "individual":
        return dict(raw)
    if spec.allocation == "team_mean":
        return {a: mean for a in raw}
    weight = spec.team_weight
    return {a: (v if weight == 0 else mean if weight == 1 else
                (1 - weight) * v + weight * mean if v is not None and mean is not None else None)
            for a, v in raw.items()}


def outcome_records(events: list[Event]) -> dict[str, Any]:
    outcomes: dict[tuple[str | None, str], dict[str, Any]] = {}
    incentives = []
    measurements = []
    teams: dict[str, list[dict[str, Any]]] = {}
    for event in events:
        task = event.data.get("task")
        if not isinstance(task, str):
            continue
        if event.kind == "harness.session":
            outcomes[event.agent_id, task] = {"agent_id": event.agent_id, "task": task,
                                              "status": "incomplete", "reward": None, "error": None}
        elif event.kind == "verifier.reward":
            outcomes[event.agent_id, task] = {"agent_id": event.agent_id, "task": task,
                                              "status": "verified", "reward": event.data.get("reward"),
                                              "error": None,
                                              **{key: event.data[key] for key in
                                                 ("measurement_id", "measurement_scope", "participant_ids")
                                                 if key in event.data}}
            if event.data.get("measurement_scope") != "team":
                measurements.append({"measurement_id": event.event_id, "measurement_scope": "individual",
                                     "agent_id": event.agent_id, "task": task, "reward": event.data.get("reward")})
        elif event.kind == "verifier.team_reward":
            teams.setdefault(task, []).append({key: event.data.get(key) for key in
                                               ("measurement_id", "measurement_scope", "participant_ids",
                                                "task", "reward")})
        elif event.kind in {"harness.task_timeout", "harness.round_timeout", "harness.failed", "verifier.failed"}:
            if (event.agent_id, task) in outcomes:
                outcomes[event.agent_id, task].update(status="failed", error=event.data.get("error") or event.kind)
        elif event.kind == "learning.reward":
            incentives.append({"agent_id": event.agent_id, **event.data})
    # A shared task has one measurement, usable only after every participant confirms the same copy.
    # Later execution/cleanup failures do not invalidate a complete set of verified references.
    for task, candidates in teams.items():
        measurement = candidates[0]
        participants = measurement["participant_ids"] or []
        references = [outcomes.get((agent_id, task), {}) for agent_id in participants]
        valid = len(candidates) == 1 and bool(participants) and all(
            reference.get("status") == "verified" and reference.get("measurement_scope") == "team"
            and reference.get("measurement_id") == measurement["measurement_id"]
            and reference.get("participant_ids") == participants
            and reference.get("reward") == measurement["reward"] for reference in references)
        measurement["status"] = "verified" if valid else "unknown"
        if not valid:
            measurement["recorded_reward"], measurement["reward"] = measurement["reward"], None
            for agent_id in participants:
                outcome = outcomes.setdefault((agent_id, task), {"agent_id": agent_id, "task": task,
                                                                  "status": "incomplete", "error": None})
                outcome["recorded_reward"] = outcome.get("reward")
                outcome.update(reward=None, measurement_id=measurement["measurement_id"],
                               measurement_scope="team", participant_ids=participants)
                if outcome["status"] == "verified":
                    outcome["status"] = "incomplete"
                outcome["error"] = outcome.get("error") or "incomplete or conflicting team measurement"
        measurements.append(measurement)
    return {"task_outcomes": list(outcomes.values()), "task_measurements": measurements, "learning_rewards": incentives}


class EpisodeStopped(RuntimeError):
    """A stop request interrupted an episode barrier."""


class RewardEpisodes:
    """A barrier over N real agent turns, followed by configurable incentive allocation.

    Feedback is delivered with the next task, never an extra unbounded model turn. It is
    present only in in_context mode; this class performs no optimizer or weight update.
    """
    def __init__(self, manifest: WorldManifest, services: ServicesAdmin | None, bundle: RunBundle):
        self.manifest, self.services, self.bundle = manifest, services, bundle
        self.agents = [a.id for a in manifest.agents]
        self.ready: dict[str, asyncio.Event] = {}
        self.arrivals: dict[str, dict[str, float | None]] = {}
        self.feedback: dict[str, str] = {}
        self.lock = asyncio.Lock()

    async def open(self, task: HarborTask) -> None:
        async with self.lock:
            if task.name in self.ready:
                if self.ready[task.name].is_set():
                    raise ValueError("rejected_unsupported: episode task names must be unique within a run")
                return
            self.ready[task.name] = asyncio.Event()
            if self.manifest.services.peer_review.enabled:
                if self.services is None:
                    raise RuntimeError("peer evaluation requires a live services connection")
                await self.services.set_epoch(task.name)
            self.bundle.record("episode.opened", task=task.name, episode=len(self.ready),
                               reward=self.manifest.rewards.model_dump(), learning=self.manifest.learning.mode)

    def instructions(self, agent_id: str) -> str:
        spec = self.manifest.rewards
        source = ("the unchanged task verifier" if spec.source == "verifier" else
                  "the fraction of assigned peer reviewers who accept your completed task")
        text = f"Episode incentive: the raw score comes from {source}. Allocation: {spec.allocation}."
        if self.manifest.task_services == "shared":
            text += (" The verifier scores the shared services once for the whole team. "
                     "That score does not measure individual contributions.")
        if spec.allocation == "mixed":
            text += f" Your allocation is (1 - {spec.team_weight}) * own score + {spec.team_weight} * team mean."
        elif spec.allocation == "team_mean":
            text += " Each participant receives the arithmetic mean of all participants' raw scores."
        if self.manifest.services.peer_review.enabled:
            peers = reviewers(self.agents, self.manifest.services.peer_review.topology)[agent_id]
            names = {a.id: a.username for a in self.manifest.agents}
            text += (" Review these participants' work: " + ", ".join(names[p] for p in peers) + ". "
                     'Use http://chat/api/verdicts with {"about": "<username>", "verdict": "accept" or "reject"}. '
                     "Evaluate task correctness using evidence available through the configured shared resources. "
                     "A missing required verdict leaves the affected reward unmeasured.")
        feedback = self.feedback.get(agent_id)
        if feedback is not None:
            text += "\nPrevious episode feedback: " + feedback
            self.bundle.record("learning.feedback_attached", agent_id=agent_id, text=feedback,
                               mode="in_context", weight_update=False)
        return text

    def confirm_feedback(self, agent_id: str) -> None:
        feedback = self.feedback.pop(agent_id, None)
        if feedback is not None:
            self.bundle.record("learning.feedback_delivered", agent_id=agent_id, text=feedback,
                               evidence="harness_response_completed", mode="in_context", weight_update=False)

    async def finish(self, agent_id: str, task: HarborTask, reward: float | None) -> None:
        async with self.lock:
            arrivals = self.arrivals.setdefault(task.name, {})
            arrivals[agent_id] = reward
            if len(arrivals) == len(self.agents):
                await self._score(task.name, arrivals)
                self.ready[task.name].set()
        # Run manager cancels all peers if any agent fails, so no failed participant can leave this hanging.
        while not self.ready[task.name].is_set():
            if stop_requested(self.bundle.root):
                raise EpisodeStopped(f"stopped while waiting for episode {task.name}")
            try:
                await asyncio.wait_for(self.ready[task.name].wait(), timeout=0.25)
            except TimeoutError:
                continue

    async def _score(self, task: str, verifier: dict[str, float | None]) -> None:
        spec = self.manifest.rewards
        raw = dict(verifier)
        evidence: dict[str, Any] = {"verifier_rewards": verifier}
        if spec.source == "peer_verdict":
            if self.services is None:
                raise RuntimeError("peer evaluation requires a live services connection")
            verdicts = await self.services.verdicts(task)
            given = {(v["from"], v["about"]): v["verdict"] for v in verdicts}
            topology = reviewers(self.agents, self.manifest.services.peer_review.topology)
            raw = {}
            for agent in self.agents:
                judges = [a for a in self.agents if agent in topology[a]]
                ratings = [given.get((j, agent)) for j in judges]
                raw[agent] = (sum(v == "accept" for v in ratings) / len(ratings)
                              if ratings and all(v in ("accept", "reject") for v in ratings) else None)
            evidence["peer_verdicts"] = verdicts
            evidence["topology"] = self.manifest.services.peer_review.topology
        allocated = allocate(raw, spec)
        for agent in self.agents:
            data: dict[str, Any] = {"task": task, "raw_reward": raw[agent], "allocated_reward": allocated[agent],
                    "source": spec.source, "allocation": spec.allocation, "evidence": evidence}
            self.bundle.record("learning.reward", agent_id=agent, **data)
            if self.manifest.learning.mode == "in_context":
                # Do not leak other agents' scores or the independent evaluator in the peer condition.
                self.feedback[agent] = json.dumps({"task": task, "raw_reward": raw[agent],
                                                   "allocated_reward": allocated[agent], "source": spec.source})
