#!/bin/bash
# usage: run_codex_once.sh <name> <prompt-file> <cwd>
NAME="$1"; PROMPT="$2"; DIR="$3"
S="/private/tmp/claude-501/-Users-seunghyun-Library-Application-Support-Claude-scratch-workspaces-d8062f15-e2ea-45f3-8fb1-ffb3d617452d-e736d814-e3e1-499b-ad20-8423e0c79efe-scratch-2026-11-08-531c0d/38c34c47-5d3d-4041-b34d-ee3f932d859d/scratchpad"
CX="/Applications/ChatGPT.app/Contents/Resources/codex-cli/bin/codex"
OUT="$S/op/distill/$NAME"; mkdir -p "$OUT"; cd "$DIR"
"$CX" exec --ignore-user-config --skip-git-repo-check -m gpt-6.1-sol -c model_reasoning_effort='"high"' -c web_search='"disabled"' \
  --disable plugins --disable apps --disable browser_use --disable computer_use --disable in_app_browser \
  -s read-only -C "$DIR" --json -o "$OUT/answer.txt" "$(cat "$PROMPT")" </dev/null >"$OUT/events.jsonl" 2>"$OUT/err.txt"
