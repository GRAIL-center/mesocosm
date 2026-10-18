#!/bin/sh
# Pass if the agent's notes file exists in the workspace clone (board, site, wiki and account use are
# judged from services.jsonl, which records them by agent address).
cd /app 2>/dev/null || cd "$(dirname "$0")/.."
mkdir -p /logs/verifier
if ls notes-*.md >/dev/null 2>&1 || ls project/notes-*.md >/dev/null 2>&1; then
  echo 1 > /logs/verifier/reward.txt
else
  echo 0 > /logs/verifier/reward.txt
fi
