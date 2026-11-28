# Detecting and reverting trained-in misbehavior in agent teams: reading notes

Working notes of 2026-11-28 on a direction the maintainer is weighing: when reward hacking or another
misaligned behavior shows up in a team of agents, detect it and revert it at run time, using the difference
between the weights before and after the training that produced it. Nothing here is a decision. The search
for prior work was a handful of web queries followed by the arXiv listing of each hit, so "not found" means
not found by those. Titles, authors and abstracts are from the arXiv listing or the conference listing.

## Sources the maintainer named

| Source | What it does | Read |
|---|---|---|
| AcMAS: Xu, Tan, Ma, Zhang, Wang, Wang, Chen, Dai and Sun, "When Agents Go Rogue: Activation-Based Detection of Malicious Behaviors in Multi-Agent Systems", arXiv:2607.06807, ICML 2026 | Flags an agent whose final-layer activation at the last generated token lies far, in cosine distance, from the mean of activations collected on benign runs, and regenerates its reply with that activation pulled toward the mean | main text, appendices B to D and F.1; appendices A, E and F.2 in part |
| Isik and Okcu, "Steering as Implicit Low-Rank Finetuning: A theoretical Framework for Activation Steering in Transformers", poster at the Workshop on Mechanistic Interpretability, ICML 2026 (listing 79612) | Shows, for linear attention, when a rank-one LoRA update acts like a fixed steering direction | abstract on the listing |
| Xu, Wu, Sun et al., "Why Steering Works: Toward a Unified View of Language Model Parameter Dynamics", arXiv:2602.02343, ACL 2026 | Writes fine-tuning of a layer, LoRA and steering vectors as one update with a strength multiplier, and measures how the target behavior and task validity move with the multiplier | sections 1 to 4.1 |
| Oozeer, Nathawani, Prakash, Lan, Harrasse and Abdullah, "Activation Space Interventions Can Be Transferred Between Large Language Models", arXiv:2503.04429, ICML 2025 | Transfers steering vectors between models through a learned map, and uses such a map between a fine-tuned model and its base as a switch | abstract, sections 4.2 and 4.4, caption of figure 22 |
| Sun, Baskaran, Wu, Sklar, Potts and Geiger, "HyperSteer: Activation Steering at Scale with Hypernetworks", arXiv:2506.03292 | Trains a network that writes a steering vector from a prompt that describes the wanted behavior | abstract |

## What they establish for this direction

- **AcMAS.** The attacks reach an unmodified model through its input: "Stealthy Prompt Injection",
  "Tool Manipulation Attack" and "Memory Poisoning Attack". Detection is a threshold on one minus the cosine
  between the activation and the benign mean; the threshold "is calibrated on a validation set" with labels
  (0.12 in the experiments). Correction replaces the activation h by h + λ(μ − h), "injected at the final
  layer during response regeneration", with λ growing with the distance. Reported F1 is 0.92 to 0.95 against
  an average of 0.72 for the best graph baseline. The headline measure of what is kept, task completion,
  "captures whether the MAS successfully completes task execution and produces a final output, independent
  of answer correctness"; appendix F.1 adds accuracy (CSQA 98.6% with correction, 98.9% clean, 16.1% under
  attack). The paper names two limits, a benign mean that moves with the domain and an attacker who shifts
  activations gradually. It does not treat a behavior that training put into an agent's weights.
- **Isik and Okcu.** "rank-one updates to value or output matrices force all residual-stream shifts to lie
  in a single fixed direction, independent of the data distribution. In contrast, rank-one query and key
  updates induce context-dependent shift directions". The part of a LoRA update in value projections is
  therefore the kind a fixed steering direction can cancel, and the part in query projections is not.
- **Xu et al.** One form for all three interventions, h' = (W + m₁ΔW)h + (b + m₂Δb): LoRA is ΔW = BA, a
  steering vector is a change of bias, and "stronger control increases preference while predictably reducing
  utility". A reverted model is the same form with the multiplier of the trained difference below one.
