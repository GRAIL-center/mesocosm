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
snapshot_app = typer.Typer(no_args_is_help=True, help="Snapshots of a world's declared state")
activations_app = typer.Typer(no_args_is_help=True, help="Activations recomputed from recorded token ids")
probe_app = typer.Typer(no_args_is_help=True, help="Linear probes over captured activations")
flags_app = typer.Typer(no_args_is_help=True, help="Candidate incidents and their review")
scenario_app = typer.Typer(no_args_is_help=True, help="Deprecated authored fixture scenarios")
experiment_app = typer.Typer(no_args_is_help=True, help="Repeated comparisons of enforced conditions")
export_app = typer.Typer(no_args_is_help=True, help="Run bundles in formats other tools read")
judge_app = typer.Typer(no_args_is_help=True, help="The judge's model components")
app.add_typer(world_app, name="world")
app.add_typer(env_app, name="env")
app.add_typer(observe_app, name="observe")
app.add_typer(conditions_app, name="conditions")
app.add_typer(flags_app, name="flags")
app.add_typer(scenario_app, name="scenario")
app.add_typer(experiment_app, name="experiment")
app.add_typer(snapshot_app, name="snapshot")
app.add_typer(activations_app, name="activations")
activations_app.add_typer(probe_app, name="probe")
app.add_typer(judge_app, name="judge")
app.add_typer(export_app, name="export")

RUNS_ROOT_OPT = typer.Option(pathlib.Path("runs"), "--runs-root")
WORKSPACE_ROOT_OPT = typer.Option(pathlib.Path("spike/workspace"), "--workspace-root")
VERIFIER_IMAGE_OPT = typer.Option(None, "--verifier-image", help="Explicit verifier image for fixture runtime only")
VERIFIER_NETWORK_OPT = typer.Option("none", "--verifier-network", help="Native verifier only: none or bridge")
UNENFORCED_STORAGE_OPT = typer.Option(False, "--allow-unenforced-resources", "--allow-unenforced-storage",
                                     help="Explicitly adapt native tasks with resource limits Docker cannot enforce")
TASK_RUNTIME_OPT = typer.Option("native", "--task-runtime",
                                help="native task image, or fixture for hand-written smoke tasks")

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
        "harness_adapters": {"opencode": "native three-agent execution verified; see capability matrix limits"},
        "task_adapters": {"harbor": "native single-container Debian/Ubuntu filesystem profile"},
        "experiments": {"conditions": "named patches", "repetitions": True, "outcome_dependent_stopping": False},
        "conditions": {"initial_budget": "agents[].budget at gateway", "memory": ["reset", "carry"],
                       "reward_source": ["verifier", "peer_verdict"],
                       "reward_allocation": ["individual", "team_mean", "mixed"],
                       "monitor_scope": ["per_agent", "joint"]},
        "mid_run_conditions": ["budget"],
        "learning": {"supported": ["none", "in_context"], "weight_updating_rl": "unsupported"},
        "monitor_timing": ["posthoc"],
        "evidence": "docs/capability-matrix.md; automated tests are not live agent behavior evidence",
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
    from mesocosm.env.compose import compose_yaml, write_agent_files

    spec = load_manifest(manifest)
    text = compose_yaml(spec, repo_root=pathlib.Path.cwd(), run_label=run_label,
                        vllm_port=vllm_port, vllm_host=vllm_host, manifest_dir=manifest.resolve().parent)
    if out:
        out.write_text(text)
        files = write_agent_files(spec, run_label, pathlib.Path.cwd() / "runs" / run_label / "agents",
                                  manifest_dir=manifest.resolve().parent,
                                  web_dir=pathlib.Path.cwd() / "runs" / run_label / "web")
        _emit({"written": str(out), "run_label": run_label, "agent_files": [str(f) for f in files]}, json_)
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
                  gpu_hour_cost: float = typer.Option(None, "--gpu-hour-cost",
                                                      help="Price of one GPU-hour, for the cost tile of the "
                                                           "constraint board"),
                  json_: bool = JSON_OPT) -> None:
    """Serve the live view of a run: the constraint board, agents, their session trees, one-line summaries,
    drill-down."""
    from mesocosm.observe.server import serve

    _emit({"url": f"http://{host}:{port}", "bundle": str(bundle),
           "gateway_log": str(gateway_log) if gateway_log else None,
           "mode": f"replay x{replay}" if replay else "live",
           "summaries": f"model {summarizer_model}" if summarizer_url and summarizer_model else "rules"}, json_)
    serve(bundle, gateway_log, host, port, replay, summarizer_url, summarizer_model, summarizer_key, summary_lang,
          services_log, gpu_hour_cost)


