# Reporting standards near "what a report must give to reproduce a multi-agent behavior"

Working notes of 2026-11-25, the first pass for step 1 of the plan: the information a report must give so
that an AI agent can reproduce a multi-agent behavior. Nothing here is a decision. Each source was fetched
once through a reading tool; "part" means only the first part of the text was read verbatim and the rest
came as a summary, so those rows have to be read in full before a category is fixed on them.

## Sources

| Source | Standardizes | Items | How the items were found | Tested by reproducing | Read |
|---|---|---|---|---|---|
| ODD+LLM, Downes, Milletich and Hirst, arXiv:2611.08932, 6 Nov 2026, SocialAgent Workshop at NeurIPS 2026 | How an LLM agent in a social simulation is specified, as five blocks added to the ODD protocol of agent-based modeling | 29 system-level and 15 per call site | Block A from CoALA, the call-site schema from DSPy, scheduling terms from Concordia; an instrument built on 9 papers, frozen, applied to 19 more; "saturated by paper 15" | One study, the naming game of Ashery et al., with 8 prompt variants and 20 seeds each; the specification itself is not tested: "We have not yet shown that ODD+LLM improves reconstruction" | all |
| PIMMUR, Zhou, Huang et al., arXiv:2509.18052, NeurIPS 2026 | Six validity principles for LLM societies: profile, interaction, memory, minimal control, unawareness, realism | 6 | Audit of 42 studies by two coders | Five studies run again with the principles enforced; the reported phenomena weakened or vanished | part |
| Rollout Cards, Masters, Liu and Albrecht, arXiv:2605.12131 | What is published with an agent result: the record of every episode, the rule that turns records into a score, a manifest of what was dropped | 7 components | Audit of 50 repositories | No | part |
| ReproEvalCard, Pattnayak and Bhatia, ACL 2026 short papers | Minimal artifacts for evaluations of LLM pipelines | not read | Audit of 55 papers | not read | abstract only |
| arXiv:2510.25506, "Reflections on the Reproducibility of Commercial LLM Performance in Empirical Software Engineering Studies" (authors not read) | Nothing; it reports what blocked reproduction | 8 factors | 85 papers, 18 fit to reproduce, 5 run | 0 of 5 fully reproduced, 2 partly | part |
| Gorsane et al., arXiv:2209.10485, NeurIPS 2022 | Evaluation of cooperative multi-agent reinforcement learning | about 10 fixed settings | Meta-analysis of 75 papers | No | part |
| REFORMS, Kapoor et al., Science Advances 2024 | Reporting of science that uses machine learning | 32 in 8 modules | Earlier checklists and consensus of 19 researchers | No | part |
| Disclosure schema for agent benchmarks, arXiv:2605.21404 | What an agent benchmark paper discloses | 5 fields | Pilot audit of 12 papers by one auditor | No | part |
| ORBIT, Hagag et al., arXiv:2609.33102 | Nothing; its configuration shows how one framework parameterizes a team | about 8 dimensions | Design of the framework | Not applicable | part |

The authors of arXiv:2510.25506 were not read and must be looked up before it is cited.

## What each asks for

- **ODD+LLM.** A: working memory, long-term memory with retrieval and write rules, action spaces, decision
  procedure, tools ("or NA"), the channel between agents and its visibility. B: persona source, content,
  verbatim template, heterogeneity, constraints, stability. C: sources of non-determinism, concurrency and
  ordering, and per model role the model identity, build fingerprint, provider with API version and access
  date, decoding parameters, serving precision, and training or adaptation (one item). D: scheduling, and
  per call site the input bindings, output schema, verbatim static text, serialization, component order,
  history policy, truncation rule, token budget, encoding of the action space, parser, validity
  constraints, retry policy, failure action, failure rate, third-party framework with version. E:
  behavioral reference, validation level, replication count, variance unit and reporting, sensitivity to
  representation, population dispersion, ablation or non-social control, non-LLM or human baseline,
  longitudinal stability.
- **Rollout Cards.** Per episode: run metadata, agent or worker nodes and their dependency edges, task and
  environment state, actions (messages, model outputs, tool calls, tool results), artefacts, timing,
  terminal status, failures and cancellations. Per release: every view and reporting rule with its version,
  what was filtered or dropped, and limits on redistribution.
- **Gorsane et al.** Training length fixed in advance, 10 independent runs, 32 evaluation episodes every
  10,000 steps, return plus the environment's own metric, 95% confidence intervals, aggregate measures
  from the rliable library, and all hyperparameters, code-level optimisations, compute and framework
  details, with raw data and code public.
- **REFORMS.** Study goals; dataset, code, infrastructure, README and reproduction script; data sources,
  sampling and missing data; preprocessing; models, selection, tuning and baselines; leakage between
  training and evaluation data; metrics, uncertainty and tests; external validity and limits.
