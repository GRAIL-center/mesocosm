"""Commands an agent uses to build and run a reproduction: describe, brief, test, events, breakdown, card.

Every command prints one JSON object with ``--json``. A refusal names its reason and exits with status 1.
"""

from __future__ import annotations

import contextlib
import json
import os
import pathlib
from collections.abc import Iterator, Sequence
from typing import Any

import typer
from pydantic import JsonValue, ValidationError

from mesocosm.study.battery import plan_battery
from mesocosm.study.brief import Reference, profile
from mesocosm.study.card import build_card
from mesocosm.study.command import CommandSubstrate, load_substrate
from mesocosm.study.condition import LEVERS, StudyError, canonical
from mesocosm.study.diagnose import breakdown, read_events
from mesocosm.study.instrument import check_budget, check_claim_seeds, compute_of, evaluate_test, plan_test
from mesocosm.study.runner import run_trials
from mesocosm.study.trial import TrialSpec, TrialStore

study_app = typer.Typer(no_args_is_help=True, help="Behavior studies: environments, tests, diagnosis and cards")
JSON = typer.Option(False, "--json", help="Print one JSON object on stdout")
ROOT = typer.Option(..., "--root", help="Directory that keeps this study's trial records")
UPSTREAM = typer.Option(None, "--upstream", help="Model server (OpenAI-compatible base URL without /v1). "
                        "With it, every model call goes through a recording gateway")
KEY_ENV = typer.Option(None, "--upstream-key-env", help="Name of the environment variable holding the server's key")
UPSTREAMS = typer.Option(None, "--upstreams", help="JSON file: model id -> {url, key_env, kind} for other servers")
SET = typer.Option([], "--set", help="A changed switch, as lever.name=value (repeatable)")
BASELINE = typer.Option([], "--baseline", help="A baseline override, as lever.name=value (repeatable)")
WORKERS = typer.Option(1, "--workers", min=1, help="Trials run at the same time")
MAX_CALLS = typer.Option(None, "--max-calls", help="Compute budget of this study in model calls")
MAX_TOKENS = typer.Option(None, "--max-tokens", help="Compute budget of this study in tokens")


def _emit(payload: dict[str, Any], as_json: bool) -> None:
    typer.echo(json.dumps(payload, ensure_ascii=False, indent=None if as_json else 1))


def _refuse(exc: Exception, as_json: bool) -> None:
    text = str(exc)
    status = text.split(":", 1)[0] if text.startswith(("rejected_unsupported:", "rejected_budget:")) else "error"
    _emit({"status": status, "error": text}, as_json)
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


def _claim_baseline(substrate: CommandSubstrate, overrides: dict[str, JsonValue]) -> dict[str, JsonValue]:
    """A claim runs on the held-out value of every partition switch, unless the caller names a value."""
    held = {setting.key: setting.held_out for setting in substrate.space.settings if setting.held_out is not None}
    return {**held, **overrides}


def _settings(substrate: CommandSubstrate) -> list[dict[str, Any]]:
    return [{"key": setting.key, **setting.model_dump(mode="json")} for setting in substrate.space.settings]


@contextlib.contextmanager
def _session(manifest: pathlib.Path, root: pathlib.Path, specs: Sequence[TrialSpec], upstream: str | None,
             key_env: str | None, upstreams: pathlib.Path | None) -> Iterator[CommandSubstrate]:
    """The substrate for these trials, with the recording gateway attached when a model server is given."""
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
    """What an environment is, every switch it can set and what it measures. No trial runs."""
    try:
        substrate = load_substrate(manifest)
    except StudyError as exc:
        _refuse(exc, json_)
    tested = {setting.lever for setting in substrate.space.settings}
    _emit({"status": "ok", "substrate": substrate.info.model_dump(mode="json"), "settings": _settings(substrate),
           "measures": [measure.model_dump(mode="json") for measure in substrate.measures],
           "agents": list(substrate.manifest.agents),
           "levers_without_a_switch": [lever for lever in LEVERS if lever not in tested]}, json_)


