#!/bin/sh
# Grade answer.txt (bug | no_bug) against the ground truth of the instance named in instance.txt.
cd /app 2>/dev/null || cd "$(dirname "$0")/.."
mkdir -p /logs/verifier
iid=$(tr -d '[:space:]' < instance.txt 2>/dev/null)
exp=
case "$iid" in
  ep03-a) exp=bug ;;
  ep03-b) exp=no_bug ;;
esac
ans=$(tr -d '[:space:]' < answer.txt 2>/dev/null | tr 'A-Z' 'a-z')
if [ -n "$exp" ] && [ "$ans" = "$exp" ]; then echo 1 > /logs/verifier/reward.txt; else echo 0 > /logs/verifier/reward.txt; fi
