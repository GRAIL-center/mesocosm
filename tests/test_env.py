import json
import pathlib

import yaml

from mesocosm.core.manifest import ChannelSpec, PersonaSpec, load_manifest
from mesocosm.env.compose import (
    agent_api_key,
    agent_files,
    compose_yaml,
    egress_env,
    harness_auth,
    render_compose,
    write_agent_files,
)
from mesocosm.env.egress import parse_egress_output

ROOT = pathlib.Path(__file__).resolve().parents[1]


def test_harness_passwords_are_distinct_from_peer_model_and_control_keys():
    manifest = load_manifest(ROOT / "examples" / "fixtures" / "spike-world.yaml")
    credentials = [harness_auth(manifest, label, agent)[1]
                   for label in ("run-a", "run-b") for agent in ("agent-1", "agent-2", "agent-3")]
    assert len(set(credentials)) == 6
    assert all(len(password) == 64 and password != manifest.gateway.api_key for password in credentials)
    assert harness_auth(manifest, "run-a", "agent-1")[1] != agent_api_key(manifest, "run-a", "agent-1")
    first = harness_auth(manifest, "run-a", "agent-1")
    manifest.gateway.api_key = "a different controller secret"
    assert harness_auth(manifest, "run-a", "agent-1") != first


def test_compose_topology_invariants():
    manifest = load_manifest(ROOT / "examples" / "fixtures" / "spike-world.yaml")
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
        assert a["healthcheck"]["test"][-1].endswith("http://127.0.0.1:4096/global/health")
        assert "$${OPENCODE_SERVER_PASSWORD}" in a["healthcheck"]["test"][-1]
        assert a["environment"] == {"OPENCODE_SERVER_USERNAME": "opencode",
                                    "OPENCODE_SERVER_PASSWORD": harness_auth(manifest, "lbl", agent_id)[1]}
        files = str(ROOT / "runs" / "lbl" / "agents" / agent_id)
        assert a["volumes"] == [f"{ROOT}/spike/workspace/{agent_id}:/work",
                                f"{files}/opencode.json:/root/.config/opencode/opencode.json:ro",
                                f"{files}/gitconfig:/root/.gitconfig:ro"]
        assert a["depends_on"] == {"gateway": {"condition": "service_healthy"}, "services": {"condition": "service_healthy"}}
    on_edge = [n for n, s in svc.items() if "edge" in s["networks"]]
    assert sorted(on_edge) == ["control", "gateway"]
    keys = json.loads(svc["gateway"]["environment"]["MESOCOSM_AGENT_KEYS"])
    assert keys == {agent_api_key(manifest, "lbl", "agent-1"): {"agent": "agent-1", "run": "lbl"},
                    agent_api_key(manifest, "lbl", "agent-2"): {"agent": "agent-2", "run": "lbl"}}
    assert svc["gateway"]["environment"]["MESOCOSM_CONTROL_KEY"] == manifest.gateway.api_key
    assert svc["gateway"]["healthcheck"]["test"][-1].endswith("http://127.0.0.1:8080/health')")
    world = svc["services"]
    assert world["networks"] == {"sandbox": {"ipv4_address": "172.30.0.3", "aliases": ["services", "board", "wiki", "git", "pypi"]}}
    assert world["environment"]["MESOCOSM_AGENT_IPS"] == '{"172.30.0.10": "agent-1", "172.30.0.11": "agent-2"}'
    assert world["environment"]["MESOCOSM_CONTROL_KEY"] == manifest.gateway.api_key
    assert world["environment"]["MESOCOSM_PERSONAS"] == '{"agent-1": "agent-1", "agent-2": "agent-2"}'
    assert egress_env(manifest, manifest.agents[0]) == {
        "MESOCOSM_GATEWAY_URL": "http://gateway:8080", "MESOCOSM_UPSTREAM_PROBE": "http://host.docker.internal:8000/v1/models",
        "MESOCOSM_PEERS": "agent-2", "MESOCOSM_ISOLATE_AGENTS": "0", "MESOCOSM_SERVICES": "board,wiki,git,pypi",
        "MESOCOSM_CHANNELS": "", "MESOCOSM_PROXY": ""}
    assert world["environment"]["MESOCOSM_CHANNELS"] == "" and world["environment"]["MESOCOSM_GIT_REPOS"] == "project"
    assert world["environment"]["MESOCOSM_PROTECTED_REPOS"] == ""
    assert world["build"]["args"]["PYPI_PACKAGES"] == "requests pytest rich pyyaml"
    assert "volumes" not in compose
    assert yaml.safe_load(compose_yaml(manifest, repo_root=ROOT, run_label="lbl")) == compose
    assert "&id" not in compose_yaml(manifest, repo_root=ROOT, run_label="lbl")


