"""Install a real external distribution and exercise it in fresh Python processes.

No registry monkeypatch, GPU, Docker, live agents, or benchmark acceptance is involved.
The fixture executes original reference/verifier subprocesses only.
"""

from __future__ import annotations

import base64
import csv
import hashlib
import io
import json
import os
import pathlib
import subprocess
import sys
import tomllib
import venv
import zipfile

import pytest
import yaml

FIXTURE = pathlib.Path(__file__).parent / "fixtures" / "integration_plugin"
REPO = pathlib.Path(__file__).resolve().parents[1]


def build_fixture_wheel(destination):
    """Build the fixture's pure-Python wheel without downloading a build toolchain."""
    config = tomllib.loads((FIXTURE / "pyproject.toml").read_text())["project"]
    name = config["name"].replace("-", "_")
    dist = f"{name}-{config['version']}.dist-info"
    files = {"contract_plugin.py": (FIXTURE / "contract_plugin.py").read_bytes(),
             f"{dist}/METADATA": f"Metadata-Version: 2.1\nName: {config['name']}\nVersion: {config['version']}\n".encode(),
             f"{dist}/WHEEL": b"Wheel-Version: 1.0\nGenerator: mesocosm-test\nRoot-Is-Purelib: true\nTag: py3-none-any\n",
             f"{dist}/entry_points.txt": "\n".join(
                 f"[{group}]\n" + "\n".join(f"{key} = {value}" for key, value in entries.items()) + "\n"
                 for group, entries in config["entry-points"].items()).encode()}
    record = io.StringIO()
    writer = csv.writer(record)
    for path, data in files.items():
        digest = base64.urlsafe_b64encode(hashlib.sha256(data).digest()).rstrip(b"=").decode()
        writer.writerow((path, f"sha256={digest}", len(data)))
    writer.writerow((f"{dist}/RECORD", "", ""))
    files[f"{dist}/RECORD"] = record.getvalue().encode()
    wheel = destination / f"{name}-{config['version']}-py3-none-any.whl"
    with zipfile.ZipFile(wheel, "w") as archive:
        for path, data in files.items():
            archive.writestr(path, data)
    return wheel


