"""Installed execution providers and their explicit operator configuration.

A backend owns a whole world, including its agent interfaces, native evaluator, evidence,
interruption and cleanup. It is trusted operator-installed code, never code from a task package.
"""

from __future__ import annotations

import pathlib
import re
from dataclasses import dataclass, field
from typing import Any, Protocol

from mesocosm.core.manifest import WorldManifest
from mesocosm.execution.protocol import ExecutionBackend


@dataclass(frozen=True)
class BackendContext:
    """Worker-local options; experiment ``execution.options`` remain in the submitted world."""

    root: pathlib.Path
    options: dict[str, Any] = field(default_factory=dict)


class BackendProvider(Protocol):
    api_version: int

    def validate(self, world: WorldManifest) -> list[str]:
        """Return unsupported world features before execution; an empty list accepts the contract."""
        ...

    def create(self, context: BackendContext) -> ExecutionBackend: ...


class DockerProvider:
    """The existing Harbor/OpenCode implementation, with its original enforcement boundaries."""

    api_version = 1

    def validate(self, world: WorldManifest) -> list[str]:
        errors = []
        if world.execution.backend != "docker":
            errors.append("rejected_unsupported: Docker execution requires execution.backend: docker")
        if world.execution.options:
            errors.append("rejected_unsupported: Docker execution.options are unsupported; use worker operator flags")
        for agent in world.agents:
            if agent.harness != "opencode":
                errors.append(f"rejected_unsupported: agent {agent.id!r} harness {agent.harness!r}; "
                              "Docker backend supports opencode")
        return errors

    def create(self, context: BackendContext) -> ExecutionBackend:
        from mesocosm.execution.docker import DockerBackend

        options = dict(context.options)
        known = {"repo_root", "vllm_host", "vllm_port", "model_key_envs", "verifier_network",
                 "allow_unenforced_resources"}
        if set(options) - known:
            raise ValueError(f"unknown Docker operator options: {sorted(set(options) - known)}")
        repo = options.pop("repo_root", None)
        if not isinstance(repo, str | pathlib.Path) or not str(repo):
            raise ValueError("Docker backend requires --repo-root or backend-config repo_root")
        model_keys = options.pop("model_key_envs", [])
        if not isinstance(model_keys, list | tuple) or any(
                not isinstance(key, str) or not re.fullmatch(r"[A-Z][A-Z0-9_]*", key) for key in model_keys):
            raise ValueError("model_key_envs must be a list of environment variable names")
        if "vllm_port" in options and (type(options["vllm_port"]) is not int or not 1 <= options["vllm_port"] <= 65535):
            raise ValueError("vllm_port must be an integer between 1 and 65535")
        if "vllm_host" in options and (not isinstance(options["vllm_host"], str) or not options["vllm_host"]):
            raise ValueError("vllm_host must be a non-empty string")
        if "allow_unenforced_resources" in options and type(options["allow_unenforced_resources"]) is not bool:
            raise ValueError("allow_unenforced_resources must be boolean")
        return DockerBackend(pathlib.Path(repo), model_key_envs=tuple(model_keys), **options)
