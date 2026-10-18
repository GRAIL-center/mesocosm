"""Snapshot manager (M6, ADR-0015): save the declared parts of a world, restore them, fork a new run.

A snapshot captures what the platform owns and can copy: the agents' workspaces, the world services'
data (board, pastes, repositories), the shared directory, the task queue state and the manifest.
It declares what it does not capture (harness state inside the agent containers, in-flight model
requests, gateway counters, container filesystems outside those paths). Restoration is verified by
hashing what came back; a fork is a new run from a restored snapshot under a patched manifest, with
its lineage recorded in the new bundle.
"""

from __future__ import annotations

import hashlib
import json
import pathlib
import secrets
import shutil
import tarfile
import time
from typing import Any, Protocol

import yaml

from mesocosm.core.records import RunBundle
from mesocosm.core.scenario import deep_merge
from mesocosm.core.workspace import reset_workspace

NOT_CAPTURED = [
    "harness state inside the agent containers (OpenCode sessions and storage)",
    "model requests in flight and the harness's current turn",
    "gateway counters (budget usage); budgets are re-applied from the manifest on restore",
    "container filesystems outside the workspaces, the services data and the shared directory",
]


class WorldState(Protocol):
    """The sandbox pieces a snapshot copies out and back in."""
    async def export_services(self, dest: pathlib.Path) -> None: ...
    async def import_services(self, src: pathlib.Path) -> None: ...
    async def export_shared(self, dest: pathlib.Path) -> None: ...
    async def import_shared(self, src: pathlib.Path) -> None: ...


class LocalWorldState:
    """Plain directories standing in for the containers (tests, host-only runs)."""

    def __init__(self, services_dir: pathlib.Path, shared_dir: pathlib.Path | None = None):
        self.services_dir, self.shared_dir = services_dir, shared_dir

    async def export_services(self, dest: pathlib.Path) -> None:
        _copy_tree(self.services_dir, dest, SERVICES_ITEMS)

    async def import_services(self, src: pathlib.Path) -> None:
        _copy_tree(src, self.services_dir, SERVICES_ITEMS, replace=True)

    async def export_shared(self, dest: pathlib.Path) -> None:
        if self.shared_dir and self.shared_dir.is_dir():
            shutil.copytree(self.shared_dir, dest, dirs_exist_ok=True)

    async def import_shared(self, src: pathlib.Path) -> None:
        if self.shared_dir and src.is_dir():
            reset_workspace(self.shared_dir)
            shutil.copytree(src, self.shared_dir, dirs_exist_ok=True)


SERVICES_ITEMS = ("board.json", "pastes.json", "git")


def _copy_tree(src: pathlib.Path, dest: pathlib.Path, items: tuple[str, ...], replace: bool = False) -> None:
    dest.mkdir(parents=True, exist_ok=True)
    for item in items:
        source, target = src / item, dest / item
        if not source.exists():
            continue
        if target.exists() and replace:
            shutil.rmtree(target) if target.is_dir() else target.unlink()
        if source.is_dir():
            shutil.copytree(source, target, dirs_exist_ok=True)
        else:
            shutil.copyfile(source, target)


