"""Run one world through the control plane with host processes only (no Docker), so an agent can be
watched in `mesocosm observe serve`:

    python spike/08_host_run.py --manifest spike/host-world.yaml --runs-root runs

Requires vLLM on :8000, the gateway on :8081 and `opencode serve` on :4096 (spike/colab_smoke.py
serve, or SPIKE.md steps 1-3). Isolation is NOT enforced: the egress check records a WARN line
saying so, and no verifier container runs (the reward is recorded as not measured). Everything
else is the real run manager: manifest, budgets and receipts, session, events, run bundle.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))

from mesocosm.adapters.harbor.task import HarborTask, Reward  # noqa: E402
from mesocosm.adapters.opencode.client import OpenCodeClient  # noqa: E402
from mesocosm.core.manifest import load_manifest, validate_manifest  # noqa: E402
from mesocosm.core.run import Components, run_world  # noqa: E402
from mesocosm.env.egress import EgressResult  # noqa: E402
from mesocosm.gateway.client import GatewayAdmin  # noqa: E402


class HostEgress:
    """No sandbox exists in host mode; say so in the record instead of pretending."""

    async def verify(self, agent_ids: list[str]) -> dict[str, EgressResult]:
        line = ("WARN", "host mode: processes share the machine's network; isolation is not enforced")
        return {a: EgressResult(ok=True, exit_code=None, lines=[line]) for a in agent_ids}


class NoVerifier:
    async def verify(self, task: HarborTask, workspace: pathlib.Path, logs_dir: pathlib.Path) -> Reward:
        return Reward(value=None, source=None, labels={"note": "host mode: verifier not run"})


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--manifest", default="spike/host-world.yaml")
    ap.add_argument("--runs-root", default="runs")
    ap.add_argument("--workspace", default="spike/workspace/agent-1")
    args = ap.parse_args()
    manifest_path = pathlib.Path(args.manifest).resolve()
    report = validate_manifest(manifest_path)
    if not report.ok:
        raise SystemExit(json.dumps({"status": "invalid_manifest", "errors": report.errors}))
    spec = load_manifest(manifest_path)
    workspace = pathlib.Path(args.workspace).resolve()
    workspace.mkdir(parents=True, exist_ok=True)
    components = Components(
        harness_for=lambda a: OpenCodeClient(a.harness_url()),
        gateway=GatewayAdmin(spec.gateway.url),
        egress=HostEgress(),
        verifier=NoVerifier(),
        workspace_for=lambda a: workspace,
    )
    result = asyncio.run(run_world(spec, manifest_path, components, pathlib.Path(args.runs_root)))
    print(json.dumps({"run_id": result.run_id, "status": result.status, "bundle": str(result.bundle_path),
                      "rewards": result.rewards, "error": result.error}))


if __name__ == "__main__":
    main()
