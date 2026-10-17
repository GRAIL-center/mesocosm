#!/bin/sh
# Verifier: reward 1 if hello.txt has the expected line, else 0. Writes the Harbor reward file.
mkdir -p /logs/verifier
if [ "$(cat /app/hello.txt 2>/dev/null)" = "Hello, Mesocosm!" ]; then
  echo 1 > /logs/verifier/reward.txt
else
  echo 0 > /logs/verifier/reward.txt
fi
