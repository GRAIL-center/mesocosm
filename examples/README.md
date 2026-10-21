# Real environments and experiment fixtures

Start with [world-native-three.yaml](world-native-three.yaml) and the
[native runtime guide](../docs/native-multiagent.md). Three independent OpenCode servers use an
external task's declared environment and its original verifier. The shared directory is a real Docker
volume. The manifest adds no authored history, decoy scorer, side-task instruction or simulated service.

The other primary configurations also use external tasks:

| Configuration | Purpose |
|---|---|
| [world-native-three.yaml](world-native-three.yaml) | Three agents, separate workspaces and a shared directory. |
| [world-benchmark.yaml](world-benchmark.yaml) | Three isolated agents performing the same external task. |
| [world-benchmark-suite.yaml](world-benchmark-suite.yaml) | Three agents consuming an external task collection from a common queue. |

Materialize tasks and replace the example control key before running. Importing a task reference does
not establish that its environment is supported: the native runtime checks its execution requirements
and refuses unsupported ones. Configurations do not promise successful collaboration or task completion.

Change actual access rules, resource budgets, memory, rewards and observation conditions to study
behavior. Keep the task and independent success criteria fixed when comparing a condition. The
[research-method note](../docs/incident-reproducibility.md) explains why an incident script cannot
establish that a behavior emerged under those conditions.

## Legacy authored fixtures

All previous authored tasks, scenario ladders, workplace histories and demo worlds now live together
under [fixtures/](fixtures/README.md). Their relative paths are preserved. They remain useful for
checking adapters, flags, replay and compatibility. They are excluded from the primary research setup.

`mesocosm run` defaults to the native task runtime. Legacy fixtures require explicit
`--task-runtime fixture --verifier-image <image>` execution. Passing a fixture or a test with a fake
inference server does not establish integration with real agents and task environments.

Built-in board, chat, wiki, pastebin and Git services remain available as optional applications. A fresh
wiki is empty unless initial documents are explicitly configured. The legacy cache is a text object
store; clock and ledger simulate time and balances; vault is a canned honeypot. Those facilities are
not substitutes for the real application or resource being studied.

## Compare conditions

After preparing a private native world, use [experiments/reward-allocation.yaml](experiments/reward-allocation.yaml).
The [experiment guide](../docs/experiments.md) explains enforced controls, repeated trials and measurement limits.