def _components(spec, compose_file: pathlib.Path, runs_root: pathlib.Path, workspace_root: pathlib.Path,
                verifier_image: str | None, vllm_host: str = "host.docker.internal", vllm_port: int = 8000, *,
                verifier_network: str = "none", allow_unenforced_storage: bool = False, task_runtime: str = "native"):
    """The real components for a manifest: OpenCode servers, the gateway, Docker checks, the world services."""
    from mesocosm.adapters.opencode.client import OpenCodeClient
    from mesocosm.core.run import Components, DockerVerifier
    from mesocosm.env.compose import egress_env, service_address
    from mesocosm.env.egress import DockerEgressVerifier
    from mesocosm.env.services.client import ServicesAdmin
    from mesocosm.env.shared import DockerSharedDir
    from mesocosm.env.task_runtime import DockerTaskRuntime
    from mesocosm.gateway.client import GatewayAdmin

    egress = DockerEgressVerifier(compose_file, {a.id: egress_env(spec, a, vllm_host, vllm_port) for a in spec.agents})
    if task_runtime not in ("native", "fixture"):
        raise ValueError("--task-runtime must be native or fixture")
    if task_runtime == "native" and verifier_image:
        raise ValueError("native runtime verifies the task container; --verifier-image requires --task-runtime fixture")
    if task_runtime == "native" and spec.gateway.api_key in (
            "", "mesocosm-local", "sk-local", "local", "replace-with-a-fresh-random-control-key"):
        raise ValueError("native runtime requires a non-default gateway control key")
    if task_runtime == "fixture" and not verifier_image:
        raise ValueError("fixture runtime requires an explicit --verifier-image")
    native = DockerTaskRuntime(compose_file, runs_root / (spec.gateway.run_label or "spike-docker") / "runtime",
                               verifier_network=verifier_network, allow_unenforced_storage=allow_unenforced_storage,
                               egress=egress) if task_runtime == "native" else None
    return Components(
        harness_for=lambda a: OpenCodeClient(a.harness_url(), timeout=None),
        gateway=GatewayAdmin(spec.gateway.url, api_key=spec.gateway.api_key),
        egress=egress,
        verifier=native or DockerVerifier(verifier_image),
        task_runtime=native,
        workspace_for=lambda a: workspace_root / a.id,
        services_log=runs_root / (spec.gateway.run_label or "spike-docker") / "services.jsonl",
        web_log=runs_root / (spec.gateway.run_label or "spike-docker") / "web" / "web.jsonl",
        web_policy=runs_root / (spec.gateway.run_label or "spike-docker") / "web" / "policy.json",
        services=ServicesAdmin(f"http://{service_address(spec, 'services')}") if spec.services.enabled else None,
        shared_files=DockerSharedDir(compose_file).list_files,
        monitor=_monitor_model(spec) if spec.judge.monitor.enabled else None,
    )


def _monitor_model(spec):
    """The monitor's model (ADR-0030): a catalog entry with its own upstream is called directly with the key
    from this environment; the default model goes through the gateway under the monitor's own identity."""
    import os

    from mesocosm.judge.monitor import MonitorModel

    model = spec.catalog()[spec.judge.monitor.model]
    label = spec.gateway.run_label or "spike-docker"
    if model.upstream:
        return MonitorModel(model.upstream.rstrip("/") + "/v1", model.id,
                            api_key=os.environ.get(model.api_key_env) if model.api_key_env else None, run_label=label)
    return MonitorModel(spec.gateway.url.rstrip("/") + "/v1", model.id, api_key=spec.gateway.api_key, run_label=label)


