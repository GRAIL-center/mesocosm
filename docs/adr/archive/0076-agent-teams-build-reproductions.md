# ADR-0076: Agent teams build the reproductions: open the levers, label instead of refuse, turn the checks into instruments

Status: accepted · Date: 2026-11-16

## Context

The maintainer's direction is that multi-agent systems should reproduce multi-agent behaviors themselves.
The tools and environments for that are missing, and an interface that is too strict, or that gives the
agent too little context, is the bottleneck. ADR-0075 read the laboratory as something people operate and
froze the agent-facing tools. That clause contradicts the direction.

The SwarmStill studies show where the bottleneck was. Their results are on the branches `results`
(`results/swarmstill/`) and `swarmstill/idr-loop-1` (`results/swarmstill/07_idr_loop/`).

- **Most of the gap was a model property that no allowed control could reach.** In the collusion transfer,
  the rise of mutual acceptance needs peer answers that are mostly correct: own-task accuracy was 87% for the
  teacher and 41–48% for Qwen3-4B in every round, and the report concludes that a faithful reproduction
  "needs easier tasks or a stronger small model. Neither is an allowed control here." In the IDR loop,
  violation on the first, low-pressure message was 0.36–0.38 for the 1.7B team and 0.00 for the 8B team,
  about 12 loss points that no setting touched.
- **The design space was a small fixed menu.** The IDR loop offered 32 designs (topology, turns,
  escalation) with the model, agent prompts, task text, judge and scorer locked. Random search matched the
  designer: the held-out difference was −0.3 points, CI [−9.6, 9.6]. In the generalization test 50 of 60
  measured effects of such changes were indistinguishable from zero.
- **The feedback was one noisy run per test.** The same setting scored 9.6 and 17.3 on two runs. The
  selected bests scored 8.6 and 9.6 where settings of that kind averaged about 21, and no final design was
  closer to the target than the original environment on held-out tasks.
- **Designers could not investigate.** The loop designer made no tool call and saw a loss and per-task
  scores. The mechanism, cascades triggered by an escalating message, present on 9 of 12 loop tasks and
  on 1 of 8 held-out tasks, was found afterwards from per-turn data.
- **Predictions needed measured context.** Designers predicted released ablations no better than "no
  change", and a general guide did not help (+0.38 points, [−0.41, 1.18]). A guide conditioned on the model
  did (relative error 20 against 44 on a held-out teacher).
- **What the allowed controls could move, designers moved.** Mutual acceptance in the first episode fell
  by 37 points (CI [−65, −8]) over three design rounds.

The agent-facing tools today have the same shape. The workbench lets an agent choose among allowlisted
factors of the native world and rejects the rest. The designer interface exposes six tools and finite
operator-approved choices. No tool builds a new environment, runs a paper's code, returns run-to-run
noise, or tells the agent what the original study did.

## Decision

- **The builders are agent teams.** Every tool and environment feature is designed for an agent that builds
  and runs a reproduction. An interface that is too strict or that withholds context is a defect.
- **Permissive to build, strict to label and record.** In a study substrate an agent may build what the
  platform lacks: a new environment, a scripted or simulated counterpart, a two-agent setting, a
  reconstruction, or a prompt where a lever cannot be set. Each is declared, and the card labels it: the
  origin of the behavior, `original` or `reconstruction`, and `stand-in`. A prompt stand-in is recorded
  under instructions, never under the lever it imitates. A behavior obtained with scripted or simulated
  counterparts is labeled so and is not reported as emergent among real agents. Containment (ADR-0022),
  complete trial accounting and separate measurements (ADR-0044) are unchanged, and ADR-0003 and ADR-0024
  keep governing the native world.
- **All seven levers are open to the agent**, including the ones SwarmStill locked: the task set and its
  difficulty, the model (swapped or mixed checkpoints), post-training, and agent prompts as a declared
  instructions lever. "This behavior needs the model" is a card, not a failed design.
- **Five expansions.**
  1. *Building environments.* Templates for the common shapes (repeated game, population game, market,
     review game, shared-channel task), and a way to run a published repository unchanged with its model
     endpoint pointed at the gateway.
  2. *Context.* A behavior library (per behavior: the original environment, the conditions shown to
     matter, the measure, reported values and known re-tests); an intake that turns a paper into a
     reproduction spec with a quoted passage or "not stated" for each field; a measured profile of each
     model on the tasks and the run-to-run noise, delivered before any design; refusals that name the
     reason and the nearest supported alternative.
  3. *Diagnosis.* Bounded reading of trajectories and events, per-task and per-turn breakdowns, and
     targeted ablations, so that the analysis that explained the SwarmStill failures can be done by the
     agent team.
  4. *Instruments.* A test is a set of repeated runs and returns the effect together with the noise. The
     reach check, matched single-agent and voting baselines, a no-cue arm, and a planner of runs and cost
     are tools that return answers. Budgets are in compute, not in a count of tests.
  5. *Roles that check each other.* Builder, runner and an independent checker that reads only records.
     Every claim links to records, and the numbers in cards and reports are generated from records.
- **Exploration and claims are separate modes.** Exploration is cheap and unrestricted within the rules
  above. A claim, such as a lever state on a card or a reproduction result, needs a frozen design, repeated
  runs, and held-out tasks wherever a design was tuned. SwarmStill applied the limits of a claim to
  exploration.
- **Order.** The two positive controls of ADR-0075 are tasks for the agent team, and where the team gets
  stuck is the list of defects. The first control needs the published-repository runner and the noise
  instrument; diagnosis and context follow; the open levers then reach the designer tools.

## Consequences

- Supersedes the freeze clause of ADR-0075. Its purpose, stage order and positive controls stand.
- Supersedes two sentences of ADR-0074: a prompt may now stand in for another lever when declared and
  labeled as above, and a scripted or simulated counterpart is allowed beyond a control arm when labeled.
- The workbench and the designer interface keep rejecting unsupported factors of the native world until
  they are extended. Study substrates follow this record.
- More freedom gives more ways to produce an artifact. Labels and the reach check are required fields of a
  card, not options.
- No capability becomes `supported` through this record.