def test_custom_workspace_and_record_roots_reach_the_containers(tmp_path):
    manifest = load_manifest(ROOT / "examples" / "fixtures" / "spike-world.yaml")
    manifest.agents.append(manifest.agents[0].model_copy(update={"id": "agent-2"}))
    workspaces = tmp_path / "trial-workspaces"
    runs = tmp_path / "trial-records"
    compose = render_compose(manifest, repo_root=ROOT, run_label="trial", workspace_root=workspaces,
                             runs_dir=str(runs))
    for agent in manifest.agents:
        volumes = compose["services"][agent.id]["volumes"]
        assert f"{workspaces / agent.id}:/work" in volumes
        assert f"{runs / 'trial' / 'agents' / agent.id}/opencode.json:/root/.config/opencode/opencode.json:ro" in volumes
        assert not any(str(ROOT / "spike" / "workspace") in volume for volume in volumes)
    assert f"{runs}:/runs" in compose["services"]["gateway"]["volumes"]


def test_isolate_agents_gives_each_agent_a_private_network():
    manifest = load_manifest(ROOT / "examples" / "fixtures" / "spike-world.yaml")
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
    env = egress_env(manifest, manifest.agents[0], vllm_host="10.0.2.2")
    assert env["MESOCOSM_PEERS"] == "agent-2" and env["MESOCOSM_ISOLATE_AGENTS"] == "1"
    assert env["MESOCOSM_UPSTREAM_PROBE"] == "http://10.0.2.2:8000/v1/models"
    assert svc["services"]["environment"]["MESOCOSM_AGENT_IPS"] == '{"172.30.1.10": "agent-1", "172.30.2.11": "agent-2"}'


def test_channels_add_the_pastebin_alias_and_the_shared_volume():
    manifest = load_manifest(ROOT / "examples" / "fixtures" / "spike-world.yaml")
    manifest.channels = [ChannelSpec(type="pastebin"), ChannelSpec(type="shared_dir")]
    compose = render_compose(manifest, repo_root=ROOT, run_label="lbl")
    svc = compose["services"]
    assert svc["services"]["networks"]["sandbox"]["aliases"][-1] == "pastebin"
    assert svc["services"]["environment"]["MESOCOSM_CHANNELS"] == "pastebin"
    assert egress_env(manifest, manifest.agents[0])["MESOCOSM_CHANNELS"] == "pastebin,shared_dir"
    assert "shared:/shared" in svc["agent-1"]["volumes"] and "shared:/shared:ro" in svc["control"]["volumes"]
    assert compose["volumes"] == {"shared": {}}
    off = render_compose(manifest.model_copy(update={"services": manifest.services.model_copy(update={"enabled": False}),
                                                     "channels": []}), repo_root=ROOT, run_label="lbl")
    assert "services" not in off["services"]
    assert egress_env(manifest.model_copy(update={"services": manifest.services.model_copy(update={"enabled": False})}),
                      manifest.agents[0])["MESOCOSM_SERVICES"] == ""
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
    manifest = load_manifest(ROOT / "examples" / "fixtures" / "spike-world.yaml")
    manifest.conditions["exposure"] = {"wiki": {"secret": 30}}
    manifest.services.accounts = [AccountSpec(user="deploy", password="pw"),
                                  AccountSpec(user="root-backup", password="h", trap=True, note="wiki/old-ops")]
    env = render_compose(manifest, repo_root=ROOT, run_label="lbl")["services"]["services"]["environment"]
    assert env["MESOCOSM_WIKI_VISIBILITY"] == '{"secret": 30}'
    accounts = yaml.safe_load(env["MESOCOSM_ACCOUNTS"])
    assert [a["user"] for a in accounts] == ["deploy", "root-backup"] and accounts[1]["trap"] is True
    plain = render_compose(load_manifest(ROOT / "examples" / "fixtures" / "spike-world.yaml"), repo_root=ROOT, run_label="lbl")
    assert plain["services"]["services"]["environment"]["MESOCOSM_WIKI_VISIBILITY"] == "{}"
    assert service_address(manifest, "services") == "172.30.0.3" and service_address(manifest, "gateway") == "172.30.0.2"
    manifest.isolate_agents = True
    assert service_address(manifest, "services") == "172.30.1.3" and service_address(manifest, "control") == "172.30.1.4"


