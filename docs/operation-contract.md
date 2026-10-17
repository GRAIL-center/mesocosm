# Operation contract (draft)

One contract, several surfaces: a Python API (`mesocosm.core.api`), a CLI (`mesocosm ...`, every command accepts `--json`), and later an HTTP/MCP wrapper. All surfaces call the same functions; configuration and enforcement rules exist once.

Changing an already supported variable is a **configuration operation**. Introducing a new variable or service is a **development operation** (implement, test, register a new version). The contract never pretends that a natural-language request has become an enforced condition.

## Verbs

| Verb | Purpose | Required evidence in the result |
|---|---|---|
| `capabilities` | List task adapters, harness adapters, models, observers; validate a proposed combination | explicit `supported` / `unsupported` / `unverified` per item, dependency versions, preflight report |
| `world validate <manifest>` | Check a manifest without running | errors, warnings, resolved versions |
| `world create <manifest>` | Assemble a world (tasks, services, workspaces, channels, memory policy, initial state) | `world_id`, versioned manifest hash, initialization checks |
| `run start <world_id> [--mode comparison\|persistent]` | Start a run within approved budgets | `run_id`, state transitions, cost ceiling |
| `run pause` / `run resume` / `run stop <run_id>` | Lifecycle (pause only where the harness supports it) | state transition record, or explicit `unsupported` |
| `run status <run_id>` | Progress, cost, failures | counters with timestamps |
| `conditions set <run_id> <condition> <value>` | Change a supported condition during a run | receipt: previous value, new value, effective event id, enforcement point, result |
| `events tail <run_id> [--filter]` | Subscribe to events | events with `run_id`, `agent_id`, `step`, `request_id` |
| `artifacts get <run_id> <kind>` | Fetch native logs, exposure records, activations summary | content-addressed blob ids |
| `snapshot create <run_id>` | Save declared world state | coverage declaration: what was captured, what was not |
| `fork <snapshot_id> --edit <patch>` | New run from a snapshot with changed conditions | parent lineage, intervention record, restoration checks |
| `flags list <run_id>` / `flags review <flag_id>` | Candidate incidents from detectors; human verdicts | detector version, catalog version, verdict record |
| `export training <run_id>` | Export records in TrainingTrace-compatible form | data split, action masks, reward version, policy version |
| `extend register <component>` | Register a new service/adapter/observer/variable version | code revision, test results, compatibility declaration |

## Receipts

Every state-changing verb returns a receipt:

```json
{
  "op": "conditions.set",
  "run_id": "run_01J...",
  "requested": {"condition": "budget.completion_tokens", "agent_id": "agent-2", "value": 20000},
  "previous": {"value": 50000},
  "enforced_at": {"component": "gateway", "event_id": "evt_...", "wallclock": "2026-10-18T03:12:44Z"},
  "result": "applied",
  "verification": {"method": "gateway_counter_reset", "ok": true}
}
```

`result` is one of `applied`, `rejected_unsupported`, `rejected_budget`, `rejected_policy`, `failed`. There is no `approximated`.

## Authority

- Experimental agents: no access to any verb. They can reach the gateway and the sandbox services only.
- Research agents: scoped tokens; `run start`, `conditions set`, `fork` and `extend register` are subject to platform budgets and isolation policy. A research agent's request is an untrusted input to a policy check, not an administrator command.
- Humans: same verbs, same receipts.

## JSON output

With `--json`, every command prints exactly one JSON object on stdout. Errors are JSON on stdout with a non-zero exit code. Logs go to stderr.
