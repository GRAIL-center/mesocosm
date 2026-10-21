"""Training contract and CPU algorithm tests. These are not real-agent run evidence."""

from __future__ import annotations

import json
import weakref
from types import SimpleNamespace

import httpx
import pytest

from mesocosm.learning.checkpoints import checkpoint_sha256, write_json
from mesocosm.learning.config import PolicyIdentity, TrainingSpec, load_training, policy_id
from mesocosm.learning.rollout import ActionRequest, AgentTrajectory, RolloutBatch
from mesocosm.learning.serving import PolicyServingError, VLLMPolicyServer
from mesocosm.learning.training import (
    TrainingError,
    action_logprobs,
    reinforce_step,
    reinforce_update,
    verify_rollout_policy,
)


def policy(**overrides):
    values = {"model": "test-policy", "base_model": "test/model", "base_revision": "a" * 40,
              "base_sha256": "b" * 64, "adapter_sha256": None, "adapter_path": None, **overrides}
    values["id"] = policy_id(values["base_model"], values["base_revision"],
                              values["base_sha256"], values["adapter_sha256"])
    return PolicyIdentity(**values)


def spec(**overrides):
    return TrainingSpec(name="test-training", world="world.yaml", base_model="test/model",
                        base_revision="a" * 40, initial_model="test-policy", server_url="http://vllm",
                        **overrides)


def batch(reward=1.0, current=None):
    trajectories = []
    for number in range(3):
        agent = f"agent-{number}"
        request = ActionRequest(request_id=f"request-{number}", agent_id=agent, task="external-task",
                                episode_id="episode-1", session_id=f"session-{number}",
                                prompt_token_ids=[4, 5], token_ids=[1, 2], token_logprobs=[-1.0, -1.0])
        trajectories.append(AgentTrajectory(agent_id=agent, task="external-task", episode_id="episode-1",
                                             reward=reward, reward_source="verifier", requests=[request]))
    return RolloutBatch(policy=current or policy(), source_run_ids=["run-1"], trajectories=trajectories)


def test_policy_identity_cannot_be_relabeled_without_changing_digest():
    record = policy().model_dump()
    record["base_sha256"] = "c" * 64
    with pytest.raises(ValueError, match="content identity"):
        PolicyIdentity.model_validate(record)


def test_action_boundaries_and_missing_rewards_fail_closed():
    data = batch().model_dump()
    data["trajectories"][0]["reward"] = None
    with pytest.raises(ValueError):
        RolloutBatch.model_validate(data)
    data = batch().model_dump()
    data["trajectories"][0]["requests"][0]["token_logprobs"] = [-1]
    with pytest.raises(ValueError, match="actual sampling logprob"):
        RolloutBatch.model_validate(data)
    data = batch().model_dump()
    data["trajectories"][0]["requests"][0]["task"] = "other-task"
    with pytest.raises(ValueError, match="rewarded agent/task/episode"):
        RolloutBatch.model_validate(data)


@pytest.mark.parametrize("value", [float("nan"), float("inf"), True])
def test_nonfinite_logprobs_or_noninteger_tokens_rejected(value):
    data = batch().trajectories[0].requests[0].model_dump()
    if isinstance(value, bool):
        data["token_ids"][0] = value
    else:
        data["token_logprobs"][0] = value
    with pytest.raises(ValueError):
        ActionRequest.model_validate(data)


def test_duplicate_request_and_fewer_than_three_distinct_agents_rejected():
    data = batch().model_dump()
    data["trajectories"][1]["requests"][0]["request_id"] = "request-0"
    with pytest.raises(ValueError, match="used twice"):
        RolloutBatch.model_validate(data)
    data = batch().model_dump()
    data["trajectories"] = [data["trajectories"][0]] * 3
    with pytest.raises(ValueError, match="three distinct"):
        RolloutBatch.model_validate(data)


def test_invalid_yaml_is_valueerror(tmp_path):
    path = tmp_path / "training.yaml"
    path.write_text("name: [")
    with pytest.raises(ValueError, match="invalid training YAML"):
        load_training(path)


