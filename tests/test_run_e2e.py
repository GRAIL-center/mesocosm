"""End-to-end run with fakes for everything that needs hardware: a fake OpenCode server that
calls the real gateway app (which proxies to a fake vLLM), a fake egress verifier, and a verifier
that writes Harbor's reward file. Exercises the M1 exit criterion with the real control plane."""

import json
import pathlib
import time

import httpx
import pytest
from fastapi import FastAPI, Request
from fastapi.responses import StreamingResponse

from mesocosm.adapters.harbor.task import HarborTask, Reward, read_reward
from mesocosm.adapters.opencode.client import OpenCodeClient
from mesocosm.core.manifest import ChannelSpec, load_manifest
from mesocosm.core.records import RunBundle
from mesocosm.core.run import Components, run_world
from mesocosm.env.egress import parse_egress_output
from mesocosm.gateway.app import Settings, create_app
from mesocosm.gateway.client import GatewayAdmin

ROOT = pathlib.Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "examples" / "spike-world.yaml"


def fake_vllm() -> FastAPI:
    up = FastAPI()

    @up.post("/v1/chat/completions")
    async def chat(request: Request):
        body = await request.json()
        n = len(body["messages"])
        return {"id": f"cmpl-{n}", "model": "mesocosm-3b",
                "choices": [{"index": 0, "message": {"role": "assistant", "content": "ok"}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 10 * n, "completion_tokens": 3, "total_tokens": 10 * n + 3}}

    return up


def fake_opencode(gateway: httpx.AsyncClient, agent_id: str, run_label: str) -> FastAPI:
    """An `opencode serve` stand-in whose assistant turn makes two model calls through the gateway."""
    app = FastAPI()
    sessions: dict[str, list] = {}

    @app.get("/global/health")
    async def health():
        return {"healthy": True, "version": "fake"}

    @app.post("/session")
    async def create(request: Request):
        sid = f"ses_{agent_id}_{len(sessions) + 1}"
        sessions[sid] = []
        return {"id": sid}

    @app.post("/session/{sid}/message")
    async def message(sid: str, request: Request):
        body = await request.json()
        hdr = {"X-Mesocosm-Agent": agent_id, "X-Mesocosm-Run": run_label}
        for i in range(2):
            r = await gateway.post("/v1/chat/completions", headers=hdr,
                                   json={"model": "mesocosm-3b", "messages": [{"role": "user", "content": "x"}] * (i + 1)})
            assert r.status_code == 200
        reply = {"info": {"role": "assistant", "sessionID": sid},
                 "parts": [{"type": "tool", "tool": "bash", "state": {"status": "completed"}}, {"type": "text", "text": "done"}]}
        sessions[sid] += [{"info": {"role": "user", "sessionID": sid}, "parts": body["parts"]}, reply]
        return reply

    @app.get("/session/{sid}/message")
    async def list_messages(sid: str):
        return sessions[sid]

    @app.get("/event")
    async def events():
        async def gen():
            yield b'data: {"type":"server.connected","properties":{}}\n\n'
            part = {"sessionID": f"ses_{agent_id}_1", "type": "tool", "tool": "bash", "state": {"status": "running"}}
            yield f'data: {json.dumps({"type": "message.part.updated", "properties": {"part": part}})}\n\n'.encode()
        return StreamingResponse(gen(), media_type="text/event-stream")

    return app


class FakeEgress:
    def __init__(self, ok: bool):
        self.ok = ok

    async def verify(self, agent_ids):
        channels = "OK channel pastebin absent (disabled)\nOK channel shared_dir absent (disabled)\n"
        text = ("OK gateway reachable\nOK HTTPS egress blocked\n" + channels if self.ok
                else "OK gateway reachable\nFAIL HTTPS egress to example.com succeeded\n" + channels)
        return {a: parse_egress_output(text, 0 if self.ok else 1) for a in agent_ids}


class FakeVerifier:
    """Behaves like tests/test.sh: reward 1 if hello.txt has the line, writing Harbor's reward file."""

    async def verify(self, task: HarborTask, workspace: pathlib.Path, logs_dir: pathlib.Path) -> Reward:
        (logs_dir / "verifier").mkdir(parents=True, exist_ok=True)
        ok = (workspace / "hello.txt").exists() and (workspace / "hello.txt").read_text().strip() == "Hello, Mesocosm!"
        (logs_dir / "verifier" / "reward.txt").write_text("1\n" if ok else "0\n")
        return read_reward(logs_dir)


@pytest.fixture
def world(tmp_path):
    manifest = load_manifest(MANIFEST)
    manifest.gateway.run_label = "e2e"
    gw_app = create_app(Settings(upstream="http://vllm", log_dir=tmp_path / "gw"),
                        transport=httpx.ASGITransport(app=fake_vllm()))
    gw_transport = httpx.ASGITransport(app=gw_app)
    gw_for_harness = httpx.AsyncClient(transport=gw_transport, base_url="http://gateway")
    workspace = tmp_path / "ws" / "agent-1"
    workspace.mkdir(parents=True)
    (workspace / "hello.txt").write_text("Hello, Mesocosm!\n")

    def harness_for(agent):
        oc_app = fake_opencode(gw_for_harness, agent.id, "e2e")
        return OpenCodeClient(agent.harness_url(), transport=httpx.ASGITransport(app=oc_app))

    services_log = tmp_path / "services.jsonl"
    stale = {"ts": 1.0, "service": "board", "op": "write", "agent": "agent-1", "post_id": 0}      # an earlier run's record
    fresh = {"ts": time.time(), "service": "board", "op": "write", "agent": "agent-1", "ip": "172.30.0.10",
             "method": "POST", "path": "/api/posts", "post_id": 1, "sha256": "abc", "size": 5, "summary": "hello"}
    services_log.write_text(json.dumps(stale) + "\n" + json.dumps(fresh) + "\n")

    def components(egress_ok=True):
        return Components(harness_for=harness_for, gateway=GatewayAdmin("http://gateway", transport=gw_transport),
                          egress=FakeEgress(egress_ok), verifier=FakeVerifier(),
                          workspace_for=lambda a: tmp_path / "ws" / a.id, services_log=services_log)

    return manifest, components, tmp_path


async def test_vertical_slice_produces_a_joinable_bundle(world):
    manifest, components, tmp_path = world
    result = await run_world(manifest, MANIFEST, components(), tmp_path / "runs")
    assert result.status == "ok" and result.rewards == {"agent-1/hello-world": 1.0}

    bundle = RunBundle.open(result.bundle_path)
    kinds = [e.kind for e in bundle.events()]
    assert kinds[:3] == ["gateway.health", "isolation.check", "conditions.set"]
    assert "task.loaded" in kinds and "harness.session" in kinds and "harness.reply" in kinds
    assert "verifier.reward" in kinds and "gateway.usage" in kinds
    # hidden channels: one receipt per supported channel, verified by the in-container check
    channel_receipts = [r for r in bundle.receipts() if r["op"] == "channels.set"]
    assert sorted(r["requested"]["channel"] for r in channel_receipts) == ["pastebin", "shared_dir"]
    assert all(r["result"] == "applied" and r["requested"]["enabled"] is False and r["verification"]["ok"]
               for r in channel_receipts)
    channel_events = [e for e in bundle.events() if e.kind == "conditions.set" and e.data["condition"].startswith("channel:")]
    assert len(channel_events) == 2 and all(e.data["enabled"] is False for e in channel_events)
    # the world services' log joins the bundle as native data and as events
    service_events = [e for e in bundle.events() if e.kind == "service.write"]
    assert service_events[0].agent_id == "agent-1" and service_events[0].data["post_id"] == 1
    assert (result.bundle_path / "native" / "services" / "services.jsonl").read_text().count("\n") == 1
    assert json.loads((result.bundle_path / "summary.json").read_text())["service_records"] == 1
    assert kinds.count("gateway.chat_completion") == 2, "every model call of the run is in the bundle"

    # receipts: the budget from the manifest was enforced at the gateway
    receipts = bundle.receipts()
    budget_receipt = next(r for r in receipts if r["requested"].get("condition") == "budget")
    assert budget_receipt["requested"] == {"condition": "budget", "agent_id": "agent-1",
                                        "value": {"completion_tokens": 40000, "requests": 200}}
    assert budget_receipt["result"] == "applied" and budget_receipt["enforced_at"]["component"] == "gateway"

    # join: gateway records carry request ids and the agent; the exposure copy has the full prompt
    chat = [e for e in bundle.events() if e.kind == "gateway.chat_completion"]
    assert all(e.agent_id == "agent-1" and e.request_id for e in chat)
    records_file = bundle.root / "native" / "gateway" / "records.jsonl"
    exposure = [json.loads(line) for line in records_file.read_text().splitlines()]
    assert {r["request_id"] for r in exposure} == {e.request_id for e in chat}
    assert exposure[-1]["request"]["messages"][0]["content"] == "x"

    # harness events were normalized with per-agent steps, native messages were kept verbatim
    tool_events = [e for e in bundle.events() if e.kind == "opencode.message.part.updated"]
    assert tool_events and tool_events[0].agent_id == "agent-1" and tool_events[0].step is not None
    native = json.loads((bundle.root / "native" / "agent-1" / "hello-world" / "messages.json").read_text())
    assert native[-1]["parts"][-1]["text"] == "done"

    # task provenance and adaptation marking
    loaded = next(e for e in bundle.events() if e.kind == "task.loaded")
    assert loaded.data["provenance"]["name"] == "hello-world" and loaded.data["adapted"] is False
    reward = next(e for e in bundle.events() if e.kind == "verifier.reward")
    assert reward.data["adapted"] is True and reward.data["reasons"] == ["budget_limited"]

    summary = json.loads((bundle.root / "summary.json").read_text())
    assert summary["status"] == "ok" and summary["model_calls"] == 2 and summary["run_label"] == "e2e"


async def test_unreachable_gateway_is_a_recorded_failure(world, tmp_path):
    manifest, components, _ = world
    comps = components()
    comps.gateway = GatewayAdmin("http://127.0.0.1:9")  # nothing listens here
    result = await run_world(manifest, MANIFEST, comps, tmp_path / "runs2")
    assert result.status == "failed" and "Connect" in (result.error or "")
    bundle = RunBundle.open(result.bundle_path)
    assert [e.kind for e in bundle.events()] == ["run.failed"]
    assert json.loads((bundle.root / "summary.json").read_text())["status"] == "failed"


async def test_failed_isolation_aborts_before_any_agent_runs(world):
    manifest, components, tmp_path = world
    result = await run_world(manifest, MANIFEST, components(egress_ok=False), tmp_path / "runs")
    assert result.status == "failed_isolation"
    bundle = RunBundle.open(result.bundle_path)
    kinds = [e.kind for e in bundle.events()]
    assert kinds == ["gateway.health", "isolation.check", "run.aborted"]
    assert not (bundle.root / "native" / "agent-1").exists()
    assert json.loads((bundle.root / "summary.json").read_text())["status"] == "failed_isolation"


async def test_unconfirmed_channel_aborts_the_run(world):
    manifest, components, tmp_path = world
    manifest.channels = [ChannelSpec(type="pastebin")]
    result = await run_world(manifest, MANIFEST, components(), tmp_path / "runs")
    assert result.status == "failed_channel_verification" and "pastebin" in result.error
    bundle = RunBundle.open(result.bundle_path)
    receipt = next(r for r in bundle.receipts() if r["op"] == "channels.set" and r["requested"]["channel"] == "pastebin")
    assert receipt["result"] == "failed" and receipt["requested"]["enabled"] is True
    assert json.loads((result.bundle_path / "summary.json").read_text())["status"] == "failed_channel_verification"
