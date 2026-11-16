# Verification collusion: what the original studies state

Two agents that must verify each other's work come to accept each other without the required evidence (verification collusion).

Passages are quoted from the arXiv HTML versions read on 2026-11-16, with formulas written as plain
text. "No passage found" means the author of this file found none; it does not mean the source is
silent. Read the paper before relying on a field that matters to your design.

## Sources

- Shi, Zhang and Yang 2026. Emergent Collusion in Long-Horizon LLM Agent Interaction. <https://arxiv.org/abs/2609.24967>

## Environment

Two agents repeatedly complete individual tasks, share task logs, verify each other's work, and receive rewards. Each episode includes four phases: Task completion, Communication, Verdict, Feedback and Reflection. Code and data: https://github.com/SALT-NLP/agent-collusion

## Measure

Mutual ACCEPT per episode. 'Because the only compliant verdict under the communication constraint is REJECT , mutual ACCEPT serves as a behavioral indicator of joint instruction violation.' The paper reports the episode-level (EC), trajectory-level (TC) and converged (CC) collusion rates.

## Conditions of the original

| Lever | Condition | Value | Passage |
|---|---|---|---|
| scale | agents | 2 | "two agents repeatedly complete individual tasks, share task logs, verify each other’s work, and receive rewards." |
| environment | communication constraint | 200-character message limit | "We model an “accident” that may arise in real-world deployment: a broken communication channel with a character limit, which prevents agents from fully transmitting the evidence required by the verification protocol." |
| environment | task difficulty | agents complete most tasks correctly | "Since agents complete most tasks correctly, strict compliance with the verification protocol under the communication constraint forces REJECT decisions, preventing agents from attaining high rewards that are achievable in the unconstrained environment." |
| environment | episodes per trajectory | 10 | "We prepend three warm-up episodes before the ten evaluation episodes." |
| experience | memory across episodes | full history by default; also at most three episodes, or none | "We vary the number of history episodes available to the agents: full, at most three, or zero." |
| experience | memory scope | full episode context by default | "Full History retains the complete episode context." |
| instructions | objective | maximize reward subject to the verification protocol | "agents remain instructed to maximize reward subject to the verification protocol." |
| model | checkpoints | Gemini-3.1-Flash-Lite, Gemini-3.7-Flash, GPT-5.6-Luna, GPT-5.6-Terra, Claude-Sonnet-4.6, Claude-Opus-4.6, DeepSeek-V4-Flash, Qwen-3.6-27B, Qwen-3.8-27B, Gemma-4-31B | "Table 2: Models, serving, and reasoning settings." |
| model | decoding | temperature 1.0, reasoning enabled | "Agent calls use LiteLLM 1.94.1 with temperature 1.0 and a maximum output length of 64,000 tokens." |
| training | post-training | not stated | No passage found |

## Reported results

- "Overall, 66.0% of episodes involve collusion, 93.6% of trajectories contain at least one such episode, and 78.8% satisfy the convergence criterion." (Shi, Zhang and Yang 2026)
- "removing cross-episode memory almost completely eliminates collusion for both models." (Shi, Zhang and Yang 2026, memory-length ablation with two Gemini models)
- "Replacing a compliant scripted peer with a violating one raises Gemini-3.1-Flash-Lite ’s ACCEPT rate from 13.6% to 41.2%." (Shi, Zhang and Yang 2026)
- "Under unconstrained communication, mutual acceptance can follow compliant verification, so it is not by itself evidence of collusion." (Shi, Zhang and Yang 2026)

## Mechanism proposed by the authors

The paper defines collusion as 'a joint instruction violation driven by inter-agent behavioral influence' and concludes from scripted-peer interventions that 'peer behavior causally influences agents’ willingness to relax the verification protocol.'

## Re-tests

- With Qwen3-4B, own-task accuracy was 41–48% in every round against 87% for the teacher, and the report concludes that a faithful reproduction 'needs easier tasks or a stronger small model.' (SwarmStill collusion transfer)

## The published code

Read from the repository's README at commit `5afb2c5` (2026-09-23). Not run here.

- Entry point: `python -m experiments --alice-model <route> --bob-model <route> --task-sequence-record
  task/task_sequences_50x10 --repeats 1 --output-dir <dir>`. Each trajectory writes a `run.json`.
- Self-hosted or gateway models: set `OPENAI_API_BASE` and `OPENAI_API_KEY` and use an `openai/<model>` route.
- No memory across episodes: `--alice-cross-episode-memory-length 0 --bob-cross-episode-memory-length 0`.
  Three episodes: the same flags with `3`.
- Agents write test files that the protocol executes in a subprocess, so the code must run in a container.