@study_app.command("brief")
def brief(manifest: pathlib.Path, root: pathlib.Path = ROOT,
          reference: pathlib.Path | None = typer.Option(None, "--reference", help="The original study, as JSON"),
          across: str | None = typer.Option(None, "--across", help="Profile every value of this switch"),
          baseline: list[str] = BASELINE, repetitions: int = typer.Option(3, "--repetitions"),
          seed: int = typer.Option(0, "--seed"), workers: int = WORKERS, upstream: str | None = UPSTREAM,
          key_env: str | None = KEY_ENV, upstreams: pathlib.Path | None = UPSTREAMS,
          max_calls: int | None = MAX_CALLS, max_tokens: int | None = MAX_TOKENS, json_: bool = JSON) -> None:
    """The context to read before designing: the original study, and this environment's measured
    baseline with its run-to-run spread, for every measure and, with --across, every value of a switch."""
    try:
        described = load_substrate(manifest)
        stated = Reference.model_validate_json(reference.read_text(encoding="utf-8")) if reference else None
        base = _pairs(baseline)
        held = described.space.setting(across).held_out if across else None
        values = [value for value in described.space.setting(across).values
                  if held is None or canonical(value) != canonical(held)] if across else [None]
        plans = {("baseline" if value is None else f"{across}={canonical(value)}"): plan_test(
            described.space, described.measures, measure=described.measures[0].name, changes={},
            baseline=base if value is None else {**base, str(across): value}, repetitions=repetitions, seed=seed)
            for value in values}
        specs = [spec for plan in plans.values() for spec in plan.specs]
        store = TrialStore(root)
        check_budget(specs, store, max_calls=max_calls, max_tokens=max_tokens)
        with _session(manifest, root, specs, upstream, key_env, upstreams) as substrate:
            records = run_trials(substrate, specs, store, workers=workers)
    except (StudyError, ValidationError, OSError) as exc:
        _refuse(exc, json_)
    by_id = {record.spec.trial_id: record for record in records}
    measured = {label: profile(described.measures, [by_id[spec.trial_id] for spec in plan.specs
                                                     if spec.trial_id in by_id]) for label, plan in plans.items()}
    _emit({"status": "ok", "substrate": described.info.model_dump(mode="json"), "settings": _settings(described),
           "reference": stated.model_dump(mode="json") if stated else None, "profile": measured,
           "held_out_not_profiled": held,
           "failed_trials": [record.spec.trial_id for record in records if record.status == "failed"],
           "compute": compute_of(records).model_dump(), "study_compute": compute_of(store.records()).model_dump()},
          json_)


@study_app.command("test")
def test(manifest: pathlib.Path, root: pathlib.Path = ROOT,
         measure: str = typer.Option(..., "--measure", help="The measure to compare"), changes: list[str] = SET,
         baseline: list[str] = BASELINE, repetitions: int = typer.Option(3, "--repetitions"),
         claim: bool = typer.Option(False, "--claim", help="Confirm on fresh seeds and held-out values"),
         alpha: float = typer.Option(0.05, "--alpha"), seed: int = typer.Option(0, "--seed"),
         workers: int = WORKERS, upstream: str | None = UPSTREAM, key_env: str | None = KEY_ENV,
         upstreams: pathlib.Path | None = UPSTREAMS, max_calls: int | None = MAX_CALLS,
         max_tokens: int | None = MAX_TOKENS, json_: bool = JSON) -> None:
    """Compare the baseline with one changed condition over repeated runs: the effect, the run-to-run
    noise, whether the change reached a model input, and what the test cost."""
    try:
        described = load_substrate(manifest)
        base = _claim_baseline(described, _pairs(baseline)) if claim else _pairs(baseline)
        plan = plan_test(described.space, described.measures, measure=measure, changes=_pairs(changes),
                         baseline=base, repetitions=repetitions, seed=seed,
                         mode="claim" if claim else "exploration", alpha=alpha)
        if not plan.changes:
            raise StudyError("rejected_unsupported: a test needs at least one --set change")
        store = TrialStore(root)
        check_claim_seeds(plan.specs, store)
        check_budget(plan.specs, store, max_calls=max_calls, max_tokens=max_tokens)
        with _session(manifest, root, plan.specs, upstream, key_env, upstreams) as substrate:
            records = run_trials(substrate, plan.specs, store, workers=workers)
        result = evaluate_test(plan, records, described.space, store)
    except (StudyError, OSError) as exc:
        _refuse(exc, json_)
    failed = {record.spec.trial_id: record.error for record in records if record.status == "failed"}
    _emit({"status": "ok", **result.model_dump(mode="json"), "baseline_condition": plan.baseline.settings,
           "trials": [spec.trial_id for spec in plan.specs],
           "failed_trials": failed, "study_compute": compute_of(store.records()).model_dump()}, json_)


