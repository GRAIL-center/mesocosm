"""Compile a declared task's Compose services into the existing sandbox project.

This deliberately supports one private task network, local build contexts, ordinary service
commands/healthchecks, and namespaced local volumes. It rejects semantics that cannot be retained
inside the sandbox boundary. Docker Compose resolves syntax only after ``prevalidate_compose``;
the compiler does not load host environment files, extend other projects, or invent task actors.
The caller must supply existing *internal* sandbox networks and protect every control endpoint
against unknown service addresses. Dropped network capabilities prevent address impersonation;
application services are still explicit communication paths, not an information-flow guarantee.
"""

from __future__ import annotations

import copy
import hashlib
import pathlib
import re
from dataclasses import dataclass
from typing import Any, Literal

COMPOSE_FILENAMES = ("docker-compose.yaml", "docker-compose.yml", "compose.yaml", "compose.yml")
_RESERVED_ALIASES = {
    "gateway", "services", "control", "proxy", "board", "wiki", "git", "pypi", "pastebin", "chat", "mcp",
    "clock", "ledger", "cache", "vault", "localhost", "host.docker.internal",
}
_SERVICE_FIELDS = {
    "image", "build", "command", "entrypoint", "working_dir", "user", "environment", "depends_on",
    "healthcheck", "volumes", "expose", "hostname", "domainname", "labels", "init", "stop_signal",
    "stop_grace_period", "read_only", "tmpfs", "cpus", "mem_limit", "mem_reservation", "pids_limit",
    "ulimits", "shm_size", "restart", "cap_drop", "security_opt", "networks", "platform",
}
_BUILD_FIELDS = {"context", "dockerfile", "args", "target", "labels", "no_cache", "pull", "shm_size"}
_PROTECTED_MOUNTS = ("/etc/opencode", "/opt/agent-start.sh", "/root/.config/opencode", "/root/.gitconfig")
_IDENTIFIER = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_.-]*$")


class TaskComposeError(RuntimeError):
    """A task Compose feature cannot preserve the controlled execution boundary."""


@dataclass
class TaskStack:
    services: dict[str, dict[str, Any]]
    volumes: dict[str, dict[str, Any]]
    main_by_agent: dict[str, dict[str, Any]]
    aliases_by_agent: dict[str, list[str]]
    service_names_by_agent: dict[str, list[str]]
    group_ids: dict[str, str]


def find_compose_file(environment_root: pathlib.Path) -> pathlib.Path | None:
    found = [environment_root / name for name in COMPOSE_FILENAMES if (environment_root / name).exists()]
    if len(found) > 1:
        raise TaskComposeError("multiple task Compose files are ambiguous")
    if found:
        return _local_path(found[0], environment_root, "Compose file", directory=False)
    return None


def _mapping(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict) or not all(isinstance(key, str) for key in value):
        raise TaskComposeError(f"{label} must be a mapping")
    return value


def _fields(value: dict[str, Any], allowed: set[str], label: str) -> None:
    unsupported = sorted(key for key in value if key not in allowed and not key.startswith("x-"))
    if unsupported:
        raise TaskComposeError(f"{label}: unsupported Compose fields: {', '.join(unsupported)}")


def _local_path(value: str | pathlib.Path, root: pathlib.Path, label: str, *, directory: bool) -> pathlib.Path:
    path = pathlib.Path(value)
    path = path if path.is_absolute() else root / path
    resolved = path.resolve()
    if not resolved.is_relative_to(root.resolve()):
        raise TaskComposeError(f"{label} must stay inside the task environment directory")
    if (directory and not resolved.is_dir()) or (not directory and not resolved.is_file()):
        raise TaskComposeError(f"{label} is missing or has the wrong file type: {path}")
    # Do not let build contexts or read-only input mounts follow outside references later.
    paths = [path, *(path.rglob("*") if directory else [])]
    if any(p.is_symlink() or not (p.is_file() or p.is_dir()) for p in paths):
        raise TaskComposeError(f"{label} contains a symlink or special file")
    return resolved


def _interpolation(value: Any) -> None:
    if isinstance(value, str) and "$" in value.replace("$$", ""):
        raise TaskComposeError("task Compose host interpolation is unsupported; escape container-shell dollars as $$")
    if isinstance(value, dict):
        for key, item in value.items():
            _interpolation(key)
            _interpolation(item)
    if isinstance(value, list):
        for item in value:
            _interpolation(item)


