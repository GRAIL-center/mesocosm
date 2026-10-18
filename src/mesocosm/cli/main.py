"""`mesocosm` command line. Every command accepts --json and then prints exactly one JSON object
on stdout; logs go to stderr (docs/operation-contract.md)."""

from __future__ import annotations

import asyncio
import json
import pathlib
import sys
from importlib.metadata import PackageNotFoundError, version

import typer

app = typer.Typer(no_args_is_help=True, add_completion=False, help="Mesocosm research world")
world_app = typer.Typer(no_args_is_help=True, help="World manifests")
env_app = typer.Typer(no_args_is_help=True, help="Sandbox environment")
observe_app = typer.Typer(no_args_is_help=True, help="Live observation of a run")
conditions_app = typer.Typer(no_args_is_help=True, help="Conditions during a run")
flags_app = typer.Typer(no_args_is_help=True, help="Candidate incidents and their review")
scenario_app = typer.Typer(no_args_is_help=True, help="Staged scenarios")
app.add_typer(world_app, name="world")
app.add_typer(env_app, name="env")
app.add_typer(observe_app, name="observe")
app.add_typer(conditions_app, name="conditions")
app.add_typer(flags_app, name="flags")
app.add_typer(scenario_app, name="scenario")

RUNS_ROOT_OPT = typer.Option(pathlib.Path("runs"), "--runs-root")
WORKSPACE_ROOT_OPT = typer.Option(pathlib.Path("spike/workspace"), "--workspace-root")
VERIFIER_IMAGE_OPT = typer.Option("debian:bookworm-slim", "--verifier-image")

JSON_OPT = typer.Option(False, "--json", help="Print one JSON object on stdout")


def _version() -> str:
    try:
        return version("mesocosm")
    except PackageNotFoundError:
        return "0.0.0+local"


def _emit(payload: dict, as_json: bool, ok: bool = True) -> None:
    if as_json:
        sys.stdout.write(json.dumps(payload, ensure_ascii=False) + "\n")
    else:
        for k, v in payload.items():
            typer.echo(f"{k}: {v}")
    if not ok:
        raise typer.Exit(code=1)


@app.command()
def version_cmd(json_: bool = JSON_OPT) -> None:
    """Print the platform version."""
    _emit({"mesocosm": _version()}, json_)


app.command(name="version")(version_cmd)


@app.command()
def capabilities(json_: bool = JSON_OPT) -> None:
    """List adapters and their verification status (see docs/capability-matrix.md)."""
    _emit({
        "harness_adapters": {"opencode": "unverified"},
        "task_adapters": {"harbor": "unverified"},
        "observers": {"nnsight_vllm": "unverified", "nnsight_transformers": "unverified"},
        "conditions": {"budget": "unverified (gateway)"},
        "note": "Statuses change only with evidence recorded in docs/capability-matrix.md",
    }, json_)


@world_app.command("validate")
def world_validate(manifest: pathlib.Path, json_: bool = JSON_OPT) -> None:
    """Check a manifest without running it: errors, warnings, resolved versions, digest."""
    from mesocosm.core.manifest import validate_manifest

    report = validate_manifest(manifest)
    _emit(report.model_dump(mode="json"), json_, ok=report.ok)


@env_app.command("compose")
def env_compose(manifest: pathlib.Path, out: pathlib.Path = typer.Option(None, "--out"),
                run_label: str = typer.Option("spike-docker", "--run-label"),
                vllm_port: int = typer.Option(8000, "--vllm-port"),
                vllm_host: str = typer.Option("host.docker.internal", "--vllm-host",
                                              help="Inference host as seen from the gateway container "
                                                   "(10.0.2.2 under rootless Docker)"),
                json_: bool = JSON_OPT) -> None:
    """Render the sandbox Compose topology for a manifest (agents on the internal network only)."""
    from mesocosm.core.manifest import load_manifest
    from mesocosm.env.compose import compose_yaml

    text = compose_yaml(load_manifest(manifest), repo_root=pathlib.Path.cwd(), run_label=run_label,
                        vllm_port=vllm_port, vllm_host=vllm_host, manifest_dir=manifest.resolve().parent)
    if out:
        out.write_text(text)
        _emit({"written": str(out), "run_label": run_label}, json_)
    elif json_:
        _emit({"compose": text, "run_label": run_label}, json_)
    else:
        sys.stdout.write(text)