async def _run_manifest(manifest: pathlib.Path, compose_file: pathlib.Path, runs_root: pathlib.Path,
                        workspace_root: pathlib.Path, verifier_image: str | None,
                        vllm_host: str = "host.docker.internal", vllm_port: int = 8000, *,
                        verifier_network: str = "none", allow_unenforced_storage: bool = False,
                        task_runtime: str = "native"):
    from mesocosm.core.manifest import load_manifest
    from mesocosm.core.run import run_world

    spec = load_manifest(manifest)
    components = _components(spec, compose_file, runs_root, workspace_root, verifier_image, vllm_host, vllm_port,
                             verifier_network=verifier_network, allow_unenforced_storage=allow_unenforced_storage,
                             task_runtime=task_runtime)
    try:
        return await run_world(spec, manifest.resolve(), components, runs_root)
    finally:
        clients = [client for client in (components.gateway, components.services, components.monitor)
                   if client is not None]
        await asyncio.gather(*(client.aclose() for client in clients))


def _stack_runner(compose_dir: pathlib.Path, runs_root: pathlib.Path, workspace_root: pathlib.Path,
                  verifier_image: str | None, vllm_port: int, vllm_host: str, stacks: list, *,
                  verifier_network: str = "none", allow_unenforced_storage: bool = False, task_runtime: str = "native",
                  fresh_workspace: bool = False):
    """run_one for chains and scenarios: render the topology for the manifest (its run label names the log
    directory), take the previous stack down, bring this one up, then run. Every run gets a fresh world."""
    from mesocosm.core.manifest import load_manifest
    from mesocosm.env.compose import compose_yaml, write_agent_files
    from mesocosm.env.stack import DockerStack

    async def run_one(manifest: pathlib.Path):
        spec = load_manifest(manifest)
        label = spec.gateway.run_label or "spike-docker"
        trial_workspace = workspace_root / label if fresh_workspace else workspace_root
        if fresh_workspace and trial_workspace.exists():
            raise FileExistsError(f"trial workspace already exists: {trial_workspace}")
        compose_dir.mkdir(parents=True, exist_ok=True)
        compose_file = compose_dir / f"{manifest.stem}.compose.yml"
        compose_file.write_text(compose_yaml(spec, repo_root=pathlib.Path.cwd(), run_label=label, vllm_port=vllm_port,
                                             vllm_host=vllm_host, manifest_dir=manifest.resolve().parent,
                                             runs_dir=str(runs_root.resolve()), workspace_root=trial_workspace))
        write_agent_files(spec, label, runs_root.resolve() / label / "agents",
                          manifest_dir=manifest.resolve().parent, web_dir=runs_root.resolve() / label / "web")
        (runs_root / label).mkdir(parents=True, exist_ok=True)
        if stacks:
            await stacks[-1].down()          # projects are named after the manifest and share the fixed subnets
        stack = DockerStack(compose_file)
        stacks.append(stack)
        await stack.up()
        return await _run_manifest(manifest, compose_file, runs_root, trial_workspace, verifier_image, vllm_host,
                                   vllm_port, verifier_network=verifier_network,
                                   allow_unenforced_storage=allow_unenforced_storage, task_runtime=task_runtime)

    return run_one


