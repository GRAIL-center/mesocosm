# ADR-0045: On-policy weight updates between real multi-agent worlds

Status: accepted · Date: 2026-10-20

## Context

ADR-0044 permits real training but distinguishes it from recorded incentives and context feedback.
A training claim requires more than an optimizer call: the sampled actions must belong to a known
policy, their rewards must come from the actual environment, the checkpoint must change, and later
real agents must use that checkpoint. The initial deployment shares one A100 between inference and
training, with at least three independent OpenCode agent processes sharing one model policy.

## Decision

Use a separate `train run` controller around complete native worlds. Within a world, the model is
frozen. Each training round runs the actual agents and original task verifiers, closes the world,
validates the entire recorded batch, performs one policy-gradient update, and activates a new adapter
alias before another fresh world begins. The final round evaluates the updated policy without another
update. The existing world `learning.mode` remains a context-feedback control; it does not mean weight
training. Initial training requires it to be `none`.

The algorithm is parameter-shared independent REINFORCE using the configured allocated return of each
agent trajectory. Its loss is the negative mean, over trajectories, of `(return - fixed_baseline)`
times the **sum** of all sampled action-token log probabilities. Prompt and tool-result tokens are
observations, not targets. All request gradients accumulate before one AdamW step with zero weight
decay and explicit gradient clipping. Adam state starts afresh each update. There are no replay epochs,
synthetic targets, learned reward substitutions or hidden return normalization. Team-mean allocation
assigns a common team return to every agent's actions; individual allocation is an independent-learner
estimator and does not claim the full gradient of a globally coupled team objective.

Use Transformers, PyTorch and PEFT's ordinary causal-LM LoRA APIs rather than a separate distributed
trainer. This keeps the single-device scheduling and external OpenCode trajectories explicit. The
initial trainer supports dropout-free causal models exposing `logits_to_keep`, with plain LoRA on
`q_proj` and `v_proj`. Unsupported model or adapter configurations fail. GPU execution uses bfloat16,
SDPA and gradient checkpointing. This is a real policy-gradient implementation, not supervised
imitation of text that agents produced.

## Provenance and numerical checks

- Bind each actual harness session to its agent, task, episode and immutable policy identity through
  the authenticated gateway control surface. Never infer reward boundaries from timestamps. Policy
  identity includes a pinned base revision, the full base snapshot digest and optional full adapter
  digest. The alias and local artifact path are also recorded.
- Record actual prompt IDs, generated action IDs and aligned sampling log probabilities from vLLM.
  Training sampling uses the unfiltered softmax distribution at temperature one, one completion and no
  repetition/frequency/presence penalties or grammar/logit constraints. Unsupported transformations
  fail rather than silently producing an off-policy objective. No text is re-tokenized for training.
- Join original verifier artifacts or actual peer verdicts to the configured allocation rule. Preserve
  original task measurements separately. Missing rewards, mismatched policy identities, missing
  actors, duplicate requests, incomplete streams or inconsistent native/event copies reject the batch.
- Recompute every action log probability under the claimed checkpoint **before** the optimizer step.
  The maximum absolute error must remain within the explicit configured tolerance. A failed parity
  check after earlier microbatches have accumulated gradients still takes no optimizer step.
- Save the exact batch, configuration, checkpoint lineage, reward and advantage distributions, gradient
  norm, parameter delta, package versions and adapter digest. Zero advantages produce an explicit
  `no_update` receipt, not a success-shaped synthetic checkpoint. Failures retain provenance and have
  no eligible `next_policy`.

A fixed action-independent baseline is a valid control variate. All-zero observed returns with a
nonzero baseline can give a noisy finite-batch update; if the true return is constant, its expected
policy gradient is zero. The record must not call this improved task performance. An actual update
and deployment establish integration, while improvement needs independent evaluation across enough
real tasks and repetitions.

## Serving lifecycle

The controller pauses vLLM using its documented sleep-level-one API before GPU training, releases the
training model, wakes inference, and loads the saved adapter with `/v1/load_lora_adapter`. Every new
checkpoint gets a new alias; an existing alias is never overwritten. The controller verifies the full
local adapter hash, the server's registered adapter path, and a real inference request using that
alias. The following world must use that alias for every agent. Its final evaluation batch is also
recomputed under the updated checkpoint, without an optimizer step.

The controller and vLLM must share checkpoint paths on the inference host. Administrative sleep and
adapter-loading endpoints stay on the trusted control plane. This does not require modifying OpenCode,
Harbor or vLLM. The supported API/load receipts plus numerical parity are consistency evidence, not
cryptographic inspection of live GPU tensors; nearby policies can both fit a numerical tolerance.

## Evidence and limits

`tests/test_learning.py` checks the loss direction, action masking, failure-before-step behavior,
no-update receipts, full-artifact tamper detection, evaluation without weight changes, and a tiny
Qwen3/PEFT CPU train-save-reload cycle. Mock HTTP tests check serving protocol behavior. These are
algorithm and integration-unit evidence, not real-agent learning evidence. Actual A100 execution,
three-agent provenance, changed checkpoint activation and subsequent policy use require their own
recorded live validation. No claim of improved behavior follows from these unit tests.

Primary API references: [PEFT LoRA](https://huggingface.co/docs/peft/en/package_reference/lora),
[Qwen3 forward and logits selection](https://huggingface.co/docs/transformers/en/model_doc/qwen3),
[vLLM sleep mode](https://docs.vllm.ai/en/latest/features/sleep_mode/), and
[dynamic LoRA loading](https://docs.vllm.ai/en/stable/features/lora/).
