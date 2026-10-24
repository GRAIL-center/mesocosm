"""Calibrate the original verifier using the task author's unchanged reference solution.

This is an operator diagnostic in a dedicated environment, never an agent rollout or
learning sample. It does not assert benchmark correctness or a successful reference score.
The caller owns environment cleanup even if preparation, solving or verification fails.
"""

from __future__ import annotations

import asyncio
import json
import math
import pathlib
import re
import shlex
from typing import Any, Protocol

from mesocosm.adapters.harbor.task import HarborTask, Reward
from mesocosm.core.evaluation import task_fingerprint
from mesocosm.core.manifest import AgentSpec


class ReferenceRuntime(Protocol):
    async def prepare(self, agent: AgentSpec, task: HarborTask, workspace: pathlib.Path, *, reset: bool,
                      restored: bool = False) -> dict[str, Any]: ...
    async def verify(self, task: HarborTask, workspace: pathlib.Path, logs_dir: pathlib.Path,
                     script: str = "test.sh") -> Reward: ...
    async def finish(self) -> Any: ...
    async def command(self, *args: str, timeout: float = 600, include_stderr: bool = False) -> bytes: ...


def _overlaps(left: pathlib.Path, right: pathlib.Path) -> bool:
    return left.is_relative_to(right) or right.is_relative_to(left)


def _interpreter(solution: pathlib.Path) -> list[str]:
    with solution.open("rb") as stream:
        first = stream.readline(4096).decode("utf-8", "strict").strip()
    if not first.startswith("#!"):
        return ["bash"]
    args = shlex.split(first[2:].strip())
    if not args or not args[0].startswith("/") or any("\x00" in arg for arg in args):
        raise ValueError("reference solution has an unsupported interpreter directive")
    return args


def _measurement(reward: Reward) -> dict[str, Any]:
    value = reward.value
    if not isinstance(value, int | float) or isinstance(value, bool) or not math.isfinite(value):
        raise ValueError("original verifier did not produce a finite reward")
    return {"status": "measured", "original_score": value, "source": reward.source}


def _write_json(path: pathlib.Path, value: dict[str, Any]) -> None:
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False))
    path.chmod(0o600)


