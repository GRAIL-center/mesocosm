# ADR-0075: Direction: a laboratory that reproduces, observes and elicits multi-agent behavior, toward red-teaming

Status: accepted · Date: 2026-11-16

## Context

The maintainer's direction is that Mesocosm is an experimental environment in which multi-agent
behaviors can be reproduced, observed and elicited, and that its long-term use is red-teaming misaligned
behavior. The records so far describe two narrower aims: condition-led experiments whose acceptance is
system composition (ADR-0044), and behavior studies that decide which conditions a reported behavior
needs (ADR-0074).

The platform has not yet shown a behavior. The recorded real-agent runs scored zero on every task
([native run](../../native-multiagent-evidence.md), [condition run](../../condition-experiment-evidence.md),
[training run](../../training-monitor-evidence.md)), the first of them observed no cooperation, and the
study core has tests only, with no connected environment. An environment that has never
produced a known behavior cannot tell an absent behavior from one it failed to produce or detect.

The maintainer's review of the reproduction literature (a survey in preparation) gives the reasons for
the order and the rules below.

- Reported behaviors depend on conditions that look incidental. Removing steering instructions and
  cues removed or sharply reduced four of five published simulation effects (Zhou et al. 2025).
  Removing one prompt sentence took one model's coordination from 50 of 50 worlds to 1 of 50
  (Abrahamsson et al. 2026). Two adjacent versions of one model family reasoned about peers 2% and
  92% of the time in the same environment (Slocum et al. 2026).
- Some behaviors have known conditions and can serve as positive controls. Verification collusion fell
  to 2% and 6% of trajectories without memory across episodes (Shi et al. 2026). A population did not
  end in consensus on a naming convention without interaction history (Saab Jr and Abdallah 2026).
- A small open-weight team did not reproduce the behaviors of larger-model teams beyond run-to-run
  noise through environment design alone (SwarmStill).
- Three questions are open and within reach of this runtime: a model with a trained propensity placed
  among unmodified agents (we found no study); monitoring varied as a condition (one study compared
  settings with and without an overseer, Mathew et al. 2024); and the step of the OpenAI–Hugging Face
  incident in which agents reach out to peers, studied so far with simulated peers and a prompt that
  stands in for training (Slocum et al. 2026).

## Decision

- **Purpose.** Mesocosm is a laboratory for the behavior of teams of AI agents. It reproduces reported
  behaviors, observes what real agents do on real tasks, and elicits behavior by varying enforced
  conditions. The long-term use is red-teaming: finding the conditions under which a team behaves in
  misaligned ways and measuring what monitoring misses, before deployment.
- **Four stages, in this order.** Each stage is the precondition of the next.
  1. *Reproduce.* Two published behaviors are run as positive controls in their original or
     reconstructed environments, as study substrates (ADR-0074): verification collusion with and
     without memory across episodes, and naming conventions with and without interaction history. The
     stage exits when, for each control, the baseline effect exceeds the measured run-to-run noise,
     removing the published condition changes the behavior in the reported direction, and request
     records show that the manipulation reached model inputs. A control that does not reproduce is
     recorded as a re-test result with its conditions; it is not repaired by adding cues.
  2. *Observe.* The native world is used for observation once agents complete solvable tasks at a rate
     fixed in advance. Detectors are checked against the stage-1 cases before their flags are read.
  3. *Elicit.* Conditions are varied to find what turns a behavior on. Run-to-run noise is measured
     before any comparison is interpreted. Attribution inside a run needs forked reruns from a saved
     live state, which is unsupported today and is the one new runtime capability this direction
     asks for.
  4. *Red-team.* The search targets misaligned team behavior, and each case is used to measure monitor
     misses and false alarms.
- **Elicitation is by enforced conditions.** ADR-0003, ADR-0024 and ADR-0044 stand: an unsupported
  condition fails, the native world has no scripted actor, and incident scripts, decoys and invented
  histories stay out of the research path. Instructions are a lever that is declared and recorded
  (ADR-0074). Every case carries the origin of the behavior at baseline (`instructed`, `cued`,
  `spontaneous` or `trained`), and a published study is reproduced with its own materials, labeled so.
- **The checkpoint is a condition, and hosted models are first-class.** Behavior is observed with the
  models that show it, including hosted models through the gateway's OpenAI-compatible upstreams.
  Open-weight models are used where weights are needed: training and activations. Multi-provider
  deployment is unverified and is verified as part of stages 1 and 2.
- **One record.** The behavior card of ADR-0074 is the record of all four stages. A card is read by
  its lever states and its condition set. The computed type is a label; no decision depends on it.
- **Freeze until stage 1 exits.** No new designer tools, training providers or integrations are added
  (the surfaces of ADR-0063 to ADR-0073 stay as they are, and ADR-0070 stays proposed). Fixes, and
  what a positive control needs, are in scope.
- **Release.** Cards, detection signals, traces and null results are published. An elicitation recipe
  for a hazardous behavior is reviewed before release. Containment through the two gateways
  (ADR-0022) is unchanged.

## Consequences

- Research milestones are the four stages. Runtime acceptance stays functional system composition and
  is tracked separately ([milestones](../../milestones.md)). No capability becomes `supported` through
  this record.
- Widens the purpose stated in ADR-0044 and ADR-0074. Their rules and decisions stand.
- The first substrate named in ADR-0074, the original Emergent Collusion environment, is the first
  positive control. The second, a naming game, has no implementation yet.
- Hosted models add API cost and send prompts to an external provider. A study that uses them states
  the provider, the checkpoint identifier and its date.
- Results from small open-weight teams are not assumed to carry over to other checkpoints. A card
  belongs to the checkpoints it was measured on.
- Still to decide in later records: the release review procedure, the forked-rerun mechanism and the
  first red-team targets.
