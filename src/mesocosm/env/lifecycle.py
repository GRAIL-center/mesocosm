"""Evidence for owned Docker workloads; client exit and process containment are separate facts."""

from __future__ import annotations

import re
from typing import Any

CONTAINER_ID = re.compile(r"[a-f0-9]{64}")
PID_NAMESPACE = re.compile(r"pid:\[[0-9]+\]")


def container_evidence(raw: dict[str, Any], container_id: str, project: str, agent_id: str) -> dict[str, Any]:
    """Reject a stale/wrong target before any mutation; retain only non-secret inspection fields."""
    labels = (raw.get("Config") or {}).get("Labels") or {}
    if raw.get("Id") != container_id or not CONTAINER_ID.fullmatch(container_id):
        raise ValueError("container identity does not match the resolved full ID")
    if labels.get("com.docker.compose.project") != project or labels.get("com.docker.compose.service") != agent_id:
        raise ValueError("container project/service labels do not match the requested workload")
    state, host = raw.get("State") or {}, raw.get("HostConfig") or {}
    if (type(state.get("Running")) is not bool or type(state.get("Restarting")) is not bool
            or type(state.get("Pid")) is not int or state["Pid"] < 0):
        raise ValueError("container inspection is missing process state")
    if state["Running"] and (state["Pid"] == 0 or not isinstance(state.get("StartedAt"), str)
                             or not state["StartedAt"]):
        raise ValueError("running container inspection is missing process identity")
    return {"container_id": container_id, "project": project, "agent_id": agent_id,
            "running": state["Running"], "restarting": state["Restarting"], "pid": state["Pid"],
            "started_at": state.get("StartedAt"), "finished_at": state.get("FinishedAt"),
            "exit_code": state.get("ExitCode"), "pid_mode": host.get("PidMode"),
            "privileged": host.get("Privileged"), "network_mode": host.get("NetworkMode"),
            "added_capabilities": host.get("CapAdd"),
            "restart_policy": (host.get("RestartPolicy") or {}).get("Name"),
            "runtime": host.get("Runtime")}


def isolation_evidence(container: dict[str, Any], probe: str | None, *, host_boot_id: str | None,
                       host_pid_namespace: str | None, init_pid_namespace: str | None) -> dict[str, Any]:
    """Compare namespaces on the same Linux kernel, without trusting Docker's requested PID mode alone.

    The control plane runs this probe before agent tools, then binds the result to the inspected
    container ID/start time. A remote daemon or an unreadable host procfs cannot establish containment.
    This is process-namespace evidence, not a claim that an arbitrary container escape is impossible.
    """
    checks = {"private_pid_mode": container["pid_mode"] in ("", "private"),
              "unprivileged": container["privileged"] is False,
              "separate_network_namespace": container["network_mode"] not in (None, "host")
              and not str(container["network_mode"]).startswith("container:"),
              "no_added_capabilities": not container["added_capabilities"],
              "restart_disabled": container["restart_policy"] in ("", "no")}
    pieces = (probe or "").splitlines()
    kernel: bool | None = None
    reason = "host or container namespace evidence unavailable"
    if (len(pieces) == 3 and host_boot_id and host_pid_namespace and init_pid_namespace
            and PID_NAMESPACE.fullmatch(pieces[0]) and PID_NAMESPACE.fullmatch(pieces[1])):
        own, init, boot = pieces
        if boot.strip() != host_boot_id.strip():
            reason = "Docker daemon is not verified on the control plane's Linux kernel"
        elif own != init or init != init_pid_namespace:
            reason = "container procfs and host init-process namespace evidence disagree"
        else:
            kernel = own != host_pid_namespace
            reason = "private PID namespace verified" if kernel else "container shares the control plane PID namespace"
    if not all(checks.values()):
        status = "unsupported"
    elif kernel is None:
        status = "unverified"
    else:
        status = "supported" if kernel else "unsupported"
    return {"container_id": container["container_id"], "started_at": container["started_at"],
            "status": status, "checks": checks, "private_pid_namespace": kernel, "reason": reason,
            "method": "docker_inspect_and_pre_action_procfs", "observed_pid": container["pid"],
            "namespace_evidence": {"host_boot_id": host_boot_id, "host_pid_namespace": host_pid_namespace,
                                   "init_pid_namespace": init_pid_namespace, "container_probe": pieces}}
