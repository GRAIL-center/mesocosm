"""Run spike steps 1-5 on a Google Colab GPU runtime through `colab exec`.

Colab has no Docker, so steps 6-7 (isolation, Harbor) stay on the 3090. Each phase is one
`colab exec` call; background services (vLLM, gateway, OpenCode) are started with Popen and
survive between calls because the kernel stays alive.

    colab new -s mesocosm --gpu L4
    colab exec -s mesocosm -f spike/colab_smoke.py --env MESOCOSM_PHASE=setup   --timeout 2400
    colab exec -s mesocosm -f spike/colab_smoke.py --env MESOCOSM_PHASE=serve   --timeout 1200
    colab exec -s mesocosm -f spike/colab_smoke.py --env MESOCOSM_PHASE=drive   --timeout 1800
    colab exec -s mesocosm -f spike/colab_smoke.py --env MESOCOSM_PHASE=probe   --timeout 1800
    colab exec -s mesocosm -f spike/colab_smoke.py --env MESOCOSM_PHASE=collect --timeout 120
    colab download -s mesocosm /content/spike-results.tgz runs/colab-spike.tgz

The script is sent as code (no __file__); everything lives under /content/mesocosm.
"""

import json
import os
import pathlib
import subprocess
import sys
import time
import urllib.request

ROOT = pathlib.Path("/content/mesocosm")
RUNS = ROOT / "runs" / "spike"
WORKSPACE = ROOT / "spike" / "workspace" / "agent-1"
PIDS = pathlib.Path("/content/mesocosm-pids.json")
LOGS = pathlib.Path("/content/mesocosm-logs")
MODEL = os.environ.get("MESOCOSM_MODEL", "Qwen/Qwen3-4B-Instruct-2507")  # ADR-0007
REPO = os.environ.get("MESOCOSM_REPO", "https://github.com/GRAIL-center/mesocosm")
API_KEY = "mesocosm-local"
# Colab's own runtime proxy listens on 8080 inside the VM, so the gateway moves to 8081 here.
GATEWAY_PORT = 8081
GATEWAY = f"http://127.0.0.1:{GATEWAY_PORT}"


def sh(cmd, check=True, cwd=None, env=None, quiet=False):
    """Run a shell command and echo its output into the kernel stream (child fds are not shown
    by `colab exec`, so everything is captured). quiet=True prints output only on failure."""
    print(f"$ {cmd}", flush=True)
    r = subprocess.run(cmd, shell=True, cwd=cwd, env=env, text=True, capture_output=True)
    if not quiet or r.returncode != 0:
        if r.stdout.strip():
            print(r.stdout[-6000:], flush=True)
        if r.stderr.strip():
            print("[stderr]", r.stderr[-3000:], flush=True)
    if check and r.returncode != 0:
        raise SystemExit(f"command failed ({r.returncode}): {cmd}")
    return r


def http_ok(url, headers=None, timeout=3):
    try:
        req = urllib.request.Request(url, headers=headers or {})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status < 400
    except Exception:
        return False


def wait_for(url, seconds, headers=None, label=""):
    t0 = time.time()
    while time.time() - t0 < seconds:
        if http_ok(url, headers):
            print(f"[ready] {label or url} after {time.time() - t0:.0f}s", flush=True)
            return True
        time.sleep(5)
    raise SystemExit(f"timeout waiting for {label or url}")


