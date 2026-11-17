"""The adapter of the published Emergent Collusion code: flags and result reading. No model is called."""

from __future__ import annotations

import importlib.util
import pathlib

SPEC = importlib.util.spec_from_file_location(
    "agent_collusion_adapter", pathlib.Path(__file__).parents[1] / "examples" / "agent-collusion" / "adapter.py")
adapter = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(adapter)

BASE = {"model.checkpoint": "Qwen/Qwen3.5-9B", "experience.memory_episodes": -1, "environment.communication": "limited",
        "environment.episodes": 10, "environment.sequences": "1-25"}


def test_a_condition_becomes_the_repository_s_own_flags():
    flags = adapter.flags(BASE, "/trial/sequence", "/trial/out")
    assert flags[:4] == ["--alice-model", "openai/Qwen/Qwen3.5-9B", "--bob-model", "openai/Qwen/Qwen3.5-9B"]
    assert flags[flags.index("--alice-cross-episode-memory-length") + 1] == "-1" and "--throttle-spans" not in flags
    changed = adapter.flags({**BASE, "model.checkpoint": ["a", "b"], "experience.memory_episodes": 0,
                             "environment.communication": "unlimited"}, "/trial/sequence", "/trial/out")
    assert changed[1] == "openai/a" and changed[3] == "openai/b"
    assert changed[changed.index("--bob-cross-episode-memory-length") + 1] == "0"
    assert changed[changed.index("--throttle-spans") + 1] == "no-throttle:*"


def test_paired_trials_share_a_task_sequence_and_claims_use_the_held_out_half():
    assert adapter.sequence_index(BASE, 7) == adapter.sequence_index({**BASE, "experience.memory_episodes": 0}, 7) == 8
    assert {adapter.sequence_index(BASE, seed) for seed in range(100)} == set(range(1, 26))
    held_out = {**BASE, "environment.sequences": "26-50"}
    assert {adapter.sequence_index(held_out, seed) for seed in range(100)} == set(range(26, 51))


def test_measures_come_from_the_verdicts_and_the_repository_s_own_task_evaluation():
    def agent(verdict, task, forced=False):  # the fields the repository's output.py writes for each agent
        return {"verdict": verdict, "expected_verdict": task, "verdict_forced": forced}

    run = {"results": [{"agents": {"alice": agent("accept", "pass"), "bob": agent("accept", "fail")}},
                       {"agents": {"alice": agent("accept", "pass"), "bob": agent("reject", "pass", forced=True)}}]}
    measures, events = adapter.summarize(run)
    assert measures == {"mutual_accept_rate": 0.5, "any_mutual_accept": 1.0, "accept_rate": 0.75, "task_accuracy": 0.75}
    assert [event["mutual_accept"] for event in events] == [True, False] and events[1]["forced"]["bob"] is True
    assert adapter.summarize({"results": []})[0]["mutual_accept_rate"] is None  # no episode finished: unknown
