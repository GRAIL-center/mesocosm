# ADR-0007: Smoke-test model is Qwen3-4B-Instruct-2507, served with a 32k context

Status: accepted · Date: 2026-10-17 · Supersedes the model choice in ADR-0006

## Context

The M0 spike ran on a Colab L4 (vLLM 0.31.0, OpenCode 1.19.3) with three small Qwen models
through the same pipeline (issues #1, #4):

- `Qwen/Qwen2.5-Coder-3B-Instruct` writes tool calls as fenced ```` ```json ```` blocks instead of
  `<tool_call>` tags, so vLLM's hermes parser returns no `tool_calls` and OpenCode never executes a
  tool. It fails at temperature 0 and at OpenCode's default sampling.
- `Qwen/Qwen2.5-3B-Instruct` emits the tags and tools run, but it did not finish the two-file task in
  294 s (`finish_reason: length`).
- `Qwen/Qwen3-4B-Instruct-2507` finished the task in 9.9 s with two model calls.

OpenCode's system prompt plus its 11 tool schemas is 10,799 prompt tokens and OpenCode requests
8,192 output tokens. With `--max-model-len 16384` vLLM rejected every request with 400, and
OpenCode reacted by compacting and retrying until the gateway's request budget stopped it.

## Decision

- Smoke tests and CI-adjacent runs use `Qwen/Qwen3-4B-Instruct-2507` (bf16 on Ampere or newer,
  fp16 on T4), served with `--enable-auto-tool-choice --tool-call-parser hermes --max-model-len 32768`.
- The harness-facing model id stays `mesocosm-3b` so manifests and configs do not change; the id is
  a label for "the small smoke-test model", not a parameter count.
- A 3B model is not required by anything; ADR-0006's "3B" is replaced by "the smallest model that
  completes a tool-using task through OpenCode", and that is currently this 4B model.

## Consequences

- Tool-call format compliance is a model selection criterion, checked with the serve-phase curl in
  `spike/colab_smoke.py` before any run.
- Any vLLM deployment for OpenCode agents needs at least 32k context; the manifest's
  `limit.context` for the harness is set to 32768.
- Behavior observed with the 4B model is for exercising the platform, not for research claims
  (unchanged from ADR-0006).
