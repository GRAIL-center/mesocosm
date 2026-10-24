"""Resource identity establishes exposure; timestamps, model text and agent proximity do not."""

from __future__ import annotations

from mesocosm.core.exposure import link_exposures
from mesocosm.core.records import Event


def event(event_id, kind, service, *, run_id="run-1", agent_id="agent-1", **data):
    return Event(run_id=run_id, event_id=event_id, agent_id=agent_id, kind=f"service.{kind}",
                 data={"service": service, **data})


def test_chat_links_exact_ids_across_three_agents_and_retains_seed_unknowns():
    events = [event("send-1", "write", "chat", message_id=1),
              event("send-2", "write", "chat", message_id=2, agent_id="agent-2"),
              event("read-3", "read_list", "chat", message_ids=[1, 2, 3], agent_id="agent-3")]
    result = link_exposures(events)[0]
    assert result.exposure_event_id == "read-3"
    assert [link.event_id for link in result.links] == ["send-1", "send-2"]
    assert all(link.relation == "observed" for link in result.links)
    assert result.link_coverage == "partial" and result.expected_sources == 3
    assert result.unresolved_sources == ["missing:chat:3"]


def test_cached_overwrite_uses_version_not_latest_writer_or_equal_content():
    events = [event("write-old", "write", "cache", object="key", object_version="v1", sha256="same-content"),
              event("write-new", "write", "cache", object="key", object_version="v2", sha256="same-content"),
              event("read", "read", "cache", object="key", object_version="v1", sha256="same-content")]
    result = link_exposures(events)[0]
    assert [link.event_id for link in result.links] == ["write-old"]
    assert result.link_coverage == "complete" and result.unresolved_sources == []


def test_absent_legacy_ids_are_unknown_and_an_empty_recorded_read_is_complete():
    events = [event(None, "write", "chat", message_id=1),
              event("old-read", "read_list", "chat", count=42),
              event(None, "read_list", "chat", message_ids=[1]),
              event("empty", "read_list", "chat", message_ids=[]),
              event("old-cache", "read", "cache", object="key", sha256="known-hash")]
    old, missing_id, empty, cache = link_exposures(events)
    assert old.link_coverage == "unknown" and old.expected_sources is None
    assert old.unresolved_sources == ["message_ids_not_recorded"]
    assert missing_id.link_coverage == "unknown"
    assert missing_id.unresolved_sources == ["exposure_event_id_not_recorded", "source_event_id_not_recorded:chat:1"]
    assert empty.link_coverage == "complete" and empty.expected_sources == 0
    assert cache.link_coverage == "unknown" and cache.expected_sources == 1


def test_cross_run_duplicates_ambiguity_and_hash_disagreements_never_get_guessed():
    events = [event("other-run", "write", "chat", run_id="run-2", message_id=1),
              event("duplicate-a", "write", "chat", message_id=2),
              event("duplicate-b", "write", "chat", message_id=2),
              event("read", "read_list", "chat", message_ids=[1, 2]),
              event("write-cache", "write", "cache", object="key", object_version="v1", sha256="a"),
              event("read-cache", "read", "cache", object="key", object_version="v1", sha256="b")]
    chat, cache = link_exposures(events)
    assert chat.links == [] and chat.unresolved_sources == ["missing:chat:1", "ambiguous:chat:2"]
    assert cache.links == [] and cache.unresolved_sources == ["content_hash_mismatch:cache:key:v1"]


def test_invalid_and_duplicate_chat_references_preserve_partial_coverage():
    events = [event("send", "write", "chat", message_id=1),
              event("read", "read_list", "chat", message_ids=[1, 1, True, "1", 0])]
    result = link_exposures(events)[0]
    assert [link.event_id for link in result.links] == ["send"]
    assert result.expected_sources == 5 and result.link_coverage == "partial"
    assert len(result.unresolved_sources) == 4
