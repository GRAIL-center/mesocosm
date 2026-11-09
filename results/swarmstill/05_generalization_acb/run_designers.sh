#!/bin/bash
# Launch the 24 frozen-prompt designer sessions (GPT-6.1 Sol in Codex, read-only, no web, empty working dir).
# usage: run_designers.sh [name ...]   (default: all gen-{N,F}-{idr,rtd,cpr,clc}-{1,2,3}); skips sessions with an answer.
S="/private/tmp/claude-501/-Users-seunghyun-Library-Application-Support-Claude-scratch-workspaces-d8062f15-e2ea-45f3-8fb1-ffb3d617452d-e736d814-e3e1-499b-ad20-8423e0c79efe-scratch-2026-11-08-531c0d/38c34c47-5d3d-4041-b34d-ee3f932d859d/scratchpad"
names=("$@")
if [ ${#names[@]} -eq 0 ]; then
  for arm in N F; do for m in idr rtd cpr clc; do for k in 1 2 3; do names+=("gen-$arm-$m-$k"); done; done; done
fi
for n in "${names[@]}"; do
  IFS=- read -r _ arm m k <<< "$n"
  [ -s "$S/op/distill/$n/answer.txt" ] && continue
  mkdir -p "$S/gen/empty/$n"
  bash "$S/scripts/run_codex_once.sh" "$n" "$S/gen/prompts/prompt_${arm}_${m}.md" "$S/gen/empty/$n" &
  sleep 2
done
wait
echo "DESIGNERS_DONE $(date -u +%H:%M)"