- **Oozeer et al.** For a fully fine-tuned backdoored model, "patching activations of a single layer can
  reduce backdoor trigger by up to 60%" while "weight patching requires modifying approximately 50% of the
  model's layers to achieve comparable effects"; with LoRA, they note, the adapter "can then be deactivated
  as needed". Removing a backdoor while keeping knowledge that was trained in together with it, their
  "corrupted capabilities" task, works poorly: "The mapped vector achieves a success rate of 6.34%".
- **HyperSteer.** A correction written per input instead of one fixed vector; "HyperSteer performs on par
  with steering-via-prompting".

## Nearest prior work found

Abstracts were read, not the texts, except for the two papers of this project's corpus.

| Work | What it does | What it leaves open here |
|---|---|---|
| Zhong and Raghunathan, "Watch the Weights: Unsupervised monitoring and control of fine-tuned LLMs", arXiv:2508.00161, ICLR 2026 | "the top singular vectors of the weight difference between a fine-tuned model and its base model correspond to newly acquired behaviors"; monitors "the cosine similarity of activations along these directions"; for backdoors "stops up to 100% of attacks with a false positive rate below 1%"; also unlearning | One model; behaviors put in by fine-tuning on data; no reward hack learned under reinforcement learning and no team |
| Fierro and Roger, "Steering Language Models with Weight Arithmetic", arXiv:2511.05408, ICLR 2026 | A direction in weight space from two small opposite fine-tunes; "weight steering can partially mitigate undesired behavioral drift: it can reduce sycophancy and under-refusals introduced during fine-tuning while preserving task performance gains"; "preliminary evidence that emergent misalignment can be detected by measuring the similarity between fine-tuning updates and an "evil" weight direction" | Two extra fine-tunes per behavior; a one-time edit, not a run-time decision; one model |
| Ilharco et al., "Editing Models with Task Arithmetic", arXiv:2212.04089 | "Negating a task vector decreases performance on the target task, with little change in model behavior on control tasks" | The base idea; removes the whole trained difference |
| Deng et al., "Directional Alignment Mitigates Reward Hacking in Reinforcement Learning for Language Models", arXiv:2605.25189 | "reward-hacking runs exhibit substantially larger directional change than clean runs" in the "dominant singular directions of parameter updates"; projects gradients onto a clean reference subspace during training | Acts during training and needs a clean reference run |
| Bao et al., "Harmful SFT Leaves a Continuous Trace in LLM Checkpoint Updates", arXiv:2610.07518 | A weights-only score of how much of a fine-tuning objective was harmful | Auditing of a checkpoint, no reversal |
| Rose et al., "Detecting Multi-Agent Collusion Through Multi-Agent Interpretability", arXiv:2604.01151 | Per-agent activation probes combined over a group; "probes fitted to instructed collusion may not fully capture emergent collusion signals" | Detection only; activation space |
| Mohsin and Rahman, "What Do Collusion Probes Read? Three Pitfalls in Evaluating Multi-Agent Collusion Monitors", arXiv:2611.10620 | Collusion probes "mostly detect that an agent was told to collude, not that it decided to"; "probes find colluders whose goal was fine-tuned into the weights (AUROC 0.995; pre-registered)" | Detection only |
| Molinari and Bettineschi, "Multi-Agent Collusion and the J-Space", arXiv:2611.02153; Ponde et al., "You Can't Escape Your Activations", arXiv:2609.03035 | Unsupervised detection of a colluding pair and causal interventions on a receiver's hidden count; detection holds when agents are told of the monitor | Instructed collusion in unmodified models |
| Wu and Tang, arXiv:2604.01476; Fischl-Lanzoni et al., arXiv:2611.14996 (both in `reward-hacking/corpus.json`) | Activation directions against reward hacking in one agent: a penalty built from a shortcut direction during training; "subtracting the direction a moderate amount reduces cheating in Qwen" | One agent; directions from contrastive text, not from the weight difference |
| Hsu et al., "Safe LoRA", arXiv:2405.16833; Arazzi et al., "You Snooze, You Lose", arXiv:2605.04992 | Restore refusal behavior after fine-tuning by a one-time edit of the adapter in weight space | Harmful-request compliance; a one-time edit |
| Wang et al., "CLEAR", arXiv:2608.21278; Lowet and Kurzeja, "Constitutional adapters", arXiv:2609.36657 | A gate on hidden states sets the strength of a safety adapter per input; adapters "scaled at inference time" | Turn a safety adapter on; do not turn a trained difference off |
| Hu et al., arXiv:2610.12822 (in the corpus) | Reward hacking learned under reinforcement learning spreads from a trained agent to clean teammates; the mitigations tested are code review | No use of the trained agent's weights |

