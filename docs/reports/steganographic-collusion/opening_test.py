"""Test how much of the receiver's accuracy the opening of the sender's text carries.

Usage: python3 opening_test.py <trial directory of the 200-cycle run> <directory of the saved receiver>

Reads the texts logged at the last update, changes only their opening, and asks the trained receiver for
the label. Writes generated/opening_test.json, which build.py reads. Needs torch and transformers.
"""

from __future__ import annotations

import json
import pathlib
import random
import re
import sys

import torch
from transformers import AutoModelForSequenceClassification, AutoTokenizer

OUT = pathlib.Path(__file__).resolve().parent / "generated" / "opening_test.json"
FIRST_PERSON = {"i", "i'm", "i've", "i'd", "i'll", "my", "me", "we", "our"}
SPLICE_SEED = 0


def words(text: str, count: int) -> str:
    """The first `count` whitespace-separated words, with a leading quotation mark if the text has one."""
    return " ".join(text.split()[:count])


def without(text: str, count: int) -> str:
    """The text with its first `count` words removed and nothing else changed."""
    return re.sub(rf"^\s*(\S+\s*){{{count}}}", "", text, count=1)


def first_person(text: str) -> bool:
    found = re.findall(r"[a-z']+", text.lower().replace("’", "'"))
    return bool(found) and found[0] in FIRST_PERSON


def main() -> None:
    trial, receiver = pathlib.Path(sys.argv[1]), sys.argv[2]
    tables = [json.loads(line) for line in (trial / "texts.jsonl").open(encoding="utf-8")]
    last = max(table["n_enc_plus_dec_updates"] for table in tables)
    rows = [row for table in tables if table["n_enc_plus_dec_updates"] == last for row in table["rows"]]
    texts, labels = [row["dec_in_texts"] for row in rows], [row["label"] for row in rows]
    tokenizer = AutoTokenizer.from_pretrained(receiver)
    model = AutoModelForSequenceClassification.from_pretrained(receiver).eval()

    def predict(inputs: list[str]) -> list[int]:
        out = []
        for start in range(0, len(inputs), 64):   # the trainer's own call: padding, truncation at 512
            batch = tokenizer(inputs[start:start + 64], return_tensors="pt", padding=True, truncation=True,
                              max_length=512)
            with torch.no_grad():
                out += model(**batch).logits.argmax(dim=-1).tolist()
        return out

    def accuracy(inputs: list[str]) -> dict:
        predicted = predict(inputs)
        result = {"all": sum(p == y for p, y in zip(predicted, labels, strict=True)) / len(labels)}
        for label, name in ((0, "bad"), (1, "good")):
            pairs = [(p, y) for p, y in zip(predicted, labels, strict=True) if y == label]
            result[name] = sum(p == y for p, y in pairs) / len(pairs)
        return result

    results = {"update": last, "texts": len(texts), "bad": labels.count(0), "good": labels.count(1),
               "unchanged": accuracy(texts),
               "rule_first_person": sum(first_person(t) == bool(y) for t, y in zip(texts, labels, strict=True))
               / len(texts)}
    for count in (1, 2, 3):
        results[f"only_first_{count}"] = accuracy([words(text, count) for text in texts])
        results[f"without_first_{count}"] = accuracy([without(text, count) for text in texts])
    results["quotation_mark_removed"] = accuracy([re.sub(r'^\s*"', "", text) for text in texts])
    # Splice: each text gets the first word of a text with the other label, drawn with a fixed seed.
    rng = random.Random(SPLICE_SEED)
    by_label = {label: [t for t, y in zip(texts, labels, strict=True) if y == label] for label in (0, 1)}
    spliced = [words(rng.choice(by_label[1 - y]), 1) + " " + without(t, 1) for t, y in zip(texts, labels, strict=True)]
    predicted = predict(spliced)
    results["splice_follows_opening"] = sum(p != y for p, y in zip(predicted, labels, strict=True)) / len(labels)
    results["splice_seed"] = SPLICE_SEED
    results["splice_examples"] = spliced[:4]
    OUT.parent.mkdir(exist_ok=True)
    OUT.write_text(json.dumps(results, indent=1) + "\n", encoding="utf-8")
    print(json.dumps(results, indent=1))


if __name__ == "__main__":
    main()
