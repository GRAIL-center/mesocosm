"""External distribution used only to verify extension plumbing, never agent research.

This deliberately executes an original reference program and verifier on the host.
It provides no agent, sandbox, network policy, monitoring, or RL support.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import shutil
import sys

from mesocosm.core.manifest import load_manifest
from mesocosm.execution.backends import BackendContext
from mesocosm.integrations.registry import integration_snapshot
from mesocosm.integrations.tasks import Task, load_task


class JsonSource:
    api_version = 1

    def load(self, path, *, dataset=None, name=None):
        data = json.loads((path / "problem.json").read_text())
        return Task(path, name or data["id"], data["instruction"], data, False, True, True,
                    dataset=dataset, source="contract-json", execution_kind="contract-cpu")

    def resolve(self, dataset, *, roots, limit=None):
        for root in roots:
            path = (root / dataset).resolve()
            if path.is_dir() and path.is_relative_to(root.resolve()):
                tasks = [path] if (path / "problem.json").is_file() else [
                    child for child in sorted(path.iterdir()) if (child / "problem.json").is_file()]
                if tasks:
                    return tasks[:limit]
        raise ValueError(f"contract dataset missing: {dataset}")

    def research_errors(self, task):
        # This is a packaging protocol test. The backend receipt explicitly denies
        # agent-rollout/research acceptance; the source does not certify originality.
        return []

    def fingerprint(self, task):
        return {"originality_verified": False, "research_acceptance": False,
                "verifier_sha256": hashlib.sha256((task.path / "verify.py").read_bytes()).hexdigest()}


class CpuProvider:
    api_version = 1

    def validate(self, world):
        if world.execution.options != {"contract_test_only": True}:
            return ["test provider requires explicit execution.options.contract_test_only"]
        if any(task.source != "contract-json" for task in world.tasks):
            return ["test provider only accepts contract-json tasks"]
        if (world.conditions or world.internet.enabled or world.rewards.enabled or world.resources.model_requests
                or world.judge.monitor.enabled or world.learning.mode != "none"):
            return ["test provider cannot enforce research controls"]
        return []

    def create(self, context: BackendContext):
        return CpuBackend(context.root)


class CpuBackend:
    def __init__(self, root):
        self.root = root
        self.process = None

    async def capabilities(self):
        return {"status": "supported", "backend": "contract-cpu", "agent_rollout": False,
                "research_acceptance": False, "purpose": "installed extension contract test"}

    async def execute(self, job_dir, input_dir, kind, entrypoint):
        if kind != "world":
            raise ValueError("test backend only executes a world")
        world = load_manifest(input_dir / entrypoint)
        bundle = job_dir / "runs" / "run_contract"
        bundle.mkdir(parents=True)
        workspace = job_dir / "contract-workspace"
        workspace.mkdir()
        events = []
        status = "ok"
        try:
            for spec in world.tasks:
                task = load_task(spec.source, input_dir / spec.path, name=spec.name)
                for filename in ("reference.py", "verify.py"):
                    argv = [sys.executable, str(task.path / filename), str(workspace)]
                    self.process = await asyncio.create_subprocess_exec(
                        *argv, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
                    try:
                        stdout, stderr = await asyncio.wait_for(self.process.communicate(), timeout=10)
                    finally:
                        if self.process.returncode is None:
                            self.process.kill()
                            await self.process.wait()
                    events.append({"kind": "contract.command", "argv": argv,
                                   "stdout": stdout.decode(), "stderr": stderr.decode(),
                                   "returncode": self.process.returncode, "agent_rollout": False})
                    if self.process.returncode != 0:
                        status = "failed"
                artifact = workspace / "answer.txt"
                if artifact.exists():
                    shutil.copyfile(artifact, bundle / "answer.txt")
        finally:
            shutil.rmtree(workspace)
            (bundle / "events.jsonl").write_text("".join(json.dumps(event) + "\n" for event in events))
            (bundle / "manifest.json").write_text(json.dumps(world.model_dump(mode="json")))
            (bundle / "integrations.json").write_text(json.dumps(integration_snapshot(world)))
        return {"status": status, "cleanup": {"status": "complete"}, "agent_rollout": False,
                "research_acceptance": False, "purpose": "external plugin contract test"}

    async def recover(self, job_dir):
        if self.process is not None and self.process.returncode is None:
            self.process.kill()
            await self.process.wait()
        # This fixture can recover only the same-process child. Production adapters
        # must persist and inspect ownership before spawning anything.
        shutil.rmtree(job_dir / "contract-workspace", ignore_errors=True)
        return {"status": "complete", "research_acceptance": False}
