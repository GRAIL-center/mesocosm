import pathlib

import yaml

from mesocosm.core.manifest import ChannelSpec, load_manifest
from mesocosm.env.compose import compose_yaml, render_compose
from mesocosm.env.egress import parse_egress_output

ROOT = pathlib.Path(__file__).resolve().parents[1]


def test_compose_topology_invariants():
    manifest = load_manifest(ROOT / "examples" / "spike-world.yaml")
    manifest.agents.append(manifest.agents[0].model_copy(update={"id": "agent-2"}))
    compose = render_compose(manifest, repo_root=ROOT, run_label="lbl")
    assert compose["networks"] == {"sandbox": {"internal": True, "ipam": {"config": [{"subnet": "172.30.0.0/24"}]}}, "edge": {}}
    svc = compose["services"]
    assert set(svc["gateway"]["networks"]) == {"sandbox", "edge"} and svc["gateway"]["networks"]["sandbox"] == {"ipv4_address": "172.30.0.2"}
    assert set(svc["control"]["networks"]) == {"sandbox", "edge"}
    for i, agent_id in enumerate(("agent-1", "agent-2")):
        a = svc[agent_id]
        assert a["networks"] == {"sandbox": {"ipv4_address": f"172.30.0.{10 + i}"}}
        assert "ports" not in a and "network_mode" not in a and "extra_hosts" not in a
        assert a["healthcheck"]["test"][-1] == "http://127.0.0.1:4096/global/health"
        assert a["environment"]["MESOCOSM_RUN"] == "lbl"
        assert a["environment"]["MESOCOSM_GATEWAY_URL"] == "http://gateway:8080"
        assert a["environment"]["MESOCOSM_SERVICES"] == "board,wiki,git,pypi,sites" and a["environment"]["MESOCOSM_CHANNELS"] == ""
        assert a["depends_on"] == {"gateway": {"condition": "service_healthy"}, "services": {"condition": "service_healthy"}}
    on_edge = [n for n, s in svc.items() if "edge" in s["networks"]]
    assert sorted(on_edge) == ["control", "gateway"]
    world = svc["services"]
    assert world["networks"] == {"sandbox": {"ipv4_address": "172.30.0.3", "aliases": ["services", "board", "wiki", "git", "pypi", "sites"]}}
    assert world["environment"]["MESOCOSM_AGENT_IPS"] == '{"172.30.0.10": "agent-1", "172.30.0.11": "agent-2"}'
    assert world["environment"]["MESOCOSM_CHANNELS"] == "" and world["environment"]["MESOCOSM_GIT_REPOS"] == "project"
    assert world["build"]["args"]["PYPI_PACKAGES"] == "requests pytest rich pyyaml"
    assert "volumes" not in compose
    assert yaml.safe_load(compose_yaml(manifest, repo_root=ROOT, run_label="lbl")) == compose
    assert "&id" not in compose_yaml(manifest, repo_root=ROOT, run_label="lbl")


def test_isolate_agents_gives_each_agent_a_private_network():
    manifest = load_manifest(ROOT / "examples" / "spike-world.yaml")
    manifest.agents.append(manifest.agents[0].model_copy(update={"id": "agent-2"}))
    manifest.isolate_agents = True
    compose = render_compose(manifest, repo_root=ROOT, run_label="lbl")
    assert set(compose["networks"]) == {"sandbox-agent-1", "sandbox-agent-2", "edge"}
    assert compose["networks"]["sandbox-agent-2"] == {"internal": True, "ipam": {"config": [{"subnet": "172.30.2.0/24"}]}}
    svc = compose["services"]
    assert set(svc["gateway"]["networks"]) == {"sandbox-agent-1", "sandbox-agent-2", "edge"}
    assert set(svc["services"]["networks"]) == {"sandbox-agent-1", "sandbox-agent-2"}   # the world spans the agents
    assert svc["agent-1"]["networks"] == {"sandbox-agent-1": {"ipv4_address": "172.30.1.10"}}
    assert svc["agent-2"]["networks"] == {"sandbox-agent-2": {"ipv4_address": "172.30.2.11"}}
    assert svc["agent-1"]["environment"]["MESOCOSM_PEERS"] == "agent-2"
    assert svc["agent-1"]["environment"]["MESOCOSM_ISOLATE_AGENTS"] == "1"
    assert svc["services"]["environment"]["MESOCOSM_AGENT_IPS"] == '{"172.30.1.10": "agent-1", "172.30.2.11": "agent-2"}'


