"""A condition changed mid-run is enforced at its component and visible in that component's log; the
rest is rejected with a receipt (the M3 negative control)."""

import json

import httpx

from mesocosm.core.conditions import set_condition
from mesocosm.core.records import RunBundle
from mesocosm.gateway.app import Settings, create_app
from mesocosm.gateway.client import GatewayAdmin


async def test_budget_changes_leave_receipts_at_the_gateway_and_in_the_bundle(tmp_path):
    settings = Settings(upstream="http://vllm", log_dir=tmp_path / "gw")
    gateway = GatewayAdmin("http://gateway", transport=httpx.ASGITransport(app=create_app(settings)))
    bundle = RunBundle.create(tmp_path / "runs", "run_c")
    bundle.write_manifest({"gateway": {"url": "http://gateway"}}, "d")
    first = await set_condition(bundle.root, "agent-1", "budget", {"completion_tokens": 50000}, gateway)
    second = await set_condition(bundle.root, "agent-1", "budget", {"completion_tokens": 20000}, gateway)
    assert first.result == "applied" and first.previous is None
    assert second.result == "applied" and second.previous == {"value": {"completion_tokens": 50000}}
    assert second.enforced_at.component == "gateway" and second.verification.ok
    log = [json.loads(ln) for ln in (tmp_path / "gw" / "gateway.jsonl").read_text().splitlines()]
    assert [r["requested"]["value"] for r in log if r["event"] == "receipt"] == [{"completion_tokens": 50000},
                                                                                 {"completion_tokens": 20000}]
    rejected = await set_condition(bundle.root, "agent-1", "exposure", {"wiki": {"x": 1}}, gateway)
    assert rejected.result == "rejected_unsupported" and rejected.verification.detail == {"mid_run": {"budget": "gateway"}}
    assert [r["result"] for r in bundle.receipts()] == ["applied", "applied", "rejected_unsupported"]
    events = [e for e in bundle.events() if e.kind == "conditions.set"]
    assert all(e.data["mid_run"] is True and e.agent_id == "agent-1" for e in events)
    assert [e.data["condition"] for e in events] == ["budget", "budget", "exposure"]
    await gateway.aclose()