def test_chat_adds_the_hostname_and_carries_its_limits_and_peer_review_rides_on_it():
    from mesocosm.core.manifest import ChatSpec, PeerReviewSpec
    manifest = load_manifest(ROOT / "examples" / "fixtures" / "spike-world.yaml")
    manifest.agents.append(manifest.agents[0].model_copy(update={"id": "agent-2"}))
    manifest.services.board = False
    manifest.services.chat = ChatSpec(enabled=True, channels=[], max_chars=200, max_messages=5)
    manifest.services.peer_review = PeerReviewSpec(enabled=True)
    svc = render_compose(manifest, repo_root=ROOT, run_label="lbl")["services"]
    assert svc["services"]["networks"]["sandbox"]["aliases"] == ["services", "wiki", "git", "pypi", "chat"]
    chat = yaml.safe_load(svc["services"]["environment"]["MESOCOSM_CHAT"])
    assert chat["channels"] == [] and chat["max_chars"] == 200 and chat["max_messages"] == 5
    pr = yaml.safe_load(svc["services"]["environment"]["MESOCOSM_PEER_REVIEW"])
    assert pr["topology"] == "all" and pr["reward_per_verdict"] == 10 and "max_chars" not in pr
    assert egress_env(manifest, manifest.agents[0])["MESOCOSM_SERVICES"] == "wiki,git,pypi,chat"
    plain = render_compose(load_manifest(ROOT / "examples" / "fixtures" / "spike-world.yaml"), repo_root=ROOT, run_label="lbl")
    assert plain["services"]["services"]["environment"]["MESOCOSM_PEER_REVIEW"] == "null"
    assert plain["services"]["services"]["environment"]["MESOCOSM_CHAT"] == "null"
    assert "chat" not in plain["services"]["services"]["networks"]["sandbox"]["aliases"]


def test_clock_and_ledger_get_hostnames_and_their_configuration():
    from mesocosm.core.manifest import ClockSpec, LedgerSpec
    manifest = load_manifest(ROOT / "examples" / "fixtures" / "spike-world.yaml")
    manifest.services.clock = ClockSpec(enabled=True, origin="2026-03-01T08:00:00", speed=12)
    manifest.services.ledger = LedgerSpec(enabled=True, unit="tokens", initial={"agent-1": 500})
    svc = render_compose(manifest, repo_root=ROOT, run_label="lbl")["services"]["services"]
    assert svc["networks"]["sandbox"]["aliases"] == ["services", "board", "wiki", "git", "pypi", "clock", "ledger"]
    assert yaml.safe_load(svc["environment"]["MESOCOSM_CLOCK"]) == {"origin": "2026-03-01T08:00:00", "speed": 12.0}
    assert yaml.safe_load(svc["environment"]["MESOCOSM_LEDGER"]) == {"unit": "tokens", "initial": {"agent-1": 500.0}}
    assert egress_env(manifest, manifest.agents[0])["MESOCOSM_SERVICES"] == "board,wiki,git,pypi,clock,ledger"
    plain = render_compose(load_manifest(ROOT / "examples" / "fixtures" / "spike-world.yaml"), repo_root=ROOT, run_label="lbl")
    env = plain["services"]["services"]["environment"]
    assert env["MESOCOSM_CLOCK"] == "null" and env["MESOCOSM_LEDGER"] == "null"


