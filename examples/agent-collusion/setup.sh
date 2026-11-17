#!/usr/bin/env bash
# Fetch the published repository at the pinned commit and build its environment inside this directory,
# interpreter included, so that a contained trial sees everything it needs under one read-only path.
set -euo pipefail
cd "$(dirname "$0")"
[ -d repo ] || git clone -q https://github.com/SALT-NLP/agent-collusion.git repo
git -C repo checkout -q 5afb2c595aa3eff6b9b829a032f4f117b36ee54d
export UV_PYTHON_INSTALL_DIR="$PWD/python" UV_PYTHON_PREFERENCE=only-managed
uv python install -q 3.12
uv venv -q --python 3.12 .venv
uv pip install -q --python .venv/bin/python -r repo/requirements.txt
.venv/bin/python -c "import litellm, openai, pytest; print('environment ready:', litellm.__name__, openai.__version__)"
