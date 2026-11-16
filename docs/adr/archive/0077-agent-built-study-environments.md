# ADR-0077: Agent-built study environments: a manifest and a command, a recording gateway link, tests as instruments, events and briefs

Status: accepted · Date: 2026-11-16

## Context

ADR-0076 decided that agent teams build the reproductions and named five expansions. The study core of
ADR-0074 could not carry them. An environment was a Python object written against the platform. A study
substrate had no recorded model calls. The only test was a claim battery of five or more repetitions per
arm. Nothing let an agent look inside a trial, and nothing told it what the original study did or how the
unchanged environment behaves.

## Decision

- **An environment is a manifest and a command** (`mesocosm.study.command`). The builder writes a JSON
  manifest next to the code: name, version, `original` or `reconstruction`, source, every switch with its
  lever and accepted values, the measures, the agent ids and the command that runs one trial. The command
  gets a condition file (`TRIAL_CONDITION`), a seed (`TRIAL_SEED`) and the path of an outcome file
  (`TRIAL_OUTCOME`) and writes `{"measures": {...}, "events": [...], "note": ""}`. A published repository
  is not changed; an adapter script next to the manifest maps the condition to the repository's own flags
  and its output to the outcome.
- **A switch carries its labels.** `stand_in_for` names the lever a switch imitates; it stays recorded
  under its own lever and the label is carried into test results and card findings. `held_out` names the
  value reserved for claims, which makes the switch a partition, not an arm. `request_model` marks a
  model switch whose values are the model ids the trial's requests must name; a list of ids is a mixed
  team.
- **Model calls of a study trial go through the model gateway** (`mesocosm.study.gateway_link`). The
  gateway of ADR-0019 and ADR-0029 is started in-process on a loopback port for the trials of one
  command, with one bearer key per trial and agent. The command reads the standard `OPENAI_BASE_URL`
  (also set as `OPENAI_API_BASE`), `OPENAI_API_KEY` and, for per-agent attribution, `MODEL_KEYS`. The
  model server's own key is withheld from the command's environment. After a trial its records come back
  as model calls with input and output digests, the requested model id and token usage. A model switch
  is enforced by evidence: a trial whose recorded requests name other ids than the condition declares
  is a failed trial.
- **A test is an instrument** (`mesocosm.study.instrument`). One test compares the baseline with one
  changed condition over repeated runs on shared seeds and returns the effect, the spread of the baseline
  (standard deviation and range), whether every variant run lies outside the baseline range, the exact
  permutation p-value with the smallest value it could take, whether the change reached a model input,
  the missing trials and the compute used. Baseline trials are named after their condition, so every
  test of a study reuses them. Budgets are in model calls and tokens across the study and fail with
  `rejected_budget`; there is no limit on the number of tests.
- **Exploration and claim are trial modes.** An exploration test needs two repetitions, takes any value
  that is not held out, and never enters a behavior card. A claim runs on the held-out value of every
  partition switch, on seeds no exploration trial used, with enough repetitions for its alpha. Cards are
  built from claim trials only.
- **An environment reports events for diagnosis** (`Event`, `mesocosm.study.diagnose`): a step index, a
  kind, an agent id and data. They are read in bounded pages and broken down as the mean of one field per
  condition and per step or agent.
- **Context comes first** (`mesocosm.study.brief`). A reference states the original study field by field,
  each with the passage that says so or null where none was found. A profile is the measured baseline of
  every declared measure with its run-to-run spread, optionally for every value of one switch.
- **Six commands** expose this to an agent: `mesocosm study describe`, `brief`, `test`, `events`,
  `breakdown` and `card`. Each prints one JSON object with `--json`; a refusal carries
  `rejected_unsupported` or `rejected_budget` and the reason.
- **Sampling seeds make the reach check decidable.** An environment that sends a per-request sampling
  seed drawn from the trial seed sends identical requests in two paired trials until the condition makes
  them differ, on a server that honors the seed. The template environment does this.

## Consequences

- Trial records gain fields with defaults (`mode`, `events`, and the model id and token usage of a call).
  Records written before this change still load and count as claim trials. The Python substrate contract
  of ADR-0074 is unchanged.
- **A command environment is not contained.** It is a host process with the caller's privileges. The
  published Emergent Collusion code executes test files that agents write, so it must not run this way
  with real models. A contained runner (the command in a container on an internal network whose only
  route is the gateway, ADR-0022) is `unsupported` and is the next piece the first positive control
  needs, together with its adapter.
- Gateway logs with full prompts and responses are written under the study root. Study roots belong
  under `runs/` and are never committed.
- Not built here: templates beyond the naming game; an intake that turns a paper into a reference;
  ready-made single-agent, voting and no-cue arms (a builder declares them as switches); the independent
  checker role; the same levers in the workbench and the designer interface, which keep their rules.
- The evidence is automated tests with scripted inference. No trial has run with a real model, and no
  capability becomes `supported` beyond what those tests cover.
- No new dependency. The gateway link needs the existing `gateway` extra.