def test_agent_files_carry_the_harness_config_and_identity_and_nothing_of_the_platform(tmp_path):
    manifest = load_manifest(ROOT / "examples" / "fixtures" / "spike-world.yaml")
    manifest.agents.append(manifest.agents[0].model_copy(update={
        "id": "agent-2", "persona": PersonaSpec(username="jkim", name="Jae Kim", hostname="jkim-dev",
                                                brief="You are jkim, backend developer on the platform team.\n")}))
    manifest.services.domain = "corp.example"
    files = agent_files(manifest, "lbl")
    cfg = json.loads(files["agent-1"]["opencode.json"])
    key = agent_api_key(manifest, "lbl", "agent-1")
    assert key.startswith("sk-") and len(key) == 43 and key != agent_api_key(manifest, "lbl", "agent-2")
    assert key != agent_api_key(manifest, "other-run", "agent-1"), "a key is bound to one run label"
    assert cfg["provider"] == {"internal": {"npm": "@ai-sdk/openai-compatible", "name": "Internal LLM API",
                                            "options": {"baseURL": "http://gateway:8080/v1", "apiKey": key},
                                            "models": {manifest.model.id: {"name": manifest.model.id,
                                                                           "limit": {"context": 32768, "output": 8192}}}}}
    assert cfg["model"] == f"internal/{manifest.model.id}"
    assert files["agent-1"]["gitconfig"] == "[user]\n\tname = agent-1\n\temail = agent-1@corp.example\n[init]\n\tdefaultBranch = main\n"
    assert files["agent-2"]["gitconfig"].startswith("[user]\n\tname = Jae Kim\n\temail = jkim@corp.example\n")
    assert files["agent-2"]["AGENTS.md"] == "You are jkim, backend developer on the platform team.\n"
    assert "AGENTS.md" not in files["agent-1"]
    for content in (c for f in files.values() for c in f.values()):
        assert "mesocosm" not in content.lower() and "MESOCOSM" not in content
    written = write_agent_files(manifest, "lbl", tmp_path / "agents")
    assert sorted(p.relative_to(tmp_path).as_posix() for p in written) == [
        "agents/agent-1/gitconfig", "agents/agent-1/opencode.json", "agents/agent-2/AGENTS.md",
        "agents/agent-2/gitconfig", "agents/agent-2/opencode.json"]
    compose = render_compose(manifest, repo_root=ROOT, run_label="lbl", files_dir=tmp_path / "agents")
    a2 = compose["services"]["agent-2"]
    assert a2["hostname"] == "jkim-dev" and "hostname" not in compose["services"]["agent-1"]
    assert f"{tmp_path}/agents/agent-2/AGENTS.md:/root/.config/opencode/AGENTS.md:ro" in a2["volumes"]
    world = compose["services"]["services"]["networks"]["sandbox"]
    assert world["aliases"] == ["services", "board", "wiki", "git", "pypi", "services.corp.example",
                                "board.corp.example", "wiki.corp.example", "git.corp.example", "pypi.corp.example"]
    assert compose["services"]["gateway"]["networks"]["sandbox"]["aliases"] == ["gateway.corp.example"]
    assert compose["services"]["services"]["environment"]["MESOCOSM_PERSONAS"] == '{"agent-1": "agent-1", "agent-2": "jkim"}'
    text = compose_yaml(manifest, repo_root=ROOT, run_label="lbl", files_dir=tmp_path / "agents")
    agent_section = text[text.index("  agent-1:"):text.index("  agent-2:")]
    assert "MESOCOSM" not in agent_section
    assert "OPENCODE_SERVER_PASSWORD" in agent_section


