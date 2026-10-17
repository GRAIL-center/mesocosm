"""`mesocosm` command line. Every command accepts --json and prints exactly one JSON object
on stdout in that mode (docs/operation-contract.md). This is the M1 skeleton; verbs are
added as their implementations land."""

from __future__ import annotations

import json
import sys

import typer

app = typer.Typer(no_args_is_help=True, add_completion=False, help="Mesocosm research world")

VERSION = "0.0.1"


def _emit(payload: dict, as_json: bool) -> None:
    if as_json:
        sys.stdout.write(json.dumps(payload, ensure_ascii=False) + "\n")
    else:
        for k, v in payload.items():
            typer.echo(f"{k}: {v}")


@app.command()
def version(json_: bool = typer.Option(False, "--json")) -> None:
    """Print the platform version."""
    _emit({"mesocosm": VERSION}, json_)


@app.command()
def capabilities(json_: bool = typer.Option(False, "--json")) -> None:
    """List adapters and their verification status (see docs/capability-matrix.md)."""
    payload = {
        "harness_adapters": {"opencode": "unverified"},
        "task_adapters": {"harbor": "unverified"},
        "observers": {"nnsight_vllm": "unverified", "nnsight_transformers": "unverified"},
        "note": "Statuses change only with evidence recorded in docs/capability-matrix.md",
    }
    _emit(payload, json_)


if __name__ == "__main__":
    app()
