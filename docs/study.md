# Building and running a behavior study

This guide is for an agent, or a team of agents, that reproduces a multi-agent behavior. It covers the
`mesocosm study` commands ([ADR-0077](adr/0077-agent-built-study-environments.md)). The rules behind them
are in [ADR-0074](adr/0074-behavior-studies.md) and [ADR-0076](adr/0076-agent-teams-build-reproductions.md).

You may build whatever the study needs: a new environment, a scripted counterpart, a prompt where a lever
cannot be set, a harder or easier task set, a different model. What the platform asks in return is that
you declare it. Labels and records are strict; what you build is not.

Every command accepts `--json` and then prints exactly one JSON object. A refusal has
`"status": "rejected_unsupported"` or `"rejected_budget"`, an `"error"` that says why, and exit status 1.

## 1. Bring an environment

An environment is a directory with a `substrate.json` and the code that runs one trial. Start from the
[naming-game template](../examples/study/naming-game/), which is about 100 lines of standard-library Python.

```json
{
 "name": "naming-game",
 "version": "0.1.0",
 "kind": "reconstruction",
 "source": "where the environment comes from and what differs from the original",
 "command": ["{python}", "naming_game.py"],
 "agents": ["a01", "a02"],
 "settings": [
  {"lever": "experience", "name": "history", "description": "interactions shown in the prompt",
   "values": [0, 5], "default": 5, "removal": 0}
 ],
 "measures": [{"name": "final_match_rate", "description": "...", "floor": 0.1}]
}
```

- `kind` is `original` when the command runs published code unchanged and `reconstruction` when the
  environment is your own implementation of a described setting.
- `settings` lists every switch. `lever` is one of `model`, `training`, `structure`, `environment`,
  `experience`, `scale`, `instructions`. A lever without a switch cannot be tested and stays `not-tested`.
- `removal` is the value that takes the ingredient away (no memory, no feedback).
- `stand_in_for` labels a switch that imitates another lever, such as a prompt line in place of training.
  It is recorded under its own lever, and the label follows it into every result.
- `held_out` names the value reserved for claims, such as the task set no design was tuned on. Explore on
  the other values.
- `request_model: true` marks a model switch whose values are the model ids the trial requests. A list of
  ids is a team of mixed checkpoints. A trial whose recorded requests name other ids fails.
- `floor` is the level at or below which the behavior counts as absent (chance level, for example).
- In `command`, `{python}`, `{condition}`, `{outcome}`, `{seed}` and `{workdir}` are replaced per trial.
  The command runs in the manifest's directory.

One trial receives

| Variable | Content |
|---|---|
| `TRIAL_CONDITION` | JSON file with the value of every switch, keyed `lever.name` |
| `TRIAL_SEED` | The seed of this repetition. Paired trials of two conditions share it |
| `TRIAL_OUTCOME` | Where to write `{"measures": {...}, "events": [...], "note": ""}` |
| `OPENAI_BASE_URL`, `OPENAI_API_BASE`, `OPENAI_API_KEY` | The recording gateway, when a model server is given |
| `MODEL_KEYS` | JSON, agent id to key, for the agents the manifest names; use an agent's key for its calls |

Report every declared measure; use `null` for one the trial could not determine, never zero. Report
events for anything you may want to look at later: `{"step": 3, "kind": "verdict", "agent_id": "a01",
"data": {"accept": true}}`. A command that exits with a non-zero status, or writes no outcome, is a failed
trial. It is kept and counted as missing. It is never rerun.

To run a published repository, leave it unchanged and put an adapter script next to the manifest. The
adapter reads the condition, calls the repository's own entry point with the matching flags, reads its
output and writes the outcome. Code that uses the OpenAI SDK or LiteLLM picks up the gateway from the
variables above.

Send a sampling seed with every model request, drawn from the trial seed. Two paired trials then send the
same requests until your condition makes them differ, and the platform can tell whether the change
reached a model input.

`mesocosm study describe substrate.json --json` checks the manifest and lists the switches, the measures
and the levers that have no switch.

## 2. Read the brief before designing

```bash
mesocosm study brief substrate.json --root runs/my-study \
  --reference examples/study/library/naming-conventions.json \
  --across experience.history --upstream http://127.0.0.1:8000 --json
```

