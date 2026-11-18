#!/usr/bin/env bash
# Fetch the published repository at the pinned commit and build its 2024 environment (Python 3.10,
# torch 2.4.0) inside this directory. Needs a CUDA GPU with about 40 GB for the three models.
set -euo pipefail
cd "$(dirname "$0")"
[ -d repo ] || git clone -q https://github.com/olliematthews/lasr-steganography.git repo
git -C repo checkout -q 905b3f3eac0f31c6fc5ff65380216b6b9248875f
export UV_PYTHON_INSTALL_DIR="$PWD/python" UV_PYTHON_PREFERENCE=only-managed
export UV_HTTP_TIMEOUT="${UV_HTTP_TIMEOUT:-600}"   # the torch wheel does not download within uv's 30 seconds
uv python install -q 3.10
uv venv -q --python 3.10 .venv
uv pip install -q --python .venv/bin/python -r requirements.txt
.venv/bin/python -c "import torch, trl, vllm; print('environment ready: torch', torch.__version__, 'trl', trl.__version__, 'cuda', torch.cuda.is_available())"
