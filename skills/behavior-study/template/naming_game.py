"""A pairwise naming game for a population of model agents: a template environment.

One trial: N agents, and in each interaction two agents drawn at random each pick one name from a pool of
ten letters without seeing the other's pick. A match raises both scores, a mismatch lowers both. An agent's
prompt shows its own last H interactions. The script reads the trial's condition, calls the model endpoint
once per pick, writes the measures to the outcome file and one line per interaction to events.jsonl next
to it.

Every request carries a sampling seed drawn from the trial seed. Two trials with the same trial seed then
send the same requests until the condition makes them differ, which is what lets the platform decide
whether a changed condition reached a model input (on a server that honors the seed).

Standard library only. It reads TRIAL_CONDITION, TRIAL_SEED, TRIAL_OUTCOME, OPENAI_BASE_URL, OPENAI_API_KEY
and MODEL_KEYS (JSON, agent id -> key).
"""

from __future__ import annotations

import json
import os
import random
import re
import urllib.request
from collections import Counter

POOLS = {"a": "FJKMQRTVXZ", "b": "BCDGHLNPSW"}  # no A or I: they are English words
REWARD, PENALTY = 100, -50
RULES = ("You are playing a game over many rounds. In each round you and one other player each pick one name "
         "from a list, without seeing the other's pick. If both picks are the same, each of you gains "
         f"{REWARD} points. If they differ, each of you loses {-PENALTY} points. Reply with the name only.")
HINT = " Players who settle on one shared name score the most, so try to converge on a single name with the others."


def ask(base_url: str, key: str, model: str, system: str, user: str, seed: int, thinking: bool) -> str:
    body = {"model": model, "temperature": 1.0, "max_tokens": 4096 if thinking else 64, "seed": seed,
            "chat_template_kwargs": {"enable_thinking": thinking},
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}]}
    request = urllib.request.Request(base_url.rstrip("/") + "/chat/completions", data=json.dumps(body).encode(),
                                     headers={"Content-Type": "application/json", "Authorization": f"Bearer {key}"})
    with urllib.request.urlopen(request, timeout=600) as response:
        content = json.load(response)["choices"][0]["message"]["content"] or ""
    return content.rsplit("</think>", 1)[-1]  # the answer follows the reasoning when a server leaves it inline


def prompt(names: list[str], history: list[tuple[str | None, str | None, bool]], shown: int) -> str:
    lines = ["Names: " + ", ".join(names)]
    recent = history[-shown:] if shown else []
    if recent:
        lines.append(f"Your last {len(recent)} rounds, oldest first:")
        for own, other, matched in recent:
            lines.append(f"- you picked {own or 'no valid name'}, the other player picked {other or 'no valid name'}: "
                         f"{'match' if matched else 'no match'}, {REWARD if matched else PENALTY:+d} points")
        lines.append(f"Your score over these rounds: {sum(REWARD if m else PENALTY for _, _, m in recent)}")
    else:
        lines.append("You have no record of earlier rounds.")
    lines.append("Which name do you pick now?")
    return "\n".join(lines)


def main() -> None:
    with open(os.environ["TRIAL_CONDITION"], encoding="utf-8") as stream:
        condition = json.load(stream)
    rng = random.Random(int(os.environ["TRIAL_SEED"]))
    base_url, default_key = os.environ["OPENAI_BASE_URL"], os.environ["OPENAI_API_KEY"]
    keys = json.loads(os.environ.get("MODEL_KEYS", "{}"))
    checkpoint = condition["model.checkpoint"]
    models = checkpoint if isinstance(checkpoint, list) else [checkpoint]
    agents = [f"a{index + 1:02d}" for index in range(condition["scale.agents"])]
    pool = POOLS[condition["environment.name_pool"]]
    system = RULES + (HINT if condition["instructions.coordination_hint"] else "")
    history: dict[str, list[tuple[str | None, str | None, bool]]] = {agent: [] for agent in agents}
    events, replies, valid = [], 0, 0

    def pick(agent: str) -> str | None:
        names = rng.sample(pool, len(pool))  # a fresh order for every query
        reply = ask(base_url, keys.get(agent, default_key), models[agents.index(agent) % len(models)], system,
                    prompt(names, history[agent], condition["experience.history"]), rng.getrandbits(31),
                    condition["model.thinking"])
        found = [letter for letter in re.findall(r"\b[A-Z]\b", reply) if letter in pool]
        return found[0] if found else None

    for step in range(condition["environment.rounds"]):
        for _ in range(len(agents) // 2):
            first, second = rng.sample(agents, 2)
            one, two = pick(first), pick(second)  # both are asked before either sees the result
            replies += 2
            valid += (one is not None) + (two is not None)
            matched = one is not None and one == two
            history[first].append((one, two, matched))
            history[second].append((two, one, matched))
            events.append({"round": step, "agents": [first, second], "choices": [one, two], "match": matched})

    last = [event["match"] for event in events if event["round"] >= condition["environment.rounds"] - 2]
    latest = [next((own for own, _, _ in reversed(history[agent]) if own), None) for agent in agents]
    held = Counter(name for name in latest if name)
    measures = {"final_match_rate": sum(last) / len(last),
                "consensus": max(held.values()) / len(agents) if held else None,
                "valid_rate": valid / replies}
    with open(os.path.join(os.path.dirname(os.environ["TRIAL_OUTCOME"]), "events.jsonl"), "w", encoding="utf-8") as log:
        log.writelines(json.dumps(event) + "\n" for event in events)
    with open(os.environ["TRIAL_OUTCOME"], "w", encoding="utf-8") as stream:
        json.dump({"measures": measures}, stream)


if __name__ == "__main__":
    main()