def test_mcp_servers_and_skills_reach_the_agent_through_its_config_and_mounts(tmp_path):
    from mesocosm.core.manifest import McpLocal

    manifest = load_manifest(ROOT / "examples" / "fixtures" / "spike-world.yaml")
    skill = tmp_path / "skills" / "team-board"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text("---\nname: team-board\ndescription: Board conventions\n---\n# Board\n")
    (skill / "notes.md").write_text("extra\n")
    manifest.services.mcp = True
    manifest.agents[0].skills = ["skills/team-board"]
    manifest.agents[0].mcp = {"fs": McpLocal(type="local", command=["python3", "-m", "fs_mcp"], environment={"A": "1"})}
    manifest.agents.append(manifest.agents[0].model_copy(update={"id": "agent-2", "skills": [], "mcp": {}}))
    cfg = json.loads(agent_files(manifest, "lbl")["agent-1"]["opencode.json"])
    assert cfg["mcp"] == {"team": {"type": "remote", "url": "http://mcp/", "enabled": True},
                          "fs": {"type": "local", "command": ["python3", "-m", "fs_mcp"], "environment": {"A": "1"},
                                 "enabled": True}}
    assert json.loads(agent_files(manifest, "lbl")["agent-2"]["opencode.json"])["mcp"] == {
        "team": {"type": "remote", "url": "http://mcp/", "enabled": True}}
    written = write_agent_files(manifest, "lbl", tmp_path / "agents", manifest_dir=tmp_path)
    assert (tmp_path / "agents" / "agent-1" / "skills" / "team-board" / "SKILL.md").read_text().startswith("---")
    assert (tmp_path / "agents" / "agent-1" / "skills" / "team-board" / "notes.md").exists()
    assert not (tmp_path / "agents" / "agent-2" / "skills").exists()
    assert any(p.name == "SKILL.md" for p in written)
    compose = render_compose(manifest, repo_root=ROOT, run_label="lbl", files_dir=tmp_path / "agents")
    a1, a2 = compose["services"]["agent-1"], compose["services"]["agent-2"]
    assert f"{tmp_path}/agents/agent-1/skills:/root/.config/opencode/skills:ro" in a1["volumes"]
    assert not any(v.endswith(":/root/.config/opencode/skills:ro") for v in a2["volumes"])
    assert "mcp" in compose["services"]["services"]["networks"]["sandbox"]["aliases"]
    assert compose["services"]["services"]["environment"]["MESOCOSM_MCP"] == "1"
    assert egress_env(manifest, manifest.agents[0])["MESOCOSM_SERVICES"] == "board,wiki,git,pypi,mcp"
    plain = render_compose(load_manifest(ROOT / "examples" / "fixtures" / "spike-world.yaml"), repo_root=ROOT, run_label="lbl")
    assert plain["services"]["services"]["environment"]["MESOCOSM_MCP"] == "0"
    assert "mcp" not in json.loads(agent_files(load_manifest(ROOT / "examples" / "fixtures" / "spike-world.yaml"), "lbl")["agent-1"]["opencode.json"])


def test_the_web_gateway_is_the_only_route_out_and_agents_carry_only_standard_proxy_variables(tmp_path):
    from mesocosm.env.compose import CA_PATH, PROXY_URL, egress_proxy_env, proxy_environment

    manifest = load_manifest(ROOT / "examples" / "fixtures" / "spike-world.yaml")
    manifest.agents.append(manifest.agents[0].model_copy(update={"id": "agent-2"}))
    manifest.services.domain = "corp.example"
    manifest.internet.enabled = True
    manifest.internet.mode = "record"
    manifest.internet.allow = ["pypi.org", "*.pythonhosted.org"]
    manifest.internet.deny = ["bad.example"]
    manifest.internet.budget.requests = 100
    files = write_agent_files(manifest, "lbl", tmp_path / "agents", web_dir=tmp_path / "web")
    assert (tmp_path / "web" / "ca" / "ca.pem").exists() and (tmp_path / "web" / "ca" / "mitmproxy-ca.pem").exists()
    assert any(p.name == "ca.pem" for p in files)
    compose = render_compose(manifest, repo_root=ROOT, run_label="lbl", files_dir=tmp_path / "agents")
    web = compose["services"]["web"]
    assert web["build"]["dockerfile"] == "spike/docker/Dockerfile.web"
    assert web["networks"] == {"sandbox": {"ipv4_address": "172.30.0.5", "aliases": ["proxy", "proxy.corp.example"]},
                               "edge": {}}, "on every sandbox network and on edge: the one route out"
    policy = json.loads(web["environment"]["MESOCOSM_WEB_POLICY"])
    assert policy["allow"] == ["pypi.org", "*.pythonhosted.org"] and policy["deny"] == ["bad.example"]
    assert policy["mode"] == "record" and policy["budget"] == {"requests": 100, "bytes": None}
    assert policy["domain"] == "corp.example"
    assert web["environment"]["MESOCOSM_AGENT_IPS"] == '{"172.30.0.10": "agent-1", "172.30.0.11": "agent-2"}'
    assert web["volumes"] == [f"{tmp_path}/web:/logs", f"{tmp_path}/web/ca:/ca"]
    assert web["command"] == ["-w", "/logs/flows.mitm"]
    for agent_id in ("agent-1", "agent-2"):
        a = compose["services"][agent_id]
        env = a["environment"]
        assert not any(k.startswith("MESOCOSM") for k in env), "nothing of ours; a workstation behind a corporate proxy"
        assert env["HTTPS_PROXY"] == env["https_proxy"] == PROXY_URL and env["HTTP_PROXY"] == PROXY_URL
        assert env["NO_PROXY"] == "localhost,127.0.0.1,gateway,board,wiki,git,pypi,services,.corp.example"
        assert env["SSL_CERT_FILE"] == env["NODE_EXTRA_CA_CERTS"] == env["PIP_CERT"] == env["GIT_SSL_CAINFO"] == CA_PATH
        assert f"{tmp_path}/web/ca/ca.pem:{CA_PATH}:ro" in a["volumes"]
        assert a["depends_on"]["web"] == {"condition": "service_healthy"}
        assert "ports" not in a and "extra_hosts" not in a and list(a["networks"]) == ["sandbox"]
    assert proxy_environment(manifest)["no_proxy"] == proxy_environment(manifest)["NO_PROXY"]
    env = egress_env(manifest, manifest.agents[0])
    assert env["MESOCOSM_PROXY"] == PROXY_URL and env["MESOCOSM_PROXY_ALLOWED"] == "pypi.org"
    assert env["MESOCOSM_PROXY_DENIED"] == "example.com" and env["MESOCOSM_CA"] == CA_PATH
    # replay: the recorded flows are mounted read-only and nothing is forwarded
    manifest.internet.mode = "replay"
    manifest.internet.replay_file = "flows.mitm"
    replay = render_compose(manifest, repo_root=ROOT, run_label="lbl", files_dir=tmp_path / "agents",
                            manifest_dir=tmp_path)["services"]["web"]
    assert replay["command"][:2] == ["--server-replay", "/logs/replay.mitm"] and "server_replay_extra=kill" in replay["command"]
    assert f"{tmp_path}/flows.mitm:/logs/replay.mitm:ro" in replay["volumes"]
    # disabled: no web service, no proxy variables, and the check expects the proxy name to be absent
    plain = load_manifest(ROOT / "examples" / "fixtures" / "spike-world.yaml")
    off = render_compose(plain, repo_root=ROOT, run_label="lbl")
    assert "web" not in off["services"]
    assert set(off["services"]["agent-1"]["environment"]) == {"OPENCODE_SERVER_USERNAME", "OPENCODE_SERVER_PASSWORD"}
    assert egress_proxy_env(plain) == {"MESOCOSM_PROXY": ""} and egress_env(plain, plain.agents[0])["MESOCOSM_PROXY"] == ""
    assert "web" not in off["services"]["agent-1"]["depends_on"]


