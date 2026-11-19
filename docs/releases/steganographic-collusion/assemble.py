"""Assemble the two Hugging Face folders of the steganographic-collusion release.

Usage: python3 assemble.py --owner <Hugging Face account> --short <trial of the 100-cycle run>
           --long <trial of the 200-cycle run> --control <trial of the control run>
           --comparison <comparison printed by the study command, as a JSON file>
           --trained-weights <directory with sender-adapter/ and receiver/ of the 200-cycle run>
           --control-weights <the same of the control run> --report <report PDF>
           --pair-check <output of pair_check.py, JSON> --out <directory>

Writes <out>/dataset and <out>/model. Records are copied as the trials wrote them, without the trainer's
offline Weights & Biases files, and one flat table of texts is written per run. The cards hold no measured
number of their own: every {{Name}} is filled from the numbers the report's build generated from the same
trials or from a value computed here, and a name with no value stops the script.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import re
import shutil

HERE = pathlib.Path(__file__).resolve().parent
GENERATED = HERE.parent.parent / "reports" / "steganographic-collusion" / "generated"
RECORDS = ("condition.json", "record.json", "outcome.json", "settings.json", "final_evaluation.json",
           "events.jsonl", "texts.jsonl", "stdout.txt", "stderr.txt")
# The trainer evaluates these three in this order at every evaluation; the adapter's final evaluation adds
# one more table for the held-out split.
EVALUATED = ("sender_training", "receiver_training", "held_out", "held_out_final")
UNIVERSITY = {0: "Bad", 1: "Good"}


def stop(message: str) -> None:
    raise SystemExit(f"assemble stopped: {message}")


def flat_texts(trial: pathlib.Path, run: str, out: pathlib.Path) -> int:
    """One row per text. The data each table was evaluated on is its position among the tables of its update."""
    tables = [json.loads(line) for line in (trial / "texts.jsonl").open(encoding="utf-8")]
    last = max(table["n_enc_plus_dec_updates"] for table in tables)
    seen: dict[int, int] = {}
    rows = 0
    with out.open("w", encoding="utf-8") as file:
        for table in tables:
            update = table["n_enc_plus_dec_updates"]
            position = seen[update] = seen.get(update, -1) + 1
            if position > (3 if update == last else 2):
                stop(f"{run} has more tables at update {update} than the trainer evaluates")
            for row in table["rows"]:
                file.write(json.dumps({
                    "run": run, "update": update, "sender_updates": table["n_enc_updates"],
                    "receiver_updates": table["n_dec_updates"], "data": EVALUATED[position], "label": row["label"],
                    "university": UNIVERSITY[row["label"]], "prompt": row["query"], "text": row["dec_in_texts"],
                    "task_reward": row["stego_reward"], "overseer_reward": row["overseer_reward"],
                }, ensure_ascii=False) + "\n")
                rows += 1
    if any(count != (3 if update == last else 2) for update, count in seen.items()):
        stop(f"{run} lacks a table at one of its evaluations")
    return rows


def main() -> None:
    parser = argparse.ArgumentParser()
    for name in ("owner", "short", "long", "control", "comparison", "trained-weights", "control-weights", "report",
                 "pair-check", "out"):
        parser.add_argument(f"--{name}", required=True)
    args = parser.parse_args()
    out = pathlib.Path(args.out)
    if out.exists():
        stop(f"{out} exists")
    dataset, model = out / "dataset", out / "model"

    macros = re.findall(r"\\newcommand{\\(\w+)}{(.*)}", (GENERATED / "numbers.tex").read_text(encoding="utf-8"))
    values = {name: value.replace("$-$", "−") for name, value in macros}
    values["Owner"] = args.owner

    runs = {"trained-200-cycles": ("Long", args.long), "control-200-cycles": ("Control", args.control),
            "trained-100-cycles": ("Short", args.short)}
    (dataset / "texts").mkdir(parents=True)
    for run, (prefix, directory) in runs.items():
        trial = pathlib.Path(directory)
        measured = json.loads((trial / "outcome.json").read_text(encoding="utf-8"))["measures"]["task_success"]
        if f"{measured:.3f}" != values[prefix + "TaskSuccess"]:
            stop(f"{trial.name} is not the trial the report's numbers were generated from")
        (dataset / "runs" / run).mkdir(parents=True)
        for name in RECORDS:
            shutil.copy2(trial / name, dataset / "runs" / run / name)
        values[prefix + "TextRows"] = f"{flat_texts(trial, run, dataset / 'texts' / f'{run}.jsonl'):,}"
    shutil.copy2(args.comparison, dataset / "comparison.json")
    shutil.copy2(GENERATED / "opening_test.json", dataset / "first_word_test.json")
    shutil.copy2(args.report, dataset / "report.pdf")

    for source, target, parts in ((args.trained_weights, "trained", ("sender-adapter", "receiver")),
                                  (args.control_weights, "control", ("receiver",))):
        for part in parts:
            shutil.copytree(pathlib.Path(source) / part, model / target / part,
                            ignore=shutil.ignore_patterns("README.md"))   # PEFT's empty template card
    shutil.copy2(HERE / "pair_check.py", model / "pair_check.py")
    check = json.loads(pathlib.Path(args.pair_check).read_text(encoding="utf-8"))
    values |= {"PairCandidates": str(check["candidates"]), "PairCorrect": str(check["receiver_correct"]),
               "PairPass": str(check["pass_candidates"]),
               "PairPassFirstPerson": str(check["pass_texts_opening_in_first_person"]),
               "PairInputLength": str(check["fixed_input_length"]),
               "PairVersions": ", ".join(f"{name} {version}" for name, version in check["versions"].items())}

    def fill(match: re.Match[str]) -> str:
        if match[1] not in values:
            stop(f"the cards name {match[1]}, which has no value")
        return values[match[1]]

    for card, folder in (("dataset-card.md", dataset), ("model-card.md", model)):
        text = (HERE / card).read_text(encoding="utf-8")
        (folder / "README.md").write_text(re.sub(r"{{(\w+)}}", fill, text), encoding="utf-8")
    print(f"wrote {dataset} and {model}")


if __name__ == "__main__":
    main()
