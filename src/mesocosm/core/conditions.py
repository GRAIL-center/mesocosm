"""Conditions changed during a run (`mesocosm conditions set`). Only what an enforcement point can
change while agents are working is accepted; everything else is `rejected_unsupported` with a
receipt, never approximated (ADR-0003)."""

from __future__ import annotations

import pathlib
from typing import Any

from mesocosm.core.receipts import EnforcedAt, Receipt, Verification
from mesocosm.core.records import RunBundle
from mesocosm.gateway.client import GatewayAdmin

MID_RUN_CONDITIONS = {"budget": "gateway"}


async def set_condition(bundle_root: str | pathlib.Path, agent_id: str, condition: str, value: Any,
                        gateway: GatewayAdmin) -> Receipt:
    bundle = RunBundle.open(bundle_root)
    if condition == "budget" and isinstance(value, dict):
        receipt = await gateway.set_budget(agent_id, value, run_id=bundle.run_id)
    else:
        receipt = Receipt(op="conditions.set", run_id=bundle.run_id,
                          requested={"condition": condition, "agent_id": agent_id, "value": value},
                          enforced_at=EnforcedAt(component="control"), result="rejected_unsupported",
                          verification=Verification(method="supported_conditions", ok=False,
                                                    detail={"mid_run": MID_RUN_CONDITIONS}))
    bundle.record_receipt(receipt.model_dump(mode="json"))
    bundle.record("conditions.set", agent_id=agent_id, condition=condition, result=receipt.result,
                  event_id=receipt.enforced_at.event_id, mid_run=True)
    return receipt
