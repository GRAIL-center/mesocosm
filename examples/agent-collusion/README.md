# Emergent Collusion, the published code

The environment of Shi, Zhang and Yang, "Emergent Collusion in Long-Horizon LLM Agent Interaction"
(arXiv:2609.24967), run unchanged as a study environment. Two agents solve their own tasks, exchange
evidence under a message limit, and judge each other's work. The paper counts mutual acceptance under that
limit as collusion and reports that it nearly disappears without memory across episodes.
[What the paper states](../../skills/behavior-study/references/verification-collusion.md).

```bash
bash examples/agent-collusion/setup.sh     # clones the repository at the pinned commit, builds its environment
uv run mesocosm study describe examples/agent-collusion/substrate.json
uv run mesocosm study test examples/agent-collusion/substrate.json --root runs/collusion \
    --baseline environment.episodes=2 --upstream http://127.0.0.1:8000
```

The agents write test files that the repository executes, so the manifest sets `"contained": true`: each
trajectory runs in a sandbox with no network except the recording gateway and no view of the host beyond
this directory. The host needs `bubblewrap`. The model server needs tool calling with a required tool
choice and a context long enough for ten episodes of history.

`adapter.py` copies the repository into the trial directory, maps the condition to the repository's flags,
and reads its `run.json`. A trial's seed chooses one of the 50 published task sequences, so paired trials
of two conditions run the same tasks; sequences 26 to 50 are held out for claims.
