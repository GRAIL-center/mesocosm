# ADR-0079: Behavior studies

Status: accepted · Date: 2026-11-16 · Consolidates ADR-0074 and ADR-0077

## Decision

- **A behavior study is the unit.** `mesocosm.study` holds it and never imports the world manifest or the
  native runtime. The native world is one possible environment among others.
- **An environment is a manifest and a command.** The manifest declares the environment's identity
  (`original` when published code runs unchanged, `reconstruction` otherwise), every switch with its
  lever, the measures and the command for one trial. The command gets a condition file, a seed and the
  path of an outcome file. Published code is not changed; an adapter script maps the condition to it.
- **Seven levers.** `model`, `training`, `structure`, `environment`, `experience`, `scale`,
  `instructions`. A switch belongs to one lever and lists the values it accepts. Anything else is refused
  with `rejected_unsupported`. A lever without a switch stays `not-tested`.
- **Switch labels.** `removal` is the value that takes an ingredient away. `stand_in_for` names the lever
  a switch imitates; it stays recorded under its own lever. `held_out` reserves a value for claims.
  `request_model` marks a model switch whose values are the model ids a trial must request.
- **Model calls go through the model gateway.** It runs in-process for the trials of one command, with a
  key per trial and agent. The server's own key is withheld from the command. A trial whose recorded
  requests name other model ids than its condition declares is a failed trial.
- **One comparison.** A plan holds the baseline and changed conditions, each run several times on shared
  seeds. Trials are named after their condition, so plans of one study share baseline runs. A comparison
  returns the effect next to the spread of the baseline, an exact permutation test, and whether the
  change reached a model input. A failed trial is kept and counted as missing; nothing is rerun.
- **Exploration and claim are trial modes.** Exploration takes any value that is not held out. A claim
  runs on the held-out values, on its own seed stream, with enough repetitions for its alpha. A behavior
  card is built from a claim plan with one changed switch per condition and gives each lever a state:
  `necessary`, `sufficient`, `modulates`, `no-effect` or `not-tested`.
- **Three commands and a skill.** `mesocosm study describe`, `test` and `card`. The workflow, the
  environment contract, references of original studies and a template live in `skills/behavior-study`.

## Consequences

- A command environment is a host process and is not contained. An environment whose agents execute code
  needs a contained runner, which does not exist. The published Emergent Collusion code is such a case.
- The reach check is decided only when an environment sends per-request sampling seeds drawn from the
  trial seed and the server honors them.
- Dropped from earlier records: the computed type label, the factorial interaction, events inside trial
  records, the reference schema and the budget estimate.
- The evidence is tests with scripted inference. No study has run with a real model.
