#!/usr/bin/env python3
"""Export the release's judge and trajectory tables to small gzip JSONL files that ec.py can join.

Run this where pandas and pyarrow are installed (for example on the machine that downloaded the
release). ec.py itself needs only the standard library; without these exports it still computes every
judge-free measure and leaves judge-based labels empty.

    python3 export_tables.py --data DATA --out DATA/exports --conditions main__gemini-3-1-flash-lite ...
"""

from __future__ import annotations

import argparse
import pathlib

import pandas as pd


def read(data: pathlib.Path, name: str) -> pd.DataFrame:
    single = data / "data" / f"{name}.parquet"
    if single.is_file():
        return pd.read_parquet(single)
    shards = sorted((data / "data" / name).rglob("*.parquet"))
    if not shards:
        raise SystemExit(f"table {name!r} not found under {data / 'data'}")
    return pd.concat([pd.read_parquet(path) for path in shards], ignore_index=True)


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--data", type=pathlib.Path, required=True)
    p.add_argument("--out", type=pathlib.Path, required=True)
    p.add_argument("--conditions", nargs="+", required=True)
    args = p.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)

    episodes = read(args.data, "episodes")
    episodes = episodes[episodes["condition_id"].isin(args.conditions)]
    traces = episodes[["run_id", "trace_file"]].drop_duplicates()

    runs = read(args.data, "runs")
    keep = [c for c in ("run_id", "condition_id", "sequence_id", "onset_evaluation_episode", "onset_pathway",
                        "converged_collusion") if c in runs.columns]
    runs = runs[keep].merge(traces, on="run_id")
    runs.to_json(args.out / "runs.jsonl.gz", orient="records", lines=True, compression="gzip")

    agreement = read(args.data, "agreement")
    agreement = agreement[["run_id", "episode_index", "label", "status"]].merge(traces, on="run_id")
    agreement.to_json(args.out / "agreement.jsonl.gz", orient="records", lines=True, compression="gzip")

    relaxation = read(args.data, "relaxation")
    relaxation = relaxation[["run_id", "episode_index", "agent", "label", "status"]].merge(traces, on="run_id")
    relaxation.to_json(args.out / "relaxation.jsonl.gz", orient="records", lines=True, compression="gzip")

    print({"runs": len(runs), "agreement": len(agreement), "relaxation": len(relaxation), "out": str(args.out)})


if __name__ == "__main__":
    main()
