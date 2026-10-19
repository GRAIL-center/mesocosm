# Examples and execution modes

Use `world-native-three.yaml` with the [native runtime runbook](../docs/native-multiagent.md) for
three actual OpenCode agents on an external task's native environment and original verifier.
The shared directory is a real Docker volume. Each agent has its own session, key and workspace.
Adding agents uses the same manifest schema; no two-agent special case is required.

`mesocosm run` defaults to the native task runtime. It refuses tasks with no native environment
instead of silently substituting the general-purpose agent image. The explicit
`--task-runtime fixture --verifier-image <image>` mode retains the hand-written communication and observer smoke tests.
Passing a fixture test does not establish real benchmark performance or full-stack integration.

The eight hand-written task families under `tasks/` and ten peer-review episodes are fixtures.
Their scorer checks range from exact strings and filenames to fixed bug labels. The seeded board
and chat messages, persona backstories, wiki incidents, keys and release notes are authored inputs.
They are not evidence of past work by running agents. The release-notes side task directly instructs
credential transfer; the timed-lookup instructions disclose the cache API; board-exchange explicitly
instructs conversation. Those behaviors must not be described as spontaneously discovered.

The built-in board, chat, wiki and pastebin are functioning small applications. Git uses real Git,
PyPI serves real downloaded wheels, and the web gateway's live mode reaches real sites. The built-in
cache is a text object store, not an integrated compiler/package cache. Clock and ledger simulate time
and balances; the clock's decommission timestamp does not destroy a workstation. Vault is an explicit
honeypot that grants no external credentials. These experimental facilities are disabled in the native
three-agent example. They remain available as opt-in fixtures, not replacements for application services.

`world-benchmark` and `world-benchmark-suite` reference externally materialized tasks. The other example
worlds, scenarios, chains and forks mainly exercise the fixture families. Unit-test fake harnesses and
fake inference servers live under `tests/`; hardware acceptance must run the actual stack separately.
