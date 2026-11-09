#!/bin/bash
# One isolated Codex designer session: stage 1 (harness spec), then stage 2 (intervention predictions)
# in the same thread. Usage: run_codex_designer.sh <session-name> <ev|noev>
set -u
NAME="$1"; COND="$2"
S="/private/tmp/claude-501/-Users-seunghyun-Library-Application-Support-Claude-scratch-workspaces-d8062f15-e2ea-45f3-8fb1-ffb3d617452d-e736d814-e3e1-499b-ad20-8423e0c79efe-scratch-2026-11-08-531c0d/38c34c47-5d3d-4041-b34d-ee3f932d859d/scratchpad"
W="/Users/seunghyun/Library/Application Support/Claude/scratch-workspaces/d8062f15-e2ea-45f3-8fb1-ffb3d617452d/e736d814-e3e1-499b-ad20-8423e0c79efe/scratch-2026-11-08-531c0d"
CX="/Applications/ChatGPT.app/Contents/Resources/codex-cli/bin/codex"
DIR="$W/designer/$COND"
OUT="$S/op/runs/$NAME"; mkdir -p "$OUT"
COMMON=(--ignore-user-config --skip-git-repo-check -m gpt-6.1-sol
        -c model_reasoning_effort='"high"' -c web_search='"disabled"'
        --disable plugins --disable apps --disable browser_use --disable computer_use --disable in_app_browser)

date -u +%FT%TZ > "$OUT/start.txt"
cd "$DIR" || exit 1
"$CX" exec "${COMMON[@]}" -s read-only -C "$DIR" --json -o "$OUT/stage1.txt" \
  "$(cat "$S/op/protocol/stage1_$COND.md")" </dev/null >"$OUT/stage1.jsonl" 2>"$OUT/stage1.err"
THREAD=$(grep -m1 '"thread.started"' "$OUT/stage1.jsonl" | sed -E 's/.*"thread_id":"([^"]+)".*/\1/')
echo "$THREAD" > "$OUT/thread.txt"
date -u +%FT%TZ > "$OUT/stage1_done.txt"
if [ -z "$THREAD" ] || [ ! -s "$OUT/stage1.txt" ]; then echo "stage1 failed" > "$OUT/FAILED"; exit 1; fi
"$CX" exec resume "${COMMON[@]}" -c sandbox_mode='"read-only"' --json -o "$OUT/stage2.txt" \
  "$THREAD" "$(cat "$S/op/protocol/stage2.md")" </dev/null >"$OUT/stage2.jsonl" 2>"$OUT/stage2.err"
date -u +%FT%TZ > "$OUT/done.txt"
[ -s "$OUT/stage2.txt" ] || echo "stage2 failed" > "$OUT/FAILED"
