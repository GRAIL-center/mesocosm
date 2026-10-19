# Architecture

This document describes the target architecture. Items marked *(M-n)* name the milestone in which they are first built; see `docs/milestones.md`.

## Design principles

1. **One chokepoint for model traffic.** Every model request from every agent passes through the model gateway. The gateway is where we learn exactly what the model received, where token budgets are enforced, and where activation capture is keyed. Nothing about the harness has to be modified for this to work.
2. **Harnesses are used as shipped.** OpenCode (and later OpenHands, Codex CLI) run as they do in deployment, with their own tools and context management. The platform observes from outside (gateway, environment services, harness event streams) and never patches harness internals.
3. **Conditions are enforced where they take effect, and leave receipts.** A resource limit is enforced by the gateway, a channel rule by the service that serves the channel, a memory policy by the workspace manager. Each applied condition records the before/after configuration and the event boundary at which it became effective. A condition the platform cannot enforce fails explicitly instead of being approximated by a prompt instruction.
4. **Two run modes over one infrastructure.** Comparison runs (reset, randomized conditions) and persistent operation (task stream, carried state) share the same components. Snapshots declare what they contain.
5. **Behavior-only by default.** Activation capture is enabled per run. Most runs record behavior only.
6. **Separate authority.** Experimental agents live inside the sandbox and have no control-plane credentials. Research agents call the operation contract with scoped tokens, inside platform-enforced budgets and isolation policy.

## Components

```
                 control network (edge)                      sandbox network (internal, no egress)
 ┌───────────────────────────────────────────┐   ┌──────────────────────────────────────────────────┐
 │ control plane (mesocosm.core, cli)        │   │  agent-1 ──┐    agent-2 ──┐    agent-N ──┐        │
 │  manifests · run manager · scheduler      │   │  OpenCode  │    OpenCode  │    OpenCode  │        │
 │  conditions + receipts · snapshots        │   │  server    │    server    │    server    │        │
 │  record store (append-only)               │   │   │ events │     │        │     │        │        │
 │                                           │   │   ▼        ▼     ▼        ▼     ▼        ▼        │
 │ inference: vLLM + NNsight (GPU)   ◄───────┼───┼─ model gateway (two NICs) ◄── shared services:    │
 │  activation capture · recompute           │   │   exposure log · budgets      board · repo · wiki │
 │                                           │   │   request ids                 web gateway · pypi  │
 │ commercial baselines (API, outside)       │   │                               read/write logs     │
 └───────────────────────────────────────────┘   └──────────────────────────────────────────────────┘
```

### Control plane — `mesocosm.core` *(M1–M3, M6)*

- **World manifest** (YAML): tasks, agents (harness + model), services, channels, memory policy, conditions, budgets, run mode. Versioned and validated before a run.
- **Run manager**: start, pause (where supported), resume, stop; automatic chaining of runs; failure recovery. A *run* is one world instance from start to end under fixed conditions.
- **Scheduler**: supplies tasks from task adapters; in persistent operation it keeps the queue non-empty.
- **Condition enforcer**: translates manifest conditions into actions on the gateway, services and workspaces and records a *receipt* (previous value, new value, effective event id, enforcement result).
- **Snapshot manager** *(M6)*: saves declared components (workspace filesystems, service databases, agent memory, pending messages, task assignments, model version) and marks what is not captured (live process state). `fork` creates a new run from a snapshot with an edited manifest.
- **Record store**: append-only event log plus blob store for native harness logs and activations. Keys: `run_id`, `agent_id`, `step`, `request_id`. Not reachable from the sandbox network.

### Model gateway — `mesocosm.gateway` *(M1)*

An authenticated OpenAI-compatible chat gateway in front of vLLM and configured API-model upstreams.

- Identifies each agent by the configured gateway key in its harness config (one key per agent and run label, ADR-0019). Control-plane callers may supply `X-Mesocosm-Agent` / `X-Mesocosm-Run` headers only with the control key; missing or unknown keys receive 401 (ADR-0043). Upstream requests carry `X-Mesocosm-Request-Id`, never the agent key or identity headers.
- Exposes authenticated `POST /v1/chat/completions` and `GET`/`HEAD /v1/models` only. Other upstream inference and management paths are refused and logged; adding an inference API requires the same enforcement and exposure records (ADR-0043).
- Logs full chat requests (messages, tools, sampling parameters) and responses (text, tool calls, usage, and token ids when vLLM supports them), including partial exposure on stream disconnects. This is the *actual information exposure* record: evidence of what the model was given.
- Serializes requests from one agent through response completion while other agents proceed concurrently. Request attempts consume their budget before reaching the upstream; completion output limits are capped by the remaining allowance. Prompt usage is checked at response boundaries and one prompt may cross its threshold. Missing usage refuses subsequent token-budgeted requests. Quota refusals use the API's ordinary error shape; `/admin/*` (budgets, pause, resume, stop, records) answers the control key only (ADR-0043).
- Routes the requested model id through one gateway to a fixed upstream from the manifest's model catalog, including OpenAI-compatible commercial baselines (ADR-0029). Provider credentials stay in the gateway's environment, outside agent containers.

