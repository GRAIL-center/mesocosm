"""Portable, indexed research inputs and authenticated original result transfers.

Only declared input roots are copied. Task bytes are never rewritten. Input content and
original run bundles may contain deliberately supplied credentials and are sensitive.
This format authenticates consistency, not the author or the safety of executable tasks.
"""

from __future__ import annotations

import hashlib
import json
import os
import pathlib
import shutil
import stat
import tempfile
import zipfile
from typing import Any
from urllib.parse import urlsplit

import yaml

from mesocosm.adapters.harbor.task import load_task
from mesocosm.core.config import deep_merge
from mesocosm.core.evaluation import pin_task_paths
from mesocosm.core.experiment import Experiment, _inactive_patches, load_experiment
from mesocosm.core.manifest import WorldManifest, validate_manifest
from mesocosm.core.research import validate_research_tasks

SCHEMA = "mesocosm.execution.package.v1"
MAX_FILES = 20000
MAX_FILE_BYTES = 256 * 1024 * 1024
MAX_TOTAL_BYTES = 512 * 1024 * 1024
MAX_INDEX_BYTES = 8 * 1024 * 1024
MAX_ARCHIVE_BYTES = MAX_TOTAL_BYTES + MAX_INDEX_BYTES + 16 * 1024 * 1024
_ENVELOPE = "package.json"


