#!/usr/bin/env bash
# Spike step 1: serve a 3B open-weight model with vLLM on the local RTX 3090 (ADR-0006).
#
# Usage:  ./01_vllm_serve.sh            # default model
#         MESOCOSM_MODEL=Qwen/Qwen2.5-3B-Instruct ./01_vllm_serve.sh
#
# Run this inside the vLLM environment (see SPIKE.md, step 0). The gateway (step 2)
# points at http://127.0.0.1:8000; OpenCode never talks to this port directly.
set -euo pipefail

MODEL="${MESOCOSM_MODEL:-Qwen/Qwen3-4B-Instruct-2507}"   # ADR-0007
SERVED_NAME="${MESOCOSM_SERVED_NAME:-qwen3-4b}"
PORT="${MESOCOSM_VLLM_PORT:-8000}"
API_KEY="${VLLM_API_KEY:-mesocosm-local}"
MAX_LEN="${MESOCOSM_MAX_MODEL_LEN:-32768}"
GPU_UTIL="${MESOCOSM_GPU_UTIL:-0.85}"

# Tool-call parser per model family (vLLM docs/features/tool_calling.md):
#   Qwen2.5-*, Qwen3-*   -> hermes   (tool template ships in tokenizer_config.json)
#   Llama-3.2-*-Instruct -> pythonic + examples/tool_chat_template_llama3.2_pythonic.jinja
# Note: Qwen2.5-Coder-3B emits fenced JSON instead of <tool_call> tags and does not work (ADR-0007).
case "$MODEL" in
  Qwen/Qwen2.5-*|Qwen/Qwen3-*) PARSER_ARGS=(--tool-call-parser hermes) ;;
  meta-llama/Llama-3.2-*)
    TEMPLATE="${MESOCOSM_CHAT_TEMPLATE:?set MESOCOSM_CHAT_TEMPLATE to vllm/examples/tool_chat_template_llama3.2_pythonic.jinja}"
    PARSER_ARGS=(--tool-call-parser pythonic --chat-template "$TEMPLATE") ;;
  *) PARSER_ARGS=(--tool-call-parser "${MESOCOSM_TOOL_PARSER:-hermes}") ;;
esac

echo "model=$MODEL served_as=$SERVED_NAME port=$PORT max_len=$MAX_LEN" >&2

exec vllm serve "$MODEL" \
  --host 127.0.0.1 --port "$PORT" \
  --served-model-name "$SERVED_NAME" \
  --api-key "$API_KEY" \
  --dtype bfloat16 \
  --max-model-len "$MAX_LEN" \
  --gpu-memory-utilization "$GPU_UTIL" \
  --enable-auto-tool-choice "${PARSER_ARGS[@]}" \
  "$@"
