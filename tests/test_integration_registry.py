"""Plugin discovery conflicts and contract failures are explicit before execution."""

from __future__ import annotations

import pathlib
import sys
from types import SimpleNamespace

import pytest

from mesocosm.core.manifest import WorldManifest
from mesocosm.integrations import registry


def entry(name, factory, group="mesocosm.task_sources"):
    return SimpleNamespace(name=name, value="mesocosm.integrations.registry:fixture", group=group,
                           load=lambda: factory,
                           dist=SimpleNamespace(files=[], metadata={"Name": "test-plugin"}, version="1"))


def install(monkeypatch, *entries):
    monkeypatch.setattr(registry.metadata, "entry_points", lambda *, group: [ep for ep in entries if ep.group == group])


def test_missing_and_duplicate_ids_do_not_load_arbitrary_code(monkeypatch):
    def forbidden():
        raise AssertionError("ambiguous plugin imported")

    install(monkeypatch, entry("harbor", forbidden), entry("duplicate", forbidden), entry("duplicate", forbidden))
    for name in ("harbor", "duplicate"):
        with pytest.raises(registry.IntegrationError, match="duplicate"):
            registry.get_task_source(name)
    with pytest.raises(registry.IntegrationError, match="not installed"):
        registry.get_task_source("missing")
    inventory = registry.integration_inventory()
    assert {row["status"] for row in inventory["task_source"]} == {"conflict"}


@pytest.mark.parametrize("api", [None, True, "1", 2])
def test_incompatible_api_is_rejected(monkeypatch, api):
    install(monkeypatch, entry("bad-api", lambda: SimpleNamespace(api_version=api)))
    with pytest.raises(registry.IntegrationError, match="API 1"):
        registry.get_task_source("bad-api")


def test_incomplete_contract_and_bad_provider_return_fail_explicitly(monkeypatch):
    install(monkeypatch, entry("incomplete", lambda: SimpleNamespace(api_version=1)))
    with pytest.raises(registry.IntegrationError, match="no load"):
        registry.get_task_source("incomplete")
    monkeypatch.setattr(registry, "get_backend_provider", lambda _: SimpleNamespace(validate=lambda _: None))
    world = WorldManifest.model_validate({"name": "invalid-provider", "model": {"id": "unused"},
        "gateway": {"url": "http://unused"}, "agents": [{"id": "one"}]})
    with pytest.raises(registry.IntegrationError, match="list of error strings"):
        registry.validate_integrations(world)


def test_editable_installation_location_does_not_change_payload_hash(tmp_path, monkeypatch):
    hashes = []
    for location in ("client", "worker"):
        root = tmp_path / location
        root.mkdir()
        source = root / "editable_sample.py"
        source.write_text("api_version = 1\n")
        locator = root / "__editable__.plugin.pth"
        locator.write_text(str(root))
        module = SimpleNamespace(__file__=str(source))
        monkeypatch.setitem(sys.modules, "editable_sample", module)
        dist = SimpleNamespace(files=[pathlib.PurePath(source.name), pathlib.PurePath(locator.name)],
                               locate_file=lambda entry, root=root: root / entry)
        hashes.append(registry._package_hash("editable_sample:Provider", SimpleNamespace(dist=dist)))
    assert hashes[0] == hashes[1]