def _sha256_file(path: pathlib.Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def hash_tree(root: pathlib.Path) -> dict[str, str]:
    if not root.is_dir():
        return {}
    return {str(p.relative_to(root)): _sha256_file(p) for p in sorted(root.rglob("*")) if p.is_file()}


def _tar_dir(src: pathlib.Path, target: pathlib.Path) -> int:
    with tarfile.open(target, "w:gz") as tar:
        n = 0
        for p in sorted(src.rglob("*")):
            tar.add(p, arcname=str(p.relative_to(src)), recursive=False)
            n += p.is_file()
    return n


def queue_state(bundle: RunBundle) -> dict[str, Any]:
    """Which tasks were loaded, assigned and finished, from the bundle's events."""
    loaded: dict[str, dict[str, Any]] = {}
    assigned: dict[str, str] = {}
    finished: dict[str, float | None] = {}
    for ev in bundle.events():
        if ev.kind == "task.loaded":
            loaded[ev.data["task"]] = {"path": (ev.data.get("provenance") or {}).get("path"),
                                       "source": ev.data.get("source", "manifest")}
        elif ev.kind == "task.assigned":
            assigned[ev.data["task"]] = ev.agent_id or ""
        elif ev.kind == "verifier.reward":
            finished[f"{ev.agent_id}/{ev.data.get('task')}"] = ev.data.get("reward")
    pending = [t for t in loaded if t not in assigned and not any(k.endswith("/" + t) for k in finished)]
    return {"loaded": loaded, "assigned": assigned, "finished": finished, "pending": pending}


async def create_snapshot(bundle_root: str | pathlib.Path, workspace_root: pathlib.Path, world: WorldState | None,
                          out_root: pathlib.Path, note: str | None = None) -> dict[str, Any]:
    bundle = RunBundle.open(bundle_root)
    manifest_json = bundle.manifest()
    agents = [a["id"] for a in (manifest_json.get("manifest") or {}).get("agents") or []]
    snapshot_id = f"snap_{int(time.time() * 1000):013d}_{secrets.token_hex(3)}"
    root = out_root / snapshot_id
    (root / "workspaces").mkdir(parents=True)
    files: dict[str, str] = {}
    captured: list[dict[str, Any]] = []
    for agent_id in agents:
        ws = workspace_root / agent_id
        if not ws.is_dir():
            continue
        n = _tar_dir(ws, root / "workspaces" / f"{agent_id}.tar.gz")
        files.update({f"workspaces/{agent_id}/{k}": v for k, v in hash_tree(ws).items()})
        captured.append({"component": "workspace", "agent_id": agent_id, "files": n})
    if world is not None:
        await world.export_services(root / "services")
        files.update({f"services/{k}": v for k, v in hash_tree(root / "services").items()})
        captured.append({"component": "services", "items": sorted(p.name for p in (root / "services").iterdir())
                         if (root / "services").is_dir() else []})
        await world.export_shared(root / "shared")
        if (root / "shared").is_dir():
            files.update({f"shared/{k}": v for k, v in hash_tree(root / "shared").items()})
            captured.append({"component": "shared_dir", "files": len(hash_tree(root / "shared"))})
    queue = queue_state(bundle)
    (root / "queue.json").write_text(json.dumps(queue, indent=1))
    (root / "manifest.json").write_text(json.dumps(manifest_json, indent=1, ensure_ascii=False))
    captured.append({"component": "task_queue", "pending": len(queue["pending"]), "finished": len(queue["finished"])})
    captured.append({"component": "manifest", "digest": manifest_json.get("digest")})
    (root / "files.json").write_text(json.dumps(files, indent=1))
    coverage = {"snapshot_id": snapshot_id, "parent_run_id": bundle.run_id, "created": time.time(), "note": note,
                "model": (manifest_json.get("manifest") or {}).get("model"), "captured": captured,
                "not_captured": NOT_CAPTURED, "files": len(files)}
    (root / "coverage.json").write_text(json.dumps(coverage, indent=1, ensure_ascii=False))
    return {**coverage, "path": str(root)}


async def restore_snapshot(snapshot_root: str | pathlib.Path, workspace_root: pathlib.Path,
                           world: WorldState | None) -> dict[str, Any]:
    root = pathlib.Path(snapshot_root)
    expected: dict[str, str] = json.loads((root / "files.json").read_text())
    restored: dict[str, int] = {}
    for tar_path in sorted((root / "workspaces").glob("*.tar.gz")):
        agent_id = tar_path.name[: -len(".tar.gz")]
        ws = workspace_root / agent_id
        reset_workspace(ws)
        with tarfile.open(tar_path, "r:gz") as tar:
            tar.extractall(ws, filter="data")
        restored[f"workspace:{agent_id}"] = len(hash_tree(ws))
    actual: dict[str, str] = {}
    for agent_id in [p.name[: -len(".tar.gz")] for p in (root / "workspaces").glob("*.tar.gz")]:
        actual.update({f"workspaces/{agent_id}/{k}": v for k, v in hash_tree(workspace_root / agent_id).items()})
    if world is not None:
        if (root / "services").is_dir():
            await world.import_services(root / "services")
            check = root / "_verify_services"
            shutil.rmtree(check, ignore_errors=True)
            await world.export_services(check)
            actual.update({f"services/{k}": v for k, v in hash_tree(check).items()})
            shutil.rmtree(check, ignore_errors=True)
            restored["services"] = sum(1 for k in expected if k.startswith("services/"))
        if (root / "shared").is_dir():
            await world.import_shared(root / "shared")
            check = root / "_verify_shared"
            shutil.rmtree(check, ignore_errors=True)
            await world.export_shared(check)
            actual.update({f"shared/{k}": v for k, v in hash_tree(check).items()})
            shutil.rmtree(check, ignore_errors=True)
            restored["shared_dir"] = sum(1 for k in expected if k.startswith("shared/"))
    mismatches = sorted(k for k in expected if actual.get(k) != expected[k])
    return {"snapshot_id": root.name, "ok": not mismatches, "restored": restored, "verified_files": len(expected),
            "mismatches": mismatches[:50], "not_captured": NOT_CAPTURED}


def fork_manifest(snapshot_root: str | pathlib.Path, patch: dict[str, Any], out_path: pathlib.Path,
                  name_suffix: str = "fork") -> dict[str, Any]:
    """The snapshot's manifest with `patch` merged in, written for a new run; relative paths are kept
    valid by resolving them against the original manifest's directory."""
    root = pathlib.Path(snapshot_root)
    stored = json.loads((root / "manifest.json").read_text())
    manifest = deep_merge(stored["manifest"], patch)
    base_dir = pathlib.Path(stored["path"]).parent if stored.get("path") else None
    if base_dir is not None:
        for task in manifest.get("tasks") or []:
            if task.get("path") and not pathlib.Path(task["path"]).is_absolute():
                task["path"] = str((base_dir / task["path"]).resolve())
        wiki = (manifest.get("services") or {}).get("wiki") or {}
        if wiki.get("docs") and not pathlib.Path(wiki["docs"]).is_absolute():
            wiki["docs"] = str((base_dir / wiki["docs"]).resolve())
        sites = (manifest.get("services") or {}).get("sites")
        if sites and not pathlib.Path(sites).is_absolute():
            manifest["services"]["sites"] = str((base_dir / sites).resolve())
        if (manifest.get("stream") or {}).get("dir") and not pathlib.Path(manifest["stream"]["dir"]).is_absolute():
            manifest["stream"]["dir"] = str((base_dir / manifest["stream"]["dir"]).resolve())
    manifest["name"] = f"{manifest['name']}-{name_suffix}"[:63]
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(yaml.safe_dump(manifest, sort_keys=False))
    coverage = json.loads((root / "coverage.json").read_text())
    lineage = {"parent_run_id": coverage["parent_run_id"], "snapshot_id": root.name, "patch": patch,
               "manifest": str(out_path)}
    return lineage


def read_tar_names(path: pathlib.Path) -> list[str]:
    with tarfile.open(path, "r:gz") as tar:
        return sorted(m.name for m in tar.getmembers() if m.isfile())