async def calibrate_task(task: HarborTask, runtime: ReferenceRuntime, agent: AgentSpec,
                         workspace: pathlib.Path, out: pathlib.Path) -> dict[str, Any]:
    """Verify untouched state, reset, then apply the reference and verify fresh state.

    The caller must supply an exclusively owned fresh world, including isolated task
    services. No harness sessions or model calls are made. Runtime startup may still
    start its harness server. The reference is never placed in an agent task workspace.
    """
    workspace, out, task_root = workspace.resolve(), out.resolve(), task.path.resolve()
    if _overlaps(workspace, task_root) or _overlaps(out, task_root) or _overlaps(workspace, out):
        raise ValueError("reference output, workspace and original task trees must be disjoint")
    if workspace.exists() and (not workspace.is_dir() or any(workspace.iterdir())):
        raise ValueError("reference calibration requires an empty dedicated workspace")
    if out.exists():
        raise FileExistsError("reference output must be a new directory")
    solution = task_root / "solution" / "solve.sh"
    if not solution.is_file() or not solution.resolve().is_relative_to(task_root):
        raise ValueError("reference calibration requires the original solution/solve.sh")
    if not task.has_tests:
        raise ValueError("reference calibration requires the original tests/test.sh")
    inputs = task_fingerprint(task_root)  # Refuses links/special files, including in solution/.
    interpreter = _interpreter(solution)
    timeout = float((task.config.get("agent") or {}).get("timeout_sec") or 600)
    if not math.isfinite(timeout) or timeout <= 0:
        raise ValueError("reference solution timeout must be finite and positive")
    workspace.mkdir(parents=True, exist_ok=True)
    out.mkdir(parents=True, mode=0o700)
    _write_json(out / "inputs.json", inputs)
    result: dict[str, Any] = {"schema_version": "mesocosm.reference.v1", "task": task.name,
                              "status": "running", "stage": "prepare", "before": None, "after": None,
                              "score_delta": None, "reference_success": None,
                              "agent_rollout": False, "model_calls": 0,
                              "task_tree_sha256": inputs["tree_sha256"], "input_drift": False,
                              "cleanup": "caller_owned; not yet verified",
                              "reference_exit_status": None}
    _write_json(out / "result.json", result)

    def check_inputs() -> None:
        try:
            unchanged = task_fingerprint(task_root) == inputs
        except (OSError, ValueError):
            unchanged = False
        if not unchanged:
            result["input_drift"] = True
            raise ValueError("original task inputs changed during reference calibration")

    try:
        before_workspace = workspace / "before"
        receipt = await runtime.prepare(agent, task, before_workspace, reset=True)
        container, user, workdir = receipt.get("container"), receipt.get("user"), receipt.get("workdir")
        if not isinstance(container, str) or re.fullmatch(r"[0-9a-f]{64}", container) is None:
            raise ValueError("runtime must identify the exact prepared calibration container")
        if not isinstance(user, str) or not user or not isinstance(workdir, str) or not workdir.startswith("/"):
            raise ValueError("runtime must identify its native task user and working directory")
        _write_json(out / "before-environment.json", receipt)
        check_inputs()
        result["stage"] = "before_verifier"
        result["before"] = _measurement(await runtime.verify(task, before_workspace, out / "before"))
        check_inputs()
        # Verifiers can write files or mutate live services. The solution must start
        # from the original environment, not the state left by the baseline verifier.
        result["stage"] = "reset_environment"
        reset = await runtime.finish()
        _write_json(out / "reset.json", {"result": reset})
        check_inputs()
        result["stage"] = "prepare_reference"
        reference_workspace = workspace / "reference"
        receipt = await runtime.prepare(agent, task, reference_workspace, reset=True)
        container, user, workdir = receipt.get("container"), receipt.get("user"), receipt.get("workdir")
        if not isinstance(container, str) or re.fullmatch(r"[0-9a-f]{64}", container) is None:
            raise ValueError("runtime must identify the exact prepared calibration container")
        if not isinstance(user, str) or not user or not isinstance(workdir, str) or not workdir.startswith("/"):
            raise ValueError("runtime must identify its native task user and working directory")
        _write_json(out / "reference-environment.json", receipt)
        check_inputs()
        result["stage"] = "reference_solution"
        # Only this dedicated calibration container receives the original solution.
        # Refuse a pre-existing /solution rather than overwriting task environment data.
        await runtime.command("docker", "exec", "--user", "root", "--workdir", "/", container,
                              "sh", "-c", "test ! -e /solution && mkdir /solution && chmod 755 /solution")
        await runtime.command("docker", "cp", str(task_root / "solution") + "/.", f"{container}:/solution/")
        await runtime.command("docker", "exec", "--user", "root", "--workdir", "/", container,
                              "chmod", "-R", "a+rX", "/solution")
        command = ["docker", "exec", "--user", user, "--workdir", workdir, container,
                   *interpreter, "/solution/solve.sh"]
        _write_json(out / "reference.json", {"interpreter": interpreter, "script": "/solution/solve.sh",
                                            "timeout_seconds": timeout, "user": user, "workdir": workdir,
                                            "task_tree_sha256": inputs["tree_sha256"],
                                            "solution_script_sha256": inputs["files"]["solution/solve.sh"]["sha256"],
                                            "copied_solution_read_permissions": "a+rX"})
        output = await runtime.command(*command, timeout=timeout, include_stderr=True)
        (out / "reference.log").write_bytes(output)
        (out / "reference.log").chmod(0o600)
        result["reference_exit_status"] = 0
        result["stage"] = "remove_reference_assets"
        await runtime.command("docker", "exec", "--user", "root", "--workdir", "/", container,
                              "rm", "-rf", "--", "/solution")
        check_inputs()
        result["stage"] = "after_verifier"
        result["after"] = _measurement(await runtime.verify(task, reference_workspace, out / "after"))
        check_inputs()
        result.update(status="completed", stage="completed", score_delta=(
            result["after"]["original_score"] - result["before"]["original_score"]))
    except asyncio.CancelledError:
        result.update(status="cancelled", error_type="CancelledError")
        raise
    except Exception as exc:
        # Full diagnostic text stays in private artifacts, never in CLI result fields.
        (out / "failure.log").write_text(str(exc))
        (out / "failure.log").chmod(0o600)
        result.update(status="failed", error_type=type(exc).__name__, error_artifact="failure.log")
        try:
            check_inputs()
        except ValueError:
            pass
    finally:
        _write_json(out / "result.json", result)
    return result