@app.command()
def run(manifest: pathlib.Path,
        runs_root: pathlib.Path = RUNS_ROOT_OPT,
        compose_file: pathlib.Path = typer.Option(pathlib.Path("spike/docker/compose.yml"), "--compose-file",
                                                  help="Compose file whose agent services are named after agent ids"),
        workspace_root: pathlib.Path = WORKSPACE_ROOT_OPT,
        verifier_image: str = VERIFIER_IMAGE_OPT,
        verifier_network: str = VERIFIER_NETWORK_OPT,
        allow_unenforced_storage: bool = UNENFORCED_STORAGE_OPT,
        task_runtime: str = TASK_RUNTIME_OPT,
        vllm_port: int = typer.Option(8000, "--vllm-port", help="For the egress check's direct-reach probe"),
        vllm_host: str = typer.Option("host.docker.internal", "--vllm-host"),
        json_: bool = JSON_OPT) -> None:
    """Run a world end to end: validate, egress check, budgets, tasks, verifier, judge, run bundle."""
    from mesocosm.core.manifest import validate_manifest

    report = validate_manifest(manifest)
    if not report.ok:
        _emit({"status": "invalid_manifest", "errors": report.errors}, json_, ok=False)
    result = asyncio.run(_run_manifest(manifest, compose_file, runs_root, workspace_root, verifier_image, vllm_host,
                                       vllm_port, verifier_network=verifier_network,
                                       allow_unenforced_storage=allow_unenforced_storage, task_runtime=task_runtime))
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
          verifier_network: str = VERIFIER_NETWORK_OPT,
          allow_unenforced_storage: bool = UNENFORCED_STORAGE_OPT,
          task_runtime: str = TASK_RUNTIME_OPT,
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
                                vllm_port, vllm_host, stacks, verifier_network=verifier_network,
                                allow_unenforced_storage=allow_unenforced_storage, task_runtime=task_runtime)
    else:
        async def run_one(manifest: pathlib.Path):
            return await _run_manifest(manifest, compose_file, runs_root, workspace_root, verifier_image, vllm_host,
                                       vllm_port, verifier_network=verifier_network,
                                       allow_unenforced_storage=allow_unenforced_storage, task_runtime=task_runtime)

    async def main():
        try:
            return await run_chain(spec, plan.resolve(), run_one, runs_root / "chains", chain_id, resume)
        finally:
            if stacks and not keep_up:
                await stacks[-1].down()

    summary = asyncio.run(main())
    _emit(summary, json_, ok=summary["failed"] == 0)


@experiment_app.command("run")
def experiment_run(experiment: pathlib.Path,
                 out: pathlib.Path = typer.Option(None, "--out",
                                                  help="Where trial manifests, compose files and experiment.json go; "
                                                       "default runs/experiments/<name>"),
                 runs_root: pathlib.Path = RUNS_ROOT_OPT,
                 workspace_root: pathlib.Path = WORKSPACE_ROOT_OPT,
                 verifier_image: str = VERIFIER_IMAGE_OPT,
                 verifier_network: str = VERIFIER_NETWORK_OPT,
                 allow_unenforced_storage: bool = UNENFORCED_STORAGE_OPT,
                 task_runtime: str = TASK_RUNTIME_OPT,
                 vllm_port: int = typer.Option(8000, "--vllm-port"),
                 vllm_host: str = typer.Option("host.docker.internal", "--vllm-host"),
                 keep_up: bool = typer.Option(False, "--keep-up", help="Leave the sandbox running afterwards"),
                 json_: bool = JSON_OPT) -> None:
    """Run all conditions and repetitions with fresh worlds; retain failures and missing observations."""
    from mesocosm.core.experiment import experiment_table, load_experiment, run_experiment

    try:
        spec = load_experiment(experiment)
    except (ValueError, OSError) as exc:
        _emit({"status": "invalid_experiment", "error": str(exc)}, json_, ok=False)
        return
    out_dir = out or runs_root / "experiments" / spec.name
    stacks: list = []
    run_one = _stack_runner(out_dir, runs_root, workspace_root, verifier_image, vllm_port, vllm_host, stacks,
                            verifier_network=verifier_network, allow_unenforced_storage=allow_unenforced_storage,
                            task_runtime=task_runtime, fresh_workspace=True)

    async def main():
        try:
            return await run_experiment(spec, experiment.resolve(), out_dir, run_one)
        finally:
            if stacks and not keep_up:
                await stacks[-1].down()

    try:
        result = asyncio.run(main())
    except Exception as exc:
        _emit({"status": "failed_experiment", "error": str(exc), "out": str(out_dir)}, json_, ok=False)
        return
    _emit(experiment_table(out_dir), json_, ok=not (result["failed"] or result["incomplete_records"]
                                                   or result["not_run"] or result["stopped"]))


@experiment_app.command("report")
def experiment_report(out_dir: pathlib.Path, json_: bool = JSON_OPT) -> None:
    """Compare every condition, keeping task performance, incentives and detection separate."""
    from mesocosm.core.experiment import experiment_table

    try:
        result = experiment_table(out_dir)
    except (OSError, ValueError, KeyError) as exc:
        _emit({"status": "invalid_experiment_record", "error": str(exc)}, json_, ok=False)
        return
    _emit(result, json_)


