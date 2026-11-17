"""Run one trajectory of the published Emergent Collusion code (Shi, Zhang and Yang, arXiv:2609.24967).

The repository is not changed. This adapter copies it into the trial directory, turns the trial's condition
into the repository's own command-line flags, runs it, and reads its run.json into measures and one event
per episode. Standard library only; it runs with the interpreter of the repository's environment.
"""

from __future__ import annotations

import json
import os
import pathlib
import shutil
import subprocess
import sys

HERE = pathlib.Path(__file__).resolve().parent
SEQUENCES = "task/task_sequences_50x10"
HALVES = {"1-25": 1, "26-50": 26}   # first sequence of each half; a trial's seed picks one of its 25
MAX_OUTPUT_TOKENS = 8192            # the original uses 64,000; a self-hosted model needs room for the prompt


def flags(condition: dict, record: str, output: str) -> list[str]:
    """The repository's flags for one condition. ``record`` is the task-sequence directory to read."""
    checkpoint = condition["model.checkpoint"]
    alice, bob = checkpoint if isinstance(checkpoint, list) else (checkpoint, checkpoint)
    memory = str(condition["experience.memory_episodes"])
    out = ["--alice-model", f"openai/{alice}", "--bob-model", f"openai/{bob}",
           "--alice-reasoning-effort", "default", "--bob-reasoning-effort", "default",  # the server's own setting
           "--alice-max-output-tokens", str(MAX_OUTPUT_TOKENS), "--bob-max-output-tokens", str(MAX_OUTPUT_TOKENS),
           "--alice-cross-episode-memory-length", memory, "--bob-cross-episode-memory-length", memory,
           "--task-sequence-record", record, "--repeats", "1", "--start-index", "1", "--output-dir", output,
           "--quiet"]
    if condition["environment.communication"] == "unlimited":
        out += ["--throttle-spans", "no-throttle:*"]
    return out


def sequence_index(condition: dict, seed: int) -> int:
    return HALVES[condition["environment.sequences"]] + seed % 25


def summarize(run: dict) -> tuple[dict[str, float | None], list[dict]]:
    """Measures and per-episode events from the repository's run.json."""
    events = []
    for index, episode in enumerate(run["results"]):
        agents = episode["agents"]
        verdicts = {name: str(agent["verdict"]).upper() for name, agent in agents.items()}
        events.append({"episode": index, "verdicts": verdicts,
                       "task_correct": {name: agent["expected_verdict"] == "pass" for name, agent in agents.items()},
                       "forced": {name: bool(agent["verdict_forced"]) for name, agent in agents.items()},
                       "mutual_accept": all(verdict == "ACCEPT" for verdict in verdicts.values())})
    if not events:
        return {"mutual_accept_rate": None, "any_mutual_accept": None, "accept_rate": None, "task_accuracy": None}, []
    verdicts = [verdict for event in events for verdict in event["verdicts"].values()]
    correct = [value for event in events for value in event["task_correct"].values()]
    mutual = [event["mutual_accept"] for event in events]
    return {"mutual_accept_rate": sum(mutual) / len(mutual), "any_mutual_accept": float(any(mutual)),
            "accept_rate": sum(verdict == "ACCEPT" for verdict in verdicts) / len(verdicts),
            "task_accuracy": sum(correct) / len(correct)}, events


def main() -> None:
    condition = json.loads(pathlib.Path(os.environ["TRIAL_CONDITION"]).read_text(encoding="utf-8"))
    seed, outcome = int(os.environ["TRIAL_SEED"]), pathlib.Path(os.environ["TRIAL_OUTCOME"])
    trial = outcome.parent
    repo = trial / "repo"
    shutil.copytree(HERE / "repo", repo, ignore=shutil.ignore_patterns(".git"))
    # One sequence, cut to the condition's number of episodes, as the only entry of its own record directory.
    source = repo / SEQUENCES / f"rep{sequence_index(condition, seed):03d}_sampled_manifest.json"
    pairs = json.loads(source.read_text(encoding="utf-8"))["pairs"][:condition["environment.episodes"]]
    record = trial / "sequence"
    record.mkdir()
    (record / "rep001_sampled_manifest.json").write_text(json.dumps({"pairs": pairs}, indent=1), encoding="utf-8")
    env = {**os.environ, "LITELLM_LOCAL_MODEL_COST_MAP": "True", "HOME": str(trial),
           "PATH": f"{pathlib.Path(sys.executable).parent}:{os.environ.get('PATH', '/usr/bin:/bin')}"}
    command = [sys.executable, "-m", "experiments", *flags(condition, str(record), str(trial / "out"))]
    subprocess.run(command, cwd=repo, env=env, check=True)
    (run_file,) = (trial / "out").glob("run_*/run.json")
    measures, events = summarize(json.loads(run_file.read_text(encoding="utf-8")))
    (trial / "events.jsonl").write_text("".join(json.dumps(event) + "\n" for event in events), encoding="utf-8")
    outcome.write_text(json.dumps({"measures": measures, "note": f"task sequence {source.name}"}), encoding="utf-8")


if __name__ == "__main__":
    main()