def _environment(value: Any, label: str) -> dict[str, Any]:
    if isinstance(value, list):
        result = {}
        for entry in value:
            if not isinstance(entry, str) or "=" not in entry:
                raise TaskComposeError(f"{label}: inherited host environment variables are unsupported")
            key, content = entry.split("=", 1)
            result[key] = content
    else:
        result = dict(_mapping(value, label))
    if any(content is None or not isinstance(content, (str, int, float, bool)) for content in result.values()):
        raise TaskComposeError(f"{label}: explicit scalar environment values are required")
    return result


def _build(value: Any, root: pathlib.Path, label: str) -> dict[str, Any]:
    spec: dict[str, Any] = {"context": value} if isinstance(value, str) else dict(_mapping(value, label))
    _fields(spec, _BUILD_FIELDS, label)
    context = _local_path(str(spec.get("context", ".")), root, f"{label} context", directory=True)
    dockerfile = _local_path(str(spec.get("dockerfile", "Dockerfile")), context, f"{label} Dockerfile", directory=False)
    if spec.get("args") is not None:
        spec["args"] = _environment(spec["args"], f"{label} args")
    return {**spec, "context": str(context), "dockerfile": str(dockerfile)}


def _networks(service: dict[str, Any], label: str) -> dict[str, Any]:
    raw = service.get("networks")
    result = {name: {} for name in raw} if isinstance(raw, list) else ({"default": {}} if raw is None else raw)
    result = _mapping(result, f"{label} networks")
    if len(result) != 1:
        raise TaskComposeError(f"{label}: exactly one original task network is supported")
    for settings in result.values():
        settings = _mapping(settings or {}, f"{label} network settings")
        _fields(settings, {"aliases"}, f"{label} network settings")
        if not isinstance(settings.get("aliases", []), list) or not all(
            isinstance(alias, str) and _IDENTIFIER.fullmatch(alias) for alias in settings.get("aliases", [])
        ):
            raise TaskComposeError(f"{label}: invalid network aliases")
    return result


def _mount_target(target: Any, label: str) -> None:
    if not isinstance(target, str) or not target.startswith("/") or ".." in pathlib.PurePosixPath(target).parts:
        raise TaskComposeError(f"{label}: mount targets must be absolute container paths")
    destination = pathlib.PurePosixPath(target)
    if any(destination == pathlib.PurePosixPath(item) or destination in pathlib.PurePosixPath(item).parents
           or pathlib.PurePosixPath(item) in destination.parents for item in _PROTECTED_MOUNTS):
        raise TaskComposeError(f"{label}: task mounts may not hide or expose harness configuration")


def _mount(value: Any, volumes: dict[str, Any], root: pathlib.Path, label: str) -> dict[str, Any]:
    mount: dict[str, Any]
    if isinstance(value, str):
        parts = value.split(":")
        if len(parts) not in (2, 3) or (len(parts) == 3 and parts[2] not in ("ro", "rw")):
            raise TaskComposeError(f"{label}: anonymous volumes and unsupported mount options are refused")
        source, short_target = parts[:2]
        short_kind = "bind" if source.startswith(("/", ".", "~")) else "volume"
        mount = {"type": short_kind, "source": source, "target": short_target,
                 "read_only": len(parts) == 3 and parts[2] == "ro"}
    else:
        mount = dict(_mapping(value, f"{label} volume"))
    _fields(mount, {"type", "source", "target", "read_only", "volume", "bind", "tmpfs"}, f"{label} volume")
    _mount_target(mount.get("target"), label)
    kind = mount.get("type")
    if kind == "volume":
        if mount.get("source") not in volumes:
            raise TaskComposeError(f"{label}: every volume must be a declared local named volume")
        _fields(_mapping(mount.get("volume") or {}, f"{label} volume options"), {"nocopy"}, "volume options")
    elif kind == "bind":
        if mount.get("read_only") is not True:
            raise TaskComposeError(f"{label}: task bind mounts must be read-only declared environment inputs")
        source_path = pathlib.Path(str(mount.get("source", "")))
        probe = source_path if source_path.is_absolute() else root / source_path
        mount["source"] = str(_local_path(source_path, root, f"{label} bind source", directory=probe.is_dir()))
        options = _mapping(mount.get("bind") or {}, f"{label} bind options")
        _fields(options, {"create_host_path"}, f"{label} bind options")
        # Compose config inserts create_host_path:true for short binds. The source already exists;
        # force false so a later deletion cannot create a host directory silently.
        mount["bind"] = {"create_host_path": False}
    elif kind == "tmpfs":
        if mount.get("source"):
            raise TaskComposeError(f"{label}: tmpfs cannot have a source")
        _fields(_mapping(mount.get("tmpfs") or {}, "tmpfs options"), {"size", "mode"}, "tmpfs options")
    else:
        raise TaskComposeError(f"{label}: unsupported mount type {kind!r}")
    return mount