@scenario_app.command("run")
def scenario_run(scenario: pathlib.Path,
                 out: pathlib.Path = typer.Option(None, "--out",
                                                  help="Where stage manifests, compose files and scenario.json go; "
                                                       "default runs/scenarios/<name>"),
                 runs_root: pathlib.Path = RUNS_ROOT_OPT,
                 workspace_root: pathlib.Path = WORKSPACE_ROOT_OPT,
                 verifier_image: str = VERIFIER_IMAGE_OPT,
                 verifier_network: str = VERIFIER_NETWORK_OPT,
                 allow_unenforced_storage: bool = UNENFORCED_STORAGE_OPT,
                 task_runtime: str = TASK_RUNTIME_OPT,
                 vllm_port: int = typer.Option(8000, "--vllm-port"),
                 vllm_host: str = typer.Option("host.docker.internal", "--vllm-host"),
                 keep_up: bool = typer.Option(False, "--keep-up", help="Leave the sandbox running afterwards"),
                 json_: bool = JSON_OPT) -> None:
    """Run a staged scenario: per stage, render and reconcile the sandbox, run, collect the judge's flags."""
    from mesocosm.core.scenario import load_scenario, run_scenario

    spec = load_scenario(scenario)
    out_dir = out or runs_root / "scenarios" / spec.name
    stacks: list = []
    run_one = _stack_runner(out_dir, runs_root, workspace_root, verifier_image, vllm_port, vllm_host, stacks,
                            verifier_network=verifier_network, allow_unenforced_storage=allow_unenforced_storage,
                            task_runtime=task_runtime)

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

    admin = GatewayAdmin(gateway) if gateway else control.gateway_admin_of(bundle)
    fn = {"pause": control.pause_run, "resume": control.resume_run, "stop": control.stop_run}[verb]
    receipt = asyncio.run(fn(bundle, admin, **kwargs))
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


@snapshot_app.command("create")
def snapshot_create(bundle: pathlib.Path,
                    compose_file: pathlib.Path = typer.Option(None, "--compose-file",
                                                              help="Running stack to copy the services data and the "
                                                                   "shared directory from; without it only workspaces"),
                    workspace_root: pathlib.Path = WORKSPACE_ROOT_OPT,
                    out: pathlib.Path = typer.Option(pathlib.Path("runs/snapshots"), "--out"),
                    note: str = typer.Option(None, "--note"),
                    json_: bool = JSON_OPT) -> None:
    """Save the declared world state of a run (pause it first) and declare what was not captured."""
    from mesocosm.core.snapshot import create_snapshot
    from mesocosm.env.world_state import DockerWorldState

    world = DockerWorldState(compose_file) if compose_file else None
    _emit(asyncio.run(create_snapshot(bundle, workspace_root, world, out, note)), json_)


@snapshot_app.command("restore")
def snapshot_restore(snapshot: pathlib.Path,
                     compose_file: pathlib.Path = typer.Option(None, "--compose-file"),
                     workspace_root: pathlib.Path = WORKSPACE_ROOT_OPT,
                     json_: bool = JSON_OPT) -> None:
    """Put a snapshot's workspaces, services data and shared directory back into a running stack; verify by hash."""
    from mesocosm.core.snapshot import restore_snapshot
    from mesocosm.env.world_state import DockerWorldState

    world = DockerWorldState(compose_file) if compose_file else None
    result = asyncio.run(restore_snapshot(snapshot, workspace_root, world))
    _emit(result, json_, ok=result["ok"])