def test_switched_off_harness_tools_reach_the_config():
    manifest = load_manifest(ROOT / "examples" / "fixtures" / "spike-world.yaml")
    assert "tools" not in json.loads(agent_files(manifest, "lbl")["agent-1"]["opencode.json"]), "as shipped by default"
    manifest.agents[0].tools = {"todowrite": False, "question": False}
    cfg = json.loads(agent_files(manifest, "lbl")["agent-1"]["opencode.json"])
    assert cfg["tools"] == {"todowrite": False, "question": False}


def test_each_agent_gets_its_own_model_and_the_gateway_the_catalog_with_keys_passed_through():
    from mesocosm.core.manifest import ModelSpec
    manifest = load_manifest(ROOT / "examples" / "fixtures" / "spike-world.yaml")
    manifest.models = [ModelSpec(id="baseline-x", upstream="https://api.example", api_key_env="BASELINE_KEY",
                                 kind="openai", context_limit=200000, output_limit=16000)]
    manifest.agents.append(manifest.agents[0].model_copy(update={"id": "agent-2", "model": "baseline-x"}))
    compose = render_compose(manifest, repo_root=ROOT, run_label="lbl")
    env = compose["services"]["gateway"]["environment"]
    assert yaml.safe_load(env["MESOCOSM_UPSTREAMS"]) == {"baseline-x": {"url": "https://api.example",
                                                                         "key_env": "BASELINE_KEY", "kind": "openai"}}
    assert env["BASELINE_KEY"] == "${BASELINE_KEY:?BASELINE_KEY is required by model baseline-x in the manifest}", \
        "compose interpolates the key at `up`; the file never holds it"
    for agent_id in ("agent-1", "agent-2"):
        assert set(compose["services"][agent_id]["environment"]) == {"OPENCODE_SERVER_USERNAME", "OPENCODE_SERVER_PASSWORD"}
    files = agent_files(manifest, "lbl")
    one, two = json.loads(files["agent-1"]["opencode.json"]), json.loads(files["agent-2"]["opencode.json"])
    assert one["model"] == f"internal/{manifest.model.id}" and two["model"] == "internal/baseline-x"
    assert list(two["provider"]["internal"]["models"]) == ["baseline-x"], "an agent's config names its model only"
    assert two["provider"]["internal"]["models"]["baseline-x"]["limit"] == {"context": 200000, "output": 16000}
    assert "BASELINE_KEY" not in json.dumps(files) and "api.example" not in json.dumps(files)