@pytest.fixture(scope="module")
def installed(tmp_path_factory):
    root = tmp_path_factory.mktemp("installed-plugin")
    wheel = build_fixture_wheel(root)
    installer = root / "installer"
    venv.EnvBuilder(with_pip=True).create(installer)
    target = root / "packages"
    result = subprocess.run([str(installer / "bin/python"), "-m", "pip", "install", "--no-index", "--no-deps",
                             "--disable-pip-version-check", "--target", str(target), str(wheel)],
                            capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stderr
    return target


def invoke(installed, code, *arguments):
    env = dict(os.environ, PYTHONPATH=os.pathsep.join([str(installed), str(REPO / "src")]))
    result = subprocess.run([sys.executable, "-c", code, *map(str, arguments)], env=env,
                            capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stdout + result.stderr
    return json.loads(result.stdout)


def make_world(root):
    task = root / "original-json-task"
    task.mkdir()
    (task / "problem.json").write_text(json.dumps({"id": "sum", "instruction": "Sum the supplied integers."}))
    (task / "reference.py").write_text(
        "import pathlib, sys\n(pathlib.Path(sys.argv[1]) / 'answer.txt').write_text(str(sum([1,2,3])))\n")
    (task / "verify.py").write_text(
        "import pathlib, sys\nassert (pathlib.Path(sys.argv[1]) / 'answer.txt').read_text() == '6'\nprint('verified')\n")
    manifest = root / "world.yaml"
    manifest.write_text(yaml.safe_dump({"name": "external-contract", "model": {"id": "unused"},
        "gateway": {"url": "http://unused.invalid"}, "services": {"enabled": False},
        "execution": {"backend": "contract-cpu", "options": {"contract_test_only": True}},
        "agents": [{"id": f"placeholder-{i}", "harness": "no-agent-contract-fixture"} for i in range(3)],
        "tasks": [{"source": "contract-json", "path": task.name}]}))
    return manifest


def test_installed_source_backend_package_worker_and_original_verifier(installed, tmp_path):
    manifest = make_world(tmp_path)
    code = '''
import asyncio, json, pathlib, sys, uuid
from mesocosm.core.manifest import validate_manifest
from mesocosm.execution.backends import BackendContext
from mesocosm.execution.package import pack_submission, extract_results
from mesocosm.execution.worker import Worker
from mesocosm.integrations.registry import get_backend_provider, backend_identity, integration_inventory

async def main():
    manifest = pathlib.Path(sys.argv[1])
    base = manifest.parent
    report = validate_manifest(manifest)
    assert report.ok, report.errors
    archive = base / "submission.zip"
    pack_submission(manifest, archive)
    identity = backend_identity("contract-cpu")
    backend = get_backend_provider("contract-cpu").create(BackendContext(base / "worker"))
    worker = Worker(base / "worker", backend, lock_file=base / "host.lock",
                    backend_id="contract-cpu", backend_identity=identity)
    await worker.start()
    job_id = uuid.uuid4().hex
    try:
        await worker.submit(archive, job_id)
        await worker.tasks[job_id]
        assert worker.record(job_id).status == "completed", worker.record(job_id)
        directory = worker.directory(job_id)
        receipt = json.loads((directory / "result.json").read_text())
        bundle = directory / "runs/run_contract"
        events = [json.loads(line) for line in (bundle / "events.jsonl").read_text().splitlines()]
        assert len(events) == 2 and events[1]["stdout"] == "verified\\n"
        assert all(event["returncode"] == 0 and event["agent_rollout"] is False for event in events)
        assert (bundle / "answer.txt").read_text() == "6"
        assert not (directory / "contract-workspace").exists()
        plugins = json.loads((bundle / "integrations.json").read_text())
        assert plugins["execution_backend:contract-cpu"] == identity
        print(json.dumps({"receipt": receipt, "inventory": integration_inventory(), "plugins": plugins}))
    finally:
        await worker.close()

asyncio.run(main())
'''
    result = invoke(installed, code, manifest)
    assert result["receipt"]["status"] == "ok"
    assert result["receipt"]["research_acceptance"] is False
    assert result["plugins"]["task_source:contract-json"]["distribution"] == "mesocosm-contract-fixture"
    assert not (tmp_path / "original-json-task" / "instruction.md").exists()


def test_cli_discovery_and_check_are_available_in_fresh_process(installed, tmp_path):
    manifest = make_world(tmp_path)
    code = '''
import json, sys
from typer.testing import CliRunner
from mesocosm.cli.main import app
runner = CliRunner()
listed = runner.invoke(app, ["integrations", "list", "--json"])
assert listed.exit_code == 0, listed.output
assert "contract_plugin" not in sys.modules, "inventory imported unselected plugin"
checked = runner.invoke(app, ["integrations", "check", sys.argv[1], "--json"])
assert checked.exit_code == 0, checked.output
print(checked.output.strip())
'''
    result = invoke(installed, code, manifest)
    assert result["status"] == "compatible" and result["runtime_preflight_required"] is True


def test_loaded_plugin_drift_requires_process_restart(installed, tmp_path):
    import shutil

    changed = tmp_path / "installed"
    shutil.copytree(installed, changed)
    code = '''
import json, pathlib, sys
from mesocosm.integrations.registry import task_source_identity, IntegrationError
before = task_source_identity("contract-json")
path = pathlib.Path(sys.argv[1]) / "contract_plugin.py"
path.write_text(path.read_text() + "\\n# installation changed after first load\\n")
try:
    task_source_identity("contract-json")
except IntegrationError as exc:
    assert "restart" in str(exc), str(exc)
    print(json.dumps({"status": "rejected", "previous": before}))
else:
    raise AssertionError("cached old code was mislabeled as new disk code")
'''
    assert invoke(changed, code, changed)["status"] == "rejected"
