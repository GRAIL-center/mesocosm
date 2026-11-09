#!/bin/bash
# usage: serve_models.sh "<hf_id>:<served_name>:<port>:<gpu_util>" ...   (started one at a time; waits for each)
export HF_HOME=/workspace/hf CUDA_HOME=/usr/local/cuda PATH=/workspace/vllm-env/bin:/usr/local/cuda/bin:$PATH
cd /workspace
for spec in "$@"; do
  IFS=: read -r hf name port util <<< "$spec"
  extra=()
  case "$hf" in
    Qwen/Qwen3-*) extra+=(--default-chat-template-kwargs '{"enable_thinking": false}') ;;
    google/gemma-4*) extra+=(--limit-mm-per-prompt '{"image": 0, "audio": 0, "video": 0}') ;;
  esac
  nohup vllm serve "$hf" --served-model-name "$name" --port "$port" --max-model-len 32768 \
    --gpu-memory-utilization "$util" --enable-prefix-caching --max-num-seqs ${MAXSEQS:-64} ${KVDTYPE:+--kv-cache-dtype $KVDTYPE} "${extra[@]}" \
    > "/workspace/vllm_$name.log" 2>&1 &
  for i in $(seq 1 120); do
    curl -s -m 3 "http://127.0.0.1:$port/v1/models" | grep -q "$name" && break
    sleep 5
  done
  curl -s -m 3 "http://127.0.0.1:$port/v1/models" | grep -q "$name" && echo "READY $name:$port" || { echo "FAILED $name"; tail -20 "/workspace/vllm_$name.log"; }
done
