# SwarmStill generalization study: AgentCollabBench

Does a model-conditioned guide help designers predict how collective failures of language-model teams respond to
changes in their environment, across behaviors, model sizes and model families? The first SwarmStill study used one
behavior (peer-review collusion, `../swarmstill_ec`). This study tests the same question on four other behaviors with
real benchmark tasks and real model runs.

## Data and harness

- Tasks: [AgentCollabBench](https://huggingface.co/datasets/AgentCollabBench/AgentCollabBench) (CC BY 4.0), 900
  scenarios over five communication topologies and four behaviors:
  - IDR, instruction decay under peer pressure.
  - RTD, survival of a tagged constraint across hops.
  - CPR, spread of a seeded false belief.
  - CLC, leakage of private context into a later task.
- Harness and scoring: the official [AgentCollabBench code](https://github.com/aritra741/AgentCollabBench) (MIT),
  used unmodified.
  - `acb_run.py` calls its `TaskRunner`; `acb_score.py` calls its metric computation.
  - RTD and CLC are programmatic.
  - IDR and CPR use the paper's judge, DeepSeek V4 Flash, here reached through an OpenAI-compatible endpoint.
- Models: every agent in a team runs the same model, served by vLLM without extended reasoning.
  - Size axis: Qwen3-1.7B, Qwen3-4B, Qwen3-8B.
  - Family axis at about 4B: Qwen3-4B, Gemma-4-E4B-it, Llama-3.2-3B-Instruct.

## Conditions

`acb_conditions.py` selects 20 single-behavior tasks per behavior from branching trees, converging DAGs and custom
graphs (balanced, seed 7). It writes four task variants of each:

| Condition | Change (task JSON only) |
|---|---|
| baseline | none |
| chain | edges rewired into a linear chain in order of first speaking turn |
| full | every agent receives from every other agent |
| specific | IDR: the second trigger prompt is replaced by the first (no escalation). RTD: tracer salience low. CPR: a second false-belief carrier (`extra_agents`). CLC: an isolation sentence appended to the Task B description |

The benchmark schema requires two IDR trigger prompts and a CPR ground truth, so removing either is not a valid
condition.

## Prediction test

1. Run all conditions.
2. Score the baseline only, and build each model's baseline profile (`acb_prompts.py`).
3. Freeze designer predictions from two arms: no guide (N) and the general guide `framework_v4.md` (F). The guide was
   frozen by sha256 before any data from this study existed.
4. Score the interventions and compare (`acb_analyze.py`).

## Run

```bash
python acb_conditions.py train.jsonl CONDITIONS
python acb_run.py CONDITIONS/<metric>/<condition> <metric> <served-model> http://HOST:PORT/v1 RUNS/<metric>/<condition>/<model>
python acb_score.py RUNS scores.jsonl --judge-base-url http://HOST:PORT/v1 --judge-model deepseek-v4-flash
python acb_prompts.py scores.jsonl CONDITIONS framework_v4.md PROMPTS
python acb_analyze.py scores.jsonl ANSWERS analysis.json
```

Run outputs, traces and scores live outside this repository.
