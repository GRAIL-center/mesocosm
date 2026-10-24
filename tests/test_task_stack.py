"""Topology/compiler plumbing only; these tests do not establish live Docker acceptance."""

import copy

import pytest

from mesocosm.env.task_stack import TaskComposeError, compile_task_stack, find_compose_file, prevalidate_compose


@pytest.fixture
def native(tmp_path):
    (tmp_path / "Dockerfile").write_text("FROM debian:bookworm\nWORKDIR /app\n")
    (tmp_path / "initial.sql").write_text("CREATE TABLE items (id INTEGER);\n")
    return {
        "services": {
            "main": {
                "build": {"context": ".", "args": {"MODE": "research"}},
                "working_dir": "/app", "command": ["python", "app.py"],
                "environment": {"DATABASE_URL": "postgres://db:5432/application"},
                "depends_on": {"db": {"condition": "service_healthy"}},
            },
            "db": {
                "image": "postgres:17", "environment": {"POSTGRES_PASSWORD": "local-task-only"},
                "volumes": ["database:/var/lib/postgresql/data", "./initial.sql:/docker-entrypoint-initdb.d/init.sql:ro"],
                "healthcheck": {"test": ["CMD-SHELL", "pg_isready -U postgres"], "interval": "2s"},
            },
            "worker": {
                "image": "publisher/worker:fixed", "command": ["worker", "--database", "db"],
                "depends_on": {"db": {"condition": "service_healthy"}},
            },
        },
        "volumes": {"database": {}},
    }


def compile_(native, root, *, scope="isolated", **kwargs):
    return compile_task_stack(native, environment_root=root, world_name="mesocosm-research",
                              task_key="dataset/task-a@revision", scope=scope,
                              agent_networks={f"agent-{i}": f"sandbox-agent-{i}" for i in range(1, 4)}, **kwargs)


def test_isolated_groups_keep_actual_services_dependencies_and_input_state(native, tmp_path):
    original = copy.deepcopy(native)
    prevalidate_compose(native, tmp_path)
    stack = compile_(native, tmp_path)
    assert native == original
    assert len(stack.services) == 6 and len(stack.volumes) == 3 and len(stack.main_by_agent) == 3
    assert len(set(stack.group_ids.values())) == 3
    for agent, main in stack.main_by_agent.items():
        network = f"sandbox-{agent}"
        assert main["networks"] == {network: {"aliases": ["main"]}}
        assert main["build"]["context"] == str(tmp_path)
        assert main["build"]["dockerfile"] == str(tmp_path / "Dockerfile")
        assert main["command"] == ["python", "app.py"]
        assert main["environment"] == original["services"]["main"]["environment"]
        sidecars = stack.service_names_by_agent[agent]
        assert len(sidecars) == 2
        db_name = next(n for n in sidecars if stack.services[n]["image"] == "postgres:17")
        db = stack.services[db_name]
        assert main["depends_on"] == {db_name: {"condition": "service_healthy"}}
        assert db["networks"] == {network: {"aliases": ["db"]}}
        assert db["healthcheck"] == original["services"]["db"]["healthcheck"]
        assert db["volumes"][0]["source"] in stack.volumes
        assert db["volumes"][1]["source"] == str(tmp_path / "initial.sql")
        assert db["volumes"][1]["bind"] == {"create_host_path": False}
        assert db["security_opt"] == ["no-new-privileges:true"]
        assert set(db["cap_drop"]) >= {"NET_RAW", "NET_ADMIN"}
        assert all("mesocosm" not in name for name in sidecars)


def test_shared_services_and_volumes_are_created_once_with_distinct_agent_runtimes(native, tmp_path):
    stack = compile_(native, tmp_path, scope="shared")
    assert len(stack.services) == 2 and len(stack.volumes) == 1 and len(stack.main_by_agent) == 3
    assert len(set(stack.group_ids.values())) == 1
    assert stack.service_names_by_agent["agent-1"] == stack.service_names_by_agent["agent-3"]
    for service in stack.services.values():
        assert set(service["networks"]) == {f"sandbox-agent-{i}" for i in range(1, 4)}
    for agent, main in stack.main_by_agent.items():
        assert main["networks"] == {f"sandbox-{agent}": {"aliases": []}}
    assert all("main" not in aliases for aliases in stack.aliases_by_agent.values())