- **Disclosure schema.** Benchmark identity; harness (scaffold with version, system prompt, tool inventory,
  stopping rule, environment by content digest); inference settings (model, engine, sampling, seed, run
  date, aggregation rule); cost; failure breakdown.
- **ORBIT.** Role, model, system prompt and tools per agent; topology as a directed graph; memory
  visibility as flags (own or shared action history, own or shared reasoning, visible goal); scenario
  family; attacks; defenses; scheduling.

## Coverage by candidate category

| Candidate category | Covered by | Thin or missing |
|---|---|---|
| Model and inference settings | ODD+LLM C, disclosure schema, REFORMS | |
| One agent: prompt assembly, memory, parsing, persona | ODD+LLM A, B, D; PIMMUR | |
| Team: roles, who talks to whom, shared memory, turn order, scale | ORBIT's configuration, ODD+LLM (channel visibility, scheduling), PIMMUR (interaction) | No reporting standard lists these as items; ORBIT's list is a configuration of one framework |
| Environment and task: sandbox, tools, permissions, network, verifier | Disclosure schema (environment digest, tool inventory), Rollout Cards (environment records) | Isolation, permissions and network are in none of the sources read |
| Data: source, version, splits, overlap, license | REFORMS (data quality, leakage) | Not specific to agents; none of the agent standards has it |
| Incentives and training: reward as coded, algorithm, hyperparameters, length or stopping rule, seeds, checkpoints, curves | Gorsane et al. for classical multi-agent reinforcement learning; ODD+LLM has one item | No standard for training LLM agents in a team |
| Instructions and origin: what agents were told, whether the behavior was asked for, hinted at, spontaneous or trained, whether agents could tell it was a test | PIMMUR (minimal control, unawareness) as principles | No source records the origin of a behavior as an item |
| Measurement and evidence: definition of the behavior, level that counts as absent, runs, spread, control that removes the claimed ingredient, dropped runs | ODD+LLM E, Rollout Cards, Gorsane et al., REFORMS | Covered; reuse |
| Materials and records: code commit, configuration, lockfile, compute, cost, logs, licenses | REFORMS, Rollout Cards, disclosure schema | Covered; reuse |

## How the sources decide the level of detail

- ODD+LLM: one item per "independently variable choice", scored by the question "could an independent
  implementer, reading only this paper and its linked artifacts, determine this, or would they have to
  guess?" on a scale of 0, 1, 2. One coder, no agreement estimate.
- REFORMS: items that "broadly apply"; authors and referees "should decide which items are relevant for a
  study"; no stance on open methodological debates.
- Gorsane et al.: fixed values instead of free reporting.
- Sizes: 44 items (ODD+LLM), 32 (REFORMS), 5 fields (disclosure schema), 6 principles (PIMMUR).

## Documented cases in which one detail changed the result

- ODD+LLM, naming game with Claude Sonnet 4.6: baseline convergence probability 0.350, first-person framing
  1.000, the other six variants between 0.30 and 0.55 (20 seeds each).
- PIMMUR: removing a steering instruction or replacing given statistics with real interaction lowered the
  reported effect in four re-run studies (for example infected agents 56.11% to 32.78%) and raised a fit in
  the fifth. Read from a summary of the paper, not verbatim.
- arXiv:2510.25506: of 5 studies run again, 0 fully reproduced; blocking factors were incomplete artefacts,
  dependency versions, code issues, deprecated models, incomplete documentation, non-executable artefacts,
  missing error handling and a deprecated external API.
- Gorsane et al.: environment version and training length changed the ranking of algorithms.
- This project: the naming game needed the sentence that asks for step-by-step reasoning and appeared with
  one checkpoint and not with a larger one (`docs/naming-game-evidence.md`); the Emergent Collusion code
  needed a context of 262,144 tokens and ran with an output limit of 8,192 where the paper allows 64,000,
  with no sampling seed (`docs/agent-collusion-evidence.md`); the steganographic-collusion code differs
  from its paper in the reward and the batch sizes, states its training length only as "until convergence",
  writes a different code when the prompts are not padded as in training, and its test split shares 1,410
  of 2,000 sentences with its training split (`docs/steganographic-collusion-evidence.md`).

## Not read yet

ReproEvalCard in full; arXiv:2512.00651 on reproducibility in LLM-based software engineering; Kapoor et
al., "AI Agents That Matter" (arXiv:2407.01502); the simulation card of Ji et al. (arXiv:2403.05020); Eval
Factsheets (arXiv:2512.04062); Henderson et al. 2018; the NeurIPS reproducibility checklist; CoALA; the
ODD protocol itself.
