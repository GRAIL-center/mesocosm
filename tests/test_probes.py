"""Linear probes over captured raw tensors (ADR-0031): labels from flags or a file, a probe file whose
metadata pins what it was trained on, scores per request, refusal of a differently captured bundle."""

import json

import numpy as np
import pytest
from safetensors.numpy import save_file

from mesocosm.core.records import RunBundle
from mesocosm.observe.activations import write_index_entry
from mesocosm.observe.probes import labels_from_flags, load_probe, read_scores, score, train

pytest.importorskip("numpy")


def captured_bundle(tmp_path, run_id="run_p", model="Qwen/Qwen3-4B", dtype="bfloat16", layer=18, n=12, seed=0,
                    flag_agent="agent-2"):
    """n requests from two agents; agent-2's tensors lean along one direction, agent-1's the other way."""
    rng = np.random.default_rng(seed)
    b = RunBundle.create(tmp_path / "runs", run_id)
    b.write_manifest({"name": "p", "agents": [{"id": "agent-1"}, {"id": "agent-2"}]}, "d" * 64)
    (b.root / "activations" / "raw").mkdir(parents=True)
    for i in range(n):
        agent = "agent-2" if i % 2 else "agent-1"
        direction = 1.0 if agent == "agent-2" else -1.0
        positions = 5 + i
        hidden = rng.normal(0, 1, size=(positions, 16)).astype(np.float32)
        hidden[:, :4] += direction * 2.0
        rid = f"r{i}"
        save_file({f"layer_{layer}": hidden.astype(np.float16)}, str(b.root / "activations" / "raw" / f"{rid}.safetensors"))
        write_index_entry(b.root, {"request_id": rid, "agent_id": agent, "ts": float(i), "model": model,
                                   "revision": "abc", "dtype": dtype, "layers": [layer], "raw_file": f"activations/raw/{rid}.safetensors",
                                   "raw_positions": "completion", "agreement": 1.0})
    if flag_agent:
        b.record_flag({"flag_id": "f1", "run_id": run_id, "agent_id": flag_agent, "kind": "impersonation",
                       "summary": "x", "detector": "state_check", "evidence": []})
    return b


def test_probe_trains_on_flag_labels_scores_requests_and_pins_its_provenance(tmp_path):
    b = captured_bundle(tmp_path)
    assert labels_from_flags(b.root, [{"request_id": "r1", "agent_id": "agent-2"}, {"request_id": "r0", "agent_id": "agent-1"}]) \
        == {"r1": 1, "r0": 0}
    report = train([b.root], layer=18, out=tmp_path / "probes" / "leak.safetensors")
    assert report["requests"] == 12 and report["n_pos"] == 6 and report["n_neg"] == 6 and report["train_accuracy"] == 1.0
    tensors, meta = load_probe(tmp_path / "probes" / "leak.safetensors")
    assert tensors["w"].shape == (16,) and meta["layer"] == "18" and meta["model"] == "Qwen/Qwen3-4B"
    assert meta["labels"] == "flags" and meta["pool"] == "mean" and meta["dtype"] == "bfloat16" and meta["n_pos"] == "6"
    scored = score(b.root, tmp_path / "probes" / "leak.safetensors")
    assert scored["scored"] == 12 and scored["probe"] == "leak" and scored["layer"] == 18
    assert scored["by_agent"]["agent-2"] > 0.9 and scored["by_agent"]["agent-1"] < 0.1
    assert scored["top"][0]["agent_id"] == "agent-2"
    rows = read_scores(b.root, "leak")
    assert [r["request_id"] for r in rows] == [f"r{i}" for i in range(12)], "in time order"
    assert all((r["score"] > 0.5) == (r["agent_id"] == "agent-2") for r in rows)
    assert (b.root / "activations" / "probes" / "leak.jsonl").exists()

    # a fresh, unflagged bundle captured the same way scores with the same probe; one captured differently is refused
    other = captured_bundle(tmp_path / "o", run_id="run_o", seed=1, flag_agent=None)
    assert score(other.root, tmp_path / "probes" / "leak.safetensors", name="leak-on-o")["by_agent"]["agent-2"] > 0.8
    wrong = captured_bundle(tmp_path / "w", run_id="run_w", dtype="float16", flag_agent=None)
    with pytest.raises(ValueError, match="captured differently.*dtype"):
        score(wrong.root, tmp_path / "probes" / "leak.safetensors")
    with pytest.raises(ValueError, match="no captured request with a raw tensor at layer 18"):
        score(captured_bundle(tmp_path / "l", run_id="run_l", layer=9, flag_agent=None).root,
              tmp_path / "probes" / "leak.safetensors")


def test_labels_file_last_pooling_and_refusals(tmp_path):
    b = captured_bundle(tmp_path, flag_agent=None)
    with pytest.raises(ValueError, match="both classes"):
        train([b.root], layer=18, out=tmp_path / "p.safetensors")
    labels = {f"r{i}": int(i % 3 == 0) for i in range(12)}        # the researcher's own labels, not the agents'
    (tmp_path / "labels.json").write_text(json.dumps(labels))
    report = train([b.root], layer=18, out=tmp_path / "p.safetensors", labels_file=tmp_path / "labels.json", pool="last")
    assert report["n_pos"] == 4 and report["n_neg"] == 8 and load_probe(tmp_path / "p.safetensors")[1]["labels"] == "file"
    assert load_probe(tmp_path / "p.safetensors")[1]["pool"] == "last"
    scored = score(b.root, tmp_path / "p.safetensors")
    assert scored["scored"] == 12 and 0.0 <= scored["mean"] <= 1.0
    with pytest.raises(ValueError, match="pool must be"):
        train([b.root], layer=18, out=tmp_path / "q.safetensors", labels_file=tmp_path / "labels.json", pool="max")
    with pytest.raises(ValueError, match="no captured request"):
        train([b.root], layer=27, out=tmp_path / "q.safetensors", labels_file=tmp_path / "labels.json")
    mixed = captured_bundle(tmp_path / "m", run_id="run_m", model="Other/Model")
    with pytest.raises(ValueError, match="captured differently"):
        train([b.root, mixed.root], layer=18, out=tmp_path / "q.safetensors", labels_file=tmp_path / "labels.json")