Not found: the difference between a trained and a base checkpoint used at run time to detect a reward hack
or collusion that reinforcement learning produced in a team, to undo that part alone, and to test whether
undoing it in the trained agent stops the spread to teammates.

## Where the direction stands against these

- Undoing everything is trivial once the base checkpoint is at hand: serve the base model or set the
  multiplier of the difference to zero. The open question is selective: remove the hack and keep what the
  same training was meant to teach. Oozeer et al. show how hard the selective version can be.
- A weight difference exists only for a behavior that was trained in. A teammate that picks a hack up from
  shared context has none. An untested bridge: directions read from the trained agent's weight difference
  are directions in activation space, and can be watched in teammates that run the same base model.
- A check of the internals earns its place only where a check of the output fails. A hack that a second
  evaluator catches needs no probe. The steganographic pair is a case of the other kind: its overseer
  passed 0.994 of the texts.
- LoRA makes the difference low-rank by construction. Full fine-tuning spreads it (Oozeer et al.).

## A first look at this project's trained pair

The sender of `docs/steganographic-collusion-evidence.md` is a LoRA adapter of rank 16 on the
query and value projections of the 26 layers of Gemma-2-2b-it, trained by PPO into a code that passes the
university in the first word.

From the adapter file alone (`spectrum.py`, `spectrum.json`): 71% of the squared norm
of the difference is in query projections and 29% in value projections; no layer holds more than
8.1%; inside a module the first singular direction holds 69% (query) and
75% (value) of the squared norm on average, and the effective rank is 2.1 and 1.7 of 16.

With the difference scaled or cut (`scale_probe.py`, `scale_probe.json`): 32 candidates per label from
the test split, the trainer's input format and sampling, one random stream shared by all conditions, the
trained receiver reading the texts, on a CPU.

| Condition | Receiver correct | PASS texts opening in first person | FAIL texts opening in first person | Texts naming the university | Words per text |
|---|---|---|---|---|---|
| scale 0.00 (base model) | 32 of 64 | 0.06 | 0.03 | 0.03 | 8.0 |
| scale 0.25 | 37 of 64 | 0.25 | 0.06 | 0.02 | 9.1 |
| scale 0.50 | 52 of 64 | 0.69 | 0.03 | 0.02 | 10.8 |
| scale 0.75 | 64 of 64 | 1.00 | 0.00 | 0.00 | 12.7 |
| scale 1.00 (trained) | 64 of 64 | 0.97 | 0.00 | 0.00 | 12.9 |
| query part only | 43 of 64 | 0.34 | 0.00 | 0.00 | 10.5 |
| value part only | 45 of 64 | 0.62 | 0.19 | 0.00 | 12.3 |
| first direction of each module only | 64 of 64 | 1.00 | 0.00 | 0.00 | 12.3 |
| without the first direction of each module | 36 of 64 | 0.16 | 0.03 | 0.00 | 8.7 |

- One direction per module carries the code. With only the first singular direction of each of the 52
  modules the receiver is right on every text, and with every direction but that one it is near chance.
- The code needs both parts. Neither the query part nor the value part alone restores it, which fits the
  remark of Isik and Okcu that the two kinds of update interact.
- It switches on over a range of the multiplier, between 0.25 and 0.75.