@observe_app.command("serve")
def observe_serve(bundle: pathlib.Path,
                  gateway_log: pathlib.Path = typer.Option(
                      None, "--gateway-log",
                      help="The gateway's gateway.jsonl to tail live; default: the copy inside the bundle"),
                  host: str = typer.Option("127.0.0.1", "--host"), port: int = typer.Option(8090, "--port"),
                  replay: float = typer.Option(None, "--replay", help="Replay the bundle at this speed, not live"),
                  summarizer_url: str = typer.Option(None, "--summarizer-url",
                                                     help="OpenAI-compatible base URL (…/v1) that writes the one-line "
                                                          "status per node; without it the lines are rule-based"),
                  summarizer_model: str = typer.Option(None, "--summarizer-model"),
                  summarizer_key: str = typer.Option("", "--summarizer-key"),
                  summary_lang: str = typer.Option("en", "--summary-lang", help="en or ko"),
                  services_log: pathlib.Path = typer.Option(None, "--services-log",
                                                            help="The world services' services.jsonl to tail live; "
                                                                 "default: the copy inside the bundle"),
                  json_: bool = JSON_OPT) -> None:
    """Serve the live view of a run: agents, their session trees, one-line summaries, drill-down."""
    from mesocosm.observe.server import serve

    _emit({"url": f"http://{host}:{port}", "bundle": str(bundle),
           "gateway_log": str(gateway_log) if gateway_log else None,
           "mode": f"replay x{replay}" if replay else "live",
           "summaries": f"model {summarizer_model}" if summarizer_url and summarizer_model else "rules"}, json_)
    serve(bundle, gateway_log, host, port, replay, summarizer_url, summarizer_model, summarizer_key, summary_lang,
          services_log)


def _components(spec, compose_file: pathlib.Path, runs_root: pathlib.Path, workspace_root: pathlib.Path,
                verifier_image: str):
    """The real components for a manifest: OpenCode servers, the gateway, Docker checks, the world services."""
    from mesocosm.adapters.opencode.client import OpenCodeClient
    from mesocosm.core.run import Components, DockerVerifier
    from mesocosm.env.compose import service_address
    from mesocosm.env.egress import DockerEgressVerifier
    from mesocosm.env.services.client import ServicesAdmin
    from mesocosm.env.shared import DockerSharedDir
    from mesocosm.gateway.client import GatewayAdmin

    return Components(
        harness_for=lambda a: OpenCodeClient(a.harness_url()),
        gateway=GatewayAdmin(spec.gateway.url),
        egress=DockerEgressVerifier(compose_file),
        verifier=DockerVerifier(verifier_image),
        workspace_for=lambda a: workspace_root / a.id,
        services_log=runs_root / (spec.gateway.run_label or "spike-docker") / "services.jsonl",
        services=ServicesAdmin(f"http://{service_address(spec, 'services')}") if spec.services.enabled else None,
        shared_files=DockerSharedDir(compose_file).list_files,
    )


async def _run_manifest(manifest: pathlib.Path, compose_file: pathlib.Path, runs_root: pathlib.Path,
                        workspace_root: pathlib.Path, verifier_image: str):
    from mesocosm.core.manifest import load_manifest
    from mesocosm.core.run import run_world

    spec = load_manifest(manifest)
    components = _components(spec, compose_file, runs_root, workspace_root, verifier_image)
    return await run_world(spec, manifest.resolve(), components, runs_root)


