# ADR-0074: Behavior studies are the organizing unit: substrates, seven-lever conditions, typing batteries and behavior cards

Status: accepted · Date: 2026-11-14

## Context

The maintainer's direction is that Mesocosm should reproduce reported multi-agent behaviors and decide,
for each one, what kind of conditions it needs: whether it appears with the environment alone, needs
experience inside a run, depends on the checkpoint, or needs post-training.

Two findings from the maintainer's coding of the reproduction literature (114 studies, 151 behaviors; in
preparation) shape the design. A behavior rarely has one condition: 78% of the coded behaviors changed
with two or more conditions, and the checkpoint changed the behavior in 88 of the 93 cases where it was
varied. And the tests that would decide a behavior's type are mostly missing: of 92 behaviors reported
without instruction, 49 had no cue-removal test, 82 no memory test and 87 no training contrast, and a
fixed list of eight types placed only 19 of 75 held-out behaviors clearly.

The existing architecture cannot run those tests. Its organizing type is the world manifest of coding
agents on real tasks; the integration contract for other environments takes that manifest too. One
world runs at a time, the three recorded real-agent runs all scored zero on their tasks, and typing one
behavior on one model needs tens of trials. Rules that protect discovery in a realistic world block the
deciding tests: no behavior catalog (ADR-0013), no scripted actor (ADR-0024), at least three agents,
and task instructions that stay as published. The behaviors to reproduce are defined in small environments, such
as a mutual-review game or a message-passing graph, and none of them is connected.

## Decision

- **A behavior study is the organizing unit.** `mesocosm.study` holds it and does not import the world
  manifest or the native runtime.
- **A substrate is the environment in which a behavior is defined.** It declares its identity, every
  switch it can set, and its own measures, and it runs one fresh trial per call. Its kind is `original`
  when the published code runs unchanged (a re-test), `reconstruction` when the setting is implemented
  from a description, and `native_world` for the Docker/OpenCode task world, which becomes one substrate
  among others. A trial reports each measure or `null` when it could not be determined, and the model
  calls in gateway order with digests of each input and output.
- **A condition is a point in a seven-lever space:** model, training, structure, environment,
  experience, scale and instructions. It assigns one declared value to every declared switch. An
  undeclared switch or value is refused with `rejected_unsupported`.
- **A typing battery decides the levers.** It repeats the study baseline to measure run-to-run noise,
  changes one switch at a time, and removes two switches together where an interaction is to be tested.
  Arms share seeds. Trials run several at a time, unattended and resumable. Every trial keeps one
  record; a failed trial stays a missing observation; nothing is stopped, retried or replaced by outcome.
- **A behavior card holds the result.** Each lever is `necessary`, `sufficient`, `modulates`,
  `no-effect` or `not-tested`, by a stated rule: an exact permutation test against the baseline at the
  plan's alpha, and absence at or below the measure's floor. A manipulation that changed no model input
  leaves its lever `not-tested`. The type is the set of necessary levers, computed and never entered
  by hand. A card belongs to one behavior measure, one substrate and one baseline condition.
- **Rules that change, for study substrates:**
  - The "no catalog" clause of ADR-0013 is superseded: cards are the catalog. Reproducing a reported
    behavior under declared conditions is the purpose of a study. Every plan declares the origin of the
    behavior at baseline (`instructed`, `cued`, `spontaneous` or `trained`), so that a prescribed or
    cued behavior is never reported as spontaneous.
  - Instructions are a lever. Prompt text may be varied as a declared, recorded switch of that lever.
    It still never stands in for another lever: an environment that cannot remove memory fails; it is
    not told to forget.
  - ADR-0024 keeps governing the native world. In other substrates a fixed or scripted counterpart is
    allowed only as a declared control arm and is never evidence of emergent behavior.
  - The number of agents is the scale lever. A study uses the counts the environment defines, including
    two, and may run one agent as a control arm. The three-agent acceptance rule keeps applying to the
    native world.

## Consequences

- This record ships the core only: conditions, the substrate contract, the trial store and runner, the
  battery plan and the card, with CPU tests against a deterministic test environment. No environment is
  connected. Every capability here is tests-only until a real substrate runs real models.
- The reach check compares paired trials call by call. With sampled decoding the first difference is
  usually an output, so a late-acting manipulation reads `undetermined`. Making it decisive needs the
  gateway to replay the paired baseline's responses for identical inputs; that is the next change,
  together with gateway identities for substrates.
- The exact test enumerates every split of two arms, which bounds a battery to 12 repetitions per arm
  and needs at least 4 at alpha 0.05.
- Still to decide in later records: the substrate registry and CLI, the first substrate (the original
  Emergent Collusion environment), detectors over recorded events (reusing `reproduction.behavior`),
  the record of re-tests of published results, and wrapping the native world as a substrate. The
  worker, the designer tools and the workbench are unchanged until then.
- Supersedes the catalog clause of ADR-0013. Limits ADR-0024 and the three-agent rule of ADR-0026 to
  the native world.
