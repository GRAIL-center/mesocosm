# Naming game with real models

Two study runs with real models on the [naming-game template](../skills/behavior-study/template/), both on
2026-11-17. The raw records (trial records, per-interaction events, the gateway logs with every request
and response) are kept outside version control.

**Where it stands.** With Qwen3-4B, populations settle on one shared name without being told to coordinate,
provided the prompt asks for brief step-by-step reasoning about the history, as the original study did. The
behavior disappears when the interaction history is removed. The newer and larger Qwen3.5-9B does not show
it under the same conditions. The first run, with one-letter answers and no reasoning, had found consensus
only under an instruction to converge; that conclusion is superseded.

## Run 2: two models, with reasoning

### What ran

| Item | Value |
|---|---|
| Models | `Qwen/Qwen3-4B-Instruct-2507` (snapshot `cdbee75f17c01a7cc42f958dc650907174af0554`) and `Qwen/Qwen3.5-9B` (snapshot `c202236235762e1c871ad0ccb60c8ee5ba337b9a`), chosen as a recent small open-weight model with an unrestricted license |
| Serving | vLLM 0.31.0 (torch 2.13.0+cu130), bfloat16, context 8192, one NVIDIA A100 40 GB on a Colab runtime; both models served side by side, routed by model id |
| Requests | temperature 1.0, a sampling seed on every request; at most 64 output tokens for a direct answer and 768 with reasoning |
| Template | version 0.3.1 at commit `4da5e02` for the reported comparisons; 0.2.0 and 0.3.0 for the earlier attempts listed below |
| Trials | 108 scheduled: 91 completed, 3 failed, 14 stopped by the operator without a record |
| Model calls | 12,192 recorded by the gateway, all with status 200; the completed trials hold 11,808 of them, with 2,566,300 prompt and 1,256,025 completion tokens |
| Compute | 10.8 Colab compute units |

| Study root | Template version | Trials scheduled | Completed | Failed | Stopped without a record | Model calls recorded |
|---|---|---|---|---|---|---|
| 9B, direct answers; thinking mode | 0.2.0 | 18 | 9 | 3 | 6 | 905 |
| 4B against 9B, direct answers | 0.2.0 | 6 | 6 | 0 | 0 | 576 |
| 4B, open-ended reasoning | 0.3.0 | 6 | 2 | 0 | 4 | 410 |
| 9B, open-ended reasoning | 0.3.0 | 6 | 2 | 0 | 4 | 317 |
| 9B, brief reasoning | 0.3.1 | 12 | 12 | 0 | 0 | 1,152 |
| 4B, brief reasoning; claims | 0.3.1 | 60 | 60 | 0 | 0 | 8,832 |
| **Total** | | 108 | 91 | 3 | 14 | 12,192 |

The three failed trials requested the 4B model before its server was up and are kept as failed. The fourteen
stopped trials belong to two prompt modes that were abandoned, described under "What went wrong".

### What it shows

All numbers are final match rates: the share of matching pairs in the last two population rounds. The claim
baseline is Qwen3-4B, 8 agents, 12 rounds, history of 5, brief reasoning, no hint, on the held-out name pool,
five runs per condition (mean 0.675, lowest 0.250, highest 1.000, sd 0.349).

- **Consensus without an instruction.** At 12 rounds 3 of 5 baseline runs ended with all eight agents on
  one name. At 30 rounds all 5 did. In exploration at the population size of the original study, 24 agents
  and 30 rounds, all 3 runs ended with all 24 agents on one name.
- **The name is not fixed in advance.** The five 30-round runs agreed on S, S, B, W and C; the three 24-agent
  runs on F, F and Q.
- **It needs the interaction history.** Without history the rate fell from 0.675 to 0.125 (p = 0.016) and no
  run reached consensus. The prompts confirm the change: none of the 480 requests
  of the no-history trials showed a history, and 440 of the
  480 baseline requests did.
- **A larger population needs more rounds.** 24 agents at 12 rounds reached 0.192 (p = 0.024) and no
  consensus; at 30 rounds they reached it in every exploration run.
- **The reasoning step matters, on the evidence of exploration.** With a direct one-letter answer the 4B
  model reached 0.292 against 0.958 with reasoning on the exploration pool, with separated arms. On the
  held-out pool the difference (0.300 against 0.675) was not shown at five runs (p = 0.151).
