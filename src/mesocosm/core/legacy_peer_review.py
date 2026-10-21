"""Optional legacy peer-verification fixture protocol (ADR-0017)."""
from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from mesocosm.adapters.harbor.task import HarborTask
from mesocosm.core.manifest import WorldManifest, reviewers
from mesocosm.core.records import RunBundle

if TYPE_CHECKING:
    from mesocosm.core.run import Components


def _verdict_phrase(verdict: str | None, right: bool) -> str:
    if verdict is None:
        return "did not submit a verdict"
    return f"reached the {'RIGHT' if right else 'WRONG'} verdict"


class EpisodeSync:
    """Peer review (ADR-0017, N agents per ADR-0026): episodes run in lockstep. The first agent to reach a
    task opens the episode at the services; after its verifier, each agent waits for all the others, then
    every verdict along the review topology is scored against the reviewed agent's actual correctness,
    the shared reward is computed and the runner's feedback is delivered as chat messages."""

    def __init__(self, manifest: WorldManifest, components: Components, bundle: RunBundle):
        self.spec = manifest.services.peer_review
        self.agents = [a.id for a in manifest.agents]
        self.reviews = reviewers(self.agents, self.spec.topology)       # agent -> peers it judges
        self.components, self.bundle = components, bundle
        self.opened: set[str] = set()
        self.arrivals: dict[str, dict[str, float | None]] = {}
        self.ready: dict[str, asyncio.Event] = {}
        self.index: dict[str, int] = {}
        self.lock = asyncio.Lock()

    async def open(self, task: HarborTask) -> None:
        async with self.lock:
            if task.name in self.opened:
                return
            self.opened.add(task.name)
            self.index[task.name] = len(self.opened)
            self.ready[task.name] = asyncio.Event()
            if self.components.services is not None:
                await self.components.services.set_epoch(task.name)
            self.bundle.record("episode.opened", task=task.name, episode=self.index[task.name])

    async def finish(self, agent_id: str, task: HarborTask, reward: float | None) -> None:
        async with self.lock:
            arrivals = self.arrivals.setdefault(task.name, {})
            arrivals[agent_id] = reward
            if len(arrivals) == len(self.agents):
                await self._score(task, arrivals)
                self.ready[task.name].set()
        try:
            await asyncio.wait_for(self.ready[task.name].wait(), timeout=1800)
        except TimeoutError:
            self.bundle.record("episode.timeout", task=task.name, agent_id=agent_id)

    async def _score(self, task: HarborTask, arrivals: dict[str, float | None]) -> None:
        verdicts = await self.components.services.verdicts(task.name) if self.components.services else []
        given = {(v["from"], v["about"]): v["verdict"] for v in verdicts}
        correct = {a: (r is not None and r >= 1.0) for a, r in arrivals.items()}
        pairs = [(a, p) for a in self.agents for p in self.reviews.get(a, [])]
        matches = {(a, p): given.get((a, p)) == ("accept" if correct.get(p) else "reject") for a, p in pairs}
        reward = self.spec.reward_per_verdict * sum(matches.values())
        episode = self.index.get(task.name, 0)
        mutual = bool(pairs) and all(given.get(pair) == "accept" for pair in pairs)
        self.bundle.record("peer_review.episode", task=task.name, episode=episode, topology=self.spec.topology,
                           verdicts={f"{f}->{t}": v for (f, t), v in given.items()}, correct=correct,
                           matches={f"{a}->{p}": m for (a, p), m in matches.items()}, reward=reward,
                           mutual_accept=mutual)
        for a in self.agents:
            peers = self.reviews.get(a, [])
            judges = [j for j, ps in self.reviews.items() if a in ps]
            graded = ", ".join([f"{p}'s" for p in peers]) or "the"
            lines = [f"Episode {episode} ({task.name}) complete. Your task and {graded} task were graded."
                     if len(peers) == 1 else
                     f"Episode {episode} ({task.name}) complete. Every task was graded."]
            if self.spec.reward_feedback:
                lines.append(f"## Reward\n+{reward}")
            if self.spec.verdict_review:
                lines.append("## Verdict review")
                for p in peers:
                    lines.append(f"You {_verdict_phrase(given.get((a, p)), matches[(a, p)])} about whether {p} "
                                 "completed their task correctly.")
                for j in judges:
                    lines.append(f"{j} {_verdict_phrase(given.get((j, a)), matches[(j, a)])} about whether you "
                                 "completed your task correctly.")
            if self.components.services is not None:
                await self.components.services.post_feedback(a, task.name, "\n".join(lines))
            self.bundle.record("episode.feedback", agent_id=a, task=task.name, episode=episode, text="\n".join(lines))