def start(name, cmd, cwd=None, env=None):
    LOGS.mkdir(exist_ok=True)
    log = open(LOGS / f"{name}.log", "ab")
    proc = subprocess.Popen(cmd, cwd=cwd, env=env, stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
    pids = json.loads(PIDS.read_text()) if PIDS.exists() else {}
    pids[name] = proc.pid
    PIDS.write_text(json.dumps(pids))
    print(f"[start] {name} pid={proc.pid} log={LOGS / name}.log", flush=True)
    return proc


def stop(name):
    pids = json.loads(PIDS.read_text()) if PIDS.exists() else {}
    pid = pids.pop(name, None)
    if pid:
        subprocess.run(f"kill -TERM -- -{pid} 2>/dev/null || kill -TERM {pid} 2>/dev/null", shell=True)
        time.sleep(5)
        subprocess.run(f"kill -KILL -- -{pid} 2>/dev/null || true", shell=True)
        print(f"[stop] {name} pid={pid}")
    PIDS.write_text(json.dumps(pids))


def gpu_dtype():
    import torch

    major, _ = torch.cuda.get_device_capability(0)
    name = torch.cuda.get_device_name(0)
    dtype = "bfloat16" if major >= 8 else "half"
    print(f"[gpu] {name} capability={major}.x -> dtype={dtype}")
    return dtype


def phase_setup():
    # The repo is private: the control machine uploads a `git archive` tarball to /content/mesocosm
    # before this phase. Clone only when nothing was uploaded.
    if not ROOT.exists():
        sh(f"git clone --depth 1 {REPO} {ROOT}")
    sh("nvidia-smi --query-gpu=name,memory.total,driver_version --format=csv")
    sh(f"{sys.executable} -m pip install -q vllm", quiet=True)
    sh(f"{sys.executable} -m pip install -q 'nnsight[vllm]' safetensors", quiet=True)
    sh(f"{sys.executable} -m pip install -q -e '{ROOT}[gateway]'", quiet=True)
    sh("node --version && npm install -g opencode-ai >/tmp/npm.log 2>&1 || (tail -20 /tmp/npm.log; exit 1)")
    sh("opencode --version")
    sh(f"{sys.executable} -c \"import torch,vllm,nnsight,transformers;"
       "print('torch',torch.__version__,'cuda',torch.version.cuda);print('vllm',vllm.__version__);"
       "print('nnsight',nnsight.__version__);print('transformers',transformers.__version__)\"")


def phase_serve():
    """Idempotent: a service that already answers its health check is left running."""
    dtype = gpu_dtype()
    RUNS.mkdir(parents=True, exist_ok=True)
    auth = {"Authorization": f"Bearer {API_KEY}"}
    if not http_ok("http://127.0.0.1:8000/v1/models", auth):
        start("vllm", ["vllm", "serve", MODEL, "--host", "127.0.0.1", "--port", "8000",
                       "--served-model-name", "mesocosm-3b", "--api-key", API_KEY, "--dtype", dtype,
                       # OpenCode's system prompt plus 11 tool schemas is ~10.8k tokens and it asks for
                       # 8192 output tokens; 16k was rejected by vLLM with a 400 on every request.
                       "--max-model-len", "32768", "--gpu-memory-utilization", "0.85",
                       "--enable-auto-tool-choice", "--tool-call-parser", "hermes"])
        wait_for("http://127.0.0.1:8000/v1/models", 900, auth, "vLLM")
    sh("nvidia-smi --query-gpu=memory.used --format=csv")

    if not http_ok(f"{GATEWAY}/mesocosm/health"):
        env = dict(os.environ, MESOCOSM_UPSTREAM="http://127.0.0.1:8000", MESOCOSM_LOG_DIR=str(RUNS),
                   MESOCOSM_RETURN_TOKEN_IDS="1", MESOCOSM_GATEWAY_PORT=str(GATEWAY_PORT),
                   MESOCOSM_BUDGET_JSON=json.dumps({"agent-1": {"completion_tokens": 40000, "requests": 200}}))
        start("gateway", [sys.executable, "-m", "mesocosm.gateway"], cwd=ROOT, env=env)
        wait_for(f"{GATEWAY}/mesocosm/health", 60, label="gateway")

    if not http_ok("http://127.0.0.1:4096/global/health"):
        # The kernel's cwd is /content, where the repo directory itself shadows the installed
        # package as a namespace package; import from src explicitly.
        sys.path.insert(0, str(ROOT / "src"))
        for mod in [m for m in sys.modules if m == "mesocosm" or m.startswith("mesocosm.")]:
            del sys.modules[mod]
        from mesocosm.adapters.opencode.client import render_opencode_config

        WORKSPACE.mkdir(parents=True, exist_ok=True)
        cfg = render_opencode_config(agent_id="agent-1", run_id="spike-host", gateway_url=GATEWAY,
                                     model_id="mesocosm-3b", api_key=API_KEY, context_limit=32768)
        (WORKSPACE / "opencode.json").write_text(json.dumps(cfg, indent=2))
        start("opencode", ["opencode", "serve", "--port", "4096"], cwd=WORKSPACE)
        wait_for("http://127.0.0.1:4096/global/health", 120, label="OpenCode")
    sh("curl -s http://127.0.0.1:4096/config/providers | python3 -c \"import json,sys;d=json.load(sys.stdin);"
       "print('providers:',[p.get('id') for p in d.get('providers',[])],'default:',d.get('default'))\"")

    # Step 2 smoke: a tool-call request through the gateway, then what the record says.
    body = json.dumps({"model": "mesocosm-3b", "temperature": 0,
                       "messages": [{"role": "user", "content": "What is in /tmp? Use the tool."}],
                       "tools": [{"type": "function", "function": {"name": "bash", "description": "run a shell command",
                                  "parameters": {"type": "object", "properties": {"cmd": {"type": "string"}},
                                                 "required": ["cmd"]}}}]})
    sh(f"curl -s {GATEWAY}/v1/chat/completions -H 'Authorization: Bearer {API_KEY}' "
       f"-H 'X-Mesocosm-Agent: curl' -H 'X-Mesocosm-Run: spike-colab' -H 'Content-Type: application/json' "
       f"-d '{body}' | head -c 1500; echo")
    sh(f"tail -1 {RUNS}/gateway.jsonl | python3 -c \"import json,sys;r=json.load(sys.stdin)['response'];"
       "print('tool_calls:',r.get('tool_calls'));print('usage:',r.get('usage'));"
       "print('has_prompt_token_ids:',r.get('prompt_token_ids') is not None)\"")


def phase_drive():
    sh(f"{sys.executable} spike/04_drive_opencode.py --url http://127.0.0.1:4096 --out runs/spike --run spike-host",
       cwd=ROOT)
    sh("ls -la spike/workspace/agent-1; cat spike/workspace/agent-1/NOTES.md 2>/dev/null | head -5", cwd=ROOT, check=False)
    # Budget exhaustion through the admin endpoint (a receipt), then the driver again -> expect 429 records.
    sh(f"curl -s -X POST {GATEWAY}/mesocosm/budget -H 'Content-Type: application/json' "
       "-d '{\"agent_id\":\"agent-1\",\"budget\":{\"completion_tokens\":200},\"run_id\":\"spike-host\"}'; echo")
    sh(f"{sys.executable} spike/04_drive_opencode.py --url http://127.0.0.1:4096 --out runs/spike --run spike-host",
       cwd=ROOT, check=False)
    sh(f"grep -c '\"event\": \"budget_exhausted\"' {RUNS}/gateway.jsonl || echo 'no budget_exhausted record'")
    sh(f"curl -s -X POST {GATEWAY}/mesocosm/budget -H 'Content-Type: application/json' "
       "-d '{\"agent_id\":\"agent-1\",\"budget\":{\"completion_tokens\":40000}}' >/dev/null")


def phase_probe():
    dtype = gpu_dtype()
    stop("vllm")
    time.sleep(10)
    sh("nvidia-smi --query-gpu=memory.used --format=csv")
    # NNSIGHT_VLLM_CLONE_READS: served values are views into engine memory that later layers
    # overwrite in place; the probe also clones inside the trace.
    env = dict(os.environ, VLLM_USE_V2_MODEL_RUNNER="0", NNSIGHT_VLLM_CLONE_READS="1")
    # Probe the serve-phase curl record (170 prompt tokens, temperature 0), not an OpenCode request.
    run = os.environ.get("MESOCOSM_PROBE_RUN", "spike-colab")
    wanted = os.environ.get("MESOCOSM_PROBE_PHASES", "hf,vllm,compare").split(",")
    for phase, extra in (("hf", f"--dtype {dtype} --run {run}"), ("vllm", f"--dtype {dtype} --gpu-mem 0.6"), ("compare", "")):
        if phase in wanted:
            sh(f"{sys.executable} spike/05_nnsight_probe.py {phase} --model {MODEL} {extra}", cwd=ROOT, env=env, check=False)


def phase_stop():
    """Stop the services named in MESOCOSM_STOP (comma separated; default all). `serve` then
    restarts whatever is missing, so stop+serve is the restart path."""
    for name in os.environ.get("MESOCOSM_STOP", "vllm,gateway,opencode").split(","):
        stop(name.strip())
    time.sleep(5)
    sh("nvidia-smi --query-gpu=memory.used --format=csv")


def phase_collect():
    for name in ("vllm", "gateway", "opencode"):
        sh(f"tail -5 {LOGS}/{name}.log 2>/dev/null || true", check=False)
    sh(f"cp -r {LOGS} {RUNS}/service-logs 2>/dev/null || true", check=False)
    sh(f"tar czf /content/spike-results.tgz -C {ROOT} runs/spike && ls -la /content/spike-results.tgz")
    sh(f"find {RUNS} -maxdepth 2 -type f | head -30")


PHASES = {"setup": phase_setup, "serve": phase_serve, "drive": phase_drive, "probe": phase_probe,
          "stop": phase_stop, "collect": phase_collect}
phase = os.environ.get("MESOCOSM_PHASE", "")
if phase not in PHASES:
    raise SystemExit(f"set MESOCOSM_PHASE to one of {sorted(PHASES)} (got {phase!r})")
print(f"=== phase {phase} ===", flush=True)
PHASES[phase]()
print(f"=== phase {phase} done ===", flush=True)
