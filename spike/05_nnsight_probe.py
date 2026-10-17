#!/usr/bin/env python3
"""Spike step 5: can we capture activations for a real request and recompute them?

Three phases, run as separate processes so the two engines never share the GPU:

  hf      transformers + NNsight: teacher-force the recorded tokens, save layer L over the
          prompt (and the completion window if present), greedy-agreement vs recorded tokens.
  vllm    NNsight's VLLM wrapper: same prompt, temperature 0, save layer L over the prompt
          (prefill step) + first sampled tokens.
  compare load both saves, report max/mean abs diff and per-position cosine.

Input: the last chat_completion record in the gateway log (default runs/spike/gateway.jsonl).
If the record has vLLM prompt_token_ids (gateway started with MESOCOSM_RETURN_TOKEN_IDS=1),
the exact token sequence is used; otherwise the messages are re-rendered with the tokenizer's
chat template and the script says so, because a re-rendered prompt may differ from what vLLM saw.

Usage (stop `vllm serve` first; both engines want the 3090):
    python 05_nnsight_probe.py hf      --model Qwen/Qwen3-4B-Instruct-2507 --layer 18
    python 05_nnsight_probe.py vllm    --model Qwen/Qwen3-4B-Instruct-2507 --layer 18
    python 05_nnsight_probe.py compare

Record in SPIKE.md: install pins that worked, shapes, agreement rate, diff stats, timings.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import sys
import time


def _last_chat_record(path: pathlib.Path, run: str | None, max_prompt_tokens: int) -> dict:
    """Last successful chat record of `run` (any run if None) whose prompt fits the probe budget.
    OpenCode's requests are ~11k tokens; the serve-phase curl request (170 tokens, temperature 0)
    is the right input for a recompute comparison."""
    recs = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    chats = [r for r in recs if r.get("event") == "chat_completion" and r.get("request")
             and r.get("status", 200) == 200 and (run is None or r.get("run") == run)]
    fitting = [r for r in chats if (r.get("response") or {}).get("usage", {}).get("prompt_tokens", 10**9) <= max_prompt_tokens]
    if not fitting:
        sys.exit(f"no chat_completion record in {path} (run={run!r}) with <= {max_prompt_tokens} prompt tokens")
    return fitting[-1]


def _prompt_from_record(rec: dict, tokenizer) -> tuple[list[int], str, list[int] | None, str]:
    """Return (prompt_ids, prompt_text, completion_ids_or_None, provenance)."""
    resp = rec.get("response") or {}
    req = rec["request"]
    completion_ids = resp.get("token_ids") or None
    if resp.get("prompt_token_ids"):
        ids = list(resp["prompt_token_ids"])
        text = tokenizer.decode(ids, skip_special_tokens=False)
        return ids, text, completion_ids, "exact prompt_token_ids from vLLM (return_token_ids)"
    kwargs = {"tokenize": False, "add_generation_prompt": True}
    if req.get("tools"):
        kwargs["tools"] = req["tools"]
    text = tokenizer.apply_chat_template(req["messages"], **kwargs)
    ids = tokenizer(text, add_special_tokens=False)["input_ids"]
    return ids, text, completion_ids, "RE-RENDERED with tokenizer chat template (may differ from vLLM's prompt)"


def _residual_from_layer_output(out):
    """HF decoder layers return a tuple whose [0] is the residual stream; vLLM's Qwen/Llama
    layers return (hidden, residual) whose SUM is the residual stream after the layer."""
    import torch

    if isinstance(out, torch.Tensor):
        return out
    if isinstance(out, (tuple, list)):
        if len(out) == 2 and all(isinstance(x, torch.Tensor) for x in out) and out[0].shape == out[1].shape:
            return out[0] + out[1]
        return out[0]
    raise TypeError(f"unexpected layer output type {type(out)!r}")


def phase_hf(args) -> None:
    import torch
    from safetensors.torch import save_file
    from transformers import AutoTokenizer

    try:
        from nnsight import TransformersModel as NNModel  # nnsight >= 0.5 naming
    except ImportError:  # older nnsight
        from nnsight import LanguageModel as NNModel

    rec = _last_chat_record(pathlib.Path(args.gateway_log), args.run, args.max_prompt_tokens)
    tok = AutoTokenizer.from_pretrained(args.model)
    prompt_ids, prompt_text, completion_ids, provenance = _prompt_from_record(rec, tok)
    print(f"[hf] request_id={rec['request_id']} prompt_tokens={len(prompt_ids)} "
          f"completion_tokens={len(completion_ids) if completion_ids else 0}")
    print(f"[hf] prompt provenance: {provenance}")

    t0 = time.time()
    dtype = torch.float16 if args.dtype == "half" else torch.bfloat16
    model = NNModel(args.model, dispatch=True, torch_dtype=dtype, device_map="cuda")
    print(f"[hf] model loaded in {time.time() - t0:.1f}s")
    layers = model.model.layers
    L = args.layer if args.layer is not None else len(layers) // 2
    print(f"[hf] n_layers={len(layers)} probing layer {L}")

    seq = list(prompt_ids) + (list(completion_ids) if completion_ids else [])
    input_ids = torch.tensor([seq], device="cuda")
    P = len(prompt_ids)
    t0 = time.time()
    # Save the layer's residual stream, the greedy prediction per position and the logits at the
    # last prompt position only: full [T, vocab] logits for a long prompt do not fit next to the model.
    with torch.no_grad(), model.trace({"input_ids": input_ids, "attention_mask": torch.ones_like(input_ids)}):
        layer_out = layers[L].output
        hidden = (layer_out[0] if isinstance(layer_out, (tuple, list)) else layer_out).save()
        logits = model.output.logits
        pred = logits.argmax(-1).save()
        last_logits = logits[0, P - 1].save()
    trace_s = time.time() - t0
    hidden = hidden.detach().float().cpu()[0]          # [T, d]
    pred = pred.detach().cpu()[0]                      # [T]
    last_logits = last_logits.detach().float().cpu()   # [V]
    print(f"[hf] trace {trace_s:.2f}s hidden={tuple(hidden.shape)} pred={tuple(pred.shape)}")

    result = {"phase": "hf", "model": args.model, "layer": L, "request_id": rec["request_id"],
              "prompt_tokens": P, "provenance": provenance, "trace_seconds": trace_s,
              "vram_peak_gb": torch.cuda.max_memory_allocated() / 2**30}
    if completion_ids:
        guesses = pred[P - 1: P - 1 + len(completion_ids)].tolist()
        agree = sum(int(a == b) for a, b in zip(guesses, completion_ids, strict=False)) / len(completion_ids)
        result["greedy_agreement_vs_recorded"] = agree
        result["recorded_temperature"] = (rec["request"] or {}).get("temperature")
        print(f"[hf] greedy agreement with recorded completion: {agree:.3f} "
              f"(meaningful only if vLLM sampled at temperature 0)")
    else:
        result["first_greedy_token"] = int(pred[P - 1])
        print(f"[hf] first greedy token after prompt: {result['first_greedy_token']!r} "
              f"{tok.decode([result['first_greedy_token']])!r}")

    out = pathlib.Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    save_file({"hidden_prompt": hidden[:P].contiguous(), "logits_last_prompt": last_logits.contiguous()},
              str(out / "hf.safetensors"))
    (out / "hf.json").write_text(json.dumps(result, indent=1))
    (out / "prompt.json").write_text(json.dumps({"prompt_ids": prompt_ids, "prompt_text": prompt_text,
                                                 "completion_ids": completion_ids}, ensure_ascii=False))
    print(f"[hf] saved to {out}")


def phase_vllm(args) -> None:
    from safetensors.torch import save_file
    from transformers import AutoTokenizer

    try:
        from nnsight.modeling.vllm import VLLM
    except ImportError as e:
        sys.exit(f"nnsight vLLM wrapper unavailable: {e!r}. Install with: pip install 'nnsight[vllm]' "
                 f"(docs tested on vLLM 0.27.1; an upstream rename can break the import).")

    out = pathlib.Path(args.out)
    prompt = json.loads((out / "prompt.json").read_text())
    tok = AutoTokenizer.from_pretrained(args.model)
    prompt_ids, prompt_text = prompt["prompt_ids"], prompt["prompt_text"]

    # The wrapper takes text prompts only: check that re-tokenizing the text gives the same ids.
    retok = tok(prompt_text, add_special_tokens=False)["input_ids"]
    mismatch = sum(int(a != b) for a, b in zip(retok, prompt_ids, strict=False)) + abs(len(retok) - len(prompt_ids))
    print(f"[vllm] re-tokenization mismatches vs prompt_ids: {mismatch} (len {len(retok)} vs {len(prompt_ids)})")

    t0 = time.time()
    model = VLLM(args.model, dispatch=True, gpu_memory_utilization=args.gpu_mem,
                 max_model_len=args.max_model_len, dtype=args.dtype)
    print(f"[vllm] engine built in {time.time() - t0:.1f}s")
    L = args.layer if args.layer is not None else len(model.model.layers) // 2

    t0 = time.time()
    with model.trace(prompt_text, temperature=0.0, top_p=1, max_tokens=args.max_tokens):
        layer_out = model.model.layers[L].output.save()
        logits = model.logits.save()
        samples = model.samples.save()
    trace_s = time.time() - t0

    hidden = _residual_from_layer_output(layer_out).detach().float().cpu()   # prefill: [P, d]
    logits = logits.detach().float().cpu()
    samples_l = samples.detach().cpu().tolist() if hasattr(samples, "detach") else list(samples)
    print(f"[vllm] trace {trace_s:.2f}s hidden={tuple(hidden.shape)} logits={tuple(logits.shape)} "
          f"samples={samples_l[:8]} -> {tok.decode([int(s) for s in samples_l[:8]])!r}")
    print(f"[vllm] layer output type: {type(layer_out).__name__} "
          f"({'(hidden, residual) pair summed' if isinstance(layer_out, (tuple, list)) else 'tensor'})")

    # Plain generation for overhead comparison.
    t0 = time.time()
    _ = model.generate(prompt_text, temperature=0.0, max_tokens=args.max_tokens)
    gen_s = time.time() - t0

    result = {"phase": "vllm", "model": args.model, "layer": L, "prompt_tokens": len(prompt_ids),
              "retokenization_mismatches": mismatch, "trace_seconds": trace_s,
              "plain_generate_seconds": gen_s, "hidden_shape": list(hidden.shape),
              "first_samples": [int(s) for s in samples_l[:8]]}
    last_logits = logits[-1] if logits.dim() == 2 else logits.reshape(-1, logits.shape[-1])[-1]
    save_file({"hidden_prompt": hidden.contiguous(), "logits_last_prompt": last_logits.contiguous()},
              str(out / "vllm.safetensors"))
    (out / "vllm.json").write_text(json.dumps(result, indent=1))
    print(f"[vllm] saved to {out}")


def phase_compare(args) -> None:
    import torch
    from safetensors.torch import load_file

    out = pathlib.Path(args.out)
    hf = load_file(str(out / "hf.safetensors"))
    vl = load_file(str(out / "vllm.safetensors"))
    hj = json.loads((out / "hf.json").read_text())
    vj = json.loads((out / "vllm.json").read_text())
    a, b = hf["hidden_prompt"], vl["hidden_prompt"]
    n = min(a.shape[0], b.shape[0])
    if a.shape != b.shape:
        print(f"[compare] shape mismatch hf={tuple(a.shape)} vllm={tuple(b.shape)}; comparing first {n} rows")
    a, b = a[:n], b[:n]
    diff = (a - b).abs()
    cos = torch.nn.functional.cosine_similarity(a, b, dim=-1)
    rel = diff.norm(dim=-1) / a.norm(dim=-1).clamp_min(1e-6)
    la, lb = hf["logits_last_prompt"], vl["logits_last_prompt"]
    report = {
        "layer_hf": hj["layer"], "layer_vllm": vj["layer"], "rows_compared": n,
        "hidden_max_abs_diff": diff.max().item(), "hidden_mean_abs_diff": diff.mean().item(),
        "hidden_rel_l2_mean": rel.mean().item(), "hidden_rel_l2_max": rel.max().item(),
        "hidden_cosine_min": cos.min().item(), "hidden_cosine_mean": cos.mean().item(),
        "last_logit_argmax_equal": bool(la.argmax() == lb.argmax()),
        "last_logit_max_abs_diff": (la - lb).abs().max().item(),
        "hf_trace_seconds": hj["trace_seconds"], "vllm_trace_seconds": vj["trace_seconds"],
        "vllm_plain_generate_seconds": vj["plain_generate_seconds"],
        "vllm_capture_overhead_ratio": vj["trace_seconds"] / max(vj["plain_generate_seconds"], 1e-6),
        "prompt_provenance": hj["provenance"],
    }
    (out / "compare.json").write_text(json.dumps(report, indent=1))
    for k, v in report.items():
        print(f"[compare] {k}: {v}")
    print("[compare] interpretation: bf16 kernels differ between engines; expect rel-L2 ~1e-2 and cosine "
          "> 0.99. A bit-exact match is NOT expected across engines; for the recompute guarantee in the "
          "proposal, recompute with the same engine (vLLM wrapper, batch 1, eager) and compare again.")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("phase", choices=["hf", "vllm", "compare"])
    ap.add_argument("--model", default="Qwen/Qwen3-4B-Instruct-2507")
    ap.add_argument("--layer", type=int, default=None, help="decoder layer index (default: middle)")
    ap.add_argument("--gateway-log", default="runs/spike/gateway.jsonl")
    ap.add_argument("--run", default=None, help="X-Mesocosm-Run label to pick the record from (default: any)")
    ap.add_argument("--max-prompt-tokens", type=int, default=4096,
                    help="skip records with longer prompts; OpenCode requests are ~11k tokens")
    ap.add_argument("--out", default="runs/spike/activations")
    ap.add_argument("--max-tokens", type=int, default=16)
    ap.add_argument("--gpu-mem", type=float, default=0.6, help="vLLM gpu_memory_utilization")
    ap.add_argument("--dtype", choices=["bfloat16", "half"], default="bfloat16",
                    help="half on pre-Ampere GPUs such as the T4")
    ap.add_argument("--max-model-len", type=int, default=16384)
    args = ap.parse_args()
    {"hf": phase_hf, "vllm": phase_vllm, "compare": phase_compare}[args.phase](args)


if __name__ == "__main__":  # required: vLLM uses the spawn start method
    main()
