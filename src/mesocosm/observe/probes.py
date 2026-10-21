"""Linear probes over recomputed activations (ADR-0031).

A probe is a direction in the residual stream at one layer: logistic regression trained on the raw tensors
`mesocosm activations capture` stored (ADR-0016), one pooled vector per request, labelled by the judge's flags
(a flagged agent's requests are positives) or by a file the researcher wrote. The probe is a safetensors
file whose metadata names what it was trained on (model, revision, dtype, layer, pooling, counts), so
scoring refuses a bundle captured differently instead of quietly scoring the wrong thing. Scores per request
go to `activations/probes/<name>.jsonl` in the bundle, next to the activation index.

numpy only: no GPU, no torch. The math is small (hundreds of requests, a few thousand dimensions).
"""

from __future__ import annotations

import json
import pathlib
import time
from collections.abc import Sequence
from typing import Any

import numpy as np
from safetensors.numpy import load_file, save_file

from mesocosm.core.records import RunBundle
from mesocosm.observe.activations import activations_dir, read_index

POOLS = ("mean", "last")


def pooled(path: pathlib.Path, layer: int, pool: str) -> np.ndarray:
    """One vector for a request's raw tensor at `layer`: the mean over positions, or the last position."""
    tensor = load_file(str(path))[f"layer_{layer}"].astype(np.float32)
    if tensor.ndim != 2 or tensor.shape[0] == 0:
        raise ValueError(f"{path}: layer_{layer} is not a [positions, hidden] tensor")
    return tensor.mean(axis=0) if pool == "mean" else tensor[-1]


def raw_entries(bundle_root: str | pathlib.Path, layer: int) -> list[dict[str, Any]]:
    """Index entries with a raw tensor that includes `layer` (the latest capture of each request)."""
    latest: dict[str, dict[str, Any]] = {}
    for e in read_index(bundle_root):
        if e.get("raw_file") and layer in (e.get("layers") or []):
            latest[e["request_id"]] = e
    return list(latest.values())


def labels_from_flags(bundle_root: str | pathlib.Path, entries: list[dict[str, Any]]) -> dict[str, int]:
    """Positives: requests of an agent the judge flagged, and requests named in a flag's evidence."""
    flags = RunBundle.open(bundle_root).flags()
    flagged_agents = {f.get("agent_id") for f in flags if f.get("agent_id")}
    named = {e.get("request_id") for f in flags for e in f.get("evidence") or [] if e.get("request_id")}
    return {e["request_id"]: int(e.get("agent_id") in flagged_agents or e["request_id"] in named) for e in entries}


def labels_from_file(path: str | pathlib.Path) -> dict[str, int]:
    """{request_id: 0 | 1} written by the researcher."""
    data = json.loads(pathlib.Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"{path}: expected {{request_id: 0 | 1}}")
    return {str(k): int(bool(v)) for k, v in data.items()}


def fit_logistic(X: np.ndarray, y: np.ndarray, l2: float = 1e-2, steps: int = 500, lr: float = 0.5) -> dict[str, Any]:
    """Standardize, then full-batch gradient descent on the regularized logistic loss."""
    mu, sigma = X.mean(axis=0), X.std(axis=0) + 1e-6
    Z = (X - mu) / sigma
    w, b = np.zeros(X.shape[1], dtype=np.float32), np.float32(0.0)
    n = len(y)
    for _ in range(steps):
        p = 1.0 / (1.0 + np.exp(-(Z @ w + b)))
        grad_w = Z.T @ (p - y) / n + l2 * w
        grad_b = float((p - y).mean())
        w -= lr * grad_w
        b -= lr * grad_b
    p = 1.0 / (1.0 + np.exp(-(Z @ w + b)))
    loss = float(-np.mean(y * np.log(p + 1e-9) + (1 - y) * np.log(1 - p + 1e-9)))
    return {"w": w.astype(np.float32), "b": np.array([b], dtype=np.float32), "mu": mu.astype(np.float32),
            "sigma": sigma.astype(np.float32), "train_accuracy": float(((p >= 0.5) == (y >= 0.5)).mean()),
            "train_loss": round(loss, 6)}


