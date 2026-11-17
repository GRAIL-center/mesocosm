# Naming game with a real model: first study run

Date: 2026-11-17. This is the first behavior study run with a real model. It used the
[naming-game template](../skills/behavior-study/template/) and the `mesocosm study` commands at commit
`ac119dc`. The raw records (trial records, per-interaction events, the gateway log with every request and
response) are kept outside version control.

## What ran

| Item | Value |
|---|---|
| Model | `Qwen/Qwen3-4B-Instruct-2507`, Hugging Face snapshot `cdbee75f17c01a7cc42f958dc650907174af0554` |
| Serving | vLLM 0.31.0 (torch 2.13.0+cu130), bfloat16, context 8192, one NVIDIA L4 on a Colab runtime |
| Requests | temperature 1.0, at most 64 output tokens, a sampling seed on every request |
| Trials | 54 scheduled, 54 completed, 0 failed: 24 exploration, 30 claim |
| Model calls | 4,056, each admitted and recorded by the gateway with status 200; 709,549 prompt and 9,476 completion tokens |
| Inference time | 11.2 minutes from the first to the last call |
| Environment | reconstruction; 4 or 8 agents, 6 or 12 population rounds, ten letters per name pool |

Every recorded request named the declared model id. The study tools, the gateway and the model server ran
on the same VM. The environment makes no tool calls and executes no model output, so it ran as a plain
process.

## What it shows

- **History dependence reproduced at this scale.** Removing the agents' interaction history lowered the
  final match rate in both claim comparisons, with the hint (0.700 to 0.175) and without it (0.325 to
  0.100). Each has p = 0.040 with five runs per condition, where the smallest possible value is 0.008.
  The two exploration comparisons at twelve rounds point the same way. The five claim comparisons were
  not corrected for multiple testing; with a correction for five tests none passes.
- **No consensus without an instruction.** Without the hint the match rate in the last three rounds was
  0.27 to 0.31 at twelve rounds. The populations did not settle on one name.
- **Mostly consensus with an instruction.** With a sentence telling agents to converge on one name, the
  match rate in the last three rounds reached 0.72 on the held-out pool and 0.94 on the exploration pool.
  This behavior is instructed. One of the five held-out runs stayed at 0.125, and the removal of the hint
  was not shown at five runs (p = 0.119).
- **The model has favorite letters.** With no history in the prompt it answered `J` in 35% and `R` in
  26% of 828 replies on pool a, and `C` in 34% of 1,100 replies on pool b. Agents with no history
  therefore match more often than the uniform chance of 0.10, and hinted populations converged on the
  favorite (`C` on pool b).
- **The reach check was decided.** vLLM honored the per-request seeds: every comparison of a switch that
  acts on the prompt returned `reached`. Only the change of the number of rounds was `undetermined`.

## What it does not show

- It is not a reproduction of the published result. Ashery, Aiello and Baronchelli report spontaneous
  consensus with 24 agents and 70B-class and hosted models. This run used 8 agents, 12 rounds, a 4B model,
  our own prompts and one-letter answers without step-by-step reasoning. Whether more rounds, more agents,
  the original prompt or a larger model produce consensus without an instruction was not tested.
- Five runs per condition leave wide noise: the hinted baseline ranged from 0.125 to 1.000.

## Conditions

Final match rate is the share of matching pairs in the last two population rounds of a trial.

| Mode | Name pool | Agents | Rounds | History | Hint | Runs | Final match rate: mean | lowest | highest | sd | Match rate, first 3 rounds | last 3 rounds |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| exploration | a | 4 | 6 | 5 | no | 3 | 0.417 | 0.000 | 0.750 | 0.382 | 0.28 | 0.39 |
| exploration | a | 8 | 6 | 0 | no | 3 | 0.167 | 0.125 | 0.250 | 0.072 | 0.33 | 0.17 |
| exploration | a | 8 | 6 | 5 | no | 3 | 0.125 | 0.000 | 0.250 | 0.125 | 0.11 | 0.19 |
| exploration | a | 8 | 6 | 5 | yes | 3 | 0.708 | 0.625 | 0.875 | 0.144 | 0.36 | 0.67 |
| exploration | a | 8 | 12 | 0 | no | 3 | 0.125 | 0.000 | 0.375 | 0.217 | 0.33 | 0.19 |
| exploration | a | 8 | 12 | 0 | yes | 3 | 0.333 | 0.250 | 0.375 | 0.072 | 0.28 | 0.39 |
| exploration | a | 8 | 12 | 5 | no | 3 | 0.333 | 0.125 | 0.625 | 0.260 | 0.11 | 0.31 |
| exploration | a | 8 | 12 | 5 | yes | 3 | 0.917 | 0.750 | 1.000 | 0.144 | 0.36 | 0.94 |
| claim | b | 4 | 12 | 5 | yes | 5 | 0.850 | 0.250 | 1.000 | 0.335 | 0.53 | 0.90 |
| claim | b | 8 | 6 | 5 | yes | 5 | 0.450 | 0.125 | 0.875 | 0.326 | 0.40 | 0.48 |
| claim | b | 8 | 12 | 0 | no | 5 | 0.100 | 0.000 | 0.250 | 0.105 | 0.08 | 0.17 |
| claim | b | 8 | 12 | 0 | yes | 5 | 0.175 | 0.000 | 0.375 | 0.190 | 0.28 | 0.18 |
| claim | b | 8 | 12 | 5 | no | 5 | 0.325 | 0.250 | 0.625 | 0.168 | 0.17 | 0.27 |
| claim | b | 8 | 12 | 5 | yes | 5 | 0.700 | 0.125 | 1.000 | 0.360 | 0.42 | 0.72 |

