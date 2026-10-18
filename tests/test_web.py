"""The web gateway's policy (ADR-0022): decisions per host, budgets, the refusal page, the CA the agents
trust and the records the proxy leaves. The proxy process itself (mitmproxy) is exercised in Docker."""

import json
import subprocess

from mesocosm.env.web import (
    Usage,
    WebPolicy,
    block_page,
    generate_ca,
    network_denied,
    policy_state,
    probe_hosts,
    read_policy_state,
    web_record,
)


def test_private_sandbox_and_local_names_are_denied_whatever_the_allow_list_says():
    policy = WebPolicy(allow=["*"], domain="corp.example")
    for host in ("10.0.2.2", "172.30.0.2", "192.168.1.1", "127.0.0.1", "169.254.169.254", "[::1]", "0.0.0.0",
                 "gateway", "board", "proxy", "control", "host.docker.internal", "localhost", "vllm",
                 "printer.local", "db.internal", "wiki.corp.example", "corp.example"):
        decision, _ = policy.decide(host, 443)
        assert decision == "denied:network", host
    assert network_denied("pypi.org") is None and network_denied("8.8.8.8") is None
    assert policy.decide("pypi.org", 443) == ("allowed", "*")
    assert policy.decide("pypi.org", 8443) == ("denied:port", "8443")


def test_allow_and_deny_are_host_globs_deny_first():
    policy = WebPolicy(allow=["pypi.org", "*.pythonhosted.org", "*.wikipedia.org"], deny=["*.m.wikipedia.org"])
    assert policy.decide("pypi.org", 443) == ("allowed", "pypi.org")
    assert policy.decide("PyPI.org.", 80) == ("allowed", "pypi.org"), "case and trailing dot do not matter"
    assert policy.decide("files.pythonhosted.org", 443) == ("allowed", "*.pythonhosted.org")
    assert policy.decide("en.wikipedia.org", 443) == ("allowed", "*.wikipedia.org")
    assert policy.decide("en.m.wikipedia.org", 443) == ("denied:deny", "*.m.wikipedia.org")
    assert policy.decide("example.com", 443) == ("denied:not_allowed", None)
    assert policy.decide("pythonhosted.org", 443) == ("denied:not_allowed", None), "a glob needs the dot"
    rt = WebPolicy.from_dict(policy.to_dict())
    assert rt == policy and rt.to_dict()["budget"] == {"requests": None, "bytes": None}


def test_budgets_are_per_agent_and_name_the_dimension():
    policy = WebPolicy(allow=["*"], budget_requests=2, budget_bytes=100)
    usage = Usage()
    assert usage.exhausted(policy, "agent-1") is None
    usage.add("agent-1", 60)
    usage.add("agent-1", 30)
    assert usage.exhausted(policy, "agent-1") == "requests" and usage.exhausted(policy, "agent-2") is None
    usage = Usage()
    usage.add("agent-2", 100)
    assert usage.exhausted(policy, "agent-2") == "bytes"


def test_the_refusal_page_names_only_the_host_and_a_reference():
    page = block_page("evil.example", "policy")
    assert "evil.example" in page and "WF-403" in page and "mesocosm" not in page.lower()
    assert "WF-429" in block_page("x.example", "budget") and "WF-451" in block_page("10.0.0.1", "network")
    assert "&lt;script&gt;" in block_page("<script>", "policy")


def test_records_carry_hash_and_a_text_excerpt_only():
    rec = web_record(agent="agent-1", ip="172.30.0.10", method="GET", scheme="https", host="pypi.org", port=443,
                     path="/pypi/requests/json", decision="allowed", rule="pypi.org", status=200, response_bytes=12,
                     content_type="application/json; charset=utf-8", body=b'{"a": 1}' + b"x" * 5000, duration_ms=120)
    assert rec["sha256"] and len(rec["excerpt"]) == 2000 and rec["excerpt"].startswith('{"a": 1}')
    binary = web_record(agent="agent-1", ip="x", method="GET", scheme="https", host="h", port=443, path="/f.whl",
                        decision="allowed", rule="*", status=200, content_type="application/octet-stream",
                        body=b"\x00\x01")
    assert "excerpt" not in binary and binary["sha256"]
    connect = web_record(agent="agent-1", ip="x", method="CONNECT", scheme="https", host="evil.example", port=443,
                         path="", decision="denied:not_allowed", rule=None, status=403, connect=True)
    assert connect["connect"] is True and "sha256" not in connect


def test_ca_is_generated_once_and_split_for_proxy_and_agents(tmp_path):
    cert = generate_ca(tmp_path / "ca", "Acme Root CA")
    store = tmp_path / "ca" / "mitmproxy-ca.pem"
    assert cert.read_text().count("BEGIN CERTIFICATE") == 1 and "PRIVATE KEY" not in cert.read_text()
    assert "PRIVATE KEY" in store.read_text() and store.read_text().endswith(cert.read_text())
    assert not (tmp_path / "ca" / "ca.key").exists(), "the key lives only in the proxy's store"
    subject = subprocess.run(["openssl", "x509", "-in", str(cert), "-noout", "-subject", "-ext", "basicConstraints"],
                             capture_output=True, text=True, check=True).stdout
    assert "Acme Root CA" in subject and "CA:TRUE" in subject
    before = cert.read_bytes()
    assert generate_ca(tmp_path / "ca", "Other").read_bytes() == before, "an existing CA is kept"


def test_policy_state_round_trips_and_probe_hosts_pick_concrete_names(tmp_path):
    policy = WebPolicy(allow=["*.wikipedia.org", "pypi.org"], deny=["example.com"], mode="record")
    (tmp_path / "policy.json").write_text(json.dumps(policy_state(policy, started=5.0, mode="regular")))
    state = read_policy_state(tmp_path / "policy.json")
    assert state["policy"] == policy.to_dict() and state["started"] == 5.0 and state["mode"] == "regular"
    assert read_policy_state(tmp_path / "missing.json") is None
    assert probe_hosts(policy) == ("pypi.org", "example.com")
    assert probe_hosts(WebPolicy(allow=["*.example.org"])) == (None, "example.com")
    assert probe_hosts(WebPolicy(allow=["example.com"], deny=["bad.example"])) == ("example.com", "bad.example")
    assert probe_hosts(WebPolicy(allow=["example.com"])) == ("example.com", "example.org")