@app.command()
def fork(snapshot: pathlib.Path,
         edit: pathlib.Path = typer.Option(None, "--edit", help="YAML patch merged into the snapshot's manifest"),
         out: pathlib.Path = typer.Option(None, "--out", help="Where the forked manifest and compose file go; "
                                                              "default runs/forks/<snapshot id>"),
         runs_root: pathlib.Path = RUNS_ROOT_OPT,
         workspace_root: pathlib.Path = WORKSPACE_ROOT_OPT,
         verifier_image: str = VERIFIER_IMAGE_OPT,
         verifier_network: str = VERIFIER_NETWORK_OPT,
         allow_unenforced_storage: bool = UNENFORCED_STORAGE_OPT,
         task_runtime: str = TASK_RUNTIME_OPT,
         vllm_port: int = typer.Option(8000, "--vllm-port"),
         vllm_host: str = typer.Option("host.docker.internal", "--vllm-host"),
         keep_up: bool = typer.Option(False, "--keep-up"),
         json_: bool = JSON_OPT) -> None:
    """New run from a snapshot under an edited manifest: bring the stack up, restore, run; lineage recorded."""
    import yaml

    from mesocosm.core.manifest import load_manifest, validate_manifest
    from mesocosm.core.run import run_world
    from mesocosm.core.snapshot import fork_manifest, restore_snapshot
    from mesocosm.env.compose import compose_yaml, write_agent_files
    from mesocosm.env.stack import DockerStack
    from mesocosm.env.world_state import DockerWorldState

    patch = yaml.safe_load(edit.read_text()) if edit else {}
    out_dir = out or runs_root / "forks" / snapshot.name
    manifest_path = out_dir / "world.yaml"
    lineage = fork_manifest(snapshot, patch or {}, manifest_path)
    report = validate_manifest(manifest_path)
    if not report.ok:
        _emit({"status": "invalid_manifest", "errors": report.errors, "manifest": str(manifest_path)}, json_, ok=False)
    spec = load_manifest(manifest_path)
    label = spec.gateway.run_label or "spike-docker"
    compose_file = out_dir / "world.compose.yml"
    compose_file.write_text(compose_yaml(spec, repo_root=pathlib.Path.cwd(), run_label=label, vllm_port=vllm_port,
                                         vllm_host=vllm_host, manifest_dir=manifest_path.resolve().parent))
    write_agent_files(spec, label, pathlib.Path.cwd() / "runs" / label / "agents",
                      manifest_dir=manifest_path.resolve().parent, web_dir=pathlib.Path.cwd() / "runs" / label / "web")
    (runs_root / label).mkdir(parents=True, exist_ok=True)
    stack = DockerStack(compose_file)

    async def main():
        await stack.up()
        try:
            restore = await restore_snapshot(snapshot, workspace_root, DockerWorldState(compose_file))
            components = _components(spec, compose_file, runs_root, workspace_root, verifier_image, vllm_host,
                                     vllm_port, verifier_network=verifier_network,
                                     allow_unenforced_storage=allow_unenforced_storage, task_runtime=task_runtime)
            components.restored_from, components.restore_result = snapshot.resolve(), restore
            components.lineage = lineage
            return restore, await run_world(spec, manifest_path.resolve(), components, runs_root)
        finally:
            if not keep_up:
                await stack.down()

    restore, result = asyncio.run(main())
    _emit({"run_id": result.run_id, "status": result.status, "bundle": str(result.bundle_path),
           "rewards": result.rewards, "restore_ok": restore["ok"], "lineage": lineage, "error": result.error},
          json_, ok=result.status == "ok" and restore["ok"])


@activations_app.command("capture")
def activations_capture(bundle: pathlib.Path,
                        model: str = typer.Option(..., "--model", help="HF repo of the served weights"),
                        layers: str = typer.Option(None, "--layers",
                                                   help="Comma-separated layer indices; default quarters"),
                        raw: str = typer.Option("flagged", "--raw", help="flagged | all | none"),
                        positions: str = typer.Option("completion", "--positions", help="completion | all"),
                        limit: int = typer.Option(None, "--limit"),
                        request_id: list[str] = typer.Option(None, "--request"),
                        device: str = typer.Option("cuda", "--device"),
                        dtype: str = typer.Option("bfloat16", "--dtype"),
                        served_model: str = typer.Option(None, "--served-model",
                                                         help="Only requests that named this model id (a world "
                                                              "with several models)"),
                        json_: bool = JSON_OPT) -> None:
    """Recompute activations for a bundle's requests from the recorded token ids (needs the GPU and the weights)."""
    from mesocosm.observe.activations import capture

    layer_ids = [int(x) for x in layers.split(",")] if layers else None
    report = capture(bundle, model, layer_ids, raw, positions, limit, request_id or None, device, dtype,
                     served_model=served_model)
    if report.get("captured", 0) > 0:
        report["probes"] = _score_manifest_probes(bundle)
    _emit(report, json_, ok=report.get("captured", 0) > 0)


