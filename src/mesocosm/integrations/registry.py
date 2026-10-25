"""Discover installed plugins without importing unselected or submitted task code.

An entry point names a zero-argument factory. Its returned provider implements API 1.
Registration is not evidence of containment, correctness, or research acceptance.
"""

from __future__ import annotations

import hashlib
import importlib
import pathlib
import re
import sys
from importlib import metadata
from typing import TYPE_CHECKING, Any, cast

if TYPE_CHECKING:
    from mesocosm.core.manifest import WorldManifest
    from mesocosm.execution.backends import BackendProvider
    from mesocosm.integrations.tasks import TaskSource

API_VERSION = 1
GROUPS = {"task_source": "mesocosm.task_sources", "execution_backend": "mesocosm.execution_backends"}
BUILTINS = {
    "task_source": {"harbor": "mesocosm.adapters.harbor.source:HarborSource"},
    "execution_backend": {"docker": "mesocosm.execution.backends:DockerProvider"},
}
_METHODS = {"task_source": ("load", "resolve", "research_errors", "fingerprint"),
            "execution_backend": ("validate", "create")}
_ID = re.compile(r"^[a-z][a-z0-9_.-]{0,63}$")
_LOADED_PACKAGES: dict[str, str] = {}


class IntegrationError(ValueError):
    """A selected integration is missing, incompatible, ambiguous, or has changed."""


def _entries(kind: str) -> list[metadata.EntryPoint]:
    return list(metadata.entry_points(group=GROUPS[kind]))


def _select(kind: str, name: str) -> tuple[str, metadata.EntryPoint | None]:
    if not _ID.fullmatch(name):
        raise IntegrationError(f"rejected_unsupported: invalid {kind} id {name!r}")
    entries = [ep for ep in _entries(kind) if ep.name == name]
    builtin = BUILTINS[kind].get(name)
    if len(entries) > 1 or (builtin and entries):
        raise IntegrationError(f"rejected_unsupported: duplicate {kind} id {name!r}; builtins cannot be overridden")
    if builtin:
        return builtin, None
    if not entries:
        raise IntegrationError(f"rejected_unsupported: {kind} {name!r} is not installed; "
                               f"install its trusted package on the client and worker")
    return entries[0].value, entries[0]


def _load(kind: str, name: str) -> tuple[Any, str, metadata.EntryPoint | None]:
    target, ep = _select(kind, name)
    try:
        if ep is not None:
            factory = ep.load()
        else:
            module, attr = target.split(":", 1)
            factory = getattr(importlib.import_module(module), attr)
        package = target.partition(":")[0].partition(".")[0]
        current_hash = _package_hash(target, ep)
        previous_hash = _LOADED_PACKAGES.setdefault(package, current_hash)
        if previous_hash != current_hash:
            raise IntegrationError(f"integration package {package!r} changed after loading; restart this process "
                                   "with the pinned installation before submitting or executing work")
        provider = factory()
    except Exception as exc:
        raise IntegrationError(f"rejected_unsupported: cannot load {kind} {name!r}: {exc}") from exc
    if type(getattr(provider, "api_version", None)) is not int or provider.api_version != API_VERSION:
        raise IntegrationError(f"rejected_unsupported: {kind} {name!r} must implement integration API {API_VERSION}")
    for method in _METHODS[kind]:
        if not callable(getattr(provider, method, None)):
            raise IntegrationError(f"rejected_unsupported: {kind} {name!r} has no {method}()")
    return provider, target, ep


def get_task_source(name: str) -> TaskSource:
    return cast("TaskSource", _load("task_source", name)[0])


def get_backend_provider(name: str) -> BackendProvider:
    return cast("BackendProvider", _load("execution_backend", name)[0])


def _package_hash(target: str, ep: metadata.EntryPoint | None) -> str:
    """Hash installed package payloads, including editable package source and resources.

    Paths in the digest are package-relative. Installation location and bytecode do
    not affect identity; native binaries do. Dependency environments need their own
    lockfile and are deliberately not claimed to be captured by this digest.
    """
    files: dict[str, pathlib.Path] = {}
    if ep is not None and ep.dist is not None:
        for entry in ep.dist.files or ():
            if any(p.endswith((".dist-info", ".egg-info")) for p in entry.parts) or ".." in entry.parts:
                continue
            if entry.suffix == ".pth" or entry.name.startswith("__editable__"):
                continue  # installation locators contain host-specific paths, not plugin payloads
            path = pathlib.Path(str(ep.dist.locate_file(entry)))
            if path.is_file() and path.suffix not in (".pyc", ".pyo") and "__pycache__" not in path.parts:
                files[str(entry)] = path
    top_name = target.partition(":")[0].partition(".")[0]
    module = sys.modules.get(top_name)
    if module is not None:
        roots = list(getattr(module, "__path__", ()))
        if roots:
            for root in roots:
                base = pathlib.Path(root)
                for path in sorted(base.rglob("*")):
                    if (path.is_file() and not path.is_symlink() and "__pycache__" not in path.parts
                            and path.suffix not in (".pyc", ".pyo")):
                        files[f"{top_name}/{path.relative_to(base).as_posix()}"] = path
        elif isinstance(getattr(module, "__file__", None), str):
            path = pathlib.Path(cast(str, module.__file__))
            files[path.name] = path
    if not files:
        raise IntegrationError(f"rejected_unsupported: cannot fingerprint integration {target!r}")
    digest = hashlib.sha256()
    for name, path in sorted(files.items()):
        digest.update(name.encode())
        digest.update(b"\0")
        digest.update(hashlib.sha256(path.read_bytes()).digest())
    return digest.hexdigest()


