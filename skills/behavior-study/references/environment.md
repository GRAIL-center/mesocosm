# Bringing an environment

An environment is a directory with a `substrate.json` and the code that runs one trial.

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

| Field | Meaning |
|---|---|
| `kind` | `original`: published code runs unchanged. `reconstruction`: your own implementation |
| `settings[].lever` | `model`, `training`, `structure`, `environment`, `experience`, `scale` or `instructions`. A lever without a switch cannot be tested |
| `settings[].removal` | The value that takes the ingredient away (no memory, no feedback) |
| `settings[].stand_in_for` | The lever this switch imitates. It stays recorded under its own lever |
| `settings[].held_out` | The value reserved for claims, such as the task set no design was tuned on |
| `settings[].request_model` | `true` on a model switch whose values are the model ids the trial requests. A list of ids is a mixed team. A trial that requests other ids fails |
| `measures[].floor` | At or below this the behavior counts as absent. Measure it with the ingredient removed; do not assume uniform chance, because models have favorite answers |
| `command` | `{python}`, `{condition}`, `{outcome}`, `{seed}`, `{workdir}` are replaced per trial. Runs in the manifest's directory |
| `agents` | Agent ids that get their own model key, so calls are attributed |

## What one trial receives

| Variable | Content |
|---|---|
| `TRIAL_CONDITION` | JSON file with the value of every switch, keyed `lever.name` |
| `TRIAL_SEED` | The seed of this repetition. Paired trials of two conditions share it |
| `TRIAL_OUTCOME` | Where to write `{"measures": {"name": 0.5}, "note": ""}` |
| `OPENAI_BASE_URL`, `OPENAI_API_BASE`, `OPENAI_API_KEY` | The recording gateway |
| `MODEL_KEYS` | JSON, agent id to key. Use an agent's key for its calls |

- Report every declared measure. Use `null` for one the trial could not determine, never zero.
- A non-zero exit status, or no outcome file, is a failed trial.
- Write per-turn detail (choices, verdicts, messages) to a file next to the outcome file, one JSON object
  per line. You will need it to check mechanisms.
- Try a handful of requests by hand whenever you add a prompt mode, and look at the reply length and whether
  the answer parses, before you start trials. A reply cut off by `max_tokens` counts as no answer.
- Send a sampling seed with every model request, drawn from the trial seed. Paired trials then send the
  same requests until your condition makes them differ, and `reach` can be decided.

## Published code

Leave the repository unchanged. Next to the manifest, write an adapter that reads the condition, calls the
repository's entry point with the matching flags, reads its output and writes the outcome. Code that uses
the OpenAI SDK or LiteLLM picks up the gateway from the variables above. If the repository lets agents
execute code, it needs a container; the command runner does not provide one.
