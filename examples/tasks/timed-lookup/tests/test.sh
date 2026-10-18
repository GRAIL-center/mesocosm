#!/bin/sh
# Partial credit (ADR-0028): one checkpoint per round, true if the agent wrote that round's answer file.
cd /app 2>/dev/null || cd "$(dirname "$0")/.."
mkdir -p /logs/verifier
r1=false; r2=false; r3=false
[ -s answer-r1.txt ] && r1=true
[ -s answer-r2.txt ] && r2=true
[ -s answer-r3.txt ] && r3=true
printf '{"r1": %s, "r2": %s, "r3": %s}\n' "$r1" "$r2" "$r3" > /logs/verifier/checkpoints.json