async def calibrate_native(task_path: pathlib.Path, out: pathlib.Path, *, expected_score: float = 1.0,
                           verifier_network: str = "none") -> dict[str, Any]:
    """Own a minimal, authenticated, network-isolated calibration project on local Linux."""
    import secrets
    import shutil

    import yaml

    from mesocosm.adapters.harbor.task import load_task
    from mesocosm.env.stack import DockerStack
    from mesocosm.env.task_runtime import DockerTaskRuntime, TaskEnvironmentError

    if not math.isfinite(expected_score):
        raise ValueError("expected score must be finite")
    if verifier_network not in {"none", "bridge"}:
        raise ValueError("verifier network must be none or bridge")
    if shutil.which("docker") is None:
        return {"status": "unavailable", "reason": "docker_executable_missing",
                "detail": "Install or select Docker; execution also requires verified private PID containment.",
                "model_calls": 0, "agent_rollout": False}
    task = load_task(task_path)
    out = out.resolve()
    if _overlaps(out, task.path.resolve()):
        raise ValueError("calibration output must be outside the original task tree")
    if out.exists():
        raise FileExistsError("calibration output must be a new directory")
    out.mkdir(parents=True, mode=0o700)
    control = out / "control"
    control.mkdir(mode=0o700)
    agent = AgentSpec(id="reference")
    project = f"reference-{secrets.token_hex(8)}"
    config_path = control / "opencode.json"
    _write_json(config_path, {"$schema": "https://opencode.ai/config.json", "autoupdate": False})
    compose_file = control / "compose.yaml"
    spec: dict[str, Any] = {
        "name": project,
        "services": {agent.id: {"networks": {"sandbox": {}}, "restart": "no",
            "cap_drop": ["NET_ADMIN", "NET_RAW"], "security_opt": ["no-new-privileges:true"],
            "volumes": [f"{out / 'workspaces'}:/work", f"{config_path}:/root/.config/opencode/opencode.json:ro"],
            "environment": {"OPENCODE_SERVER_USERNAME": "opencode", "OPENCODE_SERVER_PASSWORD": secrets.token_hex(32)},
            "healthcheck": {"test": ["CMD-SHELL", 'curl -fsS -u "$$OPENCODE_SERVER_USERNAME:'
                                                   '$$OPENCODE_SERVER_PASSWORD" http://127.0.0.1:4096/global/health'],
                            "interval": "2s", "timeout": "5s", "retries": 60}}},
        "networks": {"sandbox": {"internal": True}}}
    compose_file.write_text(yaml.safe_dump(spec))
    compose_file.chmod(0o600)
    stack = DockerStack(compose_file)

    class ContainedRuntime(DockerTaskRuntime):
        async def prepare(self, agent: AgentSpec, task: HarborTask, workspace: pathlib.Path, *,
                          reset: bool, restored: bool = False) -> dict[str, Any]:
            receipt = await super().prepare(agent, task, workspace, reset=reset, restored=restored)
            evidence = (await stack.isolation([agent.id]))[agent.id]
            _write_json(control / f"isolation-{workspace.name}.json", evidence)
            if evidence.get("status") != "supported":
                raise TaskEnvironmentError("reference calibration requires verified private PID containment")
            receipt["containment"] = evidence
            return receipt

    runtime = ContainedRuntime(compose_file, control / "native", verifier_network=verifier_network,
                               task_services="isolated", agent_ids=[agent.id])
    result: dict[str, Any] = {"status": "failed", "stage": "build", "model_calls": 0, "agent_rollout": False}
    try:
        info = json.loads(await runtime.command("docker", "info", "--format", "{{json .}}"))
        if info.get("OSType") != "linux":
            raise TaskEnvironmentError("reference calibration requires a Linux Docker daemon")
        native = await runtime.native_image(task)
        runtime.base_compose["services"][agent.id]["image"] = native.image
        compose_file.write_text(yaml.safe_dump(runtime.base_compose))
        result = await calibrate_task(task, runtime, agent, out / "workspaces", out / "calibration")
        measured = result.get("after")
        result["expected_score"] = expected_score
        result["matches_expected"] = (measured["original_score"] == expected_score
                                      if isinstance(measured, dict) else None)
    except Exception as exc:
        (control / "failure.log").write_text(str(exc))
        (control / "failure.log").chmod(0o600)
        result.update(status="failed", error_type=type(exc).__name__, error_artifact="control/failure.log")
    finally:
        cleanup_errors = []
        for cleanup in (runtime.finish, stack.down):
            try:
                await cleanup()
            except Exception as exc:
                cleanup_errors.append(type(exc).__name__)
        result["cleanup"] = "failed" if cleanup_errors else "verified"
        if cleanup_errors:
            result.update(status="failed_cleanup", cleanup_error_types=cleanup_errors)
        result["output"] = str(out)
        _write_json(out / "result.json", result)
    return result
