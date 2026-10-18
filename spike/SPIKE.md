# M0 spike runbook

Goal: before writing the platform, find out which of our riskiest assumptions hold on one workstation. Hardware: one RTX 3090 (24 GB), Docker, Python 3.11–3.13. Model: `Qwen/Qwen3-4B-Instruct-2507` (ADR-0007; the 3B Coder model from ADR-0006 cannot drive OpenCode's tools).

Steps 1–5 were run on a Colab L4 on 2026-10-17 with `spike/colab_smoke.py`; results are in issues #1–#5 and `docs/capability-matrix.md`. Steps 6–7 ran on a Colab L4 on 2026-10-18 with rootless Docker (`spike/colab_docker_setup.sh`; the gateway reaches the host's vLLM at 10.0.2.2, so render the topology with `--vllm-host 10.0.2.2`); results are in issues #6–#7.

Order follows risk. Day 1 is host-only (no Docker). Day 2 adds the isolation topology and Harbor. Fill the result tables at the bottom as you go; they become the first evidence rows of `docs/capability-matrix.md`.

Everything the spike writes goes under `runs/` (git-ignored).

## Day 1: host only

### 0. Environments

Two Python environments: vLLM pins torch and would fight with the project's deps.

```bash
# project env (gateway, driver, tests)
uv sync --extra dev --extra gateway

# vLLM env (also used for the NNsight probe, which needs nnsight[vllm])
uv venv .venv-vllm --python 3.12
source .venv-vllm/bin/activate
uv pip install "vllm==0.31.0"             # the version the Colab run validated
uv pip install "nnsight>=0.8.0" "vllm==0.31.0" safetensors   # the vLLM wrapper worked on 0.31.0
deactivate

# OpenCode (host)
npm install -g opencode-ai     # or: curl -fsSL https://opencode.ai/install | bash
opencode --version
```

Record: vLLM version, nnsight version, torch version, OpenCode version, CUDA driver.

### 1. Serve the model with vLLM

```bash
source .venv-vllm/bin/activate
./spike/01_vllm_serve.sh           # :8000, api key mesocosm-local, hermes tool parser
curl -s -H 'Authorization: Bearer mesocosm-local' localhost:8000/v1/models | jq .
```

Record: startup time, VRAM used (`nvidia-smi`), max_model_len accepted, any tool-parser warnings.

### 2. Start the gateway

```bash
MESOCOSM_LOG_DIR=runs/spike \
MESOCOSM_RETURN_TOKEN_IDS=1 \
MESOCOSM_BUDGET_JSON='{"agent-1": {"completion_tokens": 40000, "requests": 200}}' \
uv run python spike/02_gateway.py          # :8080
curl -s localhost:8080/mesocosm/health | jq .
```

Smoke-test the proxy without OpenCode (a tool-call request):

```bash
curl -s localhost:8080/v1/chat/completions -H 'Authorization: Bearer mesocosm-local' \
  -H 'X-Mesocosm-Agent: curl' -H 'Content-Type: application/json' -d '{
  "model":"mesocosm-3b","temperature":0,
  "messages":[{"role":"user","content":"What is in /tmp? Use the tool."}],
  "tools":[{"type":"function","function":{"name":"bash","description":"run a shell command",
    "parameters":{"type":"object","properties":{"cmd":{"type":"string"}},"required":["cmd"]}}}]}' | jq .
tail -1 runs/spike/gateway.jsonl | jq '.response | {tool_calls, usage, has_ids: (.prompt_token_ids != null)}'
```

Record: did the model emit a structured `tool_calls` (hermes parser working)? Does the response carry `prompt_token_ids` / `token_ids` (vLLM `return_token_ids`)? If vLLM rejects `return_token_ids`, restart the gateway with `MESOCOSM_RETURN_TOKEN_IDS=0` and note that exact token ids need another route (M5).

### 3. Configure OpenCode and start its server

```bash
cd spike/workspace/agent-1
cp ../../03_opencode.json ./opencode.json
export MESOCOSM_GATEWAY_KEY=mesocosm-local
opencode serve --port 4096                  # project dir = this workspace
```

In another shell: `curl -s localhost:4096/config/providers | jq '.providers[].id'` should list `mesocosm`.

Record: does OpenCode accept the custom provider? Does `/config/providers` show the model? Any auth prompt?

### 4. Drive OpenCode and check the exposure record

```bash
uv run python spike/04_drive_opencode.py --url http://127.0.0.1:4096 --out runs/spike --run spike-host
```

Expected: the task completes with tool calls (bash/edit), `opencode_events.jsonl` fills up, and the gateway table lists every model request with message counts, tool names and token usage. The last block prints the system prompt the model actually received.

The same path through the control plane (manifest → egress check → budgets → task → verifier → run bundle) is `uv run mesocosm run examples/spike-world.yaml --json`; it needs the Docker topology of step 6 because it refuses to start without a passing egress check.

Record: number of model calls for the task; tool-call success (did `hello.py` and `NOTES.md` appear in the workspace?); whether all calls carry `X-Mesocosm-Agent: agent-1`; event types seen; any permission stalls (if a request waits on a permission, `POST /session/{id}/permissions/{permissionID}` answers it; `"permission": "allow"` in opencode.json should prevent it).

Then exhaust the budget on purpose: restart the gateway with `{"agent-1": {"completion_tokens": 500}}`, run step 4 again, and confirm the 429 appears in `gateway.jsonl` as `budget_exhausted` and that OpenCode surfaces an error instead of hanging. This is the first enforcement-point receipt.

### 5. Activations: capture and recompute

Stop `vllm serve` (both engines want the GPU). Then, in the vLLM env:

```bash
source .venv-vllm/bin/activate
python spike/05_nnsight_probe.py hf      --model Qwen/Qwen3-4B-Instruct-2507
python spike/05_nnsight_probe.py vllm    --model Qwen/Qwen3-4B-Instruct-2507 --gpu-mem 0.6
python spike/05_nnsight_probe.py compare
```

What it does: takes the last gateway record, teacher-forces the exact tokens through transformers+NNsight (layer L over the prompt, greedy agreement with the recorded completion), then traces the same prompt through NNsight's vLLM wrapper (prefill activations at layer L, first sampled tokens), then compares.

Record: whether `nnsight.modeling.vllm` imports against the installed vLLM; `(hidden, residual)` pair vs tensor at the layer output; shapes; greedy agreement; rel-L2 and cosine between engines; capture overhead ratio; VRAM. Also note: NNsight's wrapper builds its *own* vLLM engine (in-process `LLM`), it does not hook a running `vllm serve`. For M5 the candidates are (a) serving through `nnsight-serve` (`nnsight.modeling.vllm.serve`, to be read), or (b) capture during execution via a vLLM plugin, or (c) recompute-only. Which one is feasible is the main M5 decision.

## Day 2: isolation and tasks

### 6. Isolation topology in Docker

Keep `vllm serve` running on the host (restart it after step 5).

```bash
docker compose -f spike/docker/compose.yml up -d --build
docker compose -f spike/docker/compose.yml exec agent-1 egress-check
docker compose -f spike/docker/compose.yml exec control \
    python 04_drive_opencode.py --url http://agent-1:4096 --out /runs/spike --run spike-docker
docker compose -f spike/docker/compose.yml logs gateway | tail -20
docker compose -f spike/docker/compose.yml down
```

Record each line of `egress-check` (gateway reachable; HTTPS/raw-IP/ICMP blocked; DNS resolves or not; inference server not directly reachable). If external DNS resolves on the internal network, M1 adds a DNS sinkhole. Record whether `host.docker.internal` works for the gateway on Linux (needs `extra_hosts`, already set).

### 7. Harbor: one task, three attempts

```bash
uv tool install harbor
./spike/06_harbor_task.sh
```

Record: job directory layout (where `reward.txt`, agent logs and `trajectory.json` land); whether the pre-integrated `opencode` agent reached our gateway from inside Harbor's sandbox (`harbor-1` records in `gateway.jsonl`); which Terminal-Bench task you ran and how long the environment build took.

## Results

Copy these into `docs/capability-matrix.md` with the evidence column pointing at files under `runs/spike/` (summaries only; do not commit run data).

### Versions (Colab L4, 2026-10-17)

| Component | Version | Notes |
|---|---|---|
| vLLM | 0.31.0 | Python 3.13.15; pulled torch 2.13.0+cu130 |
| nnsight | 0.8.0 | `nnsight[vllm]`; wrapper works on vLLM 0.31 |
| torch / CUDA | 2.13.0+cu130 / 13.0 | driver 580.82.07 |
| transformers | 5.17.0 | |
| OpenCode | 1.19.3 | Node 20.19.0 |
| Harbor | 0.24.0 | Colab L4 2026-10-18, `uv tool install harbor` |
| Docker | 29.9.1 rootless | Colab L4 2026-10-18; fuse-overlayfs, slirp4netns, runc wrapper (`spike/colab_runc_wrapper.py`) |

### Checks

| # | Check | Result | Evidence |
|---|---|---|---|
| 1 | vLLM serves the small model with the hermes tool parser | pass for `Qwen3-4B-Instruct-2507` and `Qwen2.5-3B-Instruct`; **fail** for `Qwen2.5-Coder-3B-Instruct` (fenced JSON, no tags); `--max-model-len` must be ≥ 32768 | #1 |
| 2 | Gateway proxies streaming and non-streaming requests | pass; error bodies of streaming requests were lost before 573835c | #2 |
| 2 | `return_token_ids` accepted; prompt/completion ids in records | pass (streaming and non-streaming) | #2 |
| 3 | OpenCode accepts the custom provider and headers | pass; 15 event types observed | #3 |
| 4 | Task completes with tool calls; every call tagged agent-1 | pass with the 4B model (2 calls, 9.9 s, hello.py + NOTES.md); 3B models did not complete | #4 |
| 4 | Budget exhaustion returns 429 and is recorded | pass; receipt returned; OpenCode surfaces `session.error`; a request budget also stopped a 200-request compaction loop | #4, #2 |
| 5 | `nnsight.modeling.vllm` imports and traces the model | pass (48 s engine build, 0.87 s trace) | #5 |
| 5 | Transformers vs vLLM activations: rel-L2 / cosine | rel-L2 mean 0.020, max 0.029; cosine min 0.9996 (after cloning served values) | #5 |
| 5 | Capture overhead ratio | 1.48× at batch 1, eager | #5 |
| 6 | Agent container: no HTTPS / raw-IP / ICMP egress | pass in both agents of `examples/docker-world.yaml` | #6 |
| 6 | External DNS resolution from the sandbox | does not resolve on the internal network (Docker 29.9.1); no sinkhole needed | #6 |
| 6 | Agent reaches gateway only; not the inference server | pass; task completed through gateway container → host vLLM (2 calls, write/bash/write) | #6 |
| 6 | `isolate_agents`: peers unreachable when on, reachable when off | pass (both variants) | #6 |
| 7 | Harbor task runs; job layout understood | not run (workstation) | |
| 7 | Harbor `opencode` agent reaches the gateway | not run (workstation) | |

### Decisions this spike feeds

- Pilot model family (tool-call quality of Qwen2.5-Coder at 3B is a floor, not the pilot choice).
- M5 capture route: `nnsight-serve`, vLLM plugin, or recompute-only.
- Whether the gateway or vLLM is the source of exact token ids.
- DNS sinkhole requirement for M1.
- Whether Harbor's sandbox can reach the gateway, or whether tasks must be imported into our own sandbox (M1 task adapter design). Answer: it can, with the provider injected; Harbor's task container itself has internet access (its default network mode), so for isolated runs tasks are imported into our sandbox and the verifier runs afterwards in a container that may have egress.