def _identity(kind: str, name: str) -> dict[str, Any]:
    _, target, ep = _load(kind, name)
    try:
        distribution = "mesocosm" if ep is None else str(ep.dist.metadata["Name"]) if ep.dist else ""
        package_version = metadata.version("mesocosm") if ep is None else ep.dist.version if ep.dist else ""
    except metadata.PackageNotFoundError:
        distribution, package_version = "mesocosm", "0.0.0+local"
    if not distribution or not package_version:
        raise IntegrationError(f"rejected_unsupported: missing distribution metadata for {kind} {name!r}")
    return {"kind": kind, "id": name, "api_version": API_VERSION, "distribution": distribution,
            "version": package_version, "entry_point": target, "code_sha256": _package_hash(target, ep)}


def task_source_identity(name: str) -> dict[str, Any]:
    return _identity("task_source", name)


def backend_identity(name: str) -> dict[str, Any]:
    return _identity("execution_backend", name)


def integration_inventory() -> dict[str, list[dict[str, Any]]]:
    """List metadata only: a broken, unselected plugin cannot prevent inspection."""
    result: dict[str, list[dict[str, Any]]] = {}
    for kind in GROUPS:
        entries = _entries(kind)
        names = sorted(set(BUILTINS[kind]) | {ep.name for ep in entries})
        rows = []
        for name in names:
            matches = [ep for ep in entries if ep.name == name]
            builtin = BUILTINS[kind].get(name)
            rows.append({"id": name, "builtin": builtin is not None,
                         "status": "conflict" if len(matches) > 1 or (builtin and matches) else "available",
                         "entry_points": ([builtin] if builtin else []) + [ep.value for ep in matches],
                         "verification": "registration only; consult adapter evidence"})
        result[kind] = rows
    return result


def integration_snapshot(world: WorldManifest) -> dict[str, dict[str, Any]]:
    names = {task.source for task in world.tasks}
    if world.stream.dir:
        names.add(world.stream.source)
    snapshot = {f"task_source:{name}": task_source_identity(name) for name in sorted(names)}
    name = world.execution.backend
    snapshot[f"execution_backend:{name}"] = backend_identity(name)
    return snapshot


def validate_integration_lock(world: WorldManifest) -> None:
    if world.integration_lock and world.integration_lock != integration_snapshot(world):
        raise IntegrationError("rejected_unsupported: integration_lock differs from installed plugins; "
                               "restore the pinned packages or explicitly create a new input package")


def validate_integrations(world: WorldManifest, base: pathlib.Path | None = None, *,
                          require_backend: str | None = None, resolve_datasets: bool = False) -> None:
    """Check selected providers before acquiring execution resources."""
    if require_backend is not None and world.execution.backend != require_backend:
        raise IntegrationError(f"rejected_unsupported: this command runs {require_backend!r}, "
                               f"but execution.backend is {world.execution.backend!r}; "
                               "submit to a worker configured with the selected backend")
    provider = get_backend_provider(world.execution.backend)
    errors = provider.validate(world)
    if not isinstance(errors, list) or any(not isinstance(error, str) for error in errors):
        raise IntegrationError("rejected_unsupported: backend validate() must return a list of error strings")
    if errors:
        raise IntegrationError("\n".join(errors))
    validate_integration_lock(world)
    for spec in world.tasks:
        get_task_source(spec.source)
        if base is None:
            continue
        if spec.path:
            paths = [base / spec.path]
        elif spec.dataset and resolve_datasets:
            from mesocosm.core.run import _tasks_root_dirs
            from mesocosm.integrations.tasks import resolve_task_paths

            paths = resolve_task_paths(spec.source, spec.dataset,
                                       roots=_tasks_root_dirs(base / "world.yaml"), limit=spec.limit)
        elif spec.dataset:
            continue
        else:
            raise IntegrationError("tasks require a path or dataset")
        for path in paths:
            from mesocosm.integrations.tasks import load_task

            task = load_task(spec.source, path, dataset=spec.dataset, name=spec.name)
            if world.profile == "research":
                from mesocosm.core.research import research_task_errors

                errors = research_task_errors(task)
                if errors:
                    raise IntegrationError("\n".join(errors))
            if world.execution.backend == "docker" and task.execution_kind != "harbor":
                raise IntegrationError(f"rejected_unsupported: Docker requires Harbor execution inputs, "
                                       f"task {task.name!r} declares {task.execution_kind!r}; select its backend")
    if world.stream.dir:
        get_task_source(world.stream.source)
