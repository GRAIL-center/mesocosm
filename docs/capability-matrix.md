# Capability matrix

What each adapter and each condition actually supports, with evidence. Status values: **supported** (tested, evidence linked), **unsupported** (tested and does not work, or no mechanism), **unverified** (not tested yet). A feature documented upstream is *unverified* until we have run it here.

Update this file in the same PR as the code or spike result that changes a row.

## Harness adapters

| Control / observation | OpenCode (server mode) | Evidence |
|---|---|---|
| Drive a session over HTTP (`POST /session`, `POST /session/{id}/message`) | unverified | spike 04 |
| Event stream (`GET /event` SSE) with tool calls and permissions | unverified | spike 04 |
| Route all model traffic through the gateway (`provider.options.baseURL`) | unverified | spike 03–04 |
| Per-agent request tagging via `provider.options.headers` | unverified | spike 03–04 |
| Run headless without permission prompts (`permission` config) | unverified | spike 04 |
| Tool allow-list per agent | unverified | |
| Memory reset between tasks (new session vs. carried session) | unverified | |
| Model swap without restarting the server | unverified | |
| Native log preservation (message/part records) | unverified | spike 04 |

## Task adapters

| Capability | Harbor | Evidence |
|---|---|---|
| Run a Terminal-Bench 2.0 task locally in Docker | unverified | spike 06 |
| Use the task's verifier independently of Harbor's run loop | unverified | spike 06 |
| Install OpenCode as the agent inside the task container | unverified | spike 06 |
| Import SWE-bench tasks | unverified | |

## Inference and observation

| Capability | vLLM + NNsight (3B, RTX 3090) | Evidence |
|---|---|---|
| OpenAI-compatible serving with automatic tool calls (`hermes` parser) | unverified | spike 01 |
| Return prompt/completion token ids in responses | unverified | spike 02 |
| Capture activations through NNsight's vLLM wrapper | unverified | spike 05 |
| Capture activations through transformers for the same tokens | unverified | spike 05 |
| Recompute activations from recorded tokens; difference within tolerance | unverified | spike 05 |
| Capture overhead (latency, VRAM) | unverified | spike 05 |

## Conditions

| Condition | Enforcement point | Status | Evidence |
|---|---|---|---|
| Completion/prompt token budget per agent | gateway | unverified | spike 02 |
| Request rate limit per agent | gateway | unverified | |
| Hidden channel available / unavailable | sandbox services | unverified | M2 |
| Information exposure timing (when a document becomes readable) | sandbox services | unverified | M2 |
| Memory carried / reset | workspace manager + harness session | unverified | M3 |
| Task difficulty | task adapter | unverified | M3 |

## Isolation

| Check | Status | Evidence |
|---|---|---|
| Agent container has no route to the internet (HTTP, DNS, ICMP) | unverified | spike 07 |
| Agent container reaches the gateway only | unverified | spike 07 |
| Gateway reaches the inference server only | unverified | spike 07 |
