"""Execution worker entry point; installed providers own their deployment requirements."""

from __future__ import annotations

import asyncio
import json
import os
import pathlib
from typing import Annotated

import typer
import yaml

worker_app = typer.Typer(no_args_is_help=True, help="Own research worlds through an installed execution backend")


def _provided(context: typer.Context, name: str) -> bool:
    source = context.get_parameter_source(name)
    return source is not None and source.name == "COMMANDLINE"


@worker_app.command()
def serve(
    context: typer.Context,
    root: Annotated[pathlib.Path, typer.Option(help="Private durable worker directory")],
    repo_root: Annotated[pathlib.Path | None, typer.Option(help="Docker: full Mesocosm source checkout")] = None,
    backend: Annotated[str, typer.Option(help="Installed execution backend identifier")] = "docker",
    backend_config: Annotated[pathlib.Path | None, typer.Option(
        help="Operator-owned YAML/JSON mapping passed to the backend factory")] = None,
    host: str = "127.0.0.1",
    port: int = 8765,
    token_env: str = "MESOCOSM_WORKER_TOKEN",
    model_key_env: Annotated[list[str] | None, typer.Option(help="Allowed model key variable; repeatable")] = None,
    vllm_host: str = "host.docker.internal",
    vllm_port: int = 8000,
    verifier_network: Annotated[str, typer.Option(help="Native verifier only: none or bridge")] = "none",
    allow_unenforced_resources: Annotated[bool, typer.Option(
        help="Explicitly record native resource limits Docker cannot enforce")] = False,
    max_pending: int = 16,
    json_: Annotated[bool, typer.Option("--json", help="Print one startup receipt on stdout")] = False,
) -> None:
    """Serve one world at a time. Use SSH forwarding or a TLS proxy for remote access."""
    try:
        import uvicorn

        from mesocosm.execution.backends import BackendContext
        from mesocosm.execution.worker import Worker, create_app
        from mesocosm.integrations.registry import backend_identity, get_backend_provider

        token = os.environ.get(token_env, "")
        if len(token) < 32:
            raise ValueError("token environment variable must contain at least 32 characters")
        if not 1 <= port <= 65535 or not 1 <= vllm_port <= 65535:
            raise ValueError("ports must be between 1 and 65535")
        options = yaml.safe_load(backend_config.read_text()) if backend_config is not None else {}
        if not isinstance(options, dict) or any(not isinstance(key, str) for key in options):
            raise ValueError("backend config must be a YAML/JSON mapping with string keys")
        if backend == "docker":
            defaults = {"vllm_host": "host.docker.internal", "vllm_port": 8000, "model_key_envs": [],
                        "verifier_network": "none", "allow_unenforced_resources": False}
            flags = {"vllm_host": vllm_host, "vllm_port": vllm_port, "model_key_envs": model_key_env or [],
                     "verifier_network": verifier_network, "allow_unenforced_resources": allow_unenforced_resources}
            names = {"model_key_envs": "model_key_env"}
            # Explicit flags override the operator file even when they equal the CLI default.
            options = defaults | options | {key: value for key, value in flags.items()
                if _provided(context, names.get(key, key))}
            if repo_root is not None:
                options["repo_root"] = repo_root
        elif any(_provided(context, key) for key in
                 ("repo_root", "model_key_env", "vllm_host", "vllm_port", "verifier_network",
                  "allow_unenforced_resources")):
            raise ValueError("Docker operator flags require --backend docker; use --backend-config for this backend")
        identity = backend_identity(backend)
        implementation = get_backend_provider(backend).create(BackendContext(root.resolve(), options))
        methods = ("capabilities", "execute", "recover")
        if any(not callable(getattr(implementation, method, None)) for method in methods):
            raise ValueError("execution backend must implement capabilities(), execute() and recover()")
        capabilities = asyncio.run(implementation.capabilities())
        if capabilities.get("status") != "supported":
            raise ValueError(f"worker prerequisite unavailable: {capabilities.get('reason', 'unknown')}")
        worker = Worker(root, implementation, max_pending=max_pending, backend_id=backend,
                        backend_identity=identity)
        app = create_app(worker, token)
    except Exception as exc:
        payload = {"status": "error", "error_type": type(exc).__name__, "detail": str(exc)}
        typer.echo(json.dumps(payload) if json_ else payload["detail"])
        raise typer.Exit(1) from None
    receipt = {"status": "starting", "host": host, "port": port, "root": str(worker.root),
               "backend": backend, "backend_identity": identity, "max_active": 1}
    if backend == "docker":
        receipt["operator_policy"] = getattr(implementation, "operator_policy", {})
    typer.echo(json.dumps(receipt) if json_ else f"Starting worker on {host}:{port}")
    # Access logging normally uses stdout; keep the machine-readable startup receipt singular.
    uvicorn.run(app, host=host, port=port, access_log=False)