## Claim comparisons

Held-out name pool, seeds unused by exploration, five runs per condition, alpha 0.05.

| Baseline (name pool b, 5 runs each) | Change | Baseline mean | Changed mean | Effect | p (exact permutation) | Reach | State on the card |
|---|---|---|---|---|---|---|---|
| hint on, 8 agents, 12 rounds, history 5 | `experience.history` to `0` | 0.700 | 0.175 | -0.525 | 0.040 | reached | `modulates` |
| hint on, 8 agents, 12 rounds, history 5 | `scale.agents` to `4` | 0.700 | 0.850 | +0.150 | 0.563 | reached | `no-effect` |
| hint on, 8 agents, 12 rounds, history 5 | `environment.rounds` to `6` | 0.700 | 0.450 | -0.250 | 0.302 | reached | `no-effect` |
| hint on, 8 agents, 12 rounds, history 5 | `instructions.coordination_hint` to `false` | 0.700 | 0.325 | -0.375 | 0.119 | reached | `no-effect` |
| hint off, 8 agents, 12 rounds, history 5 | `experience.history` to `0` | 0.325 | 0.100 | -0.225 | 0.040 | reached | `modulates` |

The card state `no-effect` here means that no effect was shown at this sample size. It is not evidence
that the change has no effect.

## Exploration comparisons

| Baseline (name pool a, 3 runs each) | Change | Baseline mean [low, high] | Changed mean [low, high] | Effect | Arms separated | Reach |
|---|---|---|---|---|---|---|
| 8 agents, 6 rounds, history 5, hint off | `experience.history` to `0` | 0.125 [0.000, 0.250] | 0.167 [0.125, 0.250] | +0.042 | no | reached |
| same | `environment.rounds` to `12` | 0.125 [0.000, 0.250] | 0.333 [0.125, 0.625] | +0.208 | no | undetermined |
| same | `instructions.coordination_hint` to `true` | 0.125 [0.000, 0.250] | 0.708 [0.625, 0.875] | +0.583 | yes | reached |
| same | `scale.agents` to `4` | 0.125 [0.000, 0.250] | 0.417 [0.000, 0.750] | +0.292 | no | reached |
| 8 agents, 12 rounds, history 5, hint off | `experience.history` to `0` | 0.333 [0.125, 0.625] | 0.125 [0.000, 0.375] | -0.208 | no | reached |
| same | `instructions.coordination_hint` to `true` | 0.333 [0.125, 0.625] | 0.917 [0.750, 1.000] | +0.583 | yes | reached |
| 8 agents, 12 rounds, history 5, hint on | `experience.history` to `0` | 0.917 [0.750, 1.000] | 0.333 [0.250, 0.375] | -0.583 | yes | reached |

## Defects this run exposed

- The template's `floor` of 0.10 assumes uniform choice. The model's favorite letters put the no-history
  match rate over all rounds between 0.11 and 0.29, so a floor has to be measured, not assumed.
- The final match rate covers 4 to 8 interactions per trial and is coarse. The match rate over the last
  three rounds, computed from the events file, is steadier.
- The state name `no-effect` overstates a non-significant comparison.
- vLLM's first start failed because its build tool `ninja` was not on `PATH`; it must be started with
  its environment's `bin` directory on `PATH`.
- `colab exec` hung three times; restarting the kernel fixed it, and processes started in their own
  session survived the restart.