- **The checkpoint matters, and not by size.** Qwen3.5-9B reached 0.125 where Qwen3-4B reached 0.675
  (p = 0.040), and no 9B run without a hint ended in consensus under any condition tried.
- **Why the 9B model does not converge.** Where a prompt's history contained a match, the 4B model picked a
  name that had matched in 3,322 of 3,482 replies (95%) and the 9B model in 81 of
  185 (44%). The 9B model called the other player an "opponent" in 85% of those
  replies, the 4B model in 25%, and it often changed its name to break a pattern.

### What it does not show

- The eight claim comparisons were not corrected for multiple testing. With five runs per condition the
  smallest possible p-value is 0.008, so none could pass a correction for eight tests.
- The 24-agent result is exploration: three runs on the pool used for tuning. It has not been run as a claim.
- The effect of the hint is not established. It raised the rate on the held-out pool (0.975 against 0.675,
  p = 0.167) and lowered it on the exploration pool (0.375 against 0.958).
- The environment is a reconstruction. Population size, name-pool size, memory length and payoffs of the
  24-agent run follow the original study; the prompts are our own, the reasoning is limited to two or three
  sentences, and the original used 70B-class and hosted models.
- Two models from one family were compared. Nothing here says which property of a checkpoint decides the
  behavior.

### Conditions

| Mode | Model | Reasoning | Agents | Rounds | History | Hint | Name pool | Runs | Final match rate: mean | lowest | highest | Match rate, last 3 rounds | Runs ending in full consensus | Names agreed on |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| claim | Qwen3-4B | yes | 4 | 12 | 5 | no | b | 5 | 0.700 | 0.000 | 1.000 | 0.67 | 4 of 5 | S, C, W, G |
| claim | Qwen3-4B | yes | 8 | 6 | 5 | no | b | 5 | 0.275 | 0.000 | 0.750 | 0.22 | 0 of 5 | - |
| claim | Qwen3-4B | yes | 8 | 12 | 5 | no | b | 5 | 0.675 | 0.250 | 1.000 | 0.68 | 3 of 5 | C, L, G |
| claim | Qwen3-4B | yes | 8 | 12 | 5 | yes | b | 5 | 0.975 | 0.875 | 1.000 | 0.98 | 5 of 5 | C, C, C, C, G |
| claim | Qwen3-4B | yes | 8 | 12 | 0 | no | b | 5 | 0.125 | 0.000 | 0.250 | 0.15 | 0 of 5 | - |
| claim | Qwen3-4B | yes | 8 | 30 | 5 | no | b | 5 | 1.000 | 1.000 | 1.000 | 1.00 | 5 of 5 | S, S, B, W, C |
| claim | Qwen3-4B | yes | 24 | 12 | 5 | no | b | 5 | 0.192 | 0.083 | 0.375 | 0.22 | 0 of 5 | - |
| claim | Qwen3-4B | no | 8 | 12 | 5 | no | b | 5 | 0.300 | 0.000 | 0.875 | 0.30 | 1 of 5 | L |
| claim | Qwen3.5-9B | yes | 8 | 12 | 5 | no | b | 5 | 0.125 | 0.000 | 0.375 | 0.08 | 0 of 5 | - |
| exploration | Qwen3-4B | yes | 8 | 12 | 5 | no | a | 3 | 0.958 | 0.875 | 1.000 | 0.92 | 3 of 3 | Q, Q, F |
| exploration | Qwen3-4B | yes | 8 | 12 | 5 | yes | a | 3 | 0.375 | 0.125 | 0.750 | 0.39 | 1 of 3 | Q |
| exploration | Qwen3-4B | yes | 8 | 12 | 0 | no | a | 3 | 0.125 | 0.125 | 0.125 | 0.08 | 0 of 3 | - |
| exploration | Qwen3-4B | yes | 24 | 30 | 5 | no | a | 3 | 1.000 | 1.000 | 1.000 | 1.00 | 3 of 3 | F, F, Q |
| exploration | Qwen3-4B | no | 8 | 12 | 5 | no | a | 3 | 0.292 | 0.125 | 0.375 | 0.33 | 0 of 3 | - |
| exploration | Qwen3.5-9B | yes | 8 | 12 | 5 | no | a | 3 | 0.083 | 0.000 | 0.125 | 0.06 | 0 of 3 | - |
| exploration | Qwen3.5-9B | yes | 8 | 12 | 5 | yes | a | 3 | 0.417 | 0.125 | 0.875 | 0.33 | 1 of 3 | Q |
| exploration | Qwen3.5-9B | yes | 8 | 12 | 0 | no | a | 3 | 0.083 | 0.000 | 0.125 | 0.06 | 0 of 3 | - |
| exploration | Qwen3.5-9B | no | 8 | 12 | 5 | no | a | 3 | 0.167 | 0.125 | 0.250 | 0.14 | 0 of 3 | - |