def _validate(compose: dict[str, Any], root: pathlib.Path) -> dict[str, dict[str, Any]]:
    _fields(compose, {"name", "version", "services", "volumes", "networks"}, "task")
    services = _mapping(compose.get("services"), "task services")
    if not services:
        raise TaskComposeError("task Compose declares no services")
    volumes = _mapping(compose.get("volumes") or {}, "task volumes")
    for name, options in volumes.items():
        if not _IDENTIFIER.fullmatch(name):
            raise TaskComposeError("invalid task volume name")
        _fields(_mapping(options or {}, f"volume {name}"), {"name", "labels"}, f"volume {name}")
    declared_networks = _mapping(compose.get("networks") or {}, "task networks")
    for name, options in declared_networks.items():
        options = _mapping(options or {}, f"network {name}")
        _fields(options, {"name", "internal", "ipam"}, f"network {name}")
        # Compose 5.6.1 inserts ipam:{} even for an implicit default network.
        # Only that empty mapping is inert; custom addressing is not preserved.
        if "ipam" in options and options["ipam"] != {}:
            raise TaskComposeError(f"network {name}: only empty IPAM is supported")
    normalized = {}
    task_networks = set()
    for name, raw in services.items():
        if not _IDENTIFIER.fullmatch(name):
            raise TaskComposeError("invalid task service name")
        service = dict(_mapping(raw, f"service {name}"))
        _fields(service, _SERVICE_FIELDS, f"service {name}")
        if not service.get("image") and not service.get("build"):
            raise TaskComposeError(f"{name}: declare an image or local build")
        if service.get("build") is not None:
            service["build"] = _build(service["build"], root, f"{name} build")
        tmpfs = service.get("tmpfs") or []
        tmpfs = [tmpfs] if isinstance(tmpfs, str) else tmpfs
        if not isinstance(tmpfs, list) or not all(isinstance(mount, str) for mount in tmpfs):
            raise TaskComposeError(f"{name}: tmpfs must list container paths")
        for mount in tmpfs:
            _mount_target(mount.split(":", 1)[0], name)
        if service.get("environment") is not None:
            service["environment"] = _environment(service["environment"], f"{name} environment")
        if service.get("restart", "no") not in ("no", None):
            raise TaskComposeError(f"{name}: restart policies prevent confirmed workload termination")
        security = service.get("security_opt") or []
        if not isinstance(security, list) or any(v not in ("no-new-privileges:true", "no-new-privileges=true")
                                               for v in security):
            raise TaskComposeError(f"{name}: custom security options are unsupported")
        caps = service.get("cap_drop") or []
        if not isinstance(caps, list) or not all(isinstance(v, str) for v in caps):
            raise TaskComposeError(f"{name}: cap_drop must be a list")
        service["cap_drop"] = sorted(set(caps) | {"NET_ADMIN", "NET_RAW"})
        service["security_opt"] = ["no-new-privileges:true"]
        service["networks"] = _networks(service, name)
        task_networks.update(service["networks"])
        if not isinstance(service.get("volumes", []), list):
            raise TaskComposeError(f"{name}: volumes must be a list")
        service["volumes"] = [_mount(v, volumes, root, name) for v in service.get("volumes", [])]
        dependencies = service.get("depends_on") or {}
        if isinstance(dependencies, list):
            dependencies = {dep: {"condition": "service_started"} for dep in dependencies}
        dependencies = _mapping(dependencies, f"{name} dependencies")
        for dependency, options in dependencies.items():
            if dependency not in services or dependency == name:
                raise TaskComposeError(f"{name}: invalid dependency {dependency!r}")
            options = _mapping(options, f"{name} dependency {dependency}")
            _fields(options, {"condition", "required", "restart"}, f"{name} dependency {dependency}")
            if options.get("restart") or options.get("required", True) is not True:
                raise TaskComposeError(f"{name}: optional or restarting dependencies are unsupported")
            if options.get("condition", "service_started") not in (
                "service_started", "service_healthy", "service_completed_successfully"
            ):
                raise TaskComposeError(f"{name}: unsupported dependency condition")
        service["depends_on"] = dependencies
        normalized[name] = service
    if len(task_networks) != 1 or set(declared_networks) - task_networks:
        raise TaskComposeError("only one shared original task network is supported; extra networks are refused")
    return normalized


