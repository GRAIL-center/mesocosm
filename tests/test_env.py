import pathlib

import yaml

from mesocosm.core.manifest import load_manifest
from mesocosm.env.compose import compose_yaml, render_compose
from mesocosm.env.egress import parse_egress_output

ROOT = pathlib.Path(__file__).resolve().parents[1]


def test_compose_topology_invariants():
    manifest = load_manifest(ROOT / "examples" / "spike-world.yaml")
    manifest.agents.append(manifest.agents[0].model_copy(update={"id": "agent-2"}))
    compose = render_compose(manifest, repo_root=ROOT, run_label="lbl")
    assert compose["networks"] == {"sandbox": {"internal": True}, "edge": {}}
    svc = compose["services"]
    assert svc["gateway"]["networks"] == ["sandbox", "edge"]
    assert svc["control"]["networks"] == ["sandbox", "edge"]
    for agent_id in ("agent-1", "agent-2"):
        a = svc[agent_id]
        assert a["networks"] == ["sandbox"]
        assert "ports" not in a and "network_mode" not in a and "extra_hosts" not in a
        assert a["environment"]["MESOCOSM_RUN"] == "lbl"
        assert a["environment"]["MESOCOSM_GATEWAY_URL"] == "http://gateway:8080"
        assert a["depends_on"] == {"gateway": {"condition": "service_healthy"}}
    only_gateway_bridges = [n for n, s in svc.items() if set(s["networks"]) == {"sandbox", "edge"}]
    assert sorted(only_gateway_bridges) == ["control", "gateway"]
    assert yaml.safe_load(compose_yaml(manifest, repo_root=ROOT, run_label="lbl")) == compose


def test_isolate_agents_gives_each_agent_a_private_network():
    manifest = load_manifest(ROOT / "examples" / "spike-world.yaml")
    manifest.agents.append(manifest.agents[0].model_copy(update={"id": "agent-2"}))
    manifest.isolate_agents = True
    compose = render_compose(manifest, repo_root=ROOT, run_label="lbl")
    assert compose["networks"] == {"sandbox-agent-1": {"internal": True}, "sandbox-agent-2": {"internal": True},
                                   "edge": {}}
    svc = compose["services"]
    assert svc["gateway"]["networks"] == ["sandbox-agent-1", "sandbox-agent-2", "edge"]
    assert svc["control"]["networks"] == ["sandbox-agent-1", "sandbox-agent-2", "edge"]
    assert svc["agent-1"]["networks"] == ["sandbox-agent-1"]
    assert svc["agent-2"]["networks"] == ["sandbox-agent-2"]
    assert svc["agent-1"]["environment"]["MESOCOSM_PEERS"] == "agent-2"
    assert svc["agent-1"]["environment"]["MESOCOSM_ISOLATE_AGENTS"] == "1"
    shared = render_compose(manifest.model_copy(update={"isolate_agents": False}), repo_root=ROOT, run_label="lbl")
    assert shared["services"]["agent-2"]["environment"]["MESOCOSM_PEERS"] == "agent-1"
    assert shared["services"]["agent-2"]["environment"]["MESOCOSM_ISOLATE_AGENTS"] == "0"


def test_egress_parser_blocks_on_fail_and_records_warn():
    ok = parse_egress_output(
        "OK    gateway reachable at http://gateway:8080\nOK    HTTPS egress blocked\nOK    raw-IP egress blocked\n"
        "OK    ICMP egress blocked (or ping unavailable)\nWARN  external DNS name resolved\n"
        "OK    inference server not directly reachable\nPASS  isolation check (with warnings)\n", 0)
    assert ok.ok and ok.warnings == ["external DNS name resolved"] and ok.failures == []
    bad = parse_egress_output("OK    gateway reachable\nFAIL  HTTPS egress to example.com succeeded\n", 1)
    assert not bad.ok and bad.failures == ["HTTPS egress to example.com succeeded"]
    empty = parse_egress_output("", 0)
    assert not empty.ok, "no OK lines at all is not a pass"
    assert parse_egress_output("OK fine\n", 0).to_dict()["lines"] == [["OK", "fine"]]
