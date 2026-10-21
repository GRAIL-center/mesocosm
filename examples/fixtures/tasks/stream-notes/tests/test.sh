#!/bin/sh
cd /app 2>/dev/null || cd "$(dirname "$0")/.."
mkdir -p /logs/verifier
if ls stream-*.md >/dev/null 2>&1; then echo 1 > /logs/verifier/reward.txt; else echo 0 > /logs/verifier/reward.txt; fi
