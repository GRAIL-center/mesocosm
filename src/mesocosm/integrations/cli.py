"""Inspect installed integrations and validate selected contracts without execution."""

from __future__ import annotations

import json
import pathlib

import typer

integration_app = typer.Typer(no_args_is_help=True, help="Installed task sources and execution backends")
JSON = typer.Option(False, "--json", help="Print one JSON object on stdout")


@integration_app.command("list")
def list_integrations(json_: bool = JSON) -> None:
    """List entry point metadata without importing external plugins."""
    from mesocosm.integrations.registry import API_VERSION, integration_inventory

    payload = {"api_version": API_VERSION, "integrations": integration_inventory()}
    typer.echo(json.dumps(payload) if json_ else json.dumps(payload, indent=2))


@integration_app.command("check")
def check(manifest: pathlib.Path, json_: bool = JSON) -> None:
    """Resolve installed providers and materialized tasks; no GPU, Docker or model calls."""
    from mesocosm.core.manifest import load_manifest, validate_manifest
    from mesocosm.integrations.registry import integration_snapshot, validate_integrations

    try:
        report = validate_manifest(manifest)
        if not report.ok:
            raise ValueError("\n".join(report.errors))
        world = load_manifest(manifest)
        validate_integrations(world, manifest.resolve().parent, resolve_datasets=True)
        payload = {"status": "compatible", "scope": "integration_contracts", "runtime_preflight_required": True,
                   "plugins": integration_snapshot(world), "warnings": report.warnings}
    except (OSError, ValueError) as exc:
        payload = {"status": "rejected_unsupported", "error": str(exc)}
        typer.echo(json.dumps(payload) if json_ else payload["error"])
        raise typer.Exit(1) from None
    typer.echo(json.dumps(payload) if json_ else json.dumps(payload, indent=2))
