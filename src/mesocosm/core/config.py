"""Configuration patches shared by experiments and snapshot forks."""

from __future__ import annotations

import copy
import pathlib
from typing import Any


def deep_merge(base: Any, patch: Any) -> Any:
    """Merge mappings, delete keys patched with None, replace other values; never alias inputs."""
    if not isinstance(base, dict) or not isinstance(patch, dict):
        return copy.deepcopy(patch)
    out = copy.deepcopy(base)
    for key, value in patch.items():
        if value is None:
            out.pop(key, None)
        else:
            out[key] = deep_merge(out.get(key), value)
    return out


def absolutize_manifest(manifest: dict[str, Any], base_dir: pathlib.Path) -> None:
    """Relocate a manifest copy; paths introduced by patches also use the base manifest's directory."""
    for task in manifest.get("tasks") or []:
        if isinstance(task, dict) and task.get("path"):
            task["path"] = str((base_dir / task["path"]).resolve())
    services = manifest.get("services") or {}
    if services.get("seed"):
        services["seed"] = str((base_dir / services["seed"]).resolve())
    wiki = services.get("wiki") or {}
    if wiki.get("docs"):
        wiki["docs"] = str((base_dir / wiki["docs"]).resolve())
    if (manifest.get("stream") or {}).get("dir"):
        manifest["stream"]["dir"] = str((base_dir / manifest["stream"]["dir"]).resolve())
    if (manifest.get("internet") or {}).get("replay_file"):
        manifest["internet"]["replay_file"] = str((base_dir / manifest["internet"]["replay_file"]).resolve())
    for agent in manifest.get("agents") or []:
        if isinstance(agent, dict) and agent.get("skills"):
            agent["skills"] = [str((base_dir / s).resolve()) for s in agent["skills"]]
    capture = (manifest.get("observe") or {}).get("capture") or {}
    if capture.get("probes"):
        capture["probes"] = [str((base_dir / s).resolve()) for s in capture["probes"]]
