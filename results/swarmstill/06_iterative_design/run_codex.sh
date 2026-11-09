#!/bin/bash
# Launch one GPT-6 Astra designer session under Codex CLI: workspace-write sandbox (network off), no user config,
# no plugins/apps/browser/computer-use/web search. Full event log goes to logs/codex_<name>.jsonl.
WS=$1; NAME=$2; LOG=$(cd "$(dirname "$0")" && pwd)/logs
C=/Applications/ChatGPT.app/Contents/Resources/codex-cli/bin/codex
cd "$WS" || exit 1
echo "start $(date -u +%FT%TZ)" > "$LOG/codex_$NAME.status"
"$C" exec -m gpt-6-astra -c model_reasoning_effort='"high"' -s workspace-write --skip-git-repo-check \
  --ignore-user-config --ignore-rules --disable apps --disable browser_use --disable browser_use_external \
  --disable computer_use --disable plugins --disable remote_plugin --disable multi_agent --disable image_generation \
  --disable in_app_browser -c web_search='"disabled"' --json "$(cat PROMPT.md)" < /dev/null \
  > "$LOG/codex_$NAME.jsonl" 2> "$LOG/codex_$NAME.err"
echo "exit $? $(date -u +%FT%TZ)" >> "$LOG/codex_$NAME.status"
