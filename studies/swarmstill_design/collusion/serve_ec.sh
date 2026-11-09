#!/bin/bash
export HF_HOME=/workspace/hf CUDA_HOME=/usr/local/cuda PATH=/workspace/vllm-env/bin:/usr/local/cuda/bin:$PATH
cd /workspace
exec vllm serve Qwen/Qwen3-4B-Instruct-2507 --served-model-name qwen3-4b --port 8000 \
  --max-model-len 131072 --gpu-memory-utilization 0.90 --enable-auto-tool-choice \
  --tool-call-parser hermes --enable-prefix-caching --max-num-seqs 64
