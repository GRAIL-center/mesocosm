# Architecture decision records

One file per decision, numbered. Status is `proposed`, `accepted`, `superseded by ADR-n`. Keep each record short: context, decision, consequences.

| ADR | Title | Status |
|---|---|---|
| [0001](0001-record-decisions.md) | Record architecture decisions | accepted |
| [0002](0002-opencode-first-unmodified.md) | OpenCode is the first harness and is used unmodified | accepted |
| [0003](0003-model-gateway.md) | A model gateway is the enforcement and exposure-logging point | accepted (error types and identity headers superseded by ADR-0019) |
| [0004](0004-training-ready-records.md) | Records are training-ready; no trainer in scope | accepted |
| [0005](0005-behavior-only-default.md) | Behavior-only runs are the default; activations are selective | accepted |
| [0006](0006-smoke-test-hardware.md) | Smoke tests run on a local RTX 3090 with a 3B model | accepted (model choice superseded by ADR-0007) |
| [0007](0007-smoke-test-model.md) | Smoke-test model is Qwen3-4B-Instruct-2507, served with a 32k context | accepted (model-id clause superseded by ADR-0019) |
| [0008](0008-live-observation.md) | Live observation reads the run bundle; it never writes to it | accepted (summary clause superseded by ADR-0009) |
| [0009](0009-model-written-status-lines.md) | Model-written status lines are derived artifacts kept beside the bundle | accepted |
| [0010](0010-world-services.md) | The world is a set of ordinary services that identify callers by address | accepted (accounts clause superseded by ADR-0011) |
| [0011](0011-conditions-enforced-by-the-world.md) | Exposure timing, accounts and traps are conditions enforced by the world services | accepted (schedule origin superseded by ADR-0018) |
| [0012](0012-memory-chaining-mid-run-conditions.md) | Memory policy, run chaining and mid-run condition changes are control-plane operations with receipts | accepted |
| [0013](0013-flags-verdicts-staged-scenarios.md) | Candidate incidents are flags from traps and state checks; people give verdicts; scenarios escalate what the world offers | accepted |
| [0014](0014-run-lifecycle-at-the-gateway.md) | Pause, resume and stop are enforced at the gateway | accepted (error types and endpoint paths superseded by ADR-0019) |
| [0015](0015-persistent-operation-and-snapshots.md) | Persistent operation is a shared work queue; snapshots copy what the platform owns and declare the rest | accepted |
| [0016](0016-activations-by-recompute.md) | Activations are recomputed from recorded token ids, not captured inside the serving engine | accepted |
| [0017](0017-peer-verification-scenario.md) | The peer-verification scenario after Shi et al. is a world protocol, not a prompt | accepted |
| [0018](0018-world-services-at-scale.md) | The world services keep journals, page their lists and count exposure from the run start | accepted (list payload superseded by ADR-0023) |
| [0019](0019-nothing-names-the-platform-inside-the-sandbox.md) | Nothing inside an agent container names the platform: mounted files, per-agent keys, an admin surface only the control plane has, personas | accepted |
| [0020](0020-a-living-world.md) | The world acts on its own (`world.events`) and accounts unlock real things (`roles`, protected repositories) | accepted |
| [0021](0021-mcp-and-skills.md) | Agents get MCP servers and skills through the harness config; the world is an MCP server too | accepted |
| [0022](0022-web-gateway.md) | The real internet reaches the sandbox only through a web gateway: a logging, policed proxy, verified like a condition | accepted |
| [0023](0023-context-budget-of-the-world.md) | The world is cheap in the agent's context: board lists are glances, task text uses `curl -s`, harness tools are a manifest choice | accepted |