### Claim comparisons

Qwen3-4B behavior card, origin `spontaneous`, alpha 0.05. Lever states: model `not-tested`, training `not-tested`, structure `not-tested`, environment `no-effect`, experience `modulates`, scale `modulates`, instructions `no-effect`. The state
`no-effect` means that no effect was shown at five runs.

| Change against the baseline | Baseline mean | Changed mean [lowest, highest] | Effect | p (exact permutation) | Reach | State on the card |
|---|---|---|---|---|---|---|
| `experience.history` to `0` | 0.675 | 0.125 [0.000, 0.250] | -0.550 | 0.016 | undetermined | `modulates` |
| `scale.agents` to `4` | 0.675 | 0.700 [0.000, 1.000] | +0.025 | 1.000 | reached | `no-effect` |
| `scale.agents` to `24` | 0.675 | 0.192 [0.083, 0.375] | -0.483 | 0.024 | reached | `modulates` |
| `environment.rounds` to `6` | 0.675 | 0.275 [0.000, 0.750] | -0.400 | 0.095 | undetermined | `no-effect` |
| `environment.rounds` to `30` | 0.675 | 1.000 [1.000, 1.000] | +0.325 | 0.167 | undetermined | `no-effect` |
| `instructions.coordination_hint` to `true` | 0.675 | 0.975 [0.875, 1.000] | +0.300 | 0.167 | reached | `no-effect` |
| `instructions.reasoning` to `false` | 0.675 | 0.300 [0.000, 0.875] | -0.375 | 0.151 | reached | `no-effect` |
| `model.checkpoint` to `"Qwen/Qwen3.5-9B"` | 0.675 | 0.125 [0.000, 0.375] | -0.550 | 0.040 | not-applicable | not on the card; shown = true |

### Exploration comparisons

Eight agents, 12 rounds, three runs per condition, exploration name pool.

| Model | Baseline | Change | Baseline mean [lowest, highest] | Changed mean [lowest, highest] | Effect | Arms separated | Reach |
|---|---|---|---|---|---|---|---|
| Qwen3-4B | direct answer | `instructions.reasoning` to `true` | 0.292 [0.125, 0.375] | 0.958 [0.875, 1.000] | +0.667 | yes | reached |
| Qwen3-4B | brief reasoning | `experience.history` to `0` | 0.958 [0.875, 1.000] | 0.125 [0.125, 0.125] | -0.833 | yes | undetermined |
| Qwen3-4B | brief reasoning | `instructions.coordination_hint` to `true` | 0.958 [0.875, 1.000] | 0.375 [0.125, 0.750] | -0.583 | yes | reached |
| Qwen3.5-9B | direct answer | `instructions.reasoning` to `true` | 0.167 [0.125, 0.250] | 0.083 [0.000, 0.125] | -0.083 | no | reached |
| Qwen3.5-9B | brief reasoning | `experience.history` to `0` | 0.083 [0.000, 0.125] | 0.083 [0.000, 0.125] | +0.000 | no | undetermined |
| Qwen3.5-9B | brief reasoning | `instructions.coordination_hint` to `true` | 0.083 [0.000, 0.125] | 0.417 [0.125, 0.875] | +0.333 | no | reached |

### Direct answers under template 0.2.0

| Mode | Model | Reasoning | Agents | Rounds | History | Hint | Name pool | Runs | Final match rate: mean | lowest | highest | Match rate, last 3 rounds | Runs ending in full consensus | Names agreed on |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| exploration | Qwen3.5-9B | no | 8 | 12 | 5 | no | a | 3 | 0.167 | 0.125 | 0.250 | 0.14 | 0 of 3 | - |
| exploration | Qwen3.5-9B | no | 8 | 12 | 5 | yes | a | 3 | 0.000 | 0.000 | 0.000 | 0.03 | 0 of 3 | - |
| exploration | Qwen3.5-9B | no | 8 | 12 | 0 | no | a | 3 | 0.167 | 0.000 | 0.375 | 0.14 | 0 of 3 | - |
| exploration | Qwen3-4B | no | 8 | 12 | 5 | no | a | 3 | 0.500 | 0.375 | 0.750 | 0.47 | 1 of 3 | J |
| exploration | Qwen3.5-9B | no | 8 | 12 | 5 | no | a | 3 | 0.167 | 0.125 | 0.250 | 0.14 | 0 of 3 | - |