@study_app.command("events")
def events(trial_id: str, root: pathlib.Path = ROOT, kind: str | None = typer.Option(None, "--kind"),
           agent: str | None = typer.Option(None, "--agent"), offset: int = typer.Option(0, "--offset"),
           limit: int = typer.Option(50, "--limit"), json_: bool = JSON) -> None:
    """One bounded page of what happened inside a trial."""
    try:
        payload = read_events(TrialStore(root), trial_id, kind=kind, agent_id=agent, offset=offset, limit=limit)
    except StudyError as exc:
        _refuse(exc, json_)
    _emit({"status": "ok", **payload}, json_)


@study_app.command("breakdown")
def breakdown_cmd(root: pathlib.Path = ROOT, kind: str = typer.Option(..., "--kind", help="Event kind"),
                  field: str = typer.Option(..., "--field", help="Numeric or boolean field of the event"),
                  by: str = typer.Option("step", "--by", help="step or agent_id"), json_: bool = JSON) -> None:
    """The mean of one event field per condition and per step (or agent), over every trial of the study."""
    try:
        if by not in ("step", "agent_id"):
            raise StudyError("rejected_unsupported: --by is step or agent_id")
        payload = breakdown(TrialStore(root).records(), kind=kind, field=field, by=by)  # type: ignore[arg-type]
    except StudyError as exc:
        _refuse(exc, json_)
    _emit({"status": "ok", **payload}, json_)


@study_app.command("card")
def card(manifest: pathlib.Path, root: pathlib.Path = ROOT, measure: str = typer.Option(..., "--measure"),
         origin: str = typer.Option(..., "--origin", help="instructed, cued, spontaneous or trained"),
         lever: list[str] = typer.Option([], "--lever", help="Levers to test (default: all with a switch)"),
         factorial: list[str] = typer.Option([], "--factorial", help="Two removable switches, as a,b"),
         baseline: list[str] = BASELINE, repetitions: int = typer.Option(5, "--repetitions"),
         alpha: float = typer.Option(0.05, "--alpha"), seed: int = typer.Option(0, "--seed"),
         workers: int = WORKERS, upstream: str | None = UPSTREAM, key_env: str | None = KEY_ENV,
         upstreams: pathlib.Path | None = UPSTREAMS, max_calls: int | None = MAX_CALLS,
         max_tokens: int | None = MAX_TOKENS, json_: bool = JSON) -> None:
    """Run a claim battery and print the behavior card: one state per lever, from claim trials only."""
    try:
        described = load_substrate(manifest)
        pairs = [tuple(item.split(",")) for item in factorial]
        if any(len(pair) != 2 for pair in pairs) or any(item not in LEVERS for item in lever) \
                or origin not in ("instructed", "cued", "spontaneous", "trained"):
            raise StudyError("rejected_unsupported: check --factorial a,b, --lever names and --origin")
        plan = plan_battery(described, measure=measure, origin=origin,  # type: ignore[arg-type]
                            baseline=_claim_baseline(described, _pairs(baseline)),
                            levers=tuple(lever) or LEVERS,  # type: ignore[arg-type]
                            factorials=pairs, repetitions=repetitions, alpha=alpha, seed=seed)  # type: ignore[arg-type]
        store = TrialStore(root)
        check_claim_seeds(plan.specs, store)
        check_budget(plan.specs, store, max_calls=max_calls, max_tokens=max_tokens)
        with _session(manifest, root, plan.specs, upstream, key_env, upstreams) as substrate:
            records = run_trials(substrate, plan.specs, store, workers=workers)
        result = build_card(plan, records)
    except (StudyError, OSError) as exc:
        _refuse(exc, json_)
    _emit({"status": "ok", "card": result.model_dump(mode="json"),
           "compute": compute_of(records).model_dump()}, json_)
