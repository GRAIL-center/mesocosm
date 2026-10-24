"""Exact-byte bundle anchors and explicit event-link coverage.

An anchor must be retained outside the bundle in storage the auditor trusts. This
module does not provide an external witness service or authenticate that storage.
A matching anchor establishes byte preservation, not truthful or complete capture.
"""

from __future__ import annotations

import hashlib
import json
import os
import pathlib
import stat
import time
from collections import Counter, deque
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictInt, model_validator

from mesocosm.core.records import Event


class FileDigest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    size: StrictInt = Field(ge=0)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


def _tree_digest(files: dict[str, FileDigest]) -> str:
    body = {name: value.model_dump() for name, value in files.items()}
    return hashlib.sha256(json.dumps(body, sort_keys=True, separators=(",", ":"),
                                     ensure_ascii=False).encode("utf-8")).hexdigest()


class BundleAnchor(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    schema_version: Literal["mesocosm.bundle-anchor.v1"] = "mesocosm.bundle-anchor.v1"
    run_id: str = Field(min_length=1)
    created_at: float = Field(default_factory=time.time)
    files: dict[str, FileDigest]
    tree_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def valid_tree(self) -> BundleAnchor:
        if not self.files:
            raise ValueError("anchor cannot describe an empty bundle")
        for name in self.files:
            path = pathlib.PurePosixPath(name)
            if path.is_absolute() or ".." in path.parts or str(path) != name or name == ".":
                raise ValueError("anchor contains a noncanonical relative path")
        if _tree_digest(self.files) != self.tree_sha256:
            raise ValueError("anchor file inventory does not match its tree digest")
        return self


def _external_anchor(root: pathlib.Path, path: str | pathlib.Path) -> pathlib.Path:
    anchor = pathlib.Path(path).resolve()
    if anchor == root or root in anchor.parents:
        raise ValueError("anchor must be stored outside the run bundle")
    return anchor


def _inventory(root: pathlib.Path) -> dict[str, tuple[int, int, int, int]]:
    inventory = {}
    for directory, directories, files in os.walk(root, followlinks=False):
        for name in directories + files:
            path = pathlib.Path(directory) / name
            info = path.lstat()
            if stat.S_ISLNK(info.st_mode) or not (stat.S_ISDIR(info.st_mode) or stat.S_ISREG(info.st_mode)):
                raise ValueError(f"bundle contains a symlink or special file: {path.relative_to(root)}")
            if stat.S_ISREG(info.st_mode):
                inventory[path.relative_to(root).as_posix()] = (info.st_dev, info.st_ino,
                                                              info.st_size, info.st_mtime_ns)
    return inventory


def _hash_bundle(root: pathlib.Path) -> dict[str, FileDigest]:
    if not root.is_dir() or not (root / "manifest.json").is_file():
        raise ValueError(f"not a run bundle with a manifest: {root}")
    before = _inventory(root)
    files = {}
    for name in sorted(before):
        path = root / name
        digest = hashlib.sha256()
        # O_NOFOLLOW rejects a file swapped to a symlink after inventory creation.
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
        with os.fdopen(fd, "rb") as stream:
            info = os.fstat(stream.fileno())
            if (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns) != before[name]:
                raise ValueError("bundle changed while hashing; stop writers before anchoring")
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
        files[name] = FileDigest(size=before[name][2], sha256=digest.hexdigest())
    if before != _inventory(root):
        raise ValueError("bundle changed while hashing; stop writers before anchoring")
    return files


def write_anchor(bundle_path: str | pathlib.Path, anchor_path: str | pathlib.Path) -> BundleAnchor:
    """Hash a quiescent bundle and exclusively create its detached anchor.

    The caller must retain the returned anchor independently. A file outside the
    bundle alone does not prove independent custody or protect against host compromise.
    Later appended reviews or other files require a new anchor, preserving the old one.
    """
    root = pathlib.Path(bundle_path).resolve()
    target = _external_anchor(root, anchor_path)
    files = _hash_bundle(root)
    anchor = BundleAnchor(run_id=root.name, files=files, tree_sha256=_tree_digest(files))
    with target.open("x", encoding="utf-8") as stream:
        stream.write(anchor.model_dump_json(indent=2) + "\n")
        stream.flush()
        os.fsync(stream.fileno())
    return anchor


def verify_anchor(bundle_path: str | pathlib.Path, anchor_path: str | pathlib.Path) -> dict[str, Any]:
    """Compare every current file to the explicitly supplied, trusted detached anchor."""
    root = pathlib.Path(bundle_path).resolve()
    target = _external_anchor(root, anchor_path)
    anchor = BundleAnchor.model_validate_json(target.read_text(encoding="utf-8"))
    current = _hash_bundle(root)
    added = sorted(current.keys() - anchor.files.keys())
    missing = sorted(anchor.files.keys() - current.keys())
    modified = sorted(name for name in current.keys() & anchor.files.keys() if current[name] != anchor.files[name])
    return {"integrity": "verified" if not (added or missing or modified) else "failed",
            "run_id": anchor.run_id, "tree_sha256": anchor.tree_sha256,
            "added": added, "missing": missing, "modified": modified,
            "anchor_path": str(target), "trusted_anchor_required": True,
            "independent_custody_verified": False, "execution_truth_verified": False,
            "capture_completeness_verified": False}


def event_graph(events: list[Event]) -> dict[str, Any]:
    """Check explicit links, reporting unknown coverage without inventing joins."""
    counts = Counter(event.event_id for event in events if event.event_id)
    ids = set(counts)
    duplicates = sorted(event_id for event_id, count in counts.items() if count > 1)
    dangling = [{"event_id": event.event_id, "target_event_id": link.event_id, "relation": link.relation}
                for event in events for link in event.links if link.event_id not in ids]
    parents = {event.event_id: {link.event_id for link in event.links if link.event_id in ids}
               for event in events if event.event_id}
    children: dict[str, list[str]] = {event_id: [] for event_id in ids}
    degrees = {event_id: len(links) for event_id, links in parents.items()}
    for child, links in parents.items():
        for parent in links:
            children[parent].append(child)
    ready = deque(event_id for event_id, degree in degrees.items() if degree == 0)
    visited = 0
    while ready:
        visited += 1
        for child in children[ready.popleft()]:
            degrees[child] -= 1
            if degrees[child] == 0:
                ready.append(child)
    acyclic = visited == len(ids)
    identity_fields = ("agent_instance_id", "operator_id", "session_id", "policy_id")
    return {"events": len(events), "events_without_id": sum(event.event_id is None for event in events),
            "links": sum(len(event.links) for event in events), "duplicate_event_ids": duplicates,
            "dangling_links": dangling, "acyclic": acyclic, "graph_valid": not (duplicates or dangling) and acyclic,
            "declared_coverage": dict(Counter(event.link_coverage for event in events)),
            "missing_agent_identity": {field: sum(getattr(event.identity, field) is None
                                                  for event in events if event.agent_id) for field in identity_fields},
            "causality_verified": False, "capture_completeness_verified": False}