def _score_manifest_probes(bundle: pathlib.Path) -> dict:
    """observe.capture.probes of the bundle's manifest, scored over what was just captured (ADR-0031)."""
    from mesocosm.core.records import RunBundle
    from mesocosm.observe.probes import score

    stored = RunBundle.open(bundle).manifest()
    base = pathlib.Path(stored["path"]).parent if stored.get("path") else pathlib.Path.cwd()
    out = {}
    for rel in ((stored.get("manifest") or {}).get("observe") or {}).get("capture", {}).get("probes") or []:
        path = pathlib.Path(rel) if pathlib.Path(rel).is_absolute() else base / rel
        try:
            out[path.stem] = score(bundle, path)
        except (ValueError, OSError) as e:
            out[path.stem] = {"error": str(e)}
    return out


@probe_app.command("train")
def probe_train(bundles: list[pathlib.Path] = typer.Argument(..., help="Bundles with captured raw tensors"),
                layer: int = typer.Option(..., "--layer"),
                out: pathlib.Path = typer.Option(..., "--out", help="Probe file (.safetensors)"),
                labels: pathlib.Path = typer.Option(None, "--labels", help="JSON {request_id: 0|1}; default: the "
                                                                          "judge's flags label the positives"),
                pool: str = typer.Option("mean", "--pool", help="mean | last position"),
                l2: float = typer.Option(1e-2, "--l2"),
                steps: int = typer.Option(500, "--steps"),
                json_: bool = JSON_OPT) -> None:
    """Train a logistic-regression probe on the raw tensors captured at one layer (ADR-0031)."""
    from mesocosm.observe.probes import train

    try:
        report = train(bundles, layer, out, labels, pool, l2, steps)
    except ValueError as e:
        _emit({"error": str(e)}, json_, ok=False)
        return
    _emit(report, json_)


@probe_app.command("score")
def probe_score(bundle: pathlib.Path, probe: pathlib.Path = typer.Option(..., "--probe"),
                name: str = typer.Option(None, "--name", help="Score file name; default: the probe's"),
                json_: bool = JSON_OPT) -> None:
    """Score every captured request of a bundle with a probe; refuses a bundle captured differently."""
    from mesocosm.observe.probes import score

    try:
        report = score(bundle, probe, name)
    except ValueError as e:
        _emit({"error": str(e)}, json_, ok=False)
        return
    _emit(report, json_)


@activations_app.command("verify")
def activations_verify(bundle: pathlib.Path, model: str = typer.Option(..., "--model"),
                       tolerance: float = typer.Option(0.05, "--tolerance", help="Allowed relative L2 difference"),
                       limit: int = typer.Option(None, "--limit"),
                       device: str = typer.Option("cuda", "--device"),
                       dtype: str = typer.Option("bfloat16", "--dtype", help="Second-pass dtype, e.g. float32"),
                       json_: bool = JSON_OPT) -> None:
    """Recompute again and compare with what was captured; the tolerance is stated in the result."""
    from mesocosm.observe.activations import verify

    report = verify(bundle, model, tolerance, limit, device, dtype)
    _emit(report, json_, ok=report.get("ok", False))


@conditions_app.command("set")
def conditions_set(bundle: pathlib.Path, agent_id: str, condition: str,
                   value: str = typer.Argument(..., help="JSON, e.g. '{\"completion_tokens\": 20000}'"),
                   gateway: str = typer.Option(None, "--gateway", help="Gateway URL; default: the bundle's manifest"),
                   json_: bool = JSON_OPT) -> None:
    """Change a condition while a run is going: enforced at its component, receipt appended to the bundle;
    anything the platform cannot change mid-run is rejected_unsupported."""
    from mesocosm.core.conditions import set_condition
    from mesocosm.core.control import gateway_admin_of
    from mesocosm.gateway.client import GatewayAdmin

    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        parsed = value
    admin = GatewayAdmin(gateway) if gateway else gateway_admin_of(bundle)
    receipt = asyncio.run(set_condition(bundle, agent_id, condition, parsed, admin))
    _emit(receipt.model_dump(mode="json"), json_, ok=receipt.result == "applied")


