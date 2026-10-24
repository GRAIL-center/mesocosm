"""A task sidecar's unknown IP never grants access to the control-plane service API."""

from __future__ import annotations

import httpx
import pytest
from test_services import client, log_lines, settings

from mesocosm.env.services.app import Settings, create_app
from mesocosm.env.services.client import ServicesAdmin


@pytest.mark.parametrize("ip,key", [("172.30.0.99", None), ("172.30.0.99", "wrong"),
                                    ("172.30.0.4", None), ("172.30.0.11", "test-control")])
async def test_every_admin_route_denies_unknown_callers_and_known_agents(tmp_path, ip, key):
    st = settings(tmp_path, repos=())
    st.chat = {"channels": ["general"]}
    st.ledger = {"unit": "credits", "initial": {"agent-1": 0}}
    app = create_app(st)
    before = st.started
    requests = [("GET", "/admin/state", None), ("GET", "/admin/state/world", None),
                ("GET", "/admin/verdicts", None), ("POST", "/admin/clock", {"started": 0}),
                ("POST", "/admin/epoch", {"epoch": "attacker"}),
                ("POST", "/admin/ledger", {"to": "agent-1", "amount": 999}),
                ("POST", "/admin/feedback", {"to": "agent-1", "text": "authored message"}),
                ("GET", "/admin/future-endpoint", None)]
    async with client(app, ip, "services", api_key=key) as untrusted:
        for method, path, body in requests:
            response = await untrusted.request(method, path, json=body,
                headers={"X-Forwarded-For": "172.30.0.4"})
            assert response.status_code == 404, (method, path, response.text)
        assert (await untrusted.get("/health")).json() == {"ok": True}
    assert st.started == before
    records = log_lines(st.log_path)
    assert len(records) == len(requests)
    assert all(record["op"] == "admin_denied" for record in records)
    assert "test-control" not in st.log_path.read_text()
    async with client(app, "172.30.0.4", "services", api_key="test-control") as control:
        state = (await control.get("/admin/state/world")).json()
    assert state["chat"]["epoch"] is None and state["chat"]["messages"] == []
    assert state["ledger"]["balances"] == {"agent-1": 0.0}


async def test_unconfigured_control_key_never_opens_admin(tmp_path, monkeypatch):
    monkeypatch.delenv("MESOCOSM_CONTROL_KEY", raising=False)
    st = settings(tmp_path, repos=())
    st.control_key = Settings().control_key
    assert st.control_key == ""
    app = create_app(st)
    async with client(app, "172.30.0.4", "services") as control:
        for header in ({}, {"Authorization": "Bearer "}, {"Authorization": "Bearer test-control"}):
            assert (await control.get("/admin/state", headers=header)).status_code == 404


async def test_admin_client_authenticates_every_supported_operation(tmp_path):
    st = settings(tmp_path, repos=())
    st.chat = {"channels": ["general"]}
    st.ledger = {"unit": "credits", "initial": {"agent-1": 0}}
    app = create_app(st)
    transport = httpx.ASGITransport(app=app, client=("172.30.0.4", 12345))
    admin = ServicesAdmin("http://services", "test-control", transport=transport)
    try:
        assert (await admin.state())["epoch"] is None
        assert await admin.set_clock(42) == {"started": 42}
        assert await admin.set_epoch("episode-1") == {"epoch": "episode-1"}
        assert (await admin.post_feedback("agent-1", "episode-1", "actual fixture feedback"))["text"]
        await admin.credit("agent-1", 2)
        assert await admin.verdicts("episode-1") == []
        state = await admin.world_state("agent-1")
        assert state["viewer"]["agent_id"] == "agent-1"
        assert state["ledger"]["balances"]["agent-1"] == 2
    finally:
        await admin.aclose()
    with pytest.raises(ValueError, match="nonempty control key"):
        ServicesAdmin("http://services", "")