def test_channels_add_the_pastebin_alias_and_the_shared_volume():
    manifest = load_manifest(ROOT / "examples" / "spike-world.yaml")
    manifest.channels = [ChannelSpec(type="pastebin"), ChannelSpec(type="shared_dir")]
    compose = render_compose(manifest, repo_root=ROOT, run_label="lbl")
    svc = compose["services"]
    assert svc["services"]["networks"]["sandbox"]["aliases"][-1] == "pastebin"
    assert svc["services"]["environment"]["MESOCOSM_CHANNELS"] == "pastebin"
    assert svc["agent-1"]["environment"]["MESOCOSM_CHANNELS"] == "pastebin,shared_dir"
    assert "shared:/shared" in svc["agent-1"]["volumes"] and "shared:/shared:ro" in svc["control"]["volumes"]
    assert compose["volumes"] == {"shared": {}}
    off = render_compose(manifest.model_copy(update={"services": manifest.services.model_copy(update={"enabled": False}),
                                                     "channels": []}), repo_root=ROOT, run_label="lbl")
    assert "services" not in off["services"] and off["services"]["agent-1"]["environment"]["MESOCOSM_SERVICES"] == ""
    assert off["services"]["agent-1"]["depends_on"] == {"gateway": {"condition": "service_healthy"}}


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


def test_exposure_schedule_and_accounts_reach_the_services_container():
    from mesocosm.core.manifest import AccountSpec
    from mesocosm.env.compose import service_address
    manifest = load_manifest(ROOT / "examples" / "spike-world.yaml")
    manifest.conditions["exposure"] = {"wiki": {"secret": 30}}
    manifest.services.accounts = [AccountSpec(user="deploy", password="pw"),
                                  AccountSpec(user="root-backup", password="h", trap=True, note="wiki/old-ops")]
    env = render_compose(manifest, repo_root=ROOT, run_label="lbl")["services"]["services"]["environment"]
    assert env["MESOCOSM_WIKI_VISIBILITY"] == '{"secret": 30}'
    accounts = yaml.safe_load(env["MESOCOSM_ACCOUNTS"])
    assert [a["user"] for a in accounts] == ["deploy", "root-backup"] and accounts[1]["trap"] is True
    plain = render_compose(load_manifest(ROOT / "examples" / "spike-world.yaml"), repo_root=ROOT, run_label="lbl")
    assert plain["services"]["services"]["environment"]["MESOCOSM_WIKI_VISIBILITY"] == "{}"
    assert service_address(manifest, "services") == "172.30.0.3" and service_address(manifest, "gateway") == "172.30.0.2"
    manifest.isolate_agents = True
    assert service_address(manifest, "services") == "172.30.1.3" and service_address(manifest, "control") == "172.30.1.4"


def test_peer_review_adds_the_chat_hostname_and_its_limits():
    from mesocosm.core.manifest import PeerReviewSpec
    manifest = load_manifest(ROOT / "examples" / "spike-world.yaml")
    manifest.agents.append(manifest.agents[0].model_copy(update={"id": "agent-2"}))
    manifest.services.board = False
    manifest.services.peer_review = PeerReviewSpec(enabled=True, max_chars=200, max_messages=5)
    svc = render_compose(manifest, repo_root=ROOT, run_label="lbl")["services"]
    assert svc["services"]["networks"]["sandbox"]["aliases"] == ["services", "wiki", "git", "pypi", "sites", "chat"]
    pr = yaml.safe_load(svc["services"]["environment"]["MESOCOSM_PEER_REVIEW"])
    assert pr["max_chars"] == 200 and pr["max_messages"] == 5 and pr["reward_per_verdict"] == 10
    assert svc["agent-1"]["environment"]["MESOCOSM_SERVICES"] == "wiki,git,pypi,sites,chat"
    plain = render_compose(load_manifest(ROOT / "examples" / "spike-world.yaml"), repo_root=ROOT, run_label="lbl")
    assert plain["services"]["services"]["environment"]["MESOCOSM_PEER_REVIEW"] == "null"
