# Capability matrix

What each adapter and each condition actually supports, with evidence. Status values: **supported** (tested, evidence linked), **unsupported** (tested and does not work, or no mechanism), **unverified** (not tested yet). A feature documented upstream is *unverified* until we have run it here.

Update this file in the same PR as the code or spike result that changes a row.

Evidence "Colab L4 2026-10-17" refers to the M0 spike run with `spike/colab_smoke.py` on a Colab L4 (vLLM 0.31.0, NNsight 0.8.0, transformers 5.17.0, torch 2.13.0+cu130, OpenCode 1.19.3, Python 3.13.15). Excerpts are in the linked issues; raw records (`runs/spike/`) are not committed.

## Harness adapters

| Control / observation | OpenCode (server mode) | Evidence |
|---|---|---|
| Drive a session over HTTP (`POST /session`, `POST /session/{id}/message`) | supported | Colab L4 2026-10-17, #4 |
| Event stream (`GET /event` SSE) with tool calls and permissions | supported (15 event types observed, listed in #3) | Colab L4 2026-10-17, #3 |
| Route all model traffic through the gateway (`provider.options.baseURL`) | supported | Colab L4 2026-10-17, #3 (`/config/providers` lists `mesocosm`), #2 |
| Per-agent request tagging via `provider.options.headers` | supported | Colab L4 2026-10-17, #2 (`X-Mesocosm-Agent` on every record) |
| Run headless without permission prompts (`permission: "allow"`) | supported (one task, one agent) | Colab L4 2026-10-17, #3 |
| Tool allow-list per agent | unverified | |
| Memory reset between tasks (new session vs. carried session) | unverified | |
| Model swap without restarting the server | unverified | |
| Native log preservation (message/part records) | supported (`/session/{id}/message` saved verbatim) | Colab L4 2026-10-17, #4 |

## Task adapters

| Capability | Harbor | Evidence |
|---|---|---|
| Run a Terminal-Bench 2.0 task locally in Docker | unverified (no Docker on Colab) | spike 06 on the workstation |
| Use the task's verifier independently of Harbor's run loop | unverified | spike 06 |
| Install OpenCode as the agent inside the task container | unverified | spike 06 |
| Import SWE-bench tasks | unverified | |

## Inference and observation

| Capability | vLLM + NNsight | Evidence |
|---|---|---|
| OpenAI-compatible serving with automatic tool calls (`hermes` parser) | supported for `Qwen3-4B-Instruct-2507` and `Qwen2.5-3B-Instruct`; **unsupported** for `Qwen2.5-Coder-3B-Instruct` (emits fenced JSON, no tags) | Colab L4 2026-10-17, #1 |
| Context length for OpenCode agents | `--max-model-len 32768` required (prompt is 10.8k tokens + 8192 output) | Colab L4 2026-10-17, #1, #2 |
| Return prompt/completion token ids in responses (`return_token_ids`) | supported, streaming and non-streaming | Colab L4 2026-10-17, #2 |
| Capture activations through NNsight's vLLM wrapper | supported on vLLM 0.31 / NNsight 0.8 (own in-process engine, not a hook into `vllm serve`); values must be cloned inside the trace | Colab L4 2026-10-17, #5 |
| Capture activations through transformers for the same tokens | supported; teacher-forced greedy reproduces the recorded completion exactly (agreement 1.0 at temperature 0) | Colab L4 2026-10-17, #5 |
| Recompute activations from recorded tokens; difference within tolerance | supported across engines at bf16 level: rel-L2 mean 0.020 / max 0.029, cosine ≥ 0.9996, last-logit argmax equal. Same-engine (wrapper vs wrapper) recompute not yet measured | Colab L4 2026-10-17, #5 |
| Capture overhead (latency, VRAM) | measured: trace 0.87 s vs plain generate 0.59 s (1.48×) at batch 1, eager; transformers path 7.6 GB VRAM for the 4B model | Colab L4 2026-10-17, #5 |

## Conditions

| Condition | Enforcement point | Status | Evidence |
|---|---|---|---|
| Completion/prompt token budget per agent | gateway | supported: receipt on set, 429 `mesocosm_budget_exhausted` to the harness, recorded; OpenCode surfaces `session.error` | Colab L4 2026-10-17, #4 |
| Request-count budget per agent | gateway | supported: stopped a 200-request compaction loop | Colab L4 2026-10-17, #2 |
| Hidden channel available / unavailable | sandbox services | unverified | M2 |
| Information exposure timing (when a document becomes readable) | sandbox services | unverified | M2 |
| Memory carried / reset | workspace manager + harness session | unverified | M3 |
| Task difficulty | task adapter | unverified | M3 |

## Isolation

| Check | Status | Evidence |
|---|---|---|
| Agent container has no route to the internet (HTTP, DNS, ICMP) | unverified (no Docker on Colab) | spike 07 on the workstation |
| Agent container reaches the gateway only | unverified | spike 07 |
| Gateway reaches the inference server only | unverified | spike 07 |
| Agents cannot reach each other when `isolate_agents: true` (per-agent networks; `egress-check` peer lines) | unverified | spike 06 |