def test_full_checkpoint_hash_detects_same_size_changes(tmp_path):
    (tmp_path / "config.json").write_text("{}")
    weights = tmp_path / "model.safetensors"
    weights.write_bytes(b"first")
    first = checkpoint_sha256(tmp_path)
    weights.write_bytes(b"other")
    assert checkpoint_sha256(tmp_path) != first
    (tmp_path / "README.md").write_text("Documentation is not model state.")
    stable = checkpoint_sha256(tmp_path)
    (tmp_path / "README.md").write_text("Edited documentation.")
    assert checkpoint_sha256(tmp_path) == stable


def test_zero_advantage_writes_no_update_without_importing_gpu_libraries(tmp_path, monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("zero advantage must not allocate a model")
    monkeypatch.setattr("mesocosm.learning.training.resolve_base", forbidden)
    receipt = reinforce_update(batch(0), policy(), spec(), tmp_path / "update")
    assert receipt["status"] == "no_update" and receipt["reason"] == "zero_advantage"
    assert receipt["optimizer_steps"] == 0 and receipt["weight_delta_l2"] == 0
    assert receipt["next_policy"] == policy().model_dump()
    persisted = json.loads((tmp_path / "update" / "update.json").read_text())
    assert persisted == receipt
    assert json.loads((tmp_path / "update" / "rollout.json").read_text())["trajectories"][0]["reward"] == 0
    with pytest.raises(FileExistsError):
        reinforce_update(batch(0), policy(), spec(), tmp_path / "update")


def test_failed_update_keeps_original_rollouts_and_never_exposes_next_policy(tmp_path, monkeypatch):
    def fail(*args):
        raise TrainingError("checkpoint mismatch")
    monkeypatch.setattr("mesocosm.learning.training.resolve_base", fail)
    with pytest.raises(TrainingError, match="checkpoint mismatch"):
        reinforce_update(batch(0), policy(), spec(baseline=0.5), tmp_path / "update")
    receipt = json.loads((tmp_path / "update" / "update.json").read_text())
    assert receipt["status"] == "failed" and "next_policy" not in receipt
    assert receipt["rewards"] == [0, 0, 0] and receipt["advantages"] == [-0.5] * 3
    assert receipt["constant_observed_reward"] and not receipt["performance_improvement_demonstrated"]


def tiny_policy(torch):
    class TinyPolicy(torch.nn.Module):
        def __init__(self):
            super().__init__()
            self.scores = torch.nn.Parameter(torch.zeros(6, 6))

        def forward(self, input_ids, use_cache, logits_to_keep):
            return SimpleNamespace(logits=self.scores[input_ids][:, -logits_to_keep:])
    return TinyPolicy()


def with_actual_unit_logprobs(model, rollout, torch):
    for trajectory in rollout.trajectories:
        for request in trajectory.requests:
            request.token_logprobs = action_logprobs(model, request, torch).detach().tolist()
    return rollout


@pytest.mark.parametrize(("reward", "baseline", "direction"), [(1.0, 0.0, 1), (0.0, 0.5, -1)])
def test_real_cpu_gradient_uses_only_actions_and_reward_sign(reward, baseline, direction):
    torch = pytest.importorskip("torch")
    model = tiny_policy(torch)
    rollout = with_actual_unit_logprobs(model, batch(reward), torch)
    old = model.scores.detach().clone()
    result = reinforce_step(model, rollout, spec(baseline=baseline, learning_rate=0.01), torch)
    assert result["optimizer_steps"] == 1 and result["weight_delta_l2"] > 0
    assert result["logprob_error_max"] == 0
    assert direction * float((model.scores[5, 1] - old[5, 1]).detach()) > 0
    assert direction * float((model.scores[1, 2] - old[1, 2]).detach()) > 0
    assert torch.equal(model.scores[4], old[4])  # prompt token 4 -> prompt token 5 is not an action.


def test_parity_failure_after_accumulated_gradients_cannot_step_optimizer():
    torch = pytest.importorskip("torch")
    model = tiny_policy(torch)
    rollout = with_actual_unit_logprobs(model, batch(), torch)
    rollout.trajectories[-1].requests[0].token_logprobs[0] -= 1
    original = model.scores.detach().clone()
    with pytest.raises(TrainingError, match="logprob parity"):
        reinforce_step(model, rollout, spec(), torch)
    assert torch.equal(model.scores, original)


def test_evaluation_verifies_all_tokens_without_updating_weights(tmp_path, monkeypatch):
    torch = pytest.importorskip("torch")
    model = tiny_policy(torch)
    rollout = with_actual_unit_logprobs(model, batch(), torch)
    monkeypatch.setattr("mesocosm.learning.training.resolve_base", lambda configured: tmp_path)
    monkeypatch.setattr("mesocosm.learning.training.checkpoint_sha256", lambda path: policy().base_sha256)
    monkeypatch.setattr("mesocosm.learning.training._load_model", lambda *args: model)
    original = model.scores.detach().clone()
    receipt = verify_rollout_policy(rollout, policy(), spec())
    assert receipt["status"] == "verified" and receipt["optimizer_steps"] == 0
    assert receipt["agents"] == ["agent-0", "agent-1", "agent-2"]
    assert receipt["action_tokens"] == 6 and receipt["requests"] == 3
    assert receipt["logprob_error_max"] == 0 and torch.equal(model.scores, original)
    rollout.trajectories[-1].requests[0].token_logprobs[-1] -= 1
    with pytest.raises(TrainingError, match="parity failed"):
        verify_rollout_policy(rollout, policy(), spec())
    assert torch.equal(model.scores, original)


def test_peft_qwen3_cpu_checkpoint_roundtrip_records_real_adapter_change(tmp_path, monkeypatch):
    """Tiny random model tests library integration, not external-task learning quality."""
    torch = pytest.importorskip("torch")
    transformers = pytest.importorskip("transformers")
    peft = pytest.importorskip("peft")
    config = transformers.Qwen3Config(vocab_size=32, hidden_size=16, intermediate_size=32,
                                      num_hidden_layers=1, num_attention_heads=2, num_key_value_heads=1,
                                      head_dim=8, attention_dropout=0.0)
    base = transformers.Qwen3ForCausalLM(config)
    base_path = tmp_path / "base"
    base.save_pretrained(base_path, safe_serialization=True)
    configured = spec(lora_rank=2, lora_alpha=4, baseline=0.5).model_copy(update={"base_model": str(base_path)})
    current = policy(base_model=str(base_path), base_sha256=checkpoint_sha256(base_path))
    model = peft.get_peft_model(base, peft.LoraConfig(task_type="CAUSAL_LM", r=2, lora_alpha=4,
                                                     target_modules=["q_proj", "v_proj"], lora_dropout=0.0))
    model.eval()
    rollout = with_actual_unit_logprobs(model, batch(0, current=current), torch)
    monkeypatch.setattr("mesocosm.learning.training._load_model", lambda *args: model)
    receipt = reinforce_update(rollout, current, configured, tmp_path / "update")
    assert receipt["status"] == "updated" and receipt["optimizer_steps"] == 1
    assert receipt["weight_delta_l2"] > 0 and receipt["rewards"] == [0, 0, 0]
    updated = PolicyIdentity.model_validate(receipt["next_policy"])
    assert updated.id != current.id and updated.adapter_sha256 == checkpoint_sha256(updated.adapter_path, adapter=True)
    loaded = peft.PeftModel.from_pretrained(transformers.Qwen3ForCausalLM.from_pretrained(base_path),
                                           updated.adapter_path)
    loaded.eval()
    before = action_logprobs(model, rollout.trajectories[0].requests[0], torch)
    after = action_logprobs(loaded, rollout.trajectories[0].requests[0], torch)
    assert torch.allclose(before, after, atol=1e-6)
    assert not torch.allclose(after, torch.tensor(rollout.trajectories[0].requests[0].token_logprobs), atol=1e-7, rtol=0)


@pytest.mark.parametrize("evaluate", [False, True])
def test_propagated_failure_does_not_retain_policy_tensors_in_tracebacks(tmp_path, monkeypatch, evaluate):
    torch = pytest.importorskip("torch")
    references = []

    def load(*args):
        model = tiny_policy(torch)
        references.append(weakref.ref(model))
        references.append(weakref.ref(model.scores))
        return model

    monkeypatch.setattr("mesocosm.learning.training.resolve_base", lambda configured: tmp_path)
    monkeypatch.setattr("mesocosm.learning.training.checkpoint_sha256", lambda path: policy().base_sha256)
    monkeypatch.setattr("mesocosm.learning.training._load_model", load)
    # Deliberately keep the propagated exception/traceback alive, as an async controller
    # does while executing its wake-up finally block. Its finished frames must not own the model.
    with pytest.raises(TrainingError, match="parity failed") as error:
        if evaluate:
            verify_rollout_policy(batch(), policy(), spec())
        else:
            reinforce_update(batch(), policy(), spec(), tmp_path / "update")
    assert error.value.__traceback__ is not None
    assert all(reference() is None for reference in references)


@pytest.mark.parametrize("compute_failed", [False, True])
def test_final_receipt_write_failure_always_releases_model_and_preserves_primary_error(
    tmp_path, monkeypatch, compute_failed,
):
    torch = pytest.importorskip("torch")
    references, writes = [], []

    def load(*args):
        model = tiny_policy(torch)
        references.extend([weakref.ref(model), weakref.ref(model.scores)])
        return model

    def write(path, value):
        if path.name == "update.json":
            writes.append(path)
            if len(writes) == 2:
                raise OSError("receipt volume is full")
        write_json(path, value)

    def step(*args):
        if compute_failed:
            raise TrainingError("original policy failure")
        return {"status": "no_update", "optimizer_steps": 0}

    monkeypatch.setattr("mesocosm.learning.training.resolve_base", lambda configured: tmp_path)
    monkeypatch.setattr("mesocosm.learning.training.checkpoint_sha256", lambda path: policy().base_sha256)
    monkeypatch.setattr("mesocosm.learning.training._load_model", load)
    monkeypatch.setattr("mesocosm.learning.training.reinforce_step", step)
    monkeypatch.setattr("mesocosm.learning.training.write_json", write)
    with pytest.raises(TrainingError if compute_failed else OSError) as error:
        reinforce_update(batch(), policy(), spec(), tmp_path / "update")
    assert error.value.__traceback__ is not None
    assert all(reference() is None for reference in references)
    if compute_failed:
        assert str(error.value) == "original policy failure"
        assert isinstance(error.value.__cause__, OSError)
        assert str(error.value.__cause__) == "receipt volume is full"
        assert "receipt volume is full" in error.value.__notes__[0]


async def test_activation_checks_hash_registers_unique_alias_and_exercises_inference(tmp_path):
    (tmp_path / "adapter_config.json").write_text("{}")
    (tmp_path / "adapter_model.safetensors").write_bytes(b"unit-test adapter")
    current = policy(adapter_path=str(tmp_path), adapter_sha256=checkpoint_sha256(tmp_path, adapter=True))
    calls, entries = [], []

    def handle(request):
        calls.append(request.url.path)
        if request.url.path == "/v1/models":
            return httpx.Response(200, json={"data": list(entries)})
        if request.url.path == "/v1/load_lora_adapter":
            payload = json.loads(request.content)
            entries.append({"id": payload["lora_name"], "root": payload["lora_path"]})
            return httpx.Response(200, text="loaded")
        if request.url.path == "/v1/completions":
            return httpx.Response(200, json={"id": "probe-1", "model": current.model,
                                             "choices": [{"text": "ok"}], "usage": {"completion_tokens": 1}})
        return httpx.Response(200)

    server = VLLMPolicyServer("http://vllm", transport=httpx.MockTransport(handle))
    try:
        await server.sleep()
        await server.wake()
        receipt = await server.load(current)
        assert receipt["policy_id"] == current.id and receipt["probe_request_id"] == "probe-1"
        assert calls == ["/sleep", "/wake_up", "/v1/models", "/v1/load_lora_adapter", "/v1/models", "/v1/completions"]
        with pytest.raises(PolicyServingError, match="already registered"):
            await server.load(current)
        (tmp_path / "adapter_model.safetensors").write_bytes(b"tampered")
        with pytest.raises(PolicyServingError, match="changed before"):
            await server.load(current)
    finally:
        await server.aclose()


async def test_registered_alias_without_expected_checkpoint_cannot_activate(tmp_path):
    current = policy(adapter_path=str(tmp_path), adapter_sha256="c" * 64)
    server = VLLMPolicyServer("http://vllm", transport=httpx.MockTransport(lambda request: httpx.Response(
        200, json={"data": [{"id": current.model, "root": "/wrong/checkpoint"}]})))
    try:
        with pytest.raises(PolicyServingError, match="registered adapter path"):
            await server.verify(current)
    finally:
        await server.aclose()
