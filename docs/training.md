# Train a policy between real multi-agent worlds

`mesocosm train run` runs a complete native world with a frozen model, updates a LoRA adapter from its
recorded actions and measured returns, and starts the next world with the updated policy. With
`episodes: 2`, the first world supplies one update and the second evaluates that checkpoint without
another update. Each world contains at least three independent OpenCode processes sharing one model
replica. Task completion, policy updates and improved performance are separate observations.

## Prepare the world and host

Follow [the native multi-agent runbook](native-multiagent.md) to materialize an unchanged external Harbor
task, configure private gateway credentials and verify the Linux Docker environment. Keep the original
task instructions, environment and verifier. Training does not need an authored incident, substitute
task or predetermined agent dialogue.

Install the control plane's training dependencies; vLLM remains in its separate serving environment:

```bash
uv sync --locked --extra gateway --extra learning
```

The `learning` extra installs PyTorch, Transformers, PEFT and safetensors. The current trainer requires
CUDA, a dropout-free causal LM exposing `logits_to_keep`, and plain LoRA on `q_proj` and `v_proj`.
Set the same explicit `dtype` in the training spec and vLLM startup: `bfloat16` (the default) or
`float16`. The trainer loads that dtype without hidden autocast or automatic loss scaling; nonfinite
gradients fail before the optimizer step. Full `float32` training is rejected because the current
vLLM CUDA dynamic-LoRA kernels support FP16/BF16 inputs and adapter weights. FP32 base-model inference
can be a separate numerical diagnostic, but is not a supported training/activation path here.
The controller and vLLM must run on the same inference host and see the same absolute checkpoint paths.
GPU memory is shared sequentially: vLLM sleeps while training or evaluation recomputation runs.
Host RAM must also hold vLLM's offloaded base weights during training.

Use a private world manifest with these controls:

```yaml
mode: comparison
memory: reset
rewards:
  enabled: true
  source: verifier
  allocation: individual
learning:
  mode: none
observe:
  activations: false
```

Keep the existing agents, actual task reference and other world conditions. All agents must use the
same initial `kind: vllm` model on the default upstream; per-agent model mixtures and custom model
upstreams are unsupported for this trainer. The native runtime handles the actual task environment.
Generic peer-verdict rewards require `peer_review.protocol: ratings` and complete real reviews;
legacy peer-review scoring is rejected. Missing measurements never become zero. The original verifier
results remain recorded independently of the incentive source and allocation.

## Start an inference server that can accept updates

Start the pinned model in the serving environment, using the same revision as the training spec.
The example extends the native runbook's Qwen3 serving command:

```bash
export VLLM_SERVER_DEV_MODE=1
export VLLM_ALLOW_RUNTIME_LORA_UPDATING=True

vllm serve Qwen/Qwen3-4B-Instruct-2507 \
  --revision cdbee75f17c01a7cc42f958dc650907174af0554 \
  --served-model-name qwen3-4b --host 0.0.0.0 --port 8000 \
  --api-key "$VLLM_API_KEY" --dtype bfloat16 \
  --max-model-len 32768 --max-num-seqs 3 --gpu-memory-utilization 0.85 \
  --enable-auto-tool-choice --tool-call-parser hermes \
  --enable-lora --max-lora-rank 8 --enable-sleep-mode --generation-config vllm
```

