"""Spectrum of the trained sender's weight difference, read from the adapter file alone.

Usage: python3 spectrum.py <folder with adapter_config.json and adapter_model.safetensors> <out.json>
"""

from __future__ import annotations

import collections
import json
import sys

import torch
from safetensors.torch import load_file

folder, out = sys.argv[1], sys.argv[2]
with open(f"{folder}/adapter_config.json", encoding="utf-8") as file:
    config = json.load(file)
scale = config["lora_alpha"] / config["r"]
weights = load_file(f"{folder}/adapter_model.safetensors")
rows = []
for key in sorted(k for k in weights if "lora_A" in k):
    a, b = weights[key].float(), weights[key.replace("lora_A", "lora_B")].float()
    # The singular values of B A are those of the product of the two small triangular factors.
    energy = (torch.linalg.svdvals(torch.linalg.qr(b).R @ torch.linalg.qr(a.T).R.T) * scale) ** 2
    parts = key.split(".")
    rows.append({"layer": int(parts[parts.index("layers") + 1]),
                 "module": next(part for part in parts if part.endswith("_proj")),
                 "squared_norm": float(energy.sum()), "first_direction_share": float(energy[0] / energy.sum()),
                 "effective_rank": float(energy.sum() ** 2 / (energy**2).sum())})
total = sum(row["squared_norm"] for row in rows)
by_module: dict[str, float] = collections.defaultdict(float)
by_layer: dict[int, float] = collections.defaultdict(float)
for row in rows:
    by_module[row["module"]] += row["squared_norm"] / total
    by_layer[row["layer"]] += row["squared_norm"] / total
summary = {"modules": len(rows), "rank": config["r"], "squared_norm": total, "share_by_module": dict(by_module),
           "largest_layer_share": max(by_layer.values())}
for module in by_module:
    group = [row for row in rows if row["module"] == module]
    summary[module] = {"mean_first_direction_share": sum(row["first_direction_share"] for row in group) / len(group),
                       "mean_effective_rank": sum(row["effective_rank"] for row in group) / len(group)}
print(json.dumps(summary, indent=1))
with open(out, "w", encoding="utf-8") as file:
    json.dump({"summary": summary, "share_by_layer": dict(sorted(by_layer.items())), "modules": rows}, file, indent=1)