def test_compilation_is_deterministic_and_task_identity_changes_namespace(native, tmp_path):
    one, two = compile_(native, tmp_path), compile_(native, tmp_path)
    assert one == two
    other = compile_task_stack(native, environment_root=tmp_path, world_name="mesocosm-research",
                               task_key="dataset/task-b@revision", scope="isolated",
                               agent_networks={f"agent-{i}": f"sandbox-agent-{i}" for i in range(1, 4)})
    assert not set(one.services) & set(other.services)
    assert not set(one.volumes) & set(other.volumes)


@pytest.mark.parametrize("field,value", [
    ("privileged", True), ("cap_add", ["NET_ADMIN"]), ("devices", ["/dev/sda:/dev/sda"]),
    ("network_mode", "host"), ("pid", "host"), ("ipc", "host"),
    ("ports", ["8080:8080"]), ("extra_hosts", ["host:host-gateway"]),
    ("env_file", [".env"]), ("secrets", ["credential"]), ("configs", ["config"]),
    ("extends", {"file": "../other.yml", "service": "main"}),
    ("volumes_from", ["control"]), ("container_name", "control"),
    ("deploy", {"replicas": 2}), ("profiles", ["optional"]), ("sysctls", {"net.ipv4.ip_forward": 1}),
    ("links", ["main"]), ("use_api_socket", True),
])
def test_boundary_escape_and_unpreserved_features_are_explicitly_rejected(native, tmp_path, field, value):
    native["services"]["worker"][field] = value
    with pytest.raises(TaskComposeError, match="unsupported Compose fields"):
        prevalidate_compose(native, tmp_path)


@pytest.mark.parametrize("field,value", [
    ("include", ["../other.yml"]), ("secrets", {"secret": {"file": "../secret"}}),
    ("configs", {"config": {"file": "../config"}}),
])
def test_cross_project_inputs_rejected_before_compose_normalization(native, tmp_path, field, value):
    native[field] = value
    with pytest.raises(TaskComposeError, match="unsupported Compose fields"):
        prevalidate_compose(native, tmp_path)


@pytest.mark.parametrize("value", ["${HOME}", "$HOME", "${KEY:?required}"])
def test_host_variable_interpolation_is_not_silently_resolved(native, tmp_path, value):
    native["services"]["main"]["environment"] = {"PATH_HINT": value}
    with pytest.raises(TaskComposeError, match="host interpolation"):
        prevalidate_compose(native, tmp_path)


def test_container_shell_dollars_survive_explicit_compose_escaping(native, tmp_path):
    native["services"]["worker"]["command"] = ["sh", "-c", "echo $$HOSTNAME"]
    prevalidate_compose(native, tmp_path)
    native["services"]["worker"]["command"] = ["sh", "-c", "echo $HOSTNAME"]
    stack = compile_(native, tmp_path)
    assert any(v["command"][-1] == "echo $HOSTNAME" for v in stack.services.values() if v["image"].endswith(":fixed"))


@pytest.mark.parametrize("value", [["HOST_KEY"], {"HOST_KEY": None}])
def test_implicit_host_environment_rejected(native, tmp_path, value):
    native["services"]["worker"]["environment"] = value
    with pytest.raises(TaskComposeError, match="environment"):
        prevalidate_compose(native, tmp_path)


@pytest.mark.parametrize("mount", [
    "/tmp:/outside", "/var/run/docker.sock:/var/run/docker.sock:ro", "../other:/input:ro",
    "./initial.sql:/input:rw", "anonymous", "database:/etc", "database:/etc/opencode/custom",
    "database:/root/.config/opencode", "undeclared:/data",
])
def test_unsafe_mounts_and_mounts_hiding_harness_configuration_rejected(native, tmp_path, mount):
    native["services"]["worker"]["volumes"] = [mount]
    with pytest.raises(TaskComposeError):
        prevalidate_compose(native, tmp_path)


def test_symlink_input_cannot_escape_task_context(native, tmp_path):
    (tmp_path / "input").symlink_to("/etc/passwd")
    native["services"]["worker"]["volumes"] = ["./input:/data/input:ro"]
    with pytest.raises(TaskComposeError, match="symlink|inside"):
        prevalidate_compose(native, tmp_path)