### Inference and observation — `mesocosm.observe` *(M5)*

- vLLM serves the open-weight model. NNsight hooks capture activations at selected layers and token positions, keyed by request id.
- Capture policy: per-step summary features (e.g. norms, probe scores) always; raw tensors only for *suspicious windows* flagged by detectors or requested by the researcher.
- Recompute service: re-runs recorded token sequences through the same weights to regenerate activations that were not stored, and reports the numerical difference from originals captured during execution. Original capture, later recomputation and a fresh rollout are three distinct operations and are labeled as such.
- Live view *(v0 shipped)*: `mesocosm observe serve` tails the run bundle and the gateway log and shows every agent as a node with its session tree (subagents), a one-line rule-based summary per node, and a drill-down to the exact model request, its reasoning field, tool calls and the timeline. Read-only, bounded, same code for live and replay (ADR-0008).

### Sandbox and services — `mesocosm.env` *(M1–M2)*

- Per-agent container: harness server + workspace, attached only to the internal sandbox network. With `isolate_agents: true` every agent gets its own internal network, so agents cannot reach each other; only the gateway and the control node span all of them.
- Shared services inside the sandbox *(v0 shipped, ADR-0010)*: official message board (`board`), shared git repositories (`git`), an "old wiki" whose pages can appear at a set time (`wiki`), an offline package index so `pip install` works (`pypi`), and the hidden `pastebin` channel when the manifest enables it. One container, one hostname per service, fixed addresses; every read and write is logged by agent identity derived from the address (environment-level exposure), complementing the gateway's model-level exposure. Fictional accounts and credential traps come with M4.
- Egress verifier runs before every run and fails the run if any outbound path exists other than the web gateway when the manifest enables it (and confirms the gateway polices: a denied host answers 403, an allowed one is reachable). The web gateway's log joins the bundle (`web.request` events).

### Harness adapters — `mesocosm.adapters.opencode` *(M1)*

- Drives OpenCode through its HTTP server (`/session`, `/session/{id}/message`, `/event` SSE). Normalizes events to the common schema while preserving native logs.
- Publishes a **capability matrix** row set: which controls (budgets, memory reset, tool allow-list, model swap) and which observations (events, tool calls, permissions) the adapter supports, with evidence.

### Task adapters — `mesocosm.adapters.harbor` *(M1, M6)*

- Imports Harbor datasets (Terminal-Bench 2.0, SWE-bench) and their verifiers; records task provenance.
- Marks tasks as *adapted* when run under shared workspaces, persistent memory or altered budgets, so scores are not presented as official benchmark results.
- Provides the task stream for persistent operation.

### Judging and catalog — `mesocosm.judge` *(M4)*

- Automatic flags from traps (honeypot credentials, planted loopholes) and environment-state checks (test tampering, unauthorized channel use, unexpected service writes).
- LLM judges using a versioned behavior catalog (Ethogram). Within an operating period the catalog and criteria are frozen; confirmed new behaviors enter the next version.
- Human review queue; dataset bundler with redaction and canary strings.

### Training export *(M8, validation only)*

Records can be exported in an OpenEnv `TrainingTrace`-compatible form: exact token sequences, action masks, reward version, policy version. No trainer runs inside Mesocosm.

## Identifiers

Every record carries `run_id`, `agent_id`, `step` and, for model calls, `request_id`. The gateway, the harness adapter and the services all stamp these, so an action, the message that preceded it, the model request that produced it and the activations of that request can be joined without heuristics.

## Networks and authority

- `sandbox` (Docker `internal: true`): agents and shared services. No default route, no DNS to the outside. With `internet.enabled` the web gateway (`proxy`, ADR-0022) sits on it too and is the agents' only route out: a policed, logging proxy whose policy is verified like a condition.
- `edge`: control plane, inference server, record store. The gateway is attached to both.
- Experimental agents hold no credentials beyond the gateway API key, which grants model access only.
- Research agents call the operation contract with scoped tokens; budgets and isolation policy are enforced by the platform, not by the agent's instructions.

## What is deliberately not here

- No modified harness forks.
- No trainer. Training happens elsewhere on exported records.
- No mock services and no scripted actors (ADR-0024). Agents reach the world's real services, each other, and the real internet only through the web gateway's policy and log (ADR-0022).
