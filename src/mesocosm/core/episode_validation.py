"""Cross-field admission checks for measured repeated-interaction conditions."""
from __future__ import annotations

from typing import TYPE_CHECKING

from mesocosm.core.episode_controls import FeedbackSpec

if TYPE_CHECKING:
    from mesocosm.core.manifest import WorldManifest


def episode_condition_errors(world: WorldManifest) -> list[str]:
    errors = []
    peer = world.services.peer_review
    agents = {agent.id for agent in world.agents}
    metrics = {term.metric for term in world.rewards.terms}
    if peer.topology == "random_ring" and (not peer.enabled or peer.protocol != "ratings"):
        errors.append("random_ring requires enabled ratings peer review")
    if peer.matching_seed != 0 and peer.topology != "random_ring":
        errors.append("matching_seed only applies to random_ring")
    for term in world.rewards.terms:
        if set(term.agent_weights) - agents:
            errors.append("reward agent_weights names an unknown agent")
        if agents and not any(term.agent_weights.get(agent, term.weight) for agent in agents):
            errors.append("reward term has zero effective weight for every participant")
    peer_needed = bool(metrics & {"peer_acceptance", "review_accuracy"})
    peer_needed |= world.reputation.enabled and "review_accuracy" in world.reputation.metrics
    if world.learning.mode == "in_context":
        peer_needed |= bool(set(world.learning.feedback.fields) & {"peer_verdicts", "review_accuracy"})
    if peer_needed and (not peer.enabled or peer.protocol != "ratings"):
        errors.append("peer reward/feedback/reputation metrics require enabled ratings peer review")
    accuracy_observed = ("review_accuracy" in metrics
                         or world.reputation.enabled and "review_accuracy" in world.reputation.metrics
                         or world.learning.mode == "in_context" and "review_accuracy" in world.learning.feedback.fields
                         or peer.enabled and (world.rewards.source == "components"
                             or world.learning.mode == "in_context" and "components" in world.learning.feedback.fields))
    if world.rewards.success_threshold != 1.0 and not accuracy_observed:
        errors.append("success_threshold only applies to review_accuracy measurements")
    if world.learning.mode == "none" and world.learning.feedback != FeedbackSpec():
        errors.append("feedback options require learning.mode: in_context")
    if not world.history.episodes and (world.history.scope != "actions" or world.history.max_chars != 60000):
        errors.append("history options require history.episodes > 0")
    if world.history.episodes and (world.memory != "reset" or world.mode != "comparison"):
        errors.append("bounded episode history requires comparison mode and memory: reset")
    if world.history.episodes and any(agent.harness != "opencode" for agent in world.agents):
        errors.append("bounded episode history requires native OpenCode messages")
    if world.reputation.enabled and (not world.rewards.enabled or world.mode != "comparison"):
        errors.append("reputation requires synchronized reward episodes")
    if world.artifacts.enabled and (not world.services.enabled or not world.services.board
                                    or world.mode != "comparison" or not world.rewards.enabled):
        errors.append("artifact exchange requires board services and synchronized reward episodes")
    if world.artifacts.enabled and world.task_services != "isolated":
        errors.append("artifact exchange requires isolated verification")
    assignment = world.task_assignment
    if assignment.policy != "all":
        if assignment.participants is None or assignment.participants >= len(agents):
            errors.append("selective assignment participants must be smaller than the agent population")
        if (world.mode != "comparison" or world.task_services != "isolated" or not world.rewards.enabled
                or world.rewards.allocation != "individual" or peer.enabled):
            errors.append("selective assignment requires isolated comparison tasks, "
                          "individual rewards and no peer review")
    return [f"rejected_unsupported: {message}" for message in errors]