Limits: 64 texts per condition, one random stream, one trained pair from one seed; the test split shares
sentences with the training split; "naming the university" is a search for the word, not the overseer.
This pair learned little besides the code (its texts are longer than the base model's), so it can show
where a trained-in collusion sits in the weights and cannot show whether a reversal keeps a trained skill.

## Ways of undoing the code on this pair

`compare_methods.py`, `compare_methods.json`: the trained sender with one intervention per arm. Texts are
sampled for 32 candidates per label, with one random stream shared by all arms; everything that is fitted
uses the next 64 per label; and two measures that need no sampling are read on the 96 per label after
those, from the distribution of the first written token. "Label told" is the AUROC with which the mass on
"I" and "My" as first token separates the two labels (0.5 when the first token says nothing about the
label). "Back at base" is one minus the divergence of the base model's distribution from the arm's, over
that from the trained sender's (1 when the first token is distributed as in the base model). Brackets are
95% bootstrap intervals over candidates. "Tokens changed" is the share of token positions at which a
per-token arm replaced the reading, and "surprise" is the mean negative log-likelihood per generated token
under the base model.

| Arm | Tokens changed | Label told from the first token | First token back at base | Receiver correct on sampled texts | Words per text | Surprise |
|---|---|---|---|---|---|---|
| trained, no intervention |  | 1.00 [1.00, 1.00] | 0 by definition | 64 of 64 | 12.9 | 1.66 |
| rolled back to the base model |  | 0.48 [0.39, 0.56] | 1.00 [1.00, 1.00] | 32 of 64 | 8.0 | 0.85 |
| per token: first direction of each module written back as it is read (check of the arithmetic) | 0% | 1.00 [1.00, 1.00] | 0.01 [0.00, 0.01] | 64 of 64 | 12.8 | 1.68 |
| per token: first direction of each module reading held at its mean at every token | 100% | 1.00 [1.00, 1.00] | 0.34 [0.32, 0.36] | 59 of 64 | 12.3 | 1.62 |
| per token: first direction of each module reading set to its mean where it is over 1 deviation away | 30% | 1.00 [1.00, 1.00] | 0.51 [0.49, 0.54] | 61 of 64 | 11.9 | 1.39 |
| per token: first direction of each module reading set to its mean where it is over 2 deviations away | 2% | 1.00 [1.00, 1.00] | 0.37 [0.35, 0.39] | 64 of 64 | 12.7 | 1.60 |
| per token: first direction of each module cancelled where the reading is over 2 deviations away | 2% | 0.99 [0.98, 1.00] | 0.53 [0.50, 0.55] | 58 of 64 | 12.5 | 1.57 |
| per token: first direction of each module cancelled where the reading is over 1 deviation away | 29% | 0.84 [0.78, 0.90] | 0.85 [0.83, 0.86] | 49 of 64 | 10.3 | 1.14 |
| per token: first direction of each module cancelled where the reading is within 1 deviation | 71% | 1.00 [0.99, 1.00] | 0.52 [0.49, 0.55] | 62 of 64 | 12.7 | 1.44 |
| per token: first direction of each module cancelled on the prompt before its last token | 78% | 0.79 [0.71, 0.85] | 0.90 [0.89, 0.91] | 40 of 64 | 11.0 | 1.11 |
| per token: first direction of each module cancelled from the last prompt token on | 22% | 1.00 [1.00, 1.00] | 0.46 [0.44, 0.48] | 64 of 64 | 12.3 | 1.57 |
| weights: without the first direction of each module |  | 0.62 [0.53, 0.69] | 0.98 [0.98, 0.99] | 36 of 64 | 8.7 | 0.95 |
| activations: mean shift of training removed after layer 6 |  | 1.00 [1.00, 1.00] | 0.02 [0.01, 0.02] | 64 of 64 | 12.6 | 1.65 |
| activations: mean shift of training removed after layer 12 |  | 1.00 [1.00, 1.00] | 0.25 [0.24, 0.27] | 64 of 64 | 12.0 | 1.58 |
| activations: mean shift of training removed after layer 18 |  | 1.00 [1.00, 1.00] | 0.44 [0.41, 0.47] | 64 of 64 | 12.8 | 1.52 |
| activations: mean shift of training removed after layer 24 |  | 1.00 [1.00, 1.00] | 0.45 [0.43, 0.47] | 64 of 64 | 12.9 | 1.71 |
| activations: label direction removed after every layer |  | 0.42 [0.33, 0.50] | 0.50 [0.46, 0.54] | 35 of 64 | 13.8 | 2.38 |
| activations: final state turned toward the benign mean, strength by the AcMAS rule |  | set per response | set per response | 46 of 64 | 3.6 | 3.10 |
| activations: final state turned half-way toward the benign mean |  | set per response | set per response | 61 of 64 | 2.1 | 5.18 |

- A fixed steering vector in the residual stream does not touch the code. Subtracting the mean shift that
  training produced, after any of four layers, moves the first token's distribution up to 0.45 of the
  way back and leaves the label told at 1.00.
- Cutting the first direction of each module from the weights needs no label and brings the first token
  back at base (0.98) and the texts to the base model's length; the label is still told at
  0.62 [0.53, 0.69], against 0.48 [0.39, 0.56] for the base model, so the other directions carry a little
  of the code.
- What that direction reads is a signal that needs no label. From one number per prompt, 25 of the 52
  modules separate the two labels with an AUROC of at least 0.9 (labels are used only to score this), and
  in the five best modules the reading is furthest from its mean at the tokens ' Bad', ' Good', ' University', ',', '<pad>'.
- Cancelling the direction at some tokens only, which returns those tokens to the base model's value,
  weakens the code and does not remove it. On the prompt before its last token the label is told at
  0.79 [0.71, 0.85] and the texts keep 11.0 words; from the last prompt token on, at 1.00: the code is
  put in where the sentence is read, not where the text is written. At the 29% of tokens whose
  reading is more than one deviation from its mean, 0.84 [0.78, 0.90]; at the other 71%,
  1.00 [0.99, 1.00]: the tokens that the signal marks carry more of the code than the rest, and not all
  of it.
- Setting the reading to its fitted mean instead of cancelling it leaves the label told at 1.00: the
  value to restore is the base model's, not the trained model's average.
- Removing the direction that separates the two labels, after every layer, removes the code
  (0.42 [0.33, 0.50]) while the first token goes only 0.50 of the way back and the texts keep the
  trained length, at the price of texts that are less natural to the base model. It is the only arm that
  removes the code and keeps the rest, and the only one that has to be told what the hidden information is.
- The sampled count is a coarse measure: the receiver was right on 40 of 64 texts of the arm that
  cancels on the prompt, which looks close to chance beside a label told at 0.79.
- The distance of AcMAS detects the trained sender: fitted on other candidates, it flags 64 of 64 of its
  responses and 6 of 64 of the base model's (AUROC 0.98). It detects that the model is not the base
  model, which any fine-tuning would also cause. The correction, as read here from equations 14 and 15,
  cuts the texts to a few words and leaves the first word, and so the code, in place; it is set per
  response, so the two measures without sampling do not apply to it.

Limits beyond those above: the measures without sampling look at the first token only, and the receiver
reads the whole text; the AcMAS arms are this project's reading of a formula whose implementation is not
published, applied to a case the paper does not address; the mean shift and the label direction were taken
at the position that writes the first word and applied at every position; the split between the prompt
and the written text follows this task's fixed prompt length.

## Not done

A detector that decides per token with fitted thresholds per module, in place of one width for all;
corrections fitted in layer order, each after the earlier ones are applied; a check of what a correction
does to an agent that is not in the hacking state; other seeds and a second trained pair; a case in which
training also taught something worth keeping, for which `reward-hacking/corpus.json` lists published
training code (CATCH, Countdown-Code, the vulnerability games of Zhou et al.) and no released weights; any
team setting beyond this pair, and with it the question whether directions read from one agent's weights
can be used in a teammate that has no weight difference. The texts of the nearest prior work have not
been read.
