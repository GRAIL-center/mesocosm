---
name: behavior-study
description: "Reproduce a reported multi-agent LLM behavior with Mesocosm and find which conditions it needs: connect or build an environment, measure the unchanged baseline and its run-to-run noise, test changes, and confirm claims on held-out values."
---

# Behavior study

You reproduce a multi-agent behavior and find out which conditions it needs. Mesocosm runs your
environment, records every model call and returns each comparison next to the run-to-run noise.

You may build whatever the study needs: a new environment, a scripted counterpart, a prompt where a
condition cannot be set directly, an easier or harder task set, another model. Declare what you built.
The records and labels are strict; what you build is not.

## Commands

Run from a Mesocosm checkout (`uv sync --extra gateway` once).

```bash
uv run mesocosm study describe substrate.json
uv run mesocosm study test substrate.json --root runs/<study> --upstream <model-server-url> \
    [--set lever.name=value ...] [--measure <name>] [--claim] [--repetitions N]
uv run mesocosm study card substrate.json --root runs/<study> --upstream <model-server-url> \
    --measure <name> --origin spontaneous
```

Add `--json` for one JSON object. A refusal has `"status": "rejected_unsupported"` and says why.
`--upstream` is an OpenAI-compatible base URL without `/v1`; `--upstream-key-env NAME` names the variable
holding its key; `--upstreams FILE` routes model ids to other servers
(`{"model-id": {"url": "...", "key_env": "NAME", "kind": "openai"}}`).

## Workflow

1. **Read the original study.** [references/](references/) states the environment, measure, conditions
   and results of the original, with quoted passages. Note what the original agents could do: if they
   solved most of their tasks and yours cannot, no change to the interaction rules will close that gap.
2. **Bring an environment.** A manifest and a command; see [references/environment.md](references/environment.md)
   and copy [template/](template/). To use published code, leave it unchanged and write a small adapter.
3. **Measure the baseline first.** `test` without `--set` runs the unchanged condition and returns every
   measure with `mean`, `low`, `high` and `sd`. That spread is the noise every later effect is read against.
   Compare the baseline with step 1 before designing anything. Change the task set or the model if the
   preconditions of the behavior are missing.
4. **Explore.** `test --set lever.name=value --measure <name>` runs the changed condition on the same
   seeds as the baseline. Read `effect` against the baseline's `low`–`high`; `separated` says whether
   every changed run lies outside that range. `reach` says whether the change altered any model request:
   `identical` means it never reached a model. Run as many tests as you like. Baseline runs are reused,
   and every result reports the calls and tokens it used and the study's total.
5. **Look inside before explaining.** Each trial keeps a directory `runs/<study>/trials/<trial-id>/` with
   `condition.json`, `record.json`, `stdout.txt`, `stderr.txt` and whatever your environment wrote next to
   its outcome file. `runs/<study>/gateway/gateway.jsonl` holds every request and response. Read these
   with your own tools. Check a proposed mechanism against per-turn data before you report it.
6. **Claim.** `test --claim --repetitions 5` reruns a comparison on the held-out value of every partition
   switch and on seeds exploration never used; `shown` is the decision. `card` does this for every
   alternative value of each switch and returns one state per lever: `necessary`, `sufficient`,
   `modulates`, `no-effect` or `not-tested`.
7. **Report** what reproduced and what did not, with the card, the baseline profile and the labels below.
   On a card, `no-effect` means no effect was shown at that number of runs, not that there is none.

## What is strict

- **Labels.** `kind` is `original` only when published code runs unchanged. A switch that imitates
  another lever (a prompt line instead of training) sets `stand_in_for`. `--origin` states how the
  behavior arises at baseline: `instructed`, `cued`, `spontaneous` or `trained`. A behavior obtained with
  a scripted counterpart is reported as such, not as emergent among real agents.
- **Records.** A failed trial stays failed and counts as missing. Nothing is rerun, replaced or deleted.
- **Claims.** Exploration results decide what to try next. Only claim runs support a statement.
- **Negative results.** "The behavior needs this model" or "the published effect did not reproduce" is
  a result. Record it. Do not add cues until the behavior appears.
- **Containment.** Without `"contained": true` the command is a plain process on the host. Set it for any
  environment whose agents execute code or use the network; the trial then reaches only the gateway.
- **Data.** Keep study roots under `runs/`; they hold prompts and responses and are never committed.