Set `VLLM_API_KEY` to the private world's `gateway.upstream_key`, as in the native runbook. Keep the
administrative inference endpoint reachable only from the trusted control plane; agents use their
authenticated gateway. `--max-lora-rank` must cover the training spec's rank. These flags enable the
documented [sleep/wake](https://docs.vllm.ai/en/latest/features/sleep_mode/) and
[dynamic adapter APIs](https://docs.vllm.ai/en/stable/features/lora/).
Before agent rollout, the controller also reads the supported `/server_info?config_format=json`
endpoint. It checks the resolved base model, pinned revision and dtype, requires unquantized weights,
and accepts only a matching/automatic KV cache and LoRA dtype. The output head must use the model dtype;
an FP32-head override or other model/config override that this trainer does not recompute is rejected.
A local serving checkpoint is checked by its full content hash. Only selected model/precision settings
are retained in the receipt; server environment variables and system environment data are not copied.
Some vLLM versions omit the internal Hugging Face config from this endpoint. In that case, preflight
reads `config.json` from the already cached pinned base snapshot, rechecks its full checkpoint hash,
and applies only the supported explicit head override. This fallback requires a causal-LM generation
configuration without remote code or alternate config paths; it never treats a missing config as empty.
The receipt records whether the head setting came from server metadata or this verified snapshot.

Training-bound gateway requests enforce full-softmax sampling: temperature `1`, top-p `1`, top-k `-1`,
min-p `0`, repetition penalty `1`, zero frequency/presence penalties, and exactly one completion.
Grammar, token/logit restrictions and other unsupported sampling transformations fail. Actual prompt
IDs, action IDs and aligned sampled-token log probabilities are recorded; no text is re-tokenized.

## Configure and run

Save a training spec beside the private world manifest, for example
`runs/native-three/training.yaml`. The revision must be a pinned 40-character commit, and
`initial_model` must match the world and serving alias:

```yaml
name: native-policy-training
world: world.yaml
episodes: 2
algorithm: reinforce
base_model: Qwen/Qwen3-4B-Instruct-2507
base_revision: cdbee75f17c01a7cc42f958dc650907174af0554
dtype: bfloat16
initial_model: qwen3-4b
server_url: http://172.17.0.1:8000
server_api_key_env: VLLM_API_KEY
baseline: 0.0
learning_rate: 0.00001
lora_rank: 8
lora_alpha: 16
max_grad_norm: 1.0
max_logprob_error: 0.1
```

Replace `server_url` with the **same HTTP root endpoint reachable from both the controller and the
gateway container**. `172.17.0.1` is an example Linux Docker host address, not a universal default;
the documented rootless Colab setup uses `10.0.2.2`. Container-local `127.0.0.1` is unsuitable.
The CLI derives gateway routing from this endpoint and rejects conflicting host/port overrides.
Export `VLLM_API_KEY` in the controller shell too. `world` resolves relative to the training spec.

```bash
uv run mesocosm train run runs/native-three/training.yaml \
  --out runs/training/native-policy-001 \
  --verifier-network bridge --allow-unenforced-resources --json
```

Use a fresh output directory. The verifier network and resource opt-in above are the explicit
adaptations required by the native runbook's external task; choose them for the actual deployment.
Each episode gets fresh agent workspaces, a unique run label and a full native bundle. A failed world,
missing provenance or unsuccessful teardown stops training before the next episode.

## What is optimized and what proves execution

The trainer uses parameter-shared independent REINFORCE. For each agent trajectory it multiplies
`allocated_return - baseline` by the sum of its sampled action-token log probabilities, averages over
trajectories, and takes one gradient-clipped AdamW step. Prompt/tool-result tokens are observations.
There are no replay epochs; Adam state starts afresh each update. `team_mean` allocation gives every
agent a common team return; individual allocation does not claim the complete gradient of a coupled
team objective. See [ADR-0045](adr/0045-on-policy-world-training.md).

The baseline is a declared control variate, not a changed task score. If every return equals the
baseline, the controller records `no_update` and stops without activating a checkpoint. A deliberately
configured baseline such as `0.5` can produce negative advantages when original returns are all zero.
That noisy finite-batch update demonstrates neither a useful learning signal nor performance improvement.

`training.json` links episodes, updates, policy identities and serving receipts. Each update preserves
the exact rollout, configuration, full base/adapter hashes, original returns, advantages, gradient norm,
actual parameter delta and package versions. Recomputed action log probabilities must match the sampled
policy within the configured tolerance before an optimizer step. Checkpoint loading uses a new alias,
verifies its registered path and runs a real inference probe. The final real world must use that alias;
its action probabilities are recomputed against the updated checkpoint without training.

Compute dtype is part of the canonical policy hash, so changing precision requires a new policy identity
and fresh rollouts. Matching declared dtypes does not guarantee cross-engine numerical agreement:
batching, attention kernels and rounding can still differ. Keep the predeclared parity tolerance when
investigating a failure; preserve the original batch and failed receipt. Historical policy IDs created
before the precision contract are rejected by the new reader rather than silently reassigned a dtype.
See [ADR-0047](adr/0047-explicit-policy-precision.md).

Failures retain their artifacts, including world results before teardown and a completed but unactivated
update after cancellation. Cancellation waits for an active GPU worker to release its model before
waking inference. Numerical parity and supported load receipts establish policy consistency, not
cryptographic inspection of GPU tensors. Report task success rates and performance changes separately;
an optimizer step, monitor score or successful command alone is not evidence of improved agents.