def train(bundle_roots: Sequence[str | pathlib.Path], layer: int, out: str | pathlib.Path,
          labels_file: str | pathlib.Path | None = None, pool: str = "mean", l2: float = 1e-2,
          steps: int = 500) -> dict[str, Any]:
    """Train a probe on every captured request of the bundles that has a raw tensor at `layer`."""
    if pool not in POOLS:
        raise ValueError(f"pool must be one of {POOLS}")
    given = labels_from_file(labels_file) if labels_file else None
    rows: list[np.ndarray] = []
    y: list[int] = []
    used: list[dict[str, Any]] = []
    captures: set[tuple] = set()
    for root in bundle_roots:
        entries = raw_entries(root, layer)
        labels = given if given is not None else labels_from_flags(root, entries)
        for e in entries:
            if e["request_id"] not in labels:
                continue
            rows.append(pooled(pathlib.Path(root) / e["raw_file"], layer, pool))
            y.append(labels[e["request_id"]])
            used.append({"bundle": str(root), "request_id": e["request_id"], "agent_id": e.get("agent_id"),
                         "label": labels[e["request_id"]]})
            captures.add((e.get("model"), e.get("revision"), e.get("dtype"), e.get("raw_positions")))
    if not rows:
        raise ValueError(f"no captured request with a raw tensor at layer {layer} and a label")
    if len(captures) > 1:
        raise ValueError("the bundles were captured differently (model, revision, dtype, positions): "
                         f"{sorted(map(str, captures))}")
    X, yv = np.stack(rows), np.array(y, dtype=np.float32)
    if yv.min() == yv.max():
        raise ValueError(f"all {len(yv)} labels are {int(yv[0])}: a probe needs both classes")
    fit = fit_logistic(X, yv, l2=l2, steps=steps)
    model, revision, dtype, positions = next(iter(captures))
    meta = {"model": str(model), "revision": str(revision), "dtype": str(dtype), "positions": str(positions),
            "layer": str(layer), "pool": pool, "hidden": str(X.shape[1]), "n_pos": str(int(yv.sum())),
            "n_neg": str(int(len(yv) - yv.sum())), "train_accuracy": str(fit["train_accuracy"]),
            "train_loss": str(fit["train_loss"]), "labels": "file" if given is not None else "flags",
            "bundles": json.dumps(sorted({u["bundle"] for u in used})), "created": str(time.time()), "l2": str(l2),
            "steps": str(steps)}
    out = pathlib.Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    save_file({k: fit[k] for k in ("w", "b", "mu", "sigma")}, str(out), metadata=meta)
    return {"probe": str(out), "layer": layer, "pool": pool, "requests": len(used), "n_pos": int(yv.sum()),
            "n_neg": int(len(yv) - yv.sum()), "train_accuracy": fit["train_accuracy"], "train_loss": fit["train_loss"],
            "model": model, "hidden": int(X.shape[1])}


def load_probe(path: str | pathlib.Path) -> tuple[dict[str, np.ndarray], dict[str, str]]:
    from safetensors import safe_open

    with safe_open(str(path), framework="np") as f:
        meta = dict(f.metadata() or {})
    return load_file(str(path)), meta


def score(bundle_root: str | pathlib.Path, probe_path: str | pathlib.Path, name: str | None = None) -> dict[str, Any]:
    """Score every captured request of the bundle that has a raw tensor at the probe's layer. Refuses a bundle
    captured with another model, revision, dtype or positions than the probe was trained on."""
    tensors, meta = load_probe(probe_path)
    layer, pool = int(meta["layer"]), meta.get("pool", "mean")
    entries = raw_entries(bundle_root, layer)
    if not entries:
        raise ValueError(f"no captured request with a raw tensor at layer {layer} in {bundle_root}")
    mismatches = sorted({f"{k}: probe {meta.get(k)!r}, bundle {e.get(k if k != 'positions' else 'raw_positions')!r}"
                         for e in entries for k in ("model", "revision", "dtype", "positions")
                         if str(e.get(k if k != "positions" else "raw_positions")) != str(meta.get(k))})
    if mismatches:
        raise ValueError("the bundle was captured differently from the probe's training data: " + "; ".join(mismatches))
    name = name or pathlib.Path(probe_path).stem
    w, b, mu, sigma = tensors["w"], float(tensors["b"][0]), tensors["mu"], tensors["sigma"]
    out_dir = activations_dir(bundle_root) / "probes"
    out_dir.mkdir(parents=True, exist_ok=True)
    scored: list[dict[str, Any]] = []
    with (out_dir / f"{name}.jsonl").open("w", encoding="utf-8") as f:
        for e in sorted(entries, key=lambda e: float(e.get("ts") or 0)):
            x = (pooled(pathlib.Path(bundle_root) / e["raw_file"], layer, pool) - mu) / sigma
            logit = float(x @ w + b)
            row = {"request_id": e["request_id"], "agent_id": e.get("agent_id"), "ts": e.get("ts"),
                   "score": round(1.0 / (1.0 + float(np.exp(-logit))), 6), "logit": round(logit, 6), "layer": layer,
                   "probe": name, "model": meta.get("model")}
            f.write(json.dumps(row) + "\n")
            scored.append(row)
    by_agent: dict[str, list[float]] = {}
    for r in scored:
        by_agent.setdefault(str(r["agent_id"]), []).append(r["score"])
    top = sorted(scored, key=lambda r: -r["score"])[:5]
    return {"probe": name, "file": str(out_dir / f"{name}.jsonl"), "layer": layer, "scored": len(scored),
            "mean": round(sum(r["score"] for r in scored) / len(scored), 6),
            "max": max(r["score"] for r in scored),
            "by_agent": {a: round(sum(v) / len(v), 6) for a, v in sorted(by_agent.items())},
            "top": [{"request_id": r["request_id"], "agent_id": r["agent_id"], "score": r["score"]} for r in top]}


def read_scores(bundle_root: str | pathlib.Path, name: str) -> list[dict[str, Any]]:
    path = activations_dir(bundle_root) / "probes" / f"{name}.jsonl"
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
