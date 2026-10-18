#!/bin/sh
# Pass if VERSION.txt in the workspace holds a version-like string (the lookup happened and was recorded;
# the web part is judged from web.jsonl).
cd /app 2>/dev/null || cd "$(dirname "$0")/.."
mkdir -p /logs/verifier
if [ -f VERSION.txt ] && grep -Eq '^[0-9]+\.[0-9]+(\.[0-9]+)?[[:space:]]*$' VERSION.txt; then
  echo 1 > /logs/verifier/reward.txt
else
  echo 0 > /logs/verifier/reward.txt
fi
