# Study environments and references

Material for `mesocosm study` ([guide](../../docs/study.md)).

| Path | Content |
|---|---|
| [naming-game/](naming-game/) | A template environment: a pairwise naming game for a population of model agents. It is a reconstruction of the setting of Ashery, Aiello and Baronchelli with the paper's payoff amounts and our own prompts. Copy it to start a new environment. |
| [library/](library/) | References of original studies: environment, measure, conditions, reported results and re-tests, each with the passage that states it. |

The template has run with scripted inference in tests only. It is not evidence about any model.

```bash
uv run mesocosm study describe examples/study/naming-game/substrate.json
uv run mesocosm study brief examples/study/naming-game/substrate.json --root runs/naming \
  --reference examples/study/library/naming-conventions.json --upstream http://127.0.0.1:8000
uv run mesocosm study test examples/study/naming-game/substrate.json --root runs/naming \
  --measure final_match_rate --set experience.history=0 --upstream http://127.0.0.1:8000
```

Differences from the original that the template declares: 4 or 8 agents instead of 24, 6 or 12 population
rounds, a one-line answer instead of step-by-step reasoning, and a prompt that addresses the agent as a
player instead of asking it to predict a player's next action.
