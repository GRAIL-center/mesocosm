"""Linux execution worker entry point; dependencies are loaded only when serving."""

from __future__ import annotations

import asyncio
import json
import os
import pathlib
from typing import Annotated

import typer

worker_app = typer.Typer(no_args_is_help=True, help="Own research worlds on a Linux Docker host")


@worker_app.command()
def serve(
    root: Annotated[pathlib.Path, typer.Option(help="Private durable worker directory")],
    repo_root: Annotated[pathlib.Path, typer.Option(help="Full Mesocosm source checkout")],
    host: str = "127.0.0.1",
    port: int = 8765,
    token_env: str = "MESOCOSM_WORKER_TOKEN",
    model_key_env: Annotated[list[str] | None, typer.Option(help="Allowed model key variable; repeatable")] = None,
    vllm_host: str = "host.docker.internal",
    vllm_port: int = 8000,
    max_pending: int = 16,
    json_: Annotated[bool, typer.Option("--json", help="Print one startup receipt on stdout")] = False,
) -> None:
    """Serve one world at a time. Use SSH forwarding or a TLS proxy for remote access."""
    try:
        import uvicorn

        from mesocosm.execution.docker import DockerBackend
        from mesocosm.execution.worker import Worker, create_app

        token = os.environ.get(token_env, "")
        if len(token) < 32:
            raise ValueError("token environment variable must contain at least 32 characters")
        if not 1 <= port <= 65535 or not 1 <= vllm_port <= 65535:
            raise ValueError("ports must be between 1 and 65535")
        backend = DockerBackend(repo_root, vllm_host, vllm_port, tuple(model_key_env or ()))
        capabilities = asyncio.run(backend.capabilities())
        if capabilities.get("status") != "supported":
            raise ValueError(f"worker prerequisite unavailable: {capabilities.get('reason', 'unknown')}")
        worker = Worker(root, backend, max_pending=max_pending)
        app = create_app(worker, token)
    except (ImportError, ValueError) as exc:
        payload = {"status": "error", "error_type": type(exc).__name__, "detail": str(exc)}
        typer.echo(json.dumps(payload) if json_ else payload["detail"])
        raise typer.Exit(1) from None
    receipt = {"status": "starting", "host": host, "port": port, "root": str(worker.root),
               "backend": "docker", "max_active": 1}
    typer.echo(json.dumps(receipt) if json_ else f"Starting worker on {host}:{port}")
    # Access logging normally uses stdout; keep the machine-readable startup receipt singular.
    uvicorn.run(app, host=host, port=port, access_log=False)
