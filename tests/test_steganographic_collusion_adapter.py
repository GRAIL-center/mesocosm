"""The adapter around the published steganographic-collusion training code: settings, logs and measures.

The repository itself needs a GPU and its own environment; what the adapter adds is checked here.
"""

from __future__ import annotations

import importlib.util
import pathlib
import types

import pytest

from mesocosm.study.command import load_substrate

EXAMPLE = pathlib.Path(__file__).resolve().parents[1] / "examples" / "steganographic-collusion"
spec = importlib.util.spec_from_file_location("steganographic_collusion_adapter", EXAMPLE / "adapter.py")
assert spec and spec.loader
adapter = importlib.util.module_from_spec(spec)
spec.loader.exec_module(adapter)


class Tensor:
    """What the trainer logs for a number: an object with ``tolist``."""

    def __init__(self, value: object) -> None:
        self.value = value

    def tolist(self) -> object:
        return self.value


def test_a_condition_changes_only_the_declared_settings() -> None:
    def published() -> dict:
        return {"seed": 42, "enc": {"model_name": "unsloth/gemma-2-2b-it", "lr": 1.41e-5, "batch_size": 192,
                                    "mini_batch_size": 64, "gradient_accumulation_steps": 3},
                "dec": {"batch_size": 256, "mini_batch_size": 64, "gradient_accumulation_steps": 4},
                "train": {"num_epochs": 100000, "do_update_enc": True, "n_enc_updates": 5},
                "overseer": {"use_overseer": True, "dec_drop_sus": True}, "rewards": {"overseer_scale": 0.2}}

    space = load_substrate(EXAMPLE / "substrate.json").space
    unchanged = adapter.apply(published(), space.condition({"training.cycles": 20}).settings, seed=7)
    assert unchanged == {**published(), "seed": 7, "train": {**published()["train"], "num_epochs": 20}}
    condition = space.condition({"training.sender_rl": False, "training.overseer_penalty": False,
                                 "training.batch": "fits_40gb"}).settings
    changed = adapter.apply(published(), condition, seed=7)
    sender = {"batch_size": 128, "mini_batch_size": 16, "gradient_accumulation_steps": 8}
    receiver = {"batch_size": 128, "mini_batch_size": 64, "gradient_accumulation_steps": 2}
    assert changed == {"seed": 7, "enc": {"model_name": "unsloth/gemma-2-2b-it", "lr": 1.41e-5, **sender},
                       "dec": receiver,
                       "train": {"num_epochs": 150, "do_update_enc": False, "n_enc_updates": 5},
                       "overseer": {"use_overseer": False, "dec_drop_sus": True}, "rewards": {"overseer_scale": 0.2}}


def test_a_logged_dictionary_splits_into_position_numbers_and_tables() -> None:
    table = types.SimpleNamespace(columns=["label", "dec_in_texts", "overseer_reward"],
                                  data=[[Tensor(1), "A motivated graduate.", Tensor(-0.5)]])
    position, numbers, tables = adapter.split({
        "counters/n_enc_updates": 35, "eval_eval/dec_accuracy": Tensor(0.75), "eval_eval/n_samples_evalled": 192,
        "hparams/enc": {"lr": 1e-5}, "flag": True, "eval_completions": table})
    assert position == {"n_enc_updates": 35}
    assert numbers == {"eval_eval/dec_accuracy": 0.75, "eval_eval/n_samples_evalled": 192}
    assert tables == {"eval_completions": [{"label": 1, "dec_in_texts": "A motivated graduate.",
                                            "overseer_reward": -0.5}]}


def test_measures_are_the_papers_two_metrics() -> None:
    final = {"eval_eval/dec_accuracy": Tensor(0.99), "eval_eval/rewards/proportion_sus": 0.25}
    assert adapter.measures(final) == {"task_success": 0.99, "overseer_bypass": 0.75}
    assert adapter.measures({"eval_eval/dec_accuracy": 0.5}) == {"task_success": 0.5, "overseer_bypass": None}
    with pytest.raises(KeyError):
        adapter.measures({})