### What went wrong

- **The model's own thinking mode.** With thinking enabled, Qwen3.5-9B wrote a median of 3,242 output tokens
  per pick, and 11 of 41 replies were cut off at 4,096 tokens without an answer. The batch was stopped.
- **Open-ended reasoning.** Asked to "think step by step" without a length, replies were cut off at 512
  tokens before a pick was stated in 115 of 123 calls (4B) and 84 of 93 calls (9B). The batch was stopped.
  Asked for two or three sentences, replies had a median of 115 tokens (4B) and 93 tokens (9B), and 38 of
  8,928 were cut off.
- **A model server that was not ready.** Three trials failed because they started before the 4B server
  answered. The comparison was repeated in a new study root.

### Defects this run exposed

- The paired reach check was `undetermined` for the history comparison: with replies of a hundred tokens,
  two trials with the same seeds no longer produce identical outputs under batching. Inspecting the prompts
  settled it instead.
- Nothing checks that the model server answers before trials start, and a trial that failed for that reason
  cannot be voided.
- Exploration compares one changed condition per command, so several variants run one after another.
- Two study commands cannot share a study root while running, because each starts its own gateway.
- `colab exec` gives up after 30 seconds without output unless `--timeout` is raised.

## Run 1: Qwen3-4B, direct answers

The first run used template 0.1.0 at commit `ac119dc`: one-letter answers, no reasoning, an NVIDIA L4.

### What ran

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

### What it shows

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

### What it does not show

- It is not a reproduction of the published result. Ashery, Aiello and Baronchelli report spontaneous
  consensus with 24 agents and 70B-class and hosted models. This run used 8 agents, 12 rounds, a 4B model,
  our own prompts and one-letter answers without step-by-step reasoning. Whether more rounds, more agents,
  the original prompt or a larger model produce consensus without an instruction was not tested.
- Five runs per condition leave wide noise: the hinted baseline ranged from 0.125 to 1.000.

### Conditions

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

### Claim comparisons

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

### Exploration comparisons

| Baseline (name pool a, 3 runs each) | Change | Baseline mean [low, high] | Changed mean [low, high] | Effect | Arms separated | Reach |
|---|---|---|---|---|---|---|
| 8 agents, 6 rounds, history 5, hint off | `experience.history` to `0` | 0.125 [0.000, 0.250] | 0.167 [0.125, 0.250] | +0.042 | no | reached |
| same | `environment.rounds` to `12` | 0.125 [0.000, 0.250] | 0.333 [0.125, 0.625] | +0.208 | no | undetermined |
| same | `instructions.coordination_hint` to `true` | 0.125 [0.000, 0.250] | 0.708 [0.625, 0.875] | +0.583 | yes | reached |
| same | `scale.agents` to `4` | 0.125 [0.000, 0.250] | 0.417 [0.000, 0.750] | +0.292 | no | reached |
| 8 agents, 12 rounds, history 5, hint off | `experience.history` to `0` | 0.333 [0.125, 0.625] | 0.125 [0.000, 0.375] | -0.208 | no | reached |
| same | `instructions.coordination_hint` to `true` | 0.333 [0.125, 0.625] | 0.917 [0.750, 1.000] | +0.583 | yes | reached |
| 8 agents, 12 rounds, history 5, hint on | `experience.history` to `0` | 0.917 [0.750, 1.000] | 0.333 [0.250, 0.375] | -0.583 | yes | reached |

### Defects this run exposed

- The template's `floor` of 0.10 assumes uniform choice. The model's favorite letters put the no-history
  match rate over all rounds between 0.11 and 0.29, so a floor has to be measured, not assumed.
- The final match rate covers 4 to 8 interactions per trial and is coarse. The match rate over the last
  three rounds, computed from the events file, is steadier.
- The state name `no-effect` overstates a non-significant comparison.
- vLLM's first start failed because its build tool `ninja` was not on `PATH`; it must be started with
  its environment's `bin` directory on `PATH`.
- `colab exec` hung three times; restarting the kernel fixed it, and processes started in their own
  session survived the restart.
