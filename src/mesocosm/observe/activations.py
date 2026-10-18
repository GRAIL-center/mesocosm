"""Activations by recompute (M5, ADR-0016).

`vllm serve` cannot be hooked (NNsight's vLLM wrapper runs its own engine), so activations are
recomputed from the exact token sequence the gateway recorded for each request (prompt token ids
plus completion token ids) by running it through the same weights with NNsight. Keyed by
request_id. Always: per-layer, per-token norms of the residual stream (a summary). Raw tensors:
only where the capture policy says (flagged requests by default). Verification: the recorded
completion must be reproduced by greedy decoding at every position (the agreement), and a second
recompute must stay within a stated tolerance of the first.

Everything that needs torch or nnsight is imported inside the functions that use it, so the bundle
side (selection, policy, index, comparison) runs and is tested without a GPU.
"""

from __future__ import annotations

import json
import math
import pathlib
import time
from typing import Any

from mesocosm.core.records import RunBundle

METHOD = "recompute"


# ---- bundle side ---------------------------------------------------------------------------------

def gateway_records(bundle_root: str | pathlib.Path) -> list[dict[str, Any]]:
    path = pathlib.Path(bundle_root) / "native" / "gateway" / "records.jsonl"
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def recomputable(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Successful chat completions whose exact token sequence was recorded (observe.activations: true)."""
    out = []
    for r in records:
        resp = r.get("response") or {}
        if r.get("event") == "chat_completion" and r.get("status", 200) == 200 and resp.get("prompt_token_ids") \
                and resp.get("token_ids"):
            out.append(r)
    return out


def token_sequence(record: dict[str, Any]) -> tuple[list[int], list[int]]:
    resp = record["response"]
    return list(resp["prompt_token_ids"]), list(resp["token_ids"])


def select(bundle_root: str | pathlib.Path, request_ids: list[str] | None = None, agents: list[str] | None = None,
           limit: int | None = None) -> list[dict[str, Any]]:
    records = recomputable(gateway_records(bundle_root))
    if request_ids:
        wanted = set(request_ids)
        records = [r for r in records if r.get("request_id") in wanted]
    if agents:
        records = [r for r in records if r.get("agent") in set(agents)]
    return records[:limit] if limit else records


def raw_request_ids(bundle_root: str | pathlib.Path, records: list[dict[str, Any]], policy: str) -> set[str]:
    """Which requests get raw tensors: `all`, `none`, or `flagged` (every request of an agent the judge
    flagged, plus requests named in flag evidence)."""
    if policy == "all":
        return {r["request_id"] for r in records}
    if policy == "none":
        return set()
    flags = RunBundle.open(bundle_root).flags()
    flagged_agents = {f.get("agent_id") for f in flags if f.get("agent_id")}
    named = {e.get("request_id") for f in flags for e in f.get("evidence") or [] if e.get("request_id")}
    return {r["request_id"] for r in records if r.get("agent") in flagged_agents or r["request_id"] in named}


def default_layers(n_layers: int) -> list[int]:
    """Every quarter of the stack: for 36 layers -> [9, 18, 27]."""
    step = max(1, n_layers // 4)
    return [i for i in range(step, n_layers, step)][:3] or [n_layers // 2]


# ---- pure math (lists of floats; tensors are converted before reaching here) ----------------------

def norms(rows: list[list[float]]) -> list[float]:
    return [round(math.sqrt(sum(x * x for x in row)), 4) for row in rows]


def agreement(pred: list[int], prompt_len: int, completion: list[int]) -> float:
    """Share of recorded completion tokens that greedy decoding reproduces at their position: the
    prediction at position P-1 should be completion[0], at P completion[1], ..."""
    if not completion:
        return 1.0
    guesses = pred[prompt_len - 1: prompt_len - 1 + len(completion)]
    hits = sum(1 for g, c in zip(guesses, completion, strict=False) if g == c)
    return round(hits / len(completion), 4)


def vector_stats(a: list[float], b: list[float]) -> dict[str, float]:
    """Relative L2 difference, cosine similarity and max absolute difference of two equally long vectors."""
    n = min(len(a), len(b))
    a, b = a[:n], b[:n]
    diff = math.sqrt(sum((x - y) ** 2 for x, y in zip(a, b, strict=True)))
    na = math.sqrt(sum(x * x for x in a)) or 1e-12
    nb = math.sqrt(sum(y * y for y in b)) or 1e-12
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    return {"rel_l2": round(diff / na, 6), "cosine": round(dot / (na * nb), 6),
            "max_abs": round(max((abs(x - y) for x, y in zip(a, b, strict=True)), default=0.0), 6), "n": n}


# ---- storage -------------------------------------------------------------------------------------

def activations_dir(bundle_root: str | pathlib.Path) -> pathlib.Path:
    return pathlib.Path(bundle_root) / "activations"


def write_index_entry(bundle_root: str | pathlib.Path, entry: dict[str, Any]) -> None:
    d = activations_dir(bundle_root)
    d.mkdir(parents=True, exist_ok=True)
    with (d / "index.jsonl").open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def read_index(bundle_root: str | pathlib.Path) -> list[dict[str, Any]]:
    path = activations_dir(bundle_root) / "index.jsonl"
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def write_summary(bundle_root: str | pathlib.Path, request_id: str, summary: dict[str, Any]) -> str:
    d = activations_dir(bundle_root) / "summaries"
    d.mkdir(parents=True, exist_ok=True)
    path = d / f"{request_id}.json"
    path.write_text(json.dumps(summary))
    return str(path.relative_to(pathlib.Path(bundle_root)))


def read_summary(bundle_root: str | pathlib.Path, request_id: str) -> dict[str, Any]:
    return json.loads((activations_dir(bundle_root) / "summaries" / f"{request_id}.json").read_text())


# ---- GPU side ------------------------------------------------------------------------------------

class Recomputer:
    """Runs exact token sequences through the model with NNsight and returns the residual stream at the
    requested layers plus the greedy prediction per position."""

    def __init__(self, model_id: str, device: str = "cuda", dtype: str = "bfloat16", backend: str = "hf"):
        import torch

        if backend != "hf":
            raise ValueError("only the transformers backend recomputes from recorded token ids today (ADR-0016)")
        try:
            from nnsight import TransformersModel as NNModel
        except ImportError:
            from nnsight import LanguageModel as NNModel
        self.torch = torch
        self.model_id, self.device, self.dtype_name, self.backend = model_id, device, dtype, backend
        t0 = time.time()
        self.model = NNModel(model_id, dispatch=True, torch_dtype=getattr(torch, dtype), device_map=device)
        self.load_seconds = round(time.time() - t0, 2)
        self.layers = self.model.model.layers
        self.revision = getattr(getattr(self.model, "config", None), "_commit_hash", None)

    @property
    def n_layers(self) -> int:
        return len(self.layers)

    def trace(self, seq: list[int], layer_ids: list[int]) -> dict[str, Any]:
        torch = self.torch
        input_ids = torch.tensor([seq], device=self.device)
        if self.device.startswith("cuda"):
            torch.cuda.reset_peak_memory_stats()
        t0 = time.time()
        saved: dict[int, Any] = {}
        with torch.no_grad(), self.model.trace({"input_ids": input_ids, "attention_mask": torch.ones_like(input_ids)}):
            for layer in layer_ids:
                out = self.layers[layer].output
                saved[layer] = (out[0] if isinstance(out, tuple | list) else out).save()
            pred = self.model.output.logits.argmax(-1).save()
        seconds = time.time() - t0
        hidden = {layer: t.detach()[0].to(torch.float16).cpu() for layer, t in saved.items()}   # [T, d]
        vram = torch.cuda.max_memory_allocated() / 2**20 if self.device.startswith("cuda") else None
        return {"hidden": hidden, "pred": pred.detach()[0].cpu().tolist(), "seconds": round(seconds, 3),
                "vram_mb": round(vram) if vram else None}


def capture(bundle_root: str | pathlib.Path, model_id: str, layers: list[int] | None = None, raw: str = "flagged",
            positions: str = "completion", limit: int | None = None, request_ids: list[str] | None = None,
            device: str = "cuda", dtype: str = "bfloat16", recomputer: Recomputer | None = None) -> dict[str, Any]:
    """Recompute activations for a bundle's requests; summaries for all, raw tensors per policy."""
    records = select(bundle_root, request_ids=request_ids, limit=limit)
    if not records:
        return {"captured": 0, "reason": "no chat completion with recorded token ids (observe.activations: true?)"}
    rc = recomputer or Recomputer(model_id, device=device, dtype=dtype)
    layer_ids = layers or default_layers(rc.n_layers)
    raw_ids = raw_request_ids(bundle_root, records, raw)
    started = time.time()
    entries = []
    for rec in records:
        prompt_ids, completion_ids = token_sequence(rec)
        seq = prompt_ids + completion_ids
        out = rc.trace(seq, layer_ids)
        P = len(prompt_ids)
        norm_by_layer = {str(layer): norms(h.float().tolist()) for layer, h in out["hidden"].items()}
        summary = {"request_id": rec["request_id"], "layers": layer_ids, "n_prompt": P,
                   "n_completion": len(completion_ids), "norms": norm_by_layer}
        summary_file = write_summary(bundle_root, rec["request_id"], summary)
        raw_file = None
        if rec["request_id"] in raw_ids:
            from safetensors.torch import save_file

            start = P if positions == "completion" else 0
            raw_dir = activations_dir(bundle_root) / "raw"
            raw_dir.mkdir(parents=True, exist_ok=True)
            path = raw_dir / f"{rec['request_id']}.safetensors"
            save_file({f"layer_{layer}": h[start:].contiguous() for layer, h in out["hidden"].items()}, str(path))
            raw_file = str(path.relative_to(pathlib.Path(bundle_root)))
        entry = {"request_id": rec["request_id"], "agent_id": rec.get("agent"), "ts": rec.get("ts"), "method": METHOD,
                 "backend": rc.backend, "model": model_id, "revision": rc.revision, "dtype": rc.dtype_name,
                 "layers": layer_ids, "n_prompt": P, "n_completion": len(completion_ids),
                 "agreement": agreement(out["pred"], P, completion_ids),
                 "temperature": (rec.get("request") or {}).get("temperature"),
                 "seconds": out["seconds"], "vram_mb": out["vram_mb"], "summary_file": summary_file,
                 "raw_file": raw_file, "raw_positions": positions if raw_file else None, "captured_at": time.time()}
        write_index_entry(bundle_root, entry)
        entries.append(entry)
    total = time.time() - started
    tokens = sum(e["n_prompt"] + e["n_completion"] for e in entries)
    report = {"captured": len(entries), "raw": sum(1 for e in entries if e["raw_file"]), "layers": layer_ids,
              "model": model_id, "backend": rc.backend, "dtype": rc.dtype_name, "model_load_seconds": rc.load_seconds,
              "total_seconds": round(total, 2), "seconds_per_request": round(total / len(entries), 3),
              "tokens": tokens, "tokens_per_second": round(tokens / total, 1) if total else None,
              "peak_vram_mb": max((e["vram_mb"] or 0) for e in entries),
              "agreement_mean": round(sum(e["agreement"] for e in entries) / len(entries), 4),
              "agreement_min": min(e["agreement"] for e in entries)}
    (activations_dir(bundle_root) / "capture.json").write_text(json.dumps(report, indent=1))
    return report


def verify(bundle_root: str | pathlib.Path, model_id: str, tolerance_rel_l2: float = 0.05, limit: int | None = None,
           device: str = "cuda", dtype: str = "bfloat16", recomputer: Recomputer | None = None) -> dict[str, Any]:
    """Recompute again (possibly in another dtype) and compare with what `capture` stored: the summary
    norms per layer and, where raw tensors exist, the tensors themselves."""
    index = read_index(bundle_root)
    if not index:
        return {"verified": 0, "ok": False, "reason": "nothing captured"}
    index = index[:limit] if limit else index
    rc = recomputer or Recomputer(model_id, device=device, dtype=dtype)
    by_id = {r["request_id"]: r for r in select(bundle_root, request_ids=[e["request_id"] for e in index])}
    results = []
    for entry in index:
        rec = by_id.get(entry["request_id"])
        if rec is None:
            continue
        prompt_ids, completion_ids = token_sequence(rec)
        out = rc.trace(prompt_ids + completion_ids, entry["layers"])
        stored = read_summary(bundle_root, entry["request_id"])
        per_layer = {}
        for layer, h in out["hidden"].items():
            stats = vector_stats(stored["norms"][str(layer)], norms(h.float().tolist()))
            if entry.get("raw_file"):
                from safetensors.torch import load_file

                raw = load_file(str(pathlib.Path(bundle_root) / entry["raw_file"]))[f"layer_{layer}"]
                start = len(prompt_ids) if entry.get("raw_positions") == "completion" else 0
                stats["raw"] = vector_stats(raw.float().flatten().tolist(), h[start:].float().flatten().tolist())
            per_layer[str(layer)] = stats
        worst = max(max(v["rel_l2"], (v.get("raw") or {}).get("rel_l2", 0.0)) for v in per_layer.values())
        results.append({"request_id": entry["request_id"], "layers": per_layer, "worst_rel_l2": worst,
                        "agreement": agreement(out["pred"], len(prompt_ids), completion_ids),
                        "stored_agreement": entry["agreement"], "within_tolerance": worst <= tolerance_rel_l2})
    report = {"verified": len(results), "ok": all(r["within_tolerance"] for r in results),
              "tolerance": {"rel_l2": tolerance_rel_l2},
              "worst_rel_l2": max((r["worst_rel_l2"] for r in results), default=None),
              "agreement_matches": all(r["agreement"] == r["stored_agreement"] for r in results),
              "stored": {"backend": index[0]["backend"], "dtype": index[0]["dtype"]},
              "second_pass": {"backend": rc.backend, "dtype": rc.dtype_name}, "requests": results,
              "verified_at": time.time()}
    (activations_dir(bundle_root) / "verification.json").write_text(json.dumps(report, indent=1))
    return report
