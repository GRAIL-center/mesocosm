"""Exact shared-service exposure links, without inferring how an agent used observed content."""

from __future__ import annotations

from collections import defaultdict
from typing import Literal

from pydantic import BaseModel, Field

from mesocosm.core.records import Event, EventLink, LinkCoverage


class ExposureEvidence(BaseModel):
    """Coverage describes only the resource references in this read, never a complete causal trace.

    `expected_sources=None` means the source cardinality itself was not recorded. Unresolved labels
    preserve absent, ambiguous or inconsistent evidence instead of treating it as a read of nothing.
    """

    exposure_event_id: str | None
    service: Literal["chat", "cache"]
    links: list[EventLink] = Field(default_factory=list)
    link_coverage: LinkCoverage = "unknown"
    expected_sources: int | None = None
    unresolved_sources: list[str] = Field(default_factory=list)


def link_exposures(events: list[Event]) -> list[ExposureEvidence]:
    """Join a chat read to unique message writes, or a cache read to its exact version's write.

    Restored/seeded objects, legacy records without IDs and conflicting IDs stay unresolved. No
    transcript text, timestamp proximity, display name or shared session establishes a link here.
    """
    sources: dict[tuple[str, str, str, str], list[Event]] = defaultdict(list)
    for event in events:
        if event.kind != "service.write":
            continue
        data = event.data
        if data.get("service") == "chat" and type(data.get("message_id")) is int:
            sources[(event.run_id, "chat", str(data["message_id"]), "")].append(event)
        if (data.get("service") == "cache" and isinstance(data.get("object_version"), str) and data["object_version"]
                and isinstance(data.get("object"), str) and data["object"]):
            sources[(event.run_id, "cache", str(data.get("object")), data["object_version"])].append(event)

    output: list[ExposureEvidence] = []
    for event in events:
        data = event.data
        service = data.get("service")
        if not ((event.kind == "service.read_list" and service == "chat")
                or (event.kind == "service.read" and service == "cache")):
            continue
        item = ExposureEvidence(exposure_event_id=event.event_id, service=service)
        references: list[tuple[str, str, str, str]] = []
        if service == "chat":
            ids = data.get("message_ids")
            if not isinstance(ids, list):
                item.unresolved_sources.append("message_ids_not_recorded")
            else:
                item.expected_sources = len(ids)
                seen: set[int] = set()
                for index, message_id in enumerate(ids):
                    if type(message_id) is not int or message_id < 1 or message_id in seen:
                        item.unresolved_sources.append(f"invalid_or_duplicate_message_id_at:{index}")
                        continue
                    seen.add(message_id)
                    references.append((event.run_id, "chat", str(message_id), ""))
        else:
            item.expected_sources = 1
            version, key = data.get("object_version"), data.get("object")
            if not isinstance(version, str) or not version or not isinstance(key, str) or not key:
                item.unresolved_sources.append("object_version_or_key_not_recorded")
            else:
                references.append((event.run_id, "cache", key, version))
        if not event.event_id:
            item.unresolved_sources.append("exposure_event_id_not_recorded")
        for reference in references:
            candidates = sources.get(reference, [])
            label = ":".join(reference[1:]).rstrip(":")
            if len(candidates) != 1:
                item.unresolved_sources.append(f"{'missing' if not candidates else 'ambiguous'}:{label}")
                continue
            source = candidates[0]
            if not source.event_id:
                item.unresolved_sources.append(f"source_event_id_not_recorded:{label}")
                continue
            if service == "cache" and source.data.get("sha256") != data.get("sha256"):
                item.unresolved_sources.append(f"content_hash_mismatch:{label}")
                continue
            item.links.append(EventLink(event_id=source.event_id, relation="observed",
                                        evidence=f"exact service reference {label} within run {event.run_id}"))
        item.link_coverage = ("partial" if item.links else "unknown") if item.unresolved_sources else "complete"
        output.append(item)
    return output
