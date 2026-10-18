"""pause / resume / stop from the control plane: gateway receipts land in the bundle, the stop marker ends
the run between tasks."""

import httpx

from mesocosm.core.control import pause_run, resume_run, stop_requested, stop_run
from mesocosm.core.records import RunBundle
from mesocosm.gateway.app import Settings, create_app
from mesocosm.gateway.client import GatewayAdmin


async def test_lifecycle_verbs_leave_receipts_events_and_the_stop_marker(tmp_path):
    gateway = GatewayAdmin("http://gateway", transport=httpx.ASGITransport(
        app=create_app(Settings(upstream="http://vllm", log_dir=tmp_path / "gw"))))
    bundle = RunBundle.create(tmp_path / "runs", "run_l")
    bundle.write_manifest({"gateway": {"url": "http://gateway", "run_label": "lbl"}}, "d")
    paused = await pause_run(bundle.root, gateway, agent_id="agent-1", reason="look")
    assert paused.requested == {"run": "lbl", "agent_id": "agent-1", "reason": "look"} and paused.run_id == "run_l"
    resumed = await resume_run(bundle.root, gateway, agent_id="agent-1")
    assert resumed.verification.detail["paused"] is True
    assert not stop_requested(bundle.root)
    stopped = await stop_run(bundle.root, gateway, reason="enough")
    assert stopped.op == "run.stop" and stop_requested(bundle.root)
    assert [r["op"] for r in bundle.receipts()] == ["run.pause", "run.resume", "run.stop"]
    kinds = [(e.kind, e.agent_id) for e in bundle.events()]
    assert kinds == [("run.paused", "agent-1"), ("run.resumed", "agent-1"), ("run.stop_requested", None)]
    await gateway.aclose()