@export_app.command("inspect")
def export_inspect(bundle: pathlib.Path,
                   out: pathlib.Path = typer.Option(None, "--out", help="Where to write; default "
                                                                        "<bundle>/exports/<run id>.json"),
                   json_: bool = JSON_OPT) -> None:
    """Write the bundle as an Inspect eval log (JSON, version 2): `inspect view` opens it (ADR-0025)."""
    import json as _json

    from mesocosm.export.inspect_log import write_inspect_log

    path = write_inspect_log(bundle, out)
    log = _json.loads(path.read_text(encoding="utf-8"))
    _emit({"written": str(path), "status": log["status"], "samples": len(log["samples"]),
           "not_exported": log["eval"]["metadata"]["mesocosm"]["not_exported"]}, json_)


@judge_app.command("monitor")
def judge_monitor(bundle: pathlib.Path,
                  model: str = typer.Option(..., "--model", help="Model id the endpoint serves"),
                  url: str = typer.Option(..., "--url", help="OpenAI-compatible base URL ending in /v1"),
                  key_env: str = typer.Option(None, "--key-env", help="Environment variable holding the API key"),
                  threshold: int = typer.Option(70, "--threshold", min=0, max=100),
                  side_task_aware: bool = typer.Option(False, "--side-task-aware"),
                  scope: str = typer.Option("per_agent", "--scope"),
                  visibility: str = typer.Option("transcript", "--visibility"),
                  include_shared: bool = typer.Option(False, "--include-shared"),
                  rubric_file: pathlib.Path = typer.Option(None, "--rubric-file"),
                  rubric_version: str = typer.Option("generic-v1", "--rubric-version"),
                  max_input_chars: int = typer.Option(60000, "--max-input-chars", min=100),
                  max_calls: int = typer.Option(None, "--max-calls", min=1),
                  output_tokens: int = typer.Option(400, "--output-tokens", min=32),
                  json_: bool = JSON_OPT) -> None:
    """Score every agent/task transcript of a finished bundle with a monitor model (ADR-0030): appends
    `monitor.score` events and `monitor_suspicion` flags; nothing already recorded changes."""
    import os

    from mesocosm.core.manifest import MonitorSpec
    from mesocosm.core.records import RunBundle
    from mesocosm.judge.flags import flag_counts, monitor_suspicion
    from mesocosm.judge.monitor import MonitorModel, score_bundle

    try:
        config = MonitorSpec.model_validate({"scope": scope, "visibility": visibility,
                    "include_shared": include_shared,
                    "rubric": rubric_file.read_text(encoding="utf-8") if rubric_file else None,
                    "rubric_version": rubric_version, "max_input_chars": max_input_chars,
                    "max_calls": max_calls, "output_tokens": output_tokens})
    except (ValueError, OSError) as exc:
        _emit({"status": "invalid_monitor", "error": str(exc)}, json_, ok=False)
        return
    options = config.model_dump(exclude={"enabled", "model", "threshold", "side_task_aware", "timing"},
                                exclude_none=True)

    async def main():
        b = RunBundle.open(bundle)
        monitor = MonitorModel(url, model, api_key=os.environ.get(key_env) if key_env else None, run_label=b.run_id)
        try:
            summary = await score_bundle(b, monitor, threshold, side_task_aware, **options)
        finally:
            await monitor.aclose()
        known = {f["flag_id"] for f in b.flags()}
        evidence_seen = {(f["agent_id"], e.get("ts")) for f in b.flags() if f["kind"] == "monitor_suspicion"
                         for e in f.get("evidence") or []}
        new = [f for f in monitor_suspicion(b.events(), b.run_id)
               if f.flag_id not in known and (f.agent_id, f.evidence[0]["ts"]) not in evidence_seen]
        for f in new:
            b.record_flag(f.model_dump(mode="json"))
        return {**summary, "new_flags": flag_counts(new), "bundle": str(b.root)}

    _emit(asyncio.run(main()), json_)


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