def test_cache_gets_a_hostname_and_its_configuration_and_is_a_service_not_a_channel():
    from mesocosm.core.manifest import CacheSpec
    manifest = load_manifest(ROOT / "examples" / "fixtures" / "spike-world.yaml")
    manifest.services.cache = CacheSpec(enabled=True, writable=True, list_recent=True, seed={"base-image": "sha"})
    svc = render_compose(manifest, repo_root=ROOT, run_label="lbl")["services"]["services"]
    assert "cache" in svc["networks"]["sandbox"]["aliases"]
    assert yaml.safe_load(svc["environment"]["MESOCOSM_CACHE"]) == {"writable": True, "list_recent": True,
                                                                     "seed": {"base-image": "sha"}}
    # the egress check confirms the cache as a present service, never as a hidden channel
    assert "cache" in egress_env(manifest, manifest.agents[0])["MESOCOSM_SERVICES"].split(",")
    assert "cache" not in egress_env(manifest, manifest.agents[0])["MESOCOSM_CHANNELS"]
    plain = render_compose(load_manifest(ROOT / "examples" / "fixtures" / "spike-world.yaml"), repo_root=ROOT, run_label="lbl")
    assert plain["services"]["services"]["environment"]["MESOCOSM_CACHE"] == "null"
    assert "cache" not in plain["services"]["services"]["networks"]["sandbox"]["aliases"]


def test_honeypot_gets_the_vault_hostname_and_its_lures():
    from mesocosm.core.manifest import HoneypotSpec
    manifest = load_manifest(ROOT / "examples" / "fixtures" / "spike-world.yaml")
    manifest.services.honeypot = HoneypotSpec(enabled=True, banner="vault", lures={"deploy-key": "x"})
    svc = render_compose(manifest, repo_root=ROOT, run_label="lbl")["services"]["services"]
    assert "vault" in svc["networks"]["sandbox"]["aliases"]
    assert yaml.safe_load(svc["environment"]["MESOCOSM_HONEYPOT"]) == {"banner": "vault", "lures": {"deploy-key": "x"}}
    assert "vault" in egress_env(manifest, manifest.agents[0])["MESOCOSM_SERVICES"].split(",")
    plain = render_compose(load_manifest(ROOT / "examples" / "fixtures" / "spike-world.yaml"), repo_root=ROOT, run_label="lbl")
    assert plain["services"]["services"]["environment"]["MESOCOSM_HONEYPOT"] == "null"


def test_services_seed_dir_is_mounted_and_env_set(tmp_path):
    """services.seed mounts the world's initial-state directory read-only and names it for the services
    container (ADR-0039); without it, neither the mount nor the env var appears."""
    import yaml

    from mesocosm.core.manifest import load_manifest

    base = yaml.safe_load((ROOT / "examples" / "fixtures" / "spike-world.yaml").read_text())
    base["services"] = {"enabled": True, "seed": "world/seed"}
    (tmp_path / "world" / "seed").mkdir(parents=True)
    (tmp_path / "tasks" / "hello-world").mkdir(parents=True)
    (tmp_path / "tasks" / "hello-world" / "instruction.md").write_text("do it\n")
    (tmp_path / "w.yaml").write_text(yaml.safe_dump(base))
    manifest = load_manifest(tmp_path / "w.yaml")
    world = render_compose(manifest, repo_root=ROOT, run_label="lbl", manifest_dir=tmp_path)["services"]["services"]
    assert f"{(tmp_path / 'world' / 'seed').resolve()}:/seed:ro" in world["volumes"]
    assert world["environment"]["MESOCOSM_SEED_DIR"] == "/seed"

    base["services"] = {"enabled": True}
    (tmp_path / "w2.yaml").write_text(yaml.safe_dump(base))
    world2 = render_compose(load_manifest(tmp_path / "w2.yaml"), repo_root=ROOT, run_label="lbl",
                            manifest_dir=tmp_path)["services"]["services"]
    assert not any(":/seed:ro" in v for v in world2["volumes"])
    assert "MESOCOSM_SEED_DIR" not in world2["environment"]
