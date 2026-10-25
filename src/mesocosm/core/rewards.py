"""Generic episode incentives; independent task measurements remain immutable."""
from __future__ import annotations

import asyncio
import json
import math
from typing import Any

from mesocosm.core.control import stop_requested
from mesocosm.core.episode_controls import component_rewards, episode_metrics, review_assignment
from mesocosm.core.manifest import RewardSpec, WorldManifest
from mesocosm.core.records import Event, RunBundle
from mesocosm.env.services.client import ServicesAdmin
from mesocosm.integrations.tasks import Task


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
    """Measured barriers, explicit peer assignments and delayed feedback; no weight updates."""
    def __init__(self, manifest: WorldManifest, services: ServicesAdmin | None, bundle: RunBundle):
        self.manifest, self.services, self.bundle = manifest, services, bundle
        self.agents = [a.id for a in manifest.agents]
        self.ready: dict[str, asyncio.Event] = {}
        self.arrivals: dict[str, dict[str, float | None]] = {}
        self.usages: dict[str, dict[str, dict[str, Any]]] = {}
        self.participants: dict[str, list[str]] = {}
        self.assignments: dict[str, dict[str, list[str]]] = {}
        self.indices: dict[str, int] = {}
        self.current_task = ""
        self.feedback: dict[str, list[dict[str, Any]]] = {}
        self.attached: dict[str, list[dict[str, Any]]] = {}
        self.history: list[dict[str, Any]] = []
        self.lock = asyncio.Lock()

    async def open(self, task: Task) -> None:
        async with self.lock:
            if task.name in self.ready:
                if self.ready[task.name].is_set():
                    raise ValueError("rejected_unsupported: episode task names must be unique within a run")
                return
            self.indices[task.name] = len(self.ready)
            self.current_task = task.name
            self.ready[task.name] = asyncio.Event()
            ids = self.participants.setdefault(task.name, list(self.agents))
            peer = self.manifest.services.peer_review
            assignment = review_assignment(ids, peer.topology, peer.matching_seed, self.indices[task.name])
            self.assignments[task.name] = assignment if peer.enabled else {}
            if peer.enabled:
                if self.services is None:
                    raise RuntimeError("peer evaluation requires a live services connection")
                await self.services.set_epoch(task.name, assignments=assignment)
                self.bundle.record("peer_review.assignment", task=task.name, assignment=assignment,
                                   topology=peer.topology, seed=peer.matching_seed)
            self.bundle.record("episode.opened", task=task.name, episode=len(self.ready), participants=ids,
                               reward=self.manifest.rewards.model_dump(), learning=self.manifest.learning.mode)

    def instructions(self, agent_id: str) -> str:
        spec = self.manifest.rewards
        if spec.source == "components":
            terms = [{"metric": t.metric, "weight": t.agent_weights.get(agent_id, t.weight), "scale": t.scale}
                     for t in spec.terms]
            source = "the sum of weight * measurement / scale for these components: " + json.dumps(terms)
        else:
            source = ("the unchanged task verifier" if spec.source == "verifier" else
                      "the fraction of assigned peer reviewers who accept your completed task")
        text = f"Episode incentive: the raw score comes from {source}. Allocation: {spec.allocation}."
        if any(t.metric == "review_accuracy" for t in spec.terms):
            text += f" Review accuracy compares your verdicts with original scores >= {spec.success_threshold}."
        if self.manifest.task_services == "shared":
            text += (" The verifier scores the shared services once for the whole team. "
                     "That score does not measure individual contributions.")
        if spec.allocation == "mixed":
            text += f" Your allocation is (1 - {spec.team_weight}) * own score + {spec.team_weight} * team mean."
        elif spec.allocation == "team_mean":
            text += " Each participant receives the arithmetic mean of all participants' raw scores."
        if self.manifest.services.peer_review.enabled:
            peers = self.assignments[self.current_task][agent_id]
            names = {a.id: a.username for a in self.manifest.agents}
            text += (" Review these participants' work: " + ", ".join(names[p] for p in peers) + ". "
                     'Use http://chat/api/verdicts with {"about": "<username>", "verdict": "accept" or "reject"}. '
                     "Evaluate task correctness using evidence available through the configured shared resources. "
                     "A missing required verdict leaves the affected reward unmeasured.")
        due = [item for item in self.feedback.get(agent_id, [])
               if item["due"] <= self.indices[self.current_task]]
        if due:
            payload = [item["data"] for item in due]
            feedback = json.dumps(payload[0] if len(payload) == 1 else payload, sort_keys=True)
            if len(feedback) > self.manifest.learning.feedback.max_chars:
                raise ValueError("rejected_unsupported: feedback exceeds max_chars; no silent truncation")
            text += "\nPrevious episode feedback: " + feedback
            self.attached[agent_id] = due
            self.bundle.record("learning.feedback_attached", agent_id=agent_id, text=feedback,
                               mode="in_context", weight_update=False)
        reputation = self.manifest.reputation
        if reputation.enabled:
            rows: list[dict[str, Any]] = []
            names = {a.id: a.username for a in self.manifest.agents}
            for episode in self.history[-reputation.window:]:
                for agent, metrics in episode["metrics"].items():
                    row: dict[str, Any] = {"participant": names[agent], "task": episode["task"]}
                    row.update({str(key): metrics[key] for key in reputation.metrics})
                    rows.append(row)
            text += "\nObserved participant history (missing measurements are unknown): " + json.dumps(rows)
            self.bundle.record("reputation.attached", agent_id=agent_id, task=self.current_task, observations=rows)
        return text

    def confirm_feedback(self, agent_id: str) -> None:
        delivered = self.attached.pop(agent_id, [])
        if delivered:
            self.feedback[agent_id] = [item for item in self.feedback.get(agent_id, []) if item not in delivered]
            payload = [item["data"] for item in delivered]
            self.bundle.record("learning.feedback_delivered", agent_id=agent_id,
                               text=json.dumps(payload[0] if len(payload) == 1 else payload, sort_keys=True),
                               evidence="harness_response_completed", mode="in_context", weight_update=False)

    async def finish(self, agent_id: str, task: Task, reward: float | None,
                     usage: dict[str, Any] | None = None) -> None:
        async with self.lock:
            arrivals = self.arrivals.setdefault(task.name, {})
            if agent_id in arrivals or agent_id not in self.participants[task.name]:
                raise ValueError("duplicate or unassigned episode arrival")
            arrivals[agent_id] = reward
            if usage is not None:
                self.usages.setdefault(task.name, {})[agent_id] = usage
            if len(arrivals) == len(self.participants[task.name]):
                await self._score(task.name, arrivals)
                self.ready[task.name].set()
        while not self.ready[task.name].is_set():
            if stop_requested(self.bundle.root):
                raise EpisodeStopped(f"stopped while waiting for episode {task.name}")
            try:
                await asyncio.wait_for(self.ready[task.name].wait(), timeout=0.25)
            except TimeoutError:
                continue

    async def _score(self, task: str, verifier: dict[str, float | None]) -> None:
        spec = self.manifest.rewards
        evidence: dict[str, Any] = {"verifier_rewards": verifier}
        verdicts: list[dict[str, Any]] = []
        peer = self.manifest.services.peer_review
        if peer.enabled:
            if self.services is None:
                raise RuntimeError("peer evaluation requires a live services connection")
            verdicts = await self.services.verdicts(task)
            evidence.update(peer_verdicts=verdicts, topology=peer.topology)
        assignment = self.assignments[task]
        usage = self.usages.get(task, {})
        metrics = episode_metrics(verifier, verdicts, assignment, usage, spec.success_threshold)
        if spec.source == "components":
            raw = component_rewards(spec.terms, metrics)
            evidence.update(assignment=assignment, usage=usage, metrics=metrics)
        elif spec.source == "peer_verdict":
            raw = {agent: values["peer_acceptance"] for agent, values in metrics.items()}
        else:
            raw = dict(verifier)
        if peer.topology == "random_ring":
            evidence["assignment"] = assignment
        allocated = allocate(raw, spec)
        self.history.append({"task": task, "metrics": metrics, "allocated": allocated})
        for agent in verifier:
            data: dict[str, Any] = {"task": task, "raw_reward": raw[agent], "allocated_reward": allocated[agent],
                    "source": spec.source, "allocation": spec.allocation, "evidence": evidence}
            self.bundle.record("learning.reward", agent_id=agent, **data)
            if self.manifest.learning.mode == "in_context":
                fields = self.manifest.learning.feedback
                available = {**data, "verifier": verifier[agent], "components": metrics[agent],
                             "review_accuracy": metrics[agent]["review_accuracy"],
                             "peer_verdicts": [v for v in verdicts if agent in (v.get("from"), v.get("about"))]}
                feedback: dict[str, Any] = {"task": task, "source": spec.source}
                feedback.update({str(key): available[key] for key in fields.fields})
                self.feedback.setdefault(agent, []).append({"due": self.indices[task] + fields.delay_episodes,
                                                            "data": feedback})
