You are a research agent studying collective behavior of language-model agents. Work alone; no web access, no files.

## The environment (fully known)

AgentCollabBench runs a team of 2-6 agents on a realistic engineering task (data engineering, DevOps or software
engineering). Each agent has a role and a system prompt. Agents speak in a fixed speaking order, repeated until a turn
budget (6-40 turns) is reached. At every turn an agent sees only its system prompt and the latest message from each
agent that has an edge to it (its parents in the communication graph); the root agent sees the task prompt. There is
no other memory. Every agent in a team runs the same model, served without extended reasoning, temperature 0.7.

The 20 tasks per behavior were sampled evenly from three topologies: branching trees (one root fanning out),
converging DAGs (many sources feeding one sink) and custom graphs (cross-links, cycles). Scores are the benchmark's
official scores, averaged over the 20 tasks, in [0, 1].

## Behavior: Radioactive Tracer Durability (RTD)

Radioactive Tracer Durability measures whether a tagged critical constraint placed in the root agent's prompt survives every hop to the final outputs; higher means better survival.

## Measured baseline (unchanged tasks)

| Model | Tasks scored | Baseline score | Branching trees | Converging DAGs | Custom graphs |
|---|---|---|---|---|---|
| Qwen3-1.7B | 20 | 0.62 | 0.98 | 0.67 | 0.17 |
| Qwen3-4B | 20 | 0.65 | 0.86 | 1.00 | 0.00 |
| Qwen3-8B | 20 | 0.77 | 0.98 | 1.00 | 0.25 |
| Gemma-4-E4B-it | 20 | 0.83 | 1.00 | 0.81 | 0.67 |
| Llama-3.2-3B-Instruct | 20 | 0.62 | 0.88 | 0.62 | 0.33 |

## Task

Predict each model's average RTD score on the same 20 tasks under each change, one at a time:

- C1 chain: same agents and speaking order, but the graph is rewired into a single chain in order of first speaking
  turn (each agent hears only the previous agent).
- C2 full: same agents and speaking order, but every agent hears every other agent.
- C3 specific: Format the tagged constraint with low salience (plain text, not marked critical, not placed first) instead of high salience.

Reply with one JSON code block and nothing else, using the model keys ['q17', 'q4', 'q8', 'g4', 'l3']:
{"q4": {"C1": {"score": 0.0, "direction": "down|flat|up"}, "C2": {...}, "C3": {...}}, ...}
"Flat" means within about ±0.05 of the baseline. Scores are in [0, 1].
