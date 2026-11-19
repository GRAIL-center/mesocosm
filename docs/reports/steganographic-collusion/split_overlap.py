"""Count how many sentences of the published dataset's test split also occur in its training split.

Usage: python3 split_overlap.py

Writes generated/split_overlap.json, which build.py reads. Needs datasets and network access.
"""

from __future__ import annotations

import json
import pathlib

from datasets import load_dataset

OUT = pathlib.Path(__file__).resolve().parent / "generated" / "split_overlap.json"
SOURCE = "rmcc11/lasr-toy-cv-with-paraphrased"

data = load_dataset(SOURCE)
test, train = set(data["test"]["input"]), set(data["train"]["input"])
OUT.write_text(json.dumps({"dataset": SOURCE, "test_sentences": len(data["test"]), "test_distinct": len(test),
                           "test_distinct_also_in_train": len(test & train),
                           "test_sentences_also_in_train": sum(text in train for text in data["test"]["input"]),
                           "train_sentences": len(data["train"]), "train_distinct": len(train)}, indent=1) + "\n",
               encoding="utf-8")
print(OUT.read_text(encoding="utf-8"))
