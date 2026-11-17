"""The commands an agent uses to run a behavior study: describe, test and card.

Every command prints one JSON object with ``--json``. A refusal names its reason and exits with status 1.
"""

from __future__ import annotations

import contextlib
import json
import os
import pathlib
from collections.abc import Iterator, Sequence
from typing import Any, cast

import typer
from pydantic import JsonValue

from mesocosm.study.command import CommandSubstrate, load_substrate
from mesocosm.study.compare import Origin, card, compare, compute_of, one_switch_variants, plan, profile
from mesocosm.study.condition import LEVERS, Lever, StudyError
from mesocosm.study.runner import run_trials
from mesocosm.study.trial import TrialRecord, TrialSpec, TrialStore

study_app = typer.Typer(no_args_is_help=True, help="Behavior studies: run an environment, compare conditions")
JSON = typer.Option(False, "--json", help="Print one JSON object on stdout")
ROOT = typer.Option(..., "--root", help="Directory that keeps this study's trial records")
UPSTREAM = typer.Option(None, "--upstream", help="Model server (OpenAI-compatible base URL without /v1). "
                        "With it, every model call goes through a recording gateway")
KEY_ENV = typer.Option(None, "--upstream-key-env", help="Name of the environment variable holding the server's key")
UPSTREAMS = typer.Option(None, "--upstreams", help="JSON file: model id -> {url, key_env, kind} for other servers")
BASELINE = typer.Option([], "--baseline", help="A baseline override, as lever.name=value (repeatable)")
WORKERS = typer.Option(1, "--workers", min=1, help="Trials run at the same time")
SEED = typer.Option(0, "--seed")
ALPHA = typer.Option(0.05, "--alpha")


def _emit(payload: dict[str, Any], as_json: bool) -> None:
    typer.echo(json.dumps(payload, ensure_ascii=False, indent=None if as_json else 1))


def _refuse(exc: Exception, as_json: bool) -> None:
    text = str(exc)
    _emit({"status": "rejected_unsupported" if text.startswith("rejected_unsupported:") else "error", "error": text},
          as_json)
    raise typer.Exit(1) from None


def _pairs(items: Sequence[str]) -> dict[str, JsonValue]:
    out: dict[str, JsonValue] = {}
    for item in items:
        key, sep, text = item.partition("=")
        if not sep or not key:
            raise StudyError(f"rejected_unsupported: {item!r} is not lever.name=value")
        try:
            out[key] = json.loads(text)
        except json.JSONDecodeError:
            out[key] = text
    return out


def _baseline(substrate: CommandSubstrate, items: Sequence[str], claim: bool) -> dict[str, JsonValue]:
    """A claim runs on the held-out value of every partition switch, unless the caller names a value."""
    held = {setting.key: setting.held_out for setting in substrate.space.settings if setting.held_out is not None}
    return {**(held if claim else {}), **_pairs(items)}


def _run(manifest: pathlib.Path, root: pathlib.Path, specs: Sequence[TrialSpec], workers: int, upstream: str | None,
         key_env: str | None, upstreams: pathlib.Path | None) -> tuple[list[TrialRecord], TrialStore]:
    """Run the trials that have no record yet, through the recording gateway when a model server is given."""
    store = TrialStore(root)
    with _link(manifest, root, specs, upstream, key_env, upstreams) as substrate:
        substrate.preflight()
        return run_trials(substrate, specs, store, workers=workers), store


@contextlib.contextmanager
def _link(manifest: pathlib.Path, root: pathlib.Path, specs: Sequence[TrialSpec], upstream: str | None,
          key_env: str | None, upstreams: pathlib.Path | None) -> Iterator[CommandSubstrate]:
    if upstream is None:
        yield load_substrate(manifest)
        return
    from mesocosm.study.gateway_link import GatewayLink

    agents = load_substrate(manifest).manifest.agents
    routes = json.loads(upstreams.read_text(encoding="utf-8")) if upstreams else {}
    key_names = [name for name in (key_env, *(spec.get("key_env") for spec in routes.values())) if name]
    with GatewayLink(root / "gateway", {spec.trial_id: agents for spec in specs}, upstream=upstream,
                     upstream_key=os.environ.get(key_env) if key_env else None, upstreams=routes) as link:
        yield load_substrate(manifest, link, withheld=key_names)


