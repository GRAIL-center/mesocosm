"""Unused conditions cannot pass preflight as effective experimental treatments."""

from __future__ import annotations

import pytest
import yaml

from mesocosm.core.condition_validation import inactive_condition_errors
from mesocosm.core.manifest import WorldManifest, validate_manifest


def world(**changes):
    return WorldManifest.model_validate({
        "name": "condition-audit", "model": {"id": "base"},
        "gateway": {"url": "http://gateway"},
        "agents": [{"id": f"agent-{i}"} for i in range(1, 4)],
        "tasks": [{"path": "original-task"}], **changes,
    })


def test_default_and_fully_serialized_default_controls_remain_valid():
    original = world()
    assert inactive_condition_errors(original) == []
    assert inactive_condition_errors(WorldManifest.model_validate(original.model_dump())) == []


@pytest.mark.parametrize("changes,field", [
    ({"services": {"chat": {"max_chars": 200}}}, "services.chat.max_chars"),
    ({"services": {"chat": {"max_messages": 5}}}, "services.chat.max_messages"),
    ({"services": {"chat": {"channels": []}}}, "services.chat.channels"),
    ({"judge": {"monitor": {"scope": "joint", "threshold": 13}}}, "judge.monitor.scope"),
    ({"judge": {"monitor": {"model": "base"}}}, "judge.monitor.model"),
    ({"internet": {"mode": "record"}}, "internet.mode"),
    ({"internet": {"budget": {"requests": 3}}}, "internet.budget"),
    ({"internet": {"allow": ["example.org"]}}, "internet.allow"),
    ({"internet": {"replay_file": "flows.jsonl"}}, "internet.replay_file"),
    ({"services": {"peer_review": {"topology": "ring"}}}, "services.peer_review.topology"),
    ({"services": {"peer_review": {"protocol": "ratings"}}}, "services.peer_review.protocol"),
    ({"services": {"peer_review": {"reward_feedback": False}}}, "services.peer_review.reward_feedback"),
    ({"stream": {"max_tasks": 3}}, "stream.max_tasks"),
    ({"stream": {"poll_seconds": 2}}, "stream.poll_seconds"),
    ({"stream": {"source": "external"}}, "stream.source"),
])
def test_inactive_nondefault_treatments_are_rejected(changes, field):
    errors = inactive_condition_errors(world(**changes))
    assert errors and any(field in error for error in errors)
    assert all(error.startswith("rejected_unsupported:") for error in errors)


def test_active_controls_are_not_rejected_as_inactive():
    configured = world(mode="persistent", stream={"max_tasks": 3},
        services={"chat": {"enabled": True, "max_chars": 200},
                  "peer_review": {"enabled": True, "protocol": "ratings", "topology": "ring"}},
        judge={"monitor": {"enabled": True, "model": "base", "scope": "joint"}},
        internet={"enabled": True, "allow": ["example.org"], "mode": "record", "budget": {"requests": 3}})
    # Cross-feature constraints (such as peer review requiring comparison) are checked by
    # validate_manifest; this helper only identifies inactive controls and exposure shape.
    assert inactive_condition_errors(configured) == []


@pytest.mark.parametrize("exposure", [
    None, [], {}, {"pages": ["secret"]}, {"wiki": {}, "ignored": 3},
    {"wiki": []}, {"wiki": {"secret": -1}}, {"wiki": {"secret": True}},
    {"wiki": {"secret": float("nan")}}, {"wiki": {"secret": float("inf")}},
    {"wiki": {"secret": "30"}}, {"wiki": {"": 30}}, {"wiki": {3: 30}},
    {"wiki": {"secret": 10 ** 1000}},
])
def test_exposure_rejects_unsupported_shape_and_nonfinite_schedule(exposure):
    errors = inactive_condition_errors(world(conditions={"exposure": exposure}))
    assert len(errors) == 1 and "conditions.exposure: expected {wiki:" in errors[0]


@pytest.mark.parametrize("pages", [{}, {"secret": 30}, {"first": 0, "second": 0.25}])
def test_existing_exposure_shape_is_preserved(pages):
    assert inactive_condition_errors(world(conditions={"exposure": {"wiki": pages}})) == []


def test_inactive_controls_are_checked_after_runtime_model_mutation():
    configured = world(services={"chat": {"enabled": True, "max_chars": 200}})
    configured.services.chat.enabled = False
    assert any("services.chat.max_chars" in error for error in inactive_condition_errors(configured))


def test_manifest_preflight_rejects_the_reproduced_noop_conditions(tmp_path):
    configured = world(
        services={"chat": {"max_chars": 200}, "peer_review": {"topology": "ring"}},
        judge={"monitor": {"scope": "joint", "threshold": 13}},
        internet={"mode": "record", "budget": {"requests": 3}},
        stream={"max_tasks": 3}, conditions={"exposure": {"wiki": {}, "unknown": 3}},
    )
    task = tmp_path / "original-task"
    task.mkdir()
    (task / "instruction.md").write_text("Validate condition configuration without model execution.")
    manifest = tmp_path / "world.yaml"
    manifest.write_text(yaml.safe_dump(configured.model_dump(mode="json")))
    report = validate_manifest(manifest)
    assert not report.ok
    for field in ("services.chat.max_chars", "services.peer_review.topology", "judge.monitor.scope",
                  "internet.mode", "stream.max_tasks", "conditions.exposure"):
        assert any(field in error for error in report.errors), (field, report.errors)