The brief returns the original study and the measured baseline. The reference states the original
environment, measure, conditions, reported results and re-tests, each with the passage that says so.
A `null` passage means the author of the entry found none. The profile gives, for every measure, the
number of runs, mean, lowest, highest and standard deviation of the unchanged environment, and with
`--across` the same for every value of one switch. Compare the profile with the original's conditions
first. If the original needs agents that mostly solve their tasks and yours solve 45%, no change to the
interaction rules will close that gap; change the task set or the model.

[Reference entries](../examples/study/library/) exist for naming conventions and verification collusion.
Write a new one as JSON in the same form, quoting the source.

## 3. Test a change

```bash
mesocosm study test substrate.json --root runs/my-study --measure final_match_rate \
  --set experience.history=0 --upstream http://127.0.0.1:8000 --json
```

One test runs the baseline and the changed condition three times each (`--repetitions`, 2 to 12) on shared
seeds. Baseline runs are reused by every later test of the same study. The result has

| Field | Meaning |
|---|---|
| `effect` | Mean of the changed condition minus mean of the baseline |
| `noise_sd`, `noise_range` | Run-to-run spread of the baseline |
| `separated` | Every changed run lies outside the range of the baseline runs |
| `p_value`, `minimum_p` | Exact permutation test, and the smallest value it could return at this size |
| `reach` | `reached`, `identical` (the change altered no request), `undetermined`, or `not-applicable` (model and training switches) |
| `stand_ins` | Changed switches that imitate another lever |
| `missing` | Trials that failed or returned no value |
| `compute`, `study_compute` | Trials, model calls and tokens of this test and of the whole study |
| `statement` | One sentence that reads the numbers above |

There is no limit on the number of tests. `--max-calls` and `--max-tokens` set a compute budget for the
study; a test that would exceed it is refused before it starts. `--baseline lever.name=value` moves the
baseline, and several `--set` options change several switches together.

## 4. Look inside

```bash
mesocosm study events x-3f2a9c0d1b7e-r00 --root runs/my-study --kind verdict --limit 20 --json
mesocosm study breakdown --root runs/my-study --kind interaction --field match --by step --json
```

`events` returns one page of a trial's events with `next_offset`. `breakdown` returns the mean of a
numeric or boolean event field per condition and per step or agent, over every trial of the study. Each
trial's directory under the root also keeps `condition.json`, `stdout.txt`, `stderr.txt` and
`record.json`, and `gateway/gateway.jsonl` keeps every request and response.

## 5. Make a claim

```bash
mesocosm study test substrate.json --root runs/my-study --measure final_match_rate \
  --set experience.history=0 --claim --repetitions 5 --json
mesocosm study card substrate.json --root runs/my-study --measure final_match_rate \
  --origin spontaneous --json
```

Exploration results are for deciding what to try next. A claim runs on the held-out value of every
partition switch (set automatically), on seeds no exploration run used, with enough repetitions for the
chosen `--alpha` (4 per arm at 0.05). `shown` is decided for claims only. `card` runs a claim battery, one
arm per alternative value of each tested switch, and returns one state per lever: `necessary`,
`sufficient`, `modulates`, `no-effect` or `not-tested`. `--origin` states how the behavior arises at
baseline (`instructed`, `cued`, `spontaneous`, `trained`); a behavior obtained with scripted counterparts
or a prompt stand-in is declared so. A result that says the behavior needs the model, or that a published
effect did not reproduce, is a result. Record it; do not add cues until it appears.

## Model servers

`--upstream` is an OpenAI-compatible base URL without `/v1`. `--upstream-key-env NAME` names the
environment variable that holds its key. `--upstreams FILE` routes model ids to other servers:
`{"model-id": {"url": "https://...", "key_env": "NAME", "kind": "openai"}}`. Keys stay with the gateway;
the command never sees them. Without `--upstream` the environment runs with no gateway and no call
records, which suits an environment that makes no model calls.

## Limits

- A command environment is a host process and is not contained. Do not run an environment whose agents
  execute code or use the network this way; a contained runner does not exist yet.
- Everything here is tested with scripted inference only. No study has run with a real model.
- Keep study roots under `runs/`. They hold prompts and responses and are never committed.
