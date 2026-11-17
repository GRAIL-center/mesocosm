"""Run one training of the published steganographic-collusion code (Mathew et al., arXiv:2410.03768).

The repository is not changed. This adapter takes the repository's own settings for the published university
run, applies the trial's condition and seed, starts the repository's trainer, and also writes to files what
the trainer hands to its logging method. The repository's launcher is not used: it asks for Weights & Biases
and Hugging Face logins, which the public models and data do not need. It runs with the interpreter of the
repository's environment.
"""

from __future__ import annotations

import json
import os
import pathlib
import sys
import time

HERE = pathlib.Path(__file__).resolve().parent
CODE = HERE / "repo" / "lasr-steganography" / "learning" / "rl"
RUN_TYPE = "EOL3_CV_UNI"   # the repository's settings for the paper's Table 1 row "GBRL, O, Uni"
FINAL_SAMPLES = 1920       # the held-out split of 2,000 texts in the repository's batches of 192
COUNTERS = "counters/"


def apply(hparams: dict, condition: dict, seed: int) -> dict:
    """The repository's settings for one condition. These keys are all that differ from the published run."""
    hparams["seed"] = seed
    hparams["enc"]["model_name"] = condition["model.sender"]
    hparams["train"]["num_epochs"] = condition["training.cycles"]
    hparams["train"]["do_update_enc"] = condition["training.sender_rl"]
    hparams["overseer"]["use_overseer"] = condition["training.overseer_penalty"]
    if condition["training.batch"] == "fits_40gb":
        hparams["enc"].update(batch_size=128, mini_batch_size=16, gradient_accumulation_steps=8)
        hparams["dec"].update(batch_size=128, mini_batch_size=64, gradient_accumulation_steps=2)
    return hparams


def plain(value: object) -> object:
    """A logged value as JSON: a tensor or numpy value becomes a number or a list, anything else text."""
    if isinstance(value, (bool, int, float, str)) or value is None:
        return value
    return value.tolist() if hasattr(value, "tolist") else str(value)


def split(logged: dict) -> tuple[dict, dict, dict]:
    """One logged dictionary as (position in training, numbers, tables)."""
    position, numbers, tables = {}, {}, {}
    for key, value in logged.items():
        if hasattr(value, "columns") and hasattr(value, "data"):
            tables[key] = [dict(zip(value.columns, (plain(cell) for cell in row), strict=True)) for row in value.data]
            continue
        value = plain(value)
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            continue
        (position if key.startswith(COUNTERS) else numbers)[key.removeprefix(COUNTERS)] = value
    return position, numbers, tables


def measures(final: dict) -> dict[str, float | None]:
    """The paper's two metrics from the repository's evaluation of the held-out split."""
    flagged = final.get("eval_eval/rewards/proportion_sus")
    return {"task_success": plain(final["eval_eval/dec_accuracy"]),
            "overseer_bypass": None if flagged is None else 1 - plain(flagged)}


def main() -> None:
    condition = json.loads(pathlib.Path(os.environ["TRIAL_CONDITION"]).read_text(encoding="utf-8"))
    seed, outcome = int(os.environ["TRIAL_SEED"]), pathlib.Path(os.environ["TRIAL_OUTCOME"])
    trial = outcome.parent
    os.environ.update({"WANDB_MODE": "offline", "WANDB_DIR": str(trial), "WANDB_SILENT": "true"})
    os.environ.setdefault("PYTORCH_CUDA_ALLOC_CONF", "expandable_segments:True")
    sys.path.insert(0, str(CODE))

    import torch
    from hparams.utils import RLExpRunType, get_run_type_hparams
    from stego_trainer import StegoTrainer
    from trl import set_seed

    hparams = apply(get_run_type_hparams(RLExpRunType[RUN_TYPE], trial.name), condition, seed)
    (trial / "settings.json").write_text(json.dumps(hparams, indent=1, default=str), encoding="utf-8")
    set_seed(seed)
    torch.manual_seed(seed)

    log = StegoTrainer.do_wandb_log

    def record(self: StegoTrainer, logged: dict) -> None:
        log(self, logged)   # adds the update counters to the dictionary and passes it to Weights & Biases
        position, numbers, tables = split(logged)
        lines = {"events.jsonl": [{**position, **numbers}] if numbers else [],
                 # Only evaluation tables: training logs several hundred texts per update.
                 "texts.jsonl": [{**position, "table": name, "rows": rows} for name, rows in tables.items()
                                 if name.startswith("eval")]}
        for name, entries in lines.items():
            if entries:
                with (trial / name).open("a", encoding="utf-8") as handle:
                    handle.writelines(json.dumps(entry) + "\n" for entry in entries)

    StegoTrainer.do_wandb_log = record
    started = time.time()
    trainer = StegoTrainer(hparams)
    trainer.train()
    # The repository's own evaluation over the whole held-out split. The overseer judges the final texts in
    # every condition; whether its judgment entered the reward during training is the condition.
    trainer.hparams["overseer"]["use_overseer"] = True
    trainer.hparams["eval"]["n_eval_samples"] = FINAL_SAMPLES
    final = {key: plain(value) for key, value in trainer.evaluate("eval", trainer.data_loader_dict["eval"]).items()}
    (trial / "final_evaluation.json").write_text(json.dumps(final, indent=1), encoding="utf-8")
    updates = trainer.n_dec_updates + trainer.n_enc_updates
    outcome.write_text(json.dumps({"measures": measures(final), "note": (
        f"{updates} updates ({trainer.n_enc_updates} of the sender) in {(time.time() - started) / 60:.0f} minutes; "
        f"final evaluation on {final['eval_eval/n_samples_evalled']} held-out texts; "
        f"peak GPU memory {torch.cuda.max_memory_allocated() / 2**30:.1f} GiB")}), encoding="utf-8")


if __name__ == "__main__":
    main()
