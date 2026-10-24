"""Explicit access to internal services; the absence of a grant denies access.

These controls constrain service requests, not arbitrary information flow. Shared mounts and external
services are separate channels, and a reader of a shared resource can see all content in that resource.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

ServiceName = Literal["board", "wiki", "chat", "cache", "pastebin", "pypi", "clock", "ledger", "vault"]
SERVICE_NAMES = {"board", "wiki", "chat", "cache", "pastebin", "pypi", "clock", "ledger", "vault", "git", "mcp"}


class AccessGrant(BaseModel):
    model_config = ConfigDict(extra="forbid")

    read: list[str] = Field(default_factory=list)
    write: list[str] = Field(default_factory=list)

    @field_validator("read", "write")
    @classmethod
    def unique_agents(cls, agents: list[str]) -> list[str]:
        if len(set(agents)) != len(agents):
            raise ValueError("access grants must not repeat agent IDs")
        return agents

    def allows(self, agent_id: str, operation: str) -> bool:
        return agent_id in (self.read if operation == "read" else self.write if operation == "write" else [])


class AccessSpec(BaseModel):
    """Opt-in internal access graph. Every unlisted service, repository, channel and DM edge is denied."""

    model_config = ConfigDict(extra="forbid")

    services: dict[ServiceName, AccessGrant] = Field(default_factory=dict)
    git: dict[str, AccessGrant] = Field(default_factory=dict)
    direct_messages: dict[str, list[str]] = Field(default_factory=dict)
    channels: dict[str, AccessGrant] = Field(default_factory=dict)

    def allows(self, agent_id: str, service: str, operation: str, repository: str | None = None) -> bool:
        grants: dict[str, AccessGrant] = {str(key): value for key, value in self.services.items()}
        grant = self.git.get(repository or "") if service == "git" else grants.get(service)
        return grant is not None and grant.allows(agent_id, operation)

    def channel_allows(self, agent_id: str, channel: str, operation: str) -> bool:
        grant = self.channels.get(channel)
        return grant is not None and grant.allows(agent_id, operation)

    def can_send(self, agent_id: str, recipient: str | None, channel: str | None) -> bool:
        if channel is not None:
            return self.channel_allows(agent_id, channel, "write")
        return recipient is not None and recipient in self.direct_messages.get(agent_id, [])

    def visible_message(self, agent_id: str, message: dict[str, Any]) -> bool:
        channel = message.get("channel")
        if channel is not None:
            return self.channel_allows(agent_id, str(channel), "read")
        sender, recipient = message.get("from"), message.get("to")
        if sender == "runner":
            return recipient == agent_id
        return (agent_id in (sender, recipient) and isinstance(sender, str)
                and recipient in self.direct_messages.get(sender, []))


def validate_access(access: AccessSpec, agent_ids: list[str], repositories: list[str], channels: list[str], *,
                    shared_directory: bool = False) -> list[str]:
    """Cross-reference an access graph against the actual world before rendering services."""
    errors: list[str] = []
    known = set(agent_ids)
    grants = [*access.services.values(), *access.git.values(), *access.channels.values()]
    referenced = {agent for grant in grants for agent in grant.read + grant.write}
    referenced.update(access.direct_messages)
    referenced.update(agent for targets in access.direct_messages.values() for agent in targets)
    if unknown := referenced - known:
        errors.append(f"services.access references unknown agents: {sorted(unknown)}")
    if unknown_repos := set(access.git) - set(repositories):
        errors.append(f"services.access.git references unknown repositories: {sorted(unknown_repos)}")
    if unknown_channels := set(access.channels) - set(channels):
        errors.append(f"services.access.channels references unknown chat channels: {sorted(unknown_channels)}")
    for service, grant in access.services.items():
        if service in ("pypi", "clock", "vault") and grant.write:
            errors.append(f"rejected_unsupported: services.access.{service} has no write operation")
    for sender, recipients in access.direct_messages.items():
        if sender in recipients or len(set(recipients)) != len(recipients):
            errors.append(f"services.access.direct_messages.{sender} must name unique other agents")
    if shared_directory:
        errors.append("rejected_unsupported: services.access with shared_dir; the shared mount bypasses service ACLs")
    return errors
