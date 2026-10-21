"""Content-addressed model artifacts, hashed in full rather than by file metadata."""

from __future__ import annotations

import hashlib
import importlib
import json
import pathlib

from mesocosm.learning.config import PolicyIdentity, TrainingSpec, policy_id


def checkpoint_sha256(path: str | pathlib.Path, *, adapter: bool = False) -> str:
    root = pathlib.Path(path)
    if adapter:
        files = [root / "adapter_config.json", root / "adapter_model.safetensors"]
    else:
        files = sorted(p for p in root.rglob("*") if p.is_file() and
                       (p.suffix in {".safetensors", ".json", ".model", ".jinja"} or
                        p.name in {"merges.txt", "vocab.txt"}) and ".cache" not in p.relative_to(root).parts)
        if not (root / "config.json").is_file() or not any(p.suffix == ".safetensors" for p in files):
            raise ValueError("base checkpoint requires config.json and full safetensors weights")
    digest = hashlib.sha256()
    for path in files:
        if not path.is_file():
            raise ValueError(f"checkpoint file is missing: {path}")
        # Length-prefix names and sizes so different file boundaries cannot share a digest.
        name = path.relative_to(root).as_posix().encode()
        digest.update(len(name).to_bytes(8, "big"))
        digest.update(name)
        digest.update(path.stat().st_size.to_bytes(8, "big"))
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(8 * 1024 * 1024), b""):
                digest.update(chunk)
    return digest.hexdigest()


def resolve_base(spec: TrainingSpec) -> pathlib.Path:
    local = pathlib.Path(spec.base_model).expanduser()
    if local.is_dir():
        return local.resolve()
    hub = importlib.import_module("huggingface_hub")
    return pathlib.Path(hub.snapshot_download(repo_id=spec.base_model, revision=spec.base_revision,
                                             allow_patterns=["*.json", "*.safetensors", "*.model", "*.jinja",
                                                             "merges.txt", "vocab.txt"]))


def initial_policy(spec: TrainingSpec) -> PolicyIdentity:
    digest = checkpoint_sha256(resolve_base(spec))
    return PolicyIdentity(id=policy_id(spec.base_model, spec.base_revision, digest, None, spec.dtype),
                          model=spec.initial_model, base_model=spec.base_model,
                          base_revision=spec.base_revision, base_sha256=digest, dtype=spec.dtype)


def write_json(path: pathlib.Path, value: object) -> None:
    """Persist a complete receipt atomically; interrupted writes never look successful."""
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2, ensure_ascii=False, allow_nan=False) + "\n")
    temporary.replace(path)
