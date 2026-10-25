# External integration contract fixture

This is an independently installed Python distribution used by
`tests/test_installed_integration.py`. It reads JSON tasks, runs their reference
program and original verifier as real CPU subprocesses, saves their output, and
cleans its temporary workspace. It has **no agents or sandbox**. It is not a
benchmark adapter, research acceptance result, or production backend template.

The explicit `contract_test_only` option and `agent_rollout: false` result keep
this check separate from real multi-agent evidence. Use the public contracts and
requirements in `docs/integrations.md` when implementing a production integration.
