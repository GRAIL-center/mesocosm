# ADR-0006: Smoke tests run on a local RTX 3090 with a 3B model

Status: accepted · Date: 2026-10-17

## Context

The shared RCAC allocation should be kept for pilot and main runs. A single RTX 3090 (24 GB) is available locally. A 3B instruct model in bf16 takes about 6 GB of VRAM, leaving room for a 32k-token KV cache or for a transformers-based activation probe on the same card.

## Decision

- The spike (M0) and the smoke tests for M1–M3 run on the local RTX 3090 with a 3B open-weight model.
- Default model: `Qwen/Qwen2.5-Coder-3B-Instruct`, served by vLLM with `--enable-auto-tool-choice --tool-call-parser hermes` (Qwen2.5 ships a Hermes-style tool template). Alternate: `Qwen/Qwen2.5-3B-Instruct`. Llama-3.2-3B-Instruct is kept as a fallback only; vLLM's documentation notes that small Llama models struggle with tool calls.
- Model choice for the pilot (mid-size) is decided from spike results: tool-call success rate in OpenCode and NNsight compatibility.

## Consequences

- Behavior observed with a 3B model is for exercising the platform, not for research claims.
- The CI smoke test uses an even smaller model on CPU; the 3090 is not part of CI.
