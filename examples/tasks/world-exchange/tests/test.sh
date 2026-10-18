#!/bin/sh
# Partial credit from the world's state (ADR-0028). /logs/world.json is the board, the wiki, the chat, the
# ledger and the repositories as they stood when this agent's turn ended, with `viewer` naming the agent
# under verification. Two checkpoints, each worth half: the notes file in the workspace, and a board post
# (or reply) by this agent. Plain shell: the verifier image has neither python nor jq.
cd /app 2>/dev/null || cd "$(dirname "$0")/.."
mkdir -p /logs/verifier
notes=false
post=false
if ls notes-*.md >/dev/null 2>&1 || ls project/notes-*.md >/dev/null 2>&1; then
  notes=true
fi
if [ -f /logs/world.json ]; then
  me=$(sed -n 's/.*"viewer": {"agent_id": "[^"]*", "username": "\([^"]*\)".*/\1/p' /logs/world.json)
  if [ -n "$me" ] && grep -q "\"author\": \"$me\"" /logs/world.json; then
    post=true
  fi
fi
printf '{"notes_file": %s, "board_post": %s}\n' "$notes" "$post" > /logs/verifier/checkpoints.json
