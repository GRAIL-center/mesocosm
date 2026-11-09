#!/bin/bash
# usage: run_all.sh "<served_name>:<port>" ...
# Runs every metric x condition for each model with the official runner (acb_run.py); 4 workers per process.
# Outputs: /workspace/acb_runs/<metric>/<condition>/<model>/<task_id>.json ; logs in /workspace/acb_logs/
cd /workspace
mkdir -p /workspace/acb_logs
for spec in "$@"; do
  IFS=: read -r name port <<< "$spec"
  for metric in idr rtd cpr clc; do
    for cond in baseline chain full specific; do
      nohup /workspace/acb-env/bin/python /workspace/study/acb_run.py "/workspace/conditions/$metric/$cond" "$metric" \
        "$name" "http://127.0.0.1:$port/v1" "/workspace/acb_runs/$metric/$cond/$name" --workers ${WORKERS:-4} \
        > "/workspace/acb_logs/${metric}_${cond}_${name}.log" 2>&1 &
    done
  done
done
echo launched
