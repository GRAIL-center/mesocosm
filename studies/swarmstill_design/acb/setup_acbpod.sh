#!/bin/bash
# ACB design pod: the same student as the collusion study (Qwen3-4B-Instruct-2507 served as qwen3-4b, bf16),
# vLLM 0.33.0, AgentCollabBench f016f60. Log: /workspace/setup.log
exec > /workspace/setup.log 2>&1
set -x
cd /workspace
export HF_HOME=/workspace/hf CUDA_HOME=/usr/local/cuda PATH=/usr/local/cuda/bin:$PATH
uv venv -q --python 3.12 /workspace/vllm-env && VIRTUAL_ENV=/workspace/vllm-env uv pip install -q "vllm==0.33.0" "transformers==5.20.2" ninja
git clone -q https://github.com/aritra741/AgentCollabBench.git /workspace/AgentCollabBench && (cd /workspace/AgentCollabBench && git checkout -q f016f600568b6d8127dc861e4c83c87b72750d63)
uv venv -q --python 3.12 /workspace/acb-env && VIRTUAL_ENV=/workspace/acb-env uv pip install -q -e /workspace/AgentCollabBench
/workspace/vllm-env/bin/hf download Qwen/Qwen3-4B-Instruct-2507 > /workspace/dl.log 2>&1 || echo DOWNLOAD_FAILED
mkdir -p /workspace/study /workspace/design/runs
PATH=/workspace/vllm-env/bin:$PATH nohup vllm serve Qwen/Qwen3-4B-Instruct-2507 --served-model-name qwen3-4b --port 8000 \
  --max-model-len 131072 --gpu-memory-utilization 0.90 --enable-auto-tool-choice --tool-call-parser hermes \
  --enable-prefix-caching --max-num-seqs 64 > /workspace/vllm.log 2>&1 &
for i in $(seq 1 120); do curl -s -m 3 http://127.0.0.1:8000/v1/models | grep -q qwen3-4b && break; sleep 5; done
curl -s -m 3 http://127.0.0.1:8000/v1/models | grep -q qwen3-4b && echo "READY qwen3-4b" || { echo "FAILED"; tail -20 /workspace/vllm.log; }
echo SETUP_DONE