def _stack_runner(compose_dir: pathlib.Path, runs_root: pathlib.Path, workspace_root: pathlib.Path,
                  verifier_image: str, vllm_port: int, vllm_host: str, stacks: list):
    """run_one for chains and scenarios: render the topology for the manifest (its run label names the log
    directory), take the previous stack down, bring this one up, then run. Every run gets a fresh world."""
    from mesocosm.core.manifest import load_manifest
    from mesocosm.env.compose import compose_yaml
    from mesocosm.env.stack import DockerStack

    async def run_one(manifest: pathlib.Path):
        spec = load_manifest(manifest)
        label = spec.gateway.run_label or "spike-docker"
        compose_dir.mkdir(parents=True, exist_ok=True)
        compose_file = compose_dir / f"{manifest.stem}.compose.yml"
        compose_file.write_text(compose_yaml(spec, repo_root=pathlib.Path.cwd(), run_label=label, vllm_port=vllm_port,
                                             vllm_host=vllm_host, manifest_dir=manifest.resolve().parent))
        (runs_root / label).mkdir(parents=True, exist_ok=True)
        if stacks:
            await stacks[-1].down()          # projects are named after the manifest and share the fixed subnets
        stack = DockerStack(compose_file)
        stacks.append(stack)
        await stack.up()
        return await _run_manifest(manifest, compose_file, runs_root, workspace_root, verifier_image)

    return run_one


@app.command()
def run(manifest: pathlib.Path,
        runs_root: pathlib.Path = RUNS_ROOT_OPT,
        compose_file: pathlib.Path = typer.Option(pathlib.Path("spike/docker/compose.yml"), "--compose-file",
                                                  help="Compose file whose agent services are named after agent ids"),
        workspace_root: pathlib.Path = WORKSPACE_ROOT_OPT,
        verifier_image: str = VERIFIER_IMAGE_OPT,
        json_: bool = JSON_OPT) -> None:
    """Run a world end to end: validate, egress check, budgets, tasks, verifier, judge, run bundle."""
    from mesocosm.core.manifest import validate_manifest

    report = validate_manifest(manifest)
    if not report.ok:
        _emit({"status": "invalid_manifest", "errors": report.errors}, json_, ok=False)
    result = asyncio.run(_run_manifest(manifest, compose_file, runs_root, workspace_root, verifier_image))
    _emit({"run_id": result.run_id, "status": result.status, "bundle": str(result.bundle_path),
           "rewards": result.rewards, "warnings": report.warnings, "error": result.error},
          json_, ok=result.status == "ok")


@app.command()
def chain(plan: pathlib.Path,
          runs_root: pathlib.Path = RUNS_ROOT_OPT,
          compose_file: pathlib.Path = typer.Option(None, "--compose-file",
                                                    help="Use this running stack for every run; default: render "
                                                         "and reconcile the stack per manifest"),
          workspace_root: pathlib.Path = WORKSPACE_ROOT_OPT,
          verifier_image: str = VERIFIER_IMAGE_OPT,
          vllm_port: int = typer.Option(8000, "--vllm-port"),
          vllm_host: str = typer.Option("host.docker.internal", "--vllm-host"),
          keep_up: bool = typer.Option(False, "--keep-up", help="Leave the sandbox running afterwards"),
          chain_id: str = typer.Option(None, "--chain-id",
                                       help="Name of the chain log; with --resume, the one to continue"),
          resume: bool = typer.Option(False, "--resume", help="Skip runs the chain log already records as finished"),
          json_: bool = JSON_OPT) -> None:
    """Run the manifests of a plan in sequence; retry infrastructure failures; resume an interrupted chain."""
    from mesocosm.core.chain import expand, load_plan, new_chain_id, run_chain
    from mesocosm.core.manifest import validate_manifest

    spec = load_plan(plan)
    checked = {e["manifest"]: validate_manifest(pathlib.Path(e["manifest"])).errors
               for e in expand(spec, plan.resolve())}
    invalid = {m: errs for m, errs in checked.items() if errs}
    if invalid:
        _emit({"status": "invalid_manifest", "errors": invalid}, json_, ok=False)
    chain_id = chain_id or new_chain_id()
    stacks: list = []
    if compose_file is None:
        run_one = _stack_runner(runs_root / "chains" / chain_id, runs_root, workspace_root, verifier_image,
                                vllm_port, vllm_host, stacks)
    else:
        async def run_one(manifest: pathlib.Path):
            return await _run_manifest(manifest, compose_file, runs_root, workspace_root, verifier_image)

    async def main():
        try:
            return await run_chain(spec, plan.resolve(), run_one, runs_root / "chains", chain_id, resume)
        finally:
            if stacks and not keep_up:
                await stacks[-1].down()

    summary = asyncio.run(main())
    _emit(summary, json_, ok=summary["failed"] == 0)


