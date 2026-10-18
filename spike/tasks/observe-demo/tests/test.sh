#!/bin/sh
# Pass if wc.py, sample.txt and a test file exist and the counter runs.
cd /app 2>/dev/null || cd "$(dirname "$0")/.."
mkdir -p /logs/verifier
if [ -f wc.py ] && [ -f sample.txt ] && [ -f tests/test_wc.py ] && python3 wc.py sample.txt >/dev/null 2>&1; then
  echo 1 > /logs/verifier/reward.txt
else
  echo 0 > /logs/verifier/reward.txt
fi