@pytest.mark.parametrize("mount", ["/", "/etc:rw", "/etc/opencode:size=1m"])
def test_tmpfs_cannot_hide_harness_files(native, tmp_path, mount):
    native["services"]["main"]["tmpfs"] = mount
    with pytest.raises(TaskComposeError, match="harness configuration"):
        prevalidate_compose(native, tmp_path)


def test_real_service_tmpfs_is_preserved(native, tmp_path):
    native["services"]["worker"]["tmpfs"] = ["/run:size=8m", "/tmp:mode=1777"]
    stack = compile_(native, tmp_path)
    assert all(service["tmpfs"] == ["/run:size=8m", "/tmp:mode=1777"]
               for service in stack.services.values() if service["image"].endswith(":fixed"))


@pytest.mark.parametrize("value", [{"external": True}, {"driver": "nfs"}, {"driver_opts": {"device": "/"}}])
def test_external_or_host_backed_volumes_are_not_renamed_as_safe_local_storage(native, tmp_path, value):
    native["volumes"]["database"] = value
    with pytest.raises(TaskComposeError, match="unsupported"):
        prevalidate_compose(native, tmp_path)


@pytest.mark.parametrize("value", [{"network_mode": "host"}, {"networks": ["default", "external"]},
                                    {"networks": {"default": {"ipv4_address": "172.30.1.10"}}},
                                    {"networks": {"default": {"mac_address": "aa:bb:cc:dd:ee:ff"}}}])
def test_sidecars_cannot_add_routes_or_claim_agent_address(native, tmp_path, value):
    native["services"]["worker"].update(value)
    with pytest.raises(TaskComposeError):
        prevalidate_compose(native, tmp_path)


@pytest.mark.parametrize("name", ["gateway", "control", "proxy", "agent-1", "git"])
def test_service_names_cannot_impersonate_controlled_dns(native, tmp_path, name):
    native["services"][name] = native["services"].pop("worker")
    with pytest.raises(TaskComposeError, match="DNS names conflict"):
        compile_(native, tmp_path)


def test_custom_world_dns_alias_is_reserved(native, tmp_path):
    native["services"]["worker"]["networks"] = {"default": {"aliases": ["gateway.corp.internal"]}}
    with pytest.raises(TaskComposeError, match="DNS names conflict"):
        compile_(native, tmp_path, reserved_aliases={"gateway.corp.internal"})


@pytest.mark.parametrize("value", ["always", "unless-stopped", "on-failure"])
def test_restart_policies_cannot_undo_confirmed_workload_termination(native, tmp_path, value):
    native["services"]["worker"]["restart"] = value
    with pytest.raises(TaskComposeError, match="restart policies"):
        prevalidate_compose(native, tmp_path)


def test_sidecar_main_dependency_is_not_silently_dropped(native, tmp_path):
    native["services"]["worker"]["depends_on"] = ["main"]
    with pytest.raises(TaskComposeError, match="dependencies on main"):
        compile_(native, tmp_path)


def test_shared_main_custom_hostname_is_explicitly_unsupported(native, tmp_path):
    native["services"]["main"]["hostname"] = "application"
    with pytest.raises(TaskComposeError, match="ambiguous"):
        compile_(native, tmp_path, scope="shared")


def test_isolated_scope_cannot_share_one_agent_network(native, tmp_path):
    with pytest.raises(TaskComposeError, match="separate existing network"):
        compile_task_stack(native, environment_root=tmp_path, world_name="world", task_key="task", scope="isolated",
                           agent_networks={"a": "sandbox", "b": "sandbox"})


@pytest.mark.parametrize("filename", ["docker-compose.yaml", "docker-compose.yml", "compose.yaml", "compose.yml"])
def test_all_standard_compose_filenames_are_detected(tmp_path, filename):
    assert find_compose_file(tmp_path) is None
    selected = tmp_path / filename
    selected.write_text("services: {}\n")
    assert find_compose_file(tmp_path) == selected


def test_multiple_compose_files_require_explicit_resolution(tmp_path):
    (tmp_path / "compose.yml").write_text("services: {}\n")
    (tmp_path / "docker-compose.yaml").write_text("services: {}\n")
    with pytest.raises(TaskComposeError, match="multiple"):
        find_compose_file(tmp_path)