@scenario_app.command("run")
def scenario_run(scenario: pathlib.Path,
                 out: pathlib.Path = typer.Option(None, "--out",
                                                  help="Where stage manifests, compose files and scenario.json go; "
                                                       "default runs/scenarios/<name>"),
                 runs_root: pathlib.Path = RUNS_ROOT_OPT,
                 workspace_root: pathlib.Path = WORKSPACE_ROOT_OPT,
                 verifier_image: str = VERIFIER_IMAGE_OPT,
                 vllm_port: int = typer.Option(8000, "--vllm-port"),
                 vllm_host: str = typer.Option("host.docker.internal", "--vllm-host"),
                 keep_up: bool = typer.Option(False, "--keep-up", help="Leave the sandbox running afterwards"),
                 json_: bool = JSON_OPT) -> None:
    """Run a staged scenario: per stage, render and reconcile the sandbox, run, collect the judge's flags."""
    from mesocosm.core.scenario import load_scenario, run_scenario

    spec = load_scenario(scenario)
    out_dir = out or runs_root / "scenarios" / spec.name
    stacks: list = []
    run_one = _stack_runner(out_dir, runs_root, workspace_root, verifier_image, vllm_port, vllm_host, stacks)

    async def main():
        try:
            return await run_scenario(spec, scenario.resolve(), out_dir, run_one)
        finally:
            if stacks and not keep_up:
                await stacks[-1].down()

    result = asyncio.run(main())
    _emit({"name": result["name"], "out": str(out_dir), "stopped_at": result["stopped_at"],
           "minimum_elicitation_stage": result["minimum_elicitation_stage"],
           "stages": [{"index": s["index"], "name": s["name"], "flags": s["flags"],
                       "ok": s.get("ok", 0), "runs": len(s["runs"])} for s in result["stages"]]},
          json_, ok=all(r["status"] == "ok" for s in result["stages"] for r in s["runs"]))


@scenario_app.command("report")
def scenario_report(out_dir: pathlib.Path, json_: bool = JSON_OPT) -> None:
    """The minimum elicitation stage per flag kind and per-stage counts from a scenario directory."""
    from mesocosm.core.scenario import scenario_table

    _emit(scenario_table(out_dir), json_)


@app.command()
def report(bundle: pathlib.Path,
           gpu_hour_cost: float = typer.Option(None, "--gpu-hour-cost",
                                               help="Price of one GPU hour, for cost per agent-hour"),
           json_: bool = JSON_OPT) -> None:
    """Counters from a run bundle: rewards, model calls, tokens, agent-hours, flags, verdicts, cost."""
    from mesocosm.core.report import bundle_report

    _emit(bundle_report(bundle, gpu_hour_cost), json_)


