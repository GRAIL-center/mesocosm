#!/usr/bin/env bash
# Spike step 6: run one Harbor task to learn the task layout, the verifier, and the job output.
#
# Three attempts, in order of certainty:
#   A) no-op agent          -> exercises environment + verifier only; shows job/trial layout
#   B) opencode via gateway -> Harbor's pre-integrated `opencode` agent, pointed at our gateway
#   C) a Terminal-Bench 2.0 task with the no-op agent (dataset flag)
#
# Requires: Docker running, vLLM (01) and the gateway (02) running on the host for attempt B.
# Install:  uv tool install harbor      (docs: getting-started/installation)
set -uo pipefail

JOBS_DIR="${MESOCOSM_HARBOR_JOBS:-./runs/harbor-jobs}"
GATEWAY_FROM_DOCKER="${MESOCOSM_GATEWAY_FROM_DOCKER:-http://host.docker.internal:8080/v1}"
MODEL_ID="${MESOCOSM_MODEL_ID:-mesocosm-3b}"
mkdir -p "$JOBS_DIR"

command -v harbor >/dev/null || { echo "harbor not found: uv tool install harbor" >&2; exit 1; }
harbor --version || true

echo; echo "=== A) hello-world with the no-op agent ==="
harbor run -t hello-world/hello-world -a nop --jobs-dir "$JOBS_DIR" || echo "A) failed (record the error)"

echo; echo "=== job layout after A ==="
find "$JOBS_DIR" -maxdepth 4 -type f | head -40
echo "(look for: config, trial dirs, verifier reward.txt/reward.json, agent logs, trajectory.json)"

echo; echo "=== B) hello-world with OpenCode routed through the Mesocosm gateway ==="
# Harbor's opencode agent reads OPENAI_API_KEY and OPENAI_BASE_URL for an OpenAI-format provider,
# writes ~/.config/opencode/opencode.json in the task container, and runs `opencode run --format=json`.
# `opencode_config` is deep-merged into that file: we use it to add the agent identity headers.
# The task container must be able to reach the host gateway; if host.docker.internal does not resolve
# inside Harbor's sandbox, record that: it is exactly the network question M1 has to settle.
harbor run -t hello-world/hello-world -a opencode -m "openai/${MODEL_ID}" \
  --ae OPENAI_API_KEY="${VLLM_API_KEY:-mesocosm-local}" \
  --ae OPENAI_BASE_URL="${GATEWAY_FROM_DOCKER}" \
  --ak opencode_config='{"provider":{"openai":{"options":{"headers":{"X-Mesocosm-Agent":"harbor-1","X-Mesocosm-Run":"spike-harbor"}}}}}' \
  --jobs-dir "$JOBS_DIR" || echo "B) failed (record the error; try --ak model_base_url=... if the env route is ignored)"

echo; echo "=== gateway records tagged harbor-1 ==="
grep -c '"agent": "harbor-1"' runs/spike/gateway.jsonl 2>/dev/null || echo "none (gateway not reached from the task container)"

echo; echo "=== C) one Terminal-Bench 2.0 task with the no-op agent ==="
harbor datasets list 2>/dev/null | head -20 || true
# Pick one task name from the dataset listing and uncomment:
# harbor run -d terminal-bench@2.0 -t <task-name> -a nop --jobs-dir "$JOBS_DIR"

echo; echo "=== view ==="
echo "harbor view $JOBS_DIR"