def _json(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def _hash(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _name(value: str) -> pathlib.PurePosixPath:
    path = pathlib.PurePosixPath(value)
    if (not value or value != path.as_posix() or path.is_absolute() or ".." in path.parts
            or "\\" in value or "\x00" in value or ":" in value or path.as_posix() == "."):
        raise ValueError("unsafe package path")
    return path


def _regular(path: pathlib.Path) -> os.stat_result:
    info = path.lstat()
    if not (stat.S_ISREG(info.st_mode) or stat.S_ISDIR(info.st_mode)):
        raise ValueError("package inputs cannot contain symlinks or special files")
    if stat.S_IMODE(info.st_mode) & 0o7000:
        raise ValueError("package inputs cannot have privileged modes")
    if stat.S_ISREG(info.st_mode) and info.st_nlink != 1:
        raise ValueError("package inputs cannot contain hardlinks")
    return info


def _identity(info: os.stat_result) -> tuple[int, ...]:
    return info.st_dev, info.st_ino, info.st_mode, info.st_nlink, info.st_size, info.st_mtime_ns, info.st_ctime_ns


def _read(path: pathlib.Path, maximum: int = MAX_FILE_BYTES) -> bytes:
    before = _regular(path)
    if not stat.S_ISREG(before.st_mode) or before.st_size > maximum:
        raise ValueError("package file exceeds size limit or is not regular")
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(fd, "rb") as source:
        if _identity(os.fstat(source.fileno())) != _identity(before):
            raise ValueError("input changed while reading")
        content = source.read(maximum + 1)
        after = os.fstat(source.fileno())
    if len(content) > maximum or _identity(before) != _identity(after) or _identity(before) != _identity(path.lstat()):
        raise ValueError("input changed while reading or exceeded size limit")
    return content


def _walk(root: pathlib.Path) -> list[pathlib.Path]:
    _regular(root)
    paths = [root]
    if root.is_dir():
        for directory, dirs, files in os.walk(root, followlinks=False):
            for name in sorted(dirs + files):
                path = pathlib.Path(directory) / name
                _regular(path)
                paths.append(path)
                if len(paths) > MAX_FILES:
                    raise ValueError("package exceeds entry limit")
    return sorted(paths)


class _Inputs:
    def __init__(self, root: pathlib.Path):
        self.root = root
        self.sources: dict[str, str] = {}
        self.total = 0
        self.count = 0

    def copy(self, source: pathlib.Path) -> str:
        _regular(source)
        key = str(source.resolve())
        if key in self.sources:
            return self.sources[key]
        paths = _walk(source)
        before = {path: _identity(path.lstat()) for path in paths}
        # Keep basenames: skill front matter and default task names depend on them.
        relative = f"inputs/{len(self.sources):04d}/{source.name}"
        _name(relative)
        target = self.root / relative
        for path in paths:
            info = _regular(path)
            self.count += 1
            if self.count > MAX_FILES:
                raise ValueError("package exceeds entry limit")
            destination = target / path.relative_to(source)
            if stat.S_ISDIR(info.st_mode):
                destination.mkdir(parents=True, exist_ok=True)
            else:
                content = _read(path)
                self.total += len(content)
                if self.total > MAX_TOTAL_BYTES:
                    raise ValueError("package exceeds total size limit")
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_bytes(content)
                destination.chmod(stat.S_IMODE(info.st_mode))
        if before != {path: _identity(path.lstat()) for path in _walk(source)}:
            raise ValueError("input changed while copying")
        for path in reversed(paths):
            if path.is_dir():
                (target / path.relative_to(source)).chmod(stat.S_IMODE(path.lstat().st_mode))
        self.sources[key] = relative
        return relative


def _yaml(path: pathlib.Path) -> dict[str, Any]:
    data = yaml.safe_load(_read(path, MAX_INDEX_BYTES))
    if not isinstance(data, dict):
        raise ValueError("submission configuration must be a YAML mapping")
    return data


def _urls(world: WorldManifest) -> None:
    urls = [model.upstream for model in world.catalog().values() if model.upstream]
    urls.extend(server.url for agent in world.agents for server in agent.mcp.values() if server.type == "remote")
    for value in urls:
        parsed = urlsplit(value)
        if parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValueError("remote submission URLs cannot contain userinfo, query credentials or fragments")


def _profile(world: WorldManifest) -> None:
    if world.profile != "research" or len(world.agents) < 3:
        raise ValueError("remote submission requires research profile with at least three agents")
    if world.mode != "comparison" or world.stream.dir or not world.tasks:
        raise ValueError("remote submission requires fixed comparison tasks; persistent streams are unsupported")
    if world.containment != "required":
        raise ValueError("remote submission requires verified containment")
    _urls(world)


def _references(data: dict[str, Any]) -> list[tuple[dict[str, Any] | list[Any], str | int]]:
    references: list[tuple[dict[str, Any] | list[Any], str | int]] = [
        (task, "path") for task in data.get("tasks", []) if task.get("path")]
    services = data.get("services") or {}
    wiki = services.get("wiki") or {}
    if wiki.get("docs"):
        references.append((wiki, "docs"))
    if (data.get("internet") or {}).get("replay_file"):
        references.append((data["internet"], "replay_file"))
    for agent in data.get("agents", []):
        references.extend((agent["skills"], index) for index in range(len(agent.get("skills", []))))
    probes = ((data.get("observe") or {}).get("capture") or {}).get("probes", [])
    references.extend((probes, index) for index in range(len(probes)))
    return references


def _get(container: dict[str, Any] | list[Any], key: str | int) -> Any:
    return container[key] if isinstance(container, dict) else container[int(key)]  # type: ignore[index]


def _set(container: dict[str, Any] | list[Any], key: str | int, value: Any) -> None:
    if isinstance(container, dict):
        container[str(key)] = value
    else:
        container[int(key)] = value


def _normalize(raw: dict[str, Any], base: pathlib.Path, inputs: _Inputs,
               stripped: set[str], prefix: str = "") -> dict[str, Any]:
    world = WorldManifest.model_validate(raw)
    _profile(world)
    data = world.model_dump(mode="json")
    pin_task_paths(data, base, required=True)
    for task in data["tasks"]:
        path = base / task["path"]
        loaded = load_task(path, dataset=task.get("dataset"), name=task.get("name"))
        validate_research_tasks(world, [loaded])
        task["name"] = loaded.name
    for key in ("api_key", "upstream_key", "url", "agent_url", "run_label"):
        if key in (raw.get("gateway") or {}):
            stripped.add(f"{prefix}gateway.{key}")
        data["gateway"].pop(key, None)
    data["gateway"]["url"] = "http://gateway.invalid"
    for index, agent in enumerate(data["agents"]):
        if (raw.get("agents") or [])[index].get("url") is not None:
            stripped.add(f"{prefix}agents.{agent['id']}.url")
        agent.pop("url", None)
    for container, reference_key in _references(data):
        _set(container, reference_key, inputs.copy(base / _get(container, reference_key)))
    return data


def _write_yaml(path: pathlib.Path, data: dict[str, Any]) -> None:
    path.write_text(yaml.safe_dump(data, sort_keys=True), encoding="utf-8")
    path.chmod(0o600)


def _condition_patch(original: dict[str, Any], normalized: dict[str, Any]) -> dict[str, Any]:
    """Retain explicit condition keys/null deletes, relocating only their effective values."""
    result: dict[str, Any] = {}
    for key, value in original.items():
        if key not in normalized:
            continue  # Worker-owned connection fields were stripped.
        if value is None:
            result[key] = None
        elif isinstance(value, dict) and isinstance(normalized[key], dict):
            nested = _condition_patch(value, normalized[key])
            if nested:
                result[key] = nested
        else:
            result[key] = normalized[key]
    return result


def _index(root: pathlib.Path) -> dict[str, dict[str, Any]]:
    entries: dict[str, dict[str, Any]] = {}
    total = 0
    for path in _walk(root):
        if path == root:
            continue
        name = path.relative_to(root).as_posix()
        _name(name)
        info = _regular(path)
        entry: dict[str, Any] = {"type": "directory" if path.is_dir() else "file",
                                 "mode": stat.S_IMODE(info.st_mode)}
        if not path.is_dir():
            content = _read(path)
            total += len(content)
            if total > MAX_TOTAL_BYTES:
                raise ValueError("package exceeds total size limit")
            entry.update(size=len(content), sha256=_hash(content))
        entries[name] = entry
    return entries


def _archive(root: pathlib.Path, destination: pathlib.Path, metadata: dict[str, Any]) -> dict[str, Any]:
    if destination.exists() or destination.is_symlink():
        raise FileExistsError("package destination must be new")
    entries = _index(root)
    body = {"schema_version": SCHEMA, **metadata, "files": entries}
    body["digest"] = _hash(_json(body))
    envelope = _json(body)
    if len(envelope) > MAX_INDEX_BYTES:
        raise ValueError("package index exceeds size limit")
    created = False
    try:
        with destination.open("xb") as target:
            created = True
            destination.chmod(0o600)
            with zipfile.ZipFile(target, "w", compression=zipfile.ZIP_STORED) as archive:
                index = zipfile.ZipInfo(_ENVELOPE)
                index.create_system = 3
                index.external_attr = (stat.S_IFREG | 0o600) << 16
                archive.writestr(index, envelope)
                for name, entry in entries.items():
                    content = b"" if entry["type"] == "directory" else _read(root / name)
                    if entry["type"] == "file" and _hash(content) != entry["sha256"]:
                        raise ValueError("input changed while archiving")
                    item = zipfile.ZipInfo(name + ("/" if entry["type"] == "directory" else ""))
                    item.create_system = 3
                    kind = stat.S_IFDIR if entry["type"] == "directory" else stat.S_IFREG
                    item.external_attr = (kind | entry["mode"]) << 16
                    archive.writestr(item, content)
        if destination.stat().st_size > MAX_ARCHIVE_BYTES:
            raise ValueError("archive exceeds size limit")
        return body
    except BaseException:
        if created:
            destination.unlink(missing_ok=True)
        raise


def pack_submission(path: pathlib.Path, destination: pathlib.Path, *, kind: str = "world") -> dict[str, Any]:
    """Copy explicitly referenced inputs and sanitize worker-owned connection settings."""
    if kind not in ("world", "experiment"):
        raise ValueError("submission kind must be world or experiment")
    with tempfile.TemporaryDirectory(prefix="mesocosm-package-") as temporary:
        root = pathlib.Path(temporary)
        inputs = _Inputs(root)
        stripped: set[str] = set()
        if kind == "world":
            normalized = _normalize(_yaml(path), path.parent, inputs, stripped)
            _write_yaml(root / "world.yaml", normalized)
            entrypoint = "world.yaml"
        else:
            experiment = load_experiment(path)
            base_path = path.parent / experiment.base
            raw_base = _yaml(base_path)
            normalized = _normalize(raw_base, base_path.parent, inputs, stripped, "base.")
            _write_yaml(root / "world.yaml", normalized)
            spec = experiment.model_dump(mode="json")
            spec["base"] = "world.yaml"
            for index, condition in enumerate(experiment.conditions):
                merged = deep_merge(raw_base, condition.patch)
                errors = _inactive_patches(condition, WorldManifest.model_validate(merged))
                if errors:
                    raise ValueError("; ".join(errors))
                patch = _normalize(merged, base_path.parent, inputs, stripped, f"conditions.{condition.name}.")
                patch.pop("name", None)
                patch["gateway"].pop("url", None)
                spec["conditions"][index]["patch"] = _condition_patch(condition.patch, patch)
            _write_yaml(root / "experiment.yaml", spec)
            entrypoint = "experiment.yaml"
        _validate(root, kind, entrypoint)
        return _archive(root, destination, {"kind": kind, "entrypoint": entrypoint,
                                           "stripped_fields": sorted(stripped)})


def _confined(root: pathlib.Path, reference: str) -> pathlib.Path:
    relative = _name(reference)
    path = root.joinpath(*relative.parts)
    if not path.resolve().is_relative_to(root.resolve()):
        raise ValueError("submission path escapes package")
    _regular(path)
    return path


def _validate_world(root: pathlib.Path, raw: dict[str, Any]) -> None:
    world = WorldManifest.model_validate(raw)
    _profile(world)
    gateway = raw.get("gateway") or {}
    if (gateway.get("url") != "http://gateway.invalid"
            or any(key in gateway for key in ("api_key", "upstream_key", "agent_url", "run_label"))
            or any("url" in agent for agent in raw["agents"])):
        raise ValueError("submission contains unbound worker connection settings")
    for container, key in _references(raw):
        _confined(root, _get(container, key))
    if any(not task.path for task in world.tasks):
        raise ValueError("all submission tasks must be materialized")
    validate_research_tasks(world, [load_task(root / str(task.path)) for task in world.tasks])
    # validate_manifest owns condition, model, skill and channel semantics, without Docker.
    with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", dir=root, delete=False) as check:
        check.write(yaml.safe_dump(raw))
        check_path = pathlib.Path(check.name)
    try:
        report = validate_manifest(check_path)
        if not report.ok:
            raise ValueError("invalid remote world: " + "; ".join(report.errors))
    finally:
        check_path.unlink()


def _validate(root: pathlib.Path, kind: str, entrypoint: str) -> None:
    if kind == "world" and entrypoint == "world.yaml":
        _validate_world(root, _yaml(root / entrypoint))
    elif kind == "experiment" and entrypoint == "experiment.yaml":
        spec = Experiment.model_validate(_yaml(root / entrypoint))
        base = _yaml(_confined(root, spec.base))
        _validate_world(root, base)
        for condition in spec.conditions:
            if any(key in (condition.patch.get("gateway") or {})
                   for key in ("api_key", "upstream_key", "url", "agent_url", "run_label")):
                raise ValueError("condition cannot override worker connection settings")
            merged = deep_merge(base, condition.patch)
            _validate_world(root, merged)
            errors = _inactive_patches(condition, WorldManifest.model_validate(merged))
            if errors:
                raise ValueError("; ".join(errors))
    else:
        raise ValueError("invalid submission kind or entrypoint")


def _unpack(archive_path: pathlib.Path, destination: pathlib.Path, *, results: bool) -> dict[str, Any]:
    if destination.exists() or destination.is_symlink():
        raise FileExistsError("extraction destination must be new")
    info = _regular(archive_path)
    if info.st_size > MAX_ARCHIVE_BYTES:
        raise ValueError("archive exceeds size limit")
    created = False
    try:
        with zipfile.ZipFile(archive_path) as archive:
            members = archive.infolist()
            if len(members) > MAX_FILES + 1 or len({member.filename for member in members}) != len(members):
                raise ValueError("duplicate or excessive archive entries")
            envelope_info = archive.getinfo(_ENVELOPE)
            if (envelope_info.file_size > MAX_INDEX_BYTES or envelope_info.compress_type != zipfile.ZIP_STORED
                    or envelope_info.flag_bits & 1
                    or stat.S_IFMT(envelope_info.external_attr >> 16) != stat.S_IFREG):
                raise ValueError("invalid package index")
            metadata = json.loads(archive.read(envelope_info))
            if not isinstance(metadata, dict) or metadata.get("schema_version") != SCHEMA:
                raise ValueError("invalid package schema")
            kind, entrypoint = metadata.get("kind"), metadata.get("entrypoint")
            if not isinstance(kind, str) or not isinstance(entrypoint, str):
                raise ValueError("invalid package kind or entrypoint")
            digest = metadata.get("digest")
            if digest != _hash(_json({key: value for key, value in metadata.items() if key != "digest"})):
                raise ValueError("package digest mismatch")
            entries = metadata.get("files")
            if not isinstance(entries, dict) or len(entries) > MAX_FILES:
                raise ValueError("invalid package index")
            if (metadata.get("kind") == "results") != results:
                raise ValueError("unexpected package kind")
            expected = {_ENVELOPE}
            total = 0
            for name, entry in entries.items():
                _name(name)
                if name == _ENVELOPE or not isinstance(entry, dict):
                    raise ValueError("invalid indexed entry")
                kind, mode = entry.get("type"), entry.get("mode")
                if kind not in ("file", "directory") or type(mode) is not int or not 0 <= mode <= 0o777:
                    raise ValueError("invalid entry type or mode")
                member_name = name + ("/" if kind == "directory" else "")
                member = archive.getinfo(member_name)
                expected.add(member_name)
                zip_mode = member.external_attr >> 16
                if (member.compress_type != zipfile.ZIP_STORED or member.flag_bits & 1
                        or stat.S_IFMT(zip_mode) != (stat.S_IFDIR if kind == "directory" else stat.S_IFREG)
                        or stat.S_IMODE(zip_mode) != mode):
                    raise ValueError("unsupported archive type, compression or mode")
                if kind == "file":
                    size = entry.get("size")
                    if type(size) is not int or not 0 <= size <= MAX_FILE_BYTES or member.file_size != size:
                        raise ValueError("invalid file size")
                    total += size
                    if total > MAX_TOTAL_BYTES:
                        raise ValueError("package exceeds total size limit")
                elif member.file_size:
                    raise ValueError("directory contains data")
                parent = pathlib.PurePosixPath(name).parent
                if (parent != pathlib.PurePosixPath(".")
                        and entries.get(parent.as_posix(), {}).get("type") != "directory"):
                    raise ValueError("missing parent directory or path collision")
            if {member.filename for member in members} != expected:
                raise ValueError("archive differs from complete package index")
            destination.mkdir(mode=0o700, parents=False)
            created = True
            ordered = sorted(entries.items(), key=lambda item: (len(pathlib.PurePosixPath(item[0]).parts), item[0]))
            for name, entry in ordered:
                target = destination / name
                if entry["type"] == "directory":
                    target.mkdir(mode=0o700)
                else:
                    content = archive.read(name)
                    if len(content) != entry["size"] or _hash(content) != entry.get("sha256"):
                        raise ValueError("package file hash mismatch")
                    with target.open("xb") as output:
                        output.write(content)
                    target.chmod(entry["mode"])
            if results:
                _validate_results(destination, entrypoint)
            else:
                _validate(destination, str(metadata["kind"]), entrypoint)
            for name, entry in sorted(entries.items(), reverse=True):
                if entry["type"] == "directory":
                    (destination / name).chmod(entry["mode"])
            (destination / _ENVELOPE).write_bytes(_json(metadata))
            (destination / _ENVELOPE).chmod(0o600)
            return metadata
    except BaseException as exc:
        if created:
            shutil.rmtree(destination)
        if isinstance(exc, (zipfile.BadZipFile, KeyError, TypeError, yaml.YAMLError, UnicodeError)):
            raise ValueError("malformed package") from exc
        raise


def unpack_submission(archive: pathlib.Path, destination: pathlib.Path) -> dict[str, Any]:
    return _unpack(archive, destination, results=False)


_RESULT_METADATA = ("job.json", "result.json", "experiment/experiment.json")


def _validate_results(root: pathlib.Path, entrypoint: str | None) -> None:
    if entrypoint != "result.json" or not (root / "result.json").is_file():
        raise ValueError("result package requires result.json")
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        relative = path.relative_to(root).as_posix()
        parts = pathlib.PurePosixPath(relative).parts
        if relative in _RESULT_METADATA:
            continue
        if len(parts) < 3 or parts[0] != "runs":
            raise ValueError("result export contains non-result files")
        bundle = root / "runs" / parts[1]
        if not (bundle / "manifest.json").is_file() or not (bundle / "events.jsonl").is_file():
            raise ValueError("result export contains a non-bundle directory")


def export_results(job_dir: pathlib.Path, destination: pathlib.Path) -> dict[str, Any]:
    """Export original owned bundles, excluding generated runtime and harness credentials."""
    with tempfile.TemporaryDirectory(prefix="mesocosm-results-") as temporary:
        root = pathlib.Path(temporary)
        for relative in _RESULT_METADATA:
            source = job_dir / relative
            if source.exists() or source.is_symlink():
                content = _read(source)
                target = root / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(content)
                target.chmod(0o600)
        runs = job_dir / "runs"
        if runs.exists() or runs.is_symlink():
            _regular(runs)
            for bundle in sorted(runs.iterdir()):
                _regular(bundle)
                if (not bundle.is_dir() or not (bundle / "manifest.json").is_file()
                        or not (bundle / "events.jsonl").is_file()):
                    continue
                # Reuse the strict copy path, then move the copied tree into its original run name.
                copied = _Inputs(root).copy(bundle)
                (root / "runs").mkdir(exist_ok=True)
                (root / copied).rename(root / "runs" / bundle.name)
                shutil.rmtree(root / "inputs")
        _validate_results(root, "result.json")
        return _archive(root, destination, {"kind": "results", "entrypoint": "result.json",
                                           "sensitive": True, "redacted": False})


def extract_results(archive: pathlib.Path, destination: pathlib.Path) -> dict[str, Any]:
    return _unpack(archive, destination, results=True)
