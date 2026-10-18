"""Run lifecycle from outside the run manager: pause, resume, stop (docs/operation-contract.md).

OpenCode cannot pause a turn, so the gateway holds a paused run's model requests and refuses a
stopped run's (ADR-0014). The run manager honors a stop marker between tasks. Every verb appends
the gateway's receipt to the bundle and records an event.
"""

from __future__ import annotations

import json
import pathlib
import time
from typing import Any

from mesocosm.core.receipts import Receipt
from mesocosm.core.records import RunBundle
from mesocosm.gateway.client import GatewayAdmin

STOP_MARKER = pathlib.Path("control") / "stop"


def run_label_of(bundle: RunBundle) -> str:
    return (bundle.manifest().get("manifest") or {}).get("gateway", {}).get("run_label") or bundle.run_id


def stop_requested(bundle_root: pathlib.Path) -> bool:
    return (bundle_root / STOP_MARKER).exists()


def gateway_url_of(bundle_root: str | pathlib.Path) -> str:
    return RunBundle.open(bundle_root).manifest()["manifest"]["gateway"]["url"]


def gateway_admin_of(bundle_root: str | pathlib.Path) -> GatewayAdmin:
    """A client for the gateway the bundle's manifest names, with its control key."""
    spec = RunBundle.open(bundle_root).manifest()["manifest"]["gateway"]
    return GatewayAdmin(spec["url"], api_key=spec.get("api_key", "mesocosm-local"))


async def pause_run(bundle_root: str | pathlib.Path, gateway: GatewayAdmin, agent_id: str | None = None,
                    reason: str | None = None) -> Receipt:
    bundle = RunBundle.open(bundle_root)
    receipt = await gateway.pause(run_label_of(bundle), agent_id, reason, run_id=bundle.run_id)
    bundle.record_receipt(receipt.model_dump(mode="json"))
    bundle.record("run.paused", agent_id=agent_id, reason=reason, event_id=receipt.enforced_at.event_id)
    return receipt


async def resume_run(bundle_root: str | pathlib.Path, gateway: GatewayAdmin,
                     agent_id: str | None = None) -> Receipt:
    bundle = RunBundle.open(bundle_root)
    receipt = await gateway.resume(run_label_of(bundle), agent_id, run_id=bundle.run_id)
    bundle.record_receipt(receipt.model_dump(mode="json"))
    detail: dict[str, Any] = receipt.verification.detail if receipt.verification else {}
    bundle.record("run.resumed", agent_id=agent_id, event_id=receipt.enforced_at.event_id, **detail)
    return receipt


async def stop_run(bundle_root: str | pathlib.Path, gateway: GatewayAdmin, reason: str | None = None) -> Receipt:
    bundle = RunBundle.open(bundle_root)
    receipt = await gateway.stop(run_label_of(bundle), run_id=bundle.run_id)
    marker = bundle.root / STOP_MARKER
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text(json.dumps({"ts": time.time(), "reason": reason, "event_id": receipt.enforced_at.event_id}))
    bundle.record_receipt(receipt.model_dump(mode="json"))
    bundle.record("run.stop_requested", reason=reason, event_id=receipt.enforced_at.event_id)
    return receipt
