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
app.add_typer(world_app, name="world")
app.add_typer(env_app, name="env")
app.add_typer(observe_app, name="observe")

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


@app.command()
def run(manifest: pathlib.Path,
        runs_root: pathlib.Path = typer.Option(pathlib.Path("runs"), "--runs-root"),
        compose_file: pathlib.Path = typer.Option(pathlib.Path("spike/docker/compose.yml"), "--compose-file",
                                                  help="Compose file whose agent services are named after agent ids"),
        workspace_root: pathlib.Path = typer.Option(pathlib.Path("spike/workspace"), "--workspace-root"),
        verifier_image: str = typer.Option("debian:bookworm-slim", "--verifier-image"),
        json_: bool = JSON_OPT) -> None:
    """Run a world end to end: validate, egress check, budgets, tasks, verifier, run bundle."""
    from mesocosm.adapters.opencode.client import OpenCodeClient
    from mesocosm.core.manifest import load_manifest, validate_manifest
    from mesocosm.core.run import Components, DockerVerifier, run_world
    from mesocosm.env.compose import service_address
    from mesocosm.env.egress import DockerEgressVerifier
    from mesocosm.env.services.client import ServicesAdmin
    from mesocosm.gateway.client import GatewayAdmin

    report = validate_manifest(manifest)
    if not report.ok:
        _emit({"status": "invalid_manifest", "errors": report.errors}, json_, ok=False)
    spec = load_manifest(manifest)
    components = Components(
        harness_for=lambda a: OpenCodeClient(a.harness_url()),
        gateway=GatewayAdmin(spec.gateway.url),
        egress=DockerEgressVerifier(compose_file),
        verifier=DockerVerifier(verifier_image),
        workspace_for=lambda a: workspace_root / a.id,
        services_log=runs_root / (spec.gateway.run_label or "spike-docker") / "services.jsonl",
        services=ServicesAdmin(f"http://{service_address(spec, 'services')}") if spec.services.enabled else None,
    )
    result = asyncio.run(run_world(spec, manifest.resolve(), components, runs_root))
    _emit({"run_id": result.run_id, "status": result.status, "bundle": str(result.bundle_path),
           "rewards": result.rewards, "warnings": report.warnings, "error": result.error},
          json_, ok=result.status == "ok")


if __name__ == "__main__":
    app()
