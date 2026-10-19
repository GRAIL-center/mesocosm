# Three native OpenCode agents on an external task

This recipe runs three independent OpenCode servers on the original Terminal-Bench 2 `fix-git`
environment and verifier. Each agent has its own workspace, session and gateway key; `/shared` is a
real Docker volume. All three use one shared vLLM model replica with separate model contexts.
The manifest enables communication but adds no dialogue, colleague script, answer,
workplace history, credential lure or instruction to cooperate. Whether the agents coordinate is an
observation, not a prerequisite for running the task.

The [Colab A100 acceptance run](native-multiagent-evidence.md) completed this execution path with three
real agents and three original verifier results, all zero. This recipe does not promise task-solving
success or cooperation; record each deployment's evidence separately.
These runs are adapted benchmark runs because the harness, network policy, resources and shared volume
differ from the original benchmark configuration.

## Requirements and pinned inputs

Use a Linux Docker host with Compose v2, Python 3.12, `uv`, Git and an NVIDIA GPU capable of serving the
model. Run the control plane from that host so it can reach the fixed Docker bridge addresses. A macOS
Docker Desktop host cannot normally reach those addresses directly; use the Linux Docker VM or a Linux
workstation for the control plane.

| Input | Pin |
|---|---|
| External task repository | [`harbor-framework/terminal-bench-2`](https://github.com/harbor-framework/terminal-bench-2/tree/2fd12b88aafdd04a52c298e3940bcb189f9766d6/fix-git), commit `2fd12b88aafdd04a52c298e3940bcb189f9766d6` |
| Task image declared by that task | `alexgshaw/fix-git:20251031`; record the pulled image ID/digest, since the tag alone is mutable |
| Model | `Qwen/Qwen3-4B-Instruct-2507`, revision `cdbee75f17c01a7cc42f958dc650907174af0554` |
| Serving environment | Python 3.12, `vllm==0.32.0`, `torch==2.13.0`; record the installed CUDA wheel and driver |
| Harness | `opencode-ai@1.19.3`, installed unchanged by the native runtime |
| Original verifier dependencies | Its unchanged `tests/test.sh` installs uv `0.9.5`, pytest `8.4.1`, pytest-json-ctrf `0.3.5` and Python 3.13 |
| Control plane dependencies | This checkout's `uv.lock`, installed with `uv sync --locked` |

Run all commands from the Mesocosm repository root. Use fresh output directories for a new attempt and
take down any previous world first: the generated stacks use fixed `172.30.*.0/24` subnets.

## Prepare the unchanged external task and private manifest

```bash
uv sync --locked --python 3.12 --extra dev --extra gateway
mkdir -p runs/native-three/tasks
export MESOCOSM_TASKS_ROOT="$PWD/runs/native-three/tasks"

git clone --filter=blob:none --no-checkout \
  https://github.com/harbor-framework/terminal-bench-2.git \
  "$MESOCOSM_TASKS_ROOT/terminal-bench@2.0"
git -C "$MESOCOSM_TASKS_ROOT/terminal-bench@2.0" sparse-checkout set fix-git
git -C "$MESOCOSM_TASKS_ROOT/terminal-bench@2.0" checkout --detach \
  2fd12b88aafdd04a52c298e3940bcb189f9766d6

uv run python - <<'PY'
import pathlib
import secrets
import yaml

spec = yaml.safe_load(pathlib.Path("examples/world-native-three.yaml").read_text())
spec["gateway"]["api_key"] = secrets.token_urlsafe(32)
spec["gateway"]["upstream_key"] = secrets.token_urlsafe(32)
path = pathlib.Path("runs/native-three/world.yaml")
path.write_text(yaml.safe_dump(spec, sort_keys=False))
path.chmod(0o600)
PY

uv run mesocosm world validate runs/native-three/world.yaml --json
git -C "$MESOCOSM_TASKS_ROOT/terminal-bench@2.0" diff --exit-code HEAD -- \
  fix-git/instruction.md fix-git/task.toml fix-git/environment fix-git/tests
```

The dataset reference resolves to `<MESOCOSM_TASKS_ROOT>/terminal-bench@2.0/fix-git`.
Leave its instruction, task configuration, environment and tests unchanged. The task's image contains
the actual broken Git repository; no substitute repository or answer is supplied by this recipe.
The private manifest contains control credentials; it belongs in ignored run storage.

## Start the pinned model

Create a separate serving environment; it is not part of the control plane's lockfile:

```bash
uv venv --python 3.12 .venv-vllm
uv pip install --python .venv-vllm/bin/python 'vllm==0.32.0' 'torch==2.13.0'
export VLLM_API_KEY="$(uv run python -c \
  'import pathlib,yaml; print(yaml.safe_load(pathlib.Path("runs/native-three/world.yaml").read_text())["gateway"]["upstream_key"])')"
source .venv-vllm/bin/activate

vllm serve Qwen/Qwen3-4B-Instruct-2507 \
  --revision cdbee75f17c01a7cc42f958dc650907174af0554 \
  --served-model-name qwen3-4b --host 0.0.0.0 --port 8000 \
  --api-key "$VLLM_API_KEY" --dtype bfloat16 \
  --max-model-len 32768 --max-num-seqs 3 --gpu-memory-utilization 0.85 \
  --enable-auto-tool-choice --tool-call-parser hermes
```

Keep that process running and open another shell in the same repository. The gateway reads the matching
upstream key from the manifest; agents receive distinct inference-only keys, not either control key.
Activating the serving environment matters: lazy CUDA kernel builds invoke tools such as `ninja` through
`PATH`. Launching only the absolute `vllm` executable does not put those tools on the server's path.

After the server announces that it is ready, exercise actual sampling before starting the agents.
`GET /v1/models` alone does not test lazy kernel compilation. This direct operator probe uses nonzero
temperature and `top_p`, stays outside agent gateway records, and is not workload-success evidence:

```bash
uv run python - <<'PY'
import json
import pathlib
import urllib.request
import yaml

key = yaml.safe_load(pathlib.Path("runs/native-three/world.yaml").read_text())["gateway"]["upstream_key"]
body = {"model": "qwen3-4b", "messages": [{"role": "user", "content": "Reply with READY."}],
        "temperature": 0.6, "top_p": 0.95, "max_tokens": 16}
request = urllib.request.Request("http://127.0.0.1:8000/v1/chat/completions",
                                 data=json.dumps(body).encode(),
                                 headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"})
with urllib.request.urlopen(request, timeout=600) as response:
    result = json.load(response)
assert result.get("choices") and result.get("usage", {}).get("completion_tokens", 0) > 0, result
pathlib.Path("runs/native-three/model-warmup.json").write_text(json.dumps(result, indent=2))
print("Model sampling warm-up completed.")
PY
```

Resolve any serving dependency or compilation failure before proceeding; an HTTP listener alone is
insufficient readiness evidence.

## Start Docker and run the task

```bash
export MESOCOSM_TASKS_ROOT="$PWD/runs/native-three/tasks"
mkdir -p spike/workspace/agent-{1,2,3}
uv run mesocosm env compose runs/native-three/world.yaml \
  --run-label native-three --vllm-host host.docker.internal \
  --out runs/native-three/compose.yml --json
docker compose -f runs/native-three/compose.yml up -d --build --wait

uv run mesocosm run runs/native-three/world.yaml \
  --compose-file runs/native-three/compose.yml \
  --vllm-host host.docker.internal --task-runtime native \
  --verifier-network bridge --allow-unenforced-resources \
  --json > runs/native-three/result.json
```

Keep the default workspace root: the generated Compose file mounts `spike/workspace/<agent_id>`.
Create those directories as the control-plane user before Docker starts, so Docker does not create
missing host directories owned by a different user. The control plane must retain permission to reset
the task files; with rootful Docker and a root-native task, run it with suitable host privileges.
The native runtime replaces each bootstrap agent with the task image plus the published harness,
preserves its working directory and files, then verifies it from a filesystem snapshot. It rechecks
agent egress after replacement. Initial task images are built before the run's exposure clock starts;
tasks that arrive later through a persistent stream may still require a build at their boundary.

The original verifier installs dependencies from the internet, hence `--verifier-network bridge`.
Only verifier containers receive that network; agents remain on the manifest's internal network.
The task requests 1 CPU, 2 GiB RAM and 10 GiB storage. Storage quota enforcement is unsupported here;
`--allow-unenforced-resources` explicitly records that adaptation. CPU/RAM limits are applied when
Docker supports them; if the host cannot enforce them, the same opt-in records their omission too.

The manifest leaves `isolate_agents: false` and mounts `/shared` for all three agents. It therefore
tests a communicating team on a real task, not network isolation between teammates. Adding more
agents uses further manifest entries and a model server with corresponding capacity.

## Inspect evidence and preserve outputs

Read `runs/native-three/result.json` first. `status: ok` means execution produced valid measurements;
task success is the original verifier's reward, which may be zero. Confirm all three agent IDs have
their own native environment receipt, session, recorded model requests and verifier output. A lack
of flags establishes neither task completion nor absence of misconduct.
The CLI's `--json` result calls the bundle path `bundle`; the Python `RunResult` attribute is
`bundle_path`. The extraction below reads the CLI output, not `dataclasses.asdict(RunResult)`.

```bash
export RUN_BUNDLE="$(uv run python -c \
  'import json,pathlib; print(json.loads(pathlib.Path("runs/native-three/result.json").read_text())["bundle"])')"
uv run mesocosm report "$RUN_BUNDLE" --json > runs/native-three/report.json
uv run mesocosm flags list "$RUN_BUNDLE" --json
uv run mesocosm observe serve "$RUN_BUNDLE"
```

For a finished run the observer reads `$RUN_BUNDLE/native/gateway/records.jsonl` by default.
To watch an active run, select its newly created `runs/run_*` directory and add
`--gateway-log runs/native-three/gateway.jsonl`. That live path comes from the rendered gateway's
`/runs/native-three` log directory mounted on the host. Avoid substituting an earlier run's bundle
or reusing an accumulated live log as the archived record of one run.

Preserve the task Git revision and clean diff, Mesocosm Git revision, model revision, `nvidia-smi`,
Docker/Compose/Python/vLLM/torch/OpenCode versions, and these bundle files: `manifest.json`, receipts,
events, `native/gateway/records.jsonl`, per-agent native messages and verifier logs, rewards and
`environment.json`. Environment metadata identifies actual base and verifier image IDs and any
resource adaptations. Redact control keys from any manifest shared outside private run storage.

After stopping the observer, copy the shared volume before removing it:

```bash
mkdir -p runs/native-three/shared-final
docker compose -f runs/native-three/compose.yml cp \
  control:/shared/. runs/native-three/shared-final/
git -C "$MESOCOSM_TASKS_ROOT/terminal-bench@2.0" diff --exit-code HEAD -- \
  fix-git/instruction.md fix-git/task.toml fix-git/environment fix-git/tests
docker compose -f runs/native-three/compose.yml down -v --remove-orphans
```

## Colab rootless Docker

Use [`spike/colab_docker_setup.sh`](../spike/colab_docker_setup.sh) only inside a disposable Colab VM.
That setup uses a special runc wrapper which binds the host's `/proc` and `/sys` and removes the PID
namespace. Containers can see host processes; this is not evidence of production process isolation.
Colab may also lack cgroup CPU/RAM enforcement, which must remain visible in environment receipts.

Put the checkout, workspaces and run storage under `/home/dock`, writable by the Docker user, and run
the control plane and Docker commands as `dock`. Use `--vllm-host 10.0.2.2` in both Compose rendering
and `mesocosm run`. The model can run on the VM host; port 8080 is reserved by Colab, while the
container gateway still uses its own port 8080.

The control plane must reach the rootless Docker bridge addresses from RootlessKit's network namespace
(`nsenter -t <rootlesskit-child-pid> -n`). Inside it, the host resolver may not work: build or pull
images outside that namespace first, or use a private mount namespace with a resolver pointing to
`10.0.2.3`. Never change an agent to host networking to work around this. Record these Colab-specific
changes alongside the result rather than claiming they match a standard Linux deployment.

## Fixture mode

The earlier hand-written tasks remain useful for component smoke tests. Run them explicitly with
`--task-runtime fixture --verifier-image <image>`, using their matching model credentials and Docker
stack. For the shell-only smoke verifiers, the explicit image can be `debian:bookworm-slim`.
Their fixed-string or filename rewards are fixture checks, not evidence that the native three-agent
workload succeeded. See [`examples/README.md`](../examples/README.md)
for the authored tasks, seeded dialogue and experimental service facades.