def prevalidate_compose(compose: dict[str, Any], environment_root: pathlib.Path) -> None:
    """Call before ``docker compose config`` so normalization cannot hide host dependencies."""
    _mapping(compose, "task Compose")
    _interpolation(compose)
    _validate(compose, environment_root)


def _name(group: str, kind: str, original: str) -> str:
    slug = re.sub(r"[^a-z0-9-]", "-", original.lower())[:24]
    suffix = hashlib.sha256(original.encode()).hexdigest()[:6]
    return f"task-{group}-{kind}-{slug}-{suffix}"


def compile_task_stack(compose: dict[str, Any], *, environment_root: pathlib.Path, world_name: str,
                       task_key: str, agent_networks: dict[str, str], scope: Literal["isolated", "shared"],
                       main_service: str = "main", reserved_aliases: set[str] | None = None) -> TaskStack:
    """Return sidecars and agent overrides; never mutate source Compose or create another main actor.

    Shared scope shares sidecars and named volumes, while each agent keeps its own main runtime.
    Sidecar-to-main RPC is unsupported in shared scope: no ambiguous ``main`` alias is emitted.
    The controller owns the complete project and removes its namespaced volumes with ``down -v``.
    """
    services = _validate(_mapping(compose, "task Compose"), environment_root)
    if main_service not in services:
        raise TaskComposeError(f"task Compose must declare {main_service!r}")
    if scope not in ("isolated", "shared") or not agent_networks:
        raise TaskComposeError("task scope and at least one agent network are required")
    if scope == "isolated" and len(set(agent_networks.values())) != len(agent_networks):
        raise TaskComposeError("isolated task groups require a separate existing network for every agent")
    if any(network == "edge" for network in agent_networks.values()):
        raise TaskComposeError("task services cannot use the outbound edge network")
    forbidden = {v.lower() for v in (_RESERVED_ALIASES | (reserved_aliases or set()) | set(agent_networks))}
    aliases: dict[str, list[str]] = {}
    for name, service in services.items():
        network = next(iter(service["networks"].values())) or {}
        names = list(dict.fromkeys([name, *(network.get("aliases") or [])]))
        if service.get("hostname"):
            names.append(service["hostname"])
        if any(not isinstance(alias, str) or alias.lower() in forbidden for alias in names):
            raise TaskComposeError(f"{name}: task DNS names conflict with a controlled service or agent")
        if name != main_service and main_service in service["depends_on"]:
            raise TaskComposeError(f"{name}: sidecar dependencies on main cannot preserve harness readiness")
        if scope == "shared" and name == main_service:
            if network.get("aliases") or service.get("hostname"):
                raise TaskComposeError("shared task main aliases/hostnames are ambiguous across agent runtimes")
            names = []
        aliases[name] = names
    result = TaskStack({}, {}, {}, {}, {}, {})
    groups = {agent: agent for agent in agent_networks} if scope == "isolated" else {"shared": "shared"}
    for member in groups:
        selected = [member] if scope == "isolated" else list(agent_networks)
        identity = "\0".join((world_name, task_key, scope, member))
        group = hashlib.sha256(identity.encode()).hexdigest()[:12]
        service_names = {name: _name(group, "s", name) for name in services if name != main_service}
        volume_names = {name: _name(group, "v", name) for name in (compose.get("volumes") or {})}
        for name, mapped in volume_names.items():
            options = compose["volumes"][name] or {}
            result.volumes[mapped] = {key: copy.deepcopy(value) for key, value in options.items() if key == "labels"}
        for name, spec in services.items():
            compiled = copy.deepcopy(spec)
            compiled["depends_on"] = {service_names[dep]: value for dep, value in spec["depends_on"].items()}
            for mount in compiled["volumes"]:
                if mount["type"] == "volume":
                    mount["source"] = volume_names[mount["source"]]
            if name == main_service:
                for agent in selected:
                    main = copy.deepcopy(compiled)
                    main["networks"] = {agent_networks[agent]: {"aliases": aliases[name]}}
                    result.main_by_agent[agent] = main
                continue
            compiled["networks"] = {agent_networks[agent]: {"aliases": aliases[name]} for agent in selected}
            result.services[service_names[name]] = compiled
        for agent in selected:
            result.aliases_by_agent[agent] = list(dict.fromkeys(alias for names in aliases.values() for alias in names))
            result.service_names_by_agent[agent] = list(service_names.values())
            result.group_ids[agent] = group
    return result