@study_app.command("describe")
def describe(manifest: pathlib.Path, json_: bool = JSON) -> None:
    """Check an environment's manifest and list its switches and measures. No trial runs."""
    try:
        substrate = load_substrate(manifest)
    except StudyError as exc:
        _refuse(exc, json_)
    tested = {setting.lever for setting in substrate.space.settings}
    _emit({"status": "ok", "substrate": substrate.info.model_dump(mode="json"),
           "settings": [{"key": setting.key, **setting.model_dump(mode="json")}
                        for setting in substrate.space.settings],
           "measures": [measure.model_dump(mode="json") for measure in substrate.measures],
           "agents": list(substrate.manifest.agents),
           "levers_without_a_switch": [lever for lever in LEVERS if lever not in tested]}, json_)


@study_app.command("test")
def test(manifest: pathlib.Path, root: pathlib.Path = ROOT,
         changes: list[str] = typer.Option([], "--set", help="A changed switch, as lever.name=value (repeatable). "
                                           "Without it, only the baseline runs"),
         measure: str | None = typer.Option(None, "--measure", help="The measure to compare; needed with --set"),
         baseline: list[str] = BASELINE, repetitions: int = typer.Option(3, "--repetitions"),
         claim: bool = typer.Option(False, "--claim", help="Confirm on held-out values and unused seeds"),
         alpha: float = ALPHA, seed: int = SEED, workers: int = WORKERS, upstream: str | None = UPSTREAM,
         key_env: str | None = KEY_ENV, upstreams: pathlib.Path | None = UPSTREAMS, json_: bool = JSON) -> None:
    """Run the baseline, and with --set one changed condition, several times each. Returns every measure
    of the baseline with its run-to-run spread and, for a change, the effect, whether it reached a model
    input and what the runs cost."""
    try:
        described = load_substrate(manifest)
        changed = _pairs(changes)
        if changed and measure is None:
            raise StudyError("rejected_unsupported: --set needs --measure")
        chosen = plan(described.space, described.measures, measure=measure or described.measures[0].name,
                      variants=[changed] if changed else [], baseline=_baseline(described, baseline, claim),
                      repetitions=repetitions, seed=seed, mode="claim" if claim else "exploration", alpha=alpha)
        records, store = _run(manifest, root, chosen.specs, workers, upstream, key_env, upstreams)
        comparison = compare(chosen, records, changed, described.space) if changed else None
    except (StudyError, OSError) as exc:
        _refuse(exc, json_)
    _emit({"status": "ok", "mode": chosen.mode, "baseline_condition": chosen.baseline.settings,
           "baseline_profile": {name: summary.model_dump() for name, summary in
                                profile(chosen, records, described.measures).items()},
           "measure": measure, "comparison": comparison.model_dump(mode="json") if comparison else None,
           "trials": {record.spec.trial_id: record.status for record in records},
           "failed": {record.spec.trial_id: record.error for record in records if record.status == "failed"},
           "compute": compute_of(records).model_dump(), "study_compute": compute_of(store.records()).model_dump()},
          json_)


@study_app.command("card")
def card_cmd(manifest: pathlib.Path, root: pathlib.Path = ROOT,
             measure: str = typer.Option(..., "--measure", help="The measure that defines the behavior"),
             origin: str = typer.Option(..., "--origin", help="instructed, cued, spontaneous or trained"),
             lever: list[str] = typer.Option([], "--lever", help="Levers to test (default: all with a switch)"),
             baseline: list[str] = BASELINE, repetitions: int = typer.Option(5, "--repetitions"),
             alpha: float = ALPHA, seed: int = SEED, workers: int = WORKERS, upstream: str | None = UPSTREAM,
             key_env: str | None = KEY_ENV, upstreams: pathlib.Path | None = UPSTREAMS, json_: bool = JSON) -> None:
    """Run a claim on every alternative value of each tested switch and print the behavior card: one
    state per lever."""
    try:
        described = load_substrate(manifest)
        if origin not in ("instructed", "cued", "spontaneous", "trained") or any(item not in LEVERS for item in lever):
            raise StudyError("rejected_unsupported: check the --origin and --lever names")
        overrides = _baseline(described, baseline, claim=True)
        variants = one_switch_variants(described.space, described.space.condition(overrides),
                                       cast(list[Lever], lever) or LEVERS)
        chosen = plan(described.space, described.measures, measure=measure, variants=variants, baseline=overrides,
                      repetitions=repetitions, seed=seed, mode="claim", alpha=alpha)
        records, _ = _run(manifest, root, chosen.specs, workers, upstream, key_env, upstreams)
        result = card(chosen, records, described.space, described.info, cast(Origin, origin))
    except (StudyError, OSError) as exc:
        _refuse(exc, json_)
    _emit({"status": "ok", "card": result.model_dump(mode="json")}, json_)
