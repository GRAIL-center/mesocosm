#!/bin/sh
# Render the agent's opencode.json (global config) from environment, then start the OpenCode server.
# Every model request from this agent goes to the gateway with the agent's identity
# in headers (ADR-0003). The agent never learns the inference server's address.
set -eu

: "${MESOCOSM_AGENT:?}"
: "${MESOCOSM_GATEWAY_URL:?}"
MESOCOSM_RUN="${MESOCOSM_RUN:-spike-docker}"
MESOCOSM_MODEL_ID="${MESOCOSM_MODEL_ID:-mesocosm-3b}"
MESOCOSM_GATEWAY_KEY="${MESOCOSM_GATEWAY_KEY:-mesocosm-local}"
MESOCOSM_CONTEXT_LIMIT="${MESOCOSM_CONTEXT_LIMIT:-32768}"

# The config lives in OpenCode's global location, not in the workspace: the workspace belongs to the
# agent and the memory policy may empty it between tasks (ADR-0012).
CONFIG_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/opencode"
mkdir -p /work "$CONFIG_DIR"
cat > "$CONFIG_DIR/opencode.json" <<EOF
{
  "\$schema": "https://opencode.ai/config.json",
  "model": "mesocosm/${MESOCOSM_MODEL_ID}",
  "small_model": "mesocosm/${MESOCOSM_MODEL_ID}",
  "permission": "allow",
  "provider": {
    "mesocosm": {
      "npm": "@ai-sdk/openai-compatible",
      "name": "Mesocosm gateway",
      "options": {
        "baseURL": "${MESOCOSM_GATEWAY_URL}/v1",
        "apiKey": "${MESOCOSM_GATEWAY_KEY}",
        "headers": {
          "X-Mesocosm-Agent": "${MESOCOSM_AGENT}",
          "X-Mesocosm-Run": "${MESOCOSM_RUN}"
        }
      },
      "models": {
        "${MESOCOSM_MODEL_ID}": {
          "name": "${MESOCOSM_MODEL_ID} via gateway",
          "limit": { "context": ${MESOCOSM_CONTEXT_LIMIT}, "output": 8192 }
        }
      }
    }
  }
}
EOF

# Commits to the shared repositories carry the agent id; services identify callers by address anyway.
git config --global user.name "${MESOCOSM_AGENT}" && git config --global user.email "${MESOCOSM_AGENT}@sandbox" \
  && git config --global init.defaultBranch main
echo "[agent-entrypoint] agent=${MESOCOSM_AGENT} run=${MESOCOSM_RUN} gateway=${MESOCOSM_GATEWAY_URL}" >&2
cd /work
exec opencode serve --hostname 0.0.0.0 --port 4096
