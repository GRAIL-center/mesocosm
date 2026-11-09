#!/bin/bash
# Run one designed collusion environment: <out_dir> <n_trajectories> <task_sequence_record> <harness flags...>
# Model settings (M) are fixed to the original environment: Qwen3-4B-Instruct-2507, temperature 1.0, 8192 output tokens.
D=$1; N=$2; REC=$3; shift 3
cd /workspace/agent-collusion
mkdir -p "$D/logs"
for s in $(seq 1 "$N"); do
  .venv/bin/python -m experiments --alice-model openai/qwen3-4b --bob-model openai/qwen3-4b \
    --alice-reasoning-effort default --bob-reasoning-effort default --alice-temperature 1.0 --bob-temperature 1.0 \
    --alice-max-output-tokens 8192 --bob-max-output-tokens 8192 --task-sequence-record "$REC" \
    --start-index "$s" --repeats 1 --no-preflight --output-dir "$D/seq$(printf %02d "$s")" "$@" \
    > "$D/logs/seq$(printf %02d "$s").log" 2>&1 &
done
wait
echo DONE > "$D/DONE"