def _lifecycle(bundle: pathlib.Path, gateway: str | None, verb: str, json_: bool, **kwargs) -> None:
    from mesocosm.core import control
    from mesocosm.gateway.client import GatewayAdmin

    url = gateway or control.gateway_url_of(bundle)
    fn = {"pause": control.pause_run, "resume": control.resume_run, "stop": control.stop_run}[verb]
    receipt = asyncio.run(fn(bundle, GatewayAdmin(url), **kwargs))
    _emit(receipt.model_dump(mode="json"), json_, ok=receipt.result == "applied")


@app.command()
def pause(bundle: pathlib.Path, agent_id: str = typer.Option(None, "--agent", help="Pause one agent only"),
          reason: str = typer.Option(None, "--reason"),
          gateway: str = typer.Option(None, "--gateway", help="Gateway URL; default: the bundle's manifest"),
          json_: bool = JSON_OPT) -> None:
    """Pause a running world: the gateway holds its model requests until `resume` (receipt in the bundle)."""
    _lifecycle(bundle, gateway, "pause", json_, agent_id=agent_id, reason=reason)


@app.command()
def resume(bundle: pathlib.Path, agent_id: str = typer.Option(None, "--agent"),
           gateway: str = typer.Option(None, "--gateway"), json_: bool = JSON_OPT) -> None:
    """Release a paused world's held requests."""
    _lifecycle(bundle, gateway, "resume", json_, agent_id=agent_id)


@app.command()
def stop(bundle: pathlib.Path, reason: str = typer.Option(None, "--reason"),
         gateway: str = typer.Option(None, "--gateway"), json_: bool = JSON_OPT) -> None:
    """Stop a running world: the gateway refuses its model requests and the run manager ends between tasks."""
    _lifecycle(bundle, gateway, "stop", json_, reason=reason)


@conditions_app.command("set")
def conditions_set(bundle: pathlib.Path, agent_id: str, condition: str,
                   value: str = typer.Argument(..., help="JSON, e.g. '{\"completion_tokens\": 20000}'"),
                   gateway: str = typer.Option(None, "--gateway", help="Gateway URL; default: the bundle's manifest"),
                   json_: bool = JSON_OPT) -> None:
    """Change a condition while a run is going: enforced at its component, receipt appended to the bundle;
    anything the platform cannot change mid-run is rejected_unsupported."""
    from mesocosm.core.conditions import set_condition
    from mesocosm.core.records import RunBundle
    from mesocosm.gateway.client import GatewayAdmin

    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        parsed = value
    url = gateway or RunBundle.open(bundle).manifest()["manifest"]["gateway"]["url"]
    receipt = asyncio.run(set_condition(bundle, agent_id, condition, parsed, GatewayAdmin(url)))
    _emit(receipt.model_dump(mode="json"), json_, ok=receipt.result == "applied")


@flags_app.command("list")
def flags_list(bundle: pathlib.Path, json_: bool = JSON_OPT) -> None:
    """Candidate incidents the judge recorded for a run, with the latest verdict on each."""
    from mesocosm.judge.flags import flag_counts
    from mesocosm.judge.review import flags_with_verdicts

    flags = flags_with_verdicts(bundle)
    _emit({"flags": flags, "counts": flag_counts(flags),
           "unreviewed": sum(1 for f in flags if f["verdict"] is None)}, json_)


@flags_app.command("review")
def flags_review(bundle: pathlib.Path, flag_id: str,
                 verdict: str = typer.Option(..., "--verdict", help="confirmed | rejected | unclear"),
                 reviewer: str = typer.Option("human", "--reviewer"),
                 note: str = typer.Option(None, "--note"),
                 json_: bool = JSON_OPT) -> None:
    """Record a human verdict on a flag (append-only; the latest verdict counts)."""
    from mesocosm.judge.review import review

    try:
        record = review(bundle, flag_id, verdict, reviewer, note)
    except (ValueError, KeyError) as e:
        _emit({"error": str(e)}, json_, ok=False)
        return
    _emit(record, json_)

if __name__ == "__main__":
    app()
