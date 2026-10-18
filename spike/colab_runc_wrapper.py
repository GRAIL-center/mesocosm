#!/usr/bin/env python3
"""runc wrapper for the Colab VM: its sandbox denies mounting procfs/sysfs/devpts/mqueue (EACCES even
for root), so replace those spec mounts with bind mounts of the host's and drop the pid namespace
(a bound host /proc is only consistent without a private pid namespace). Networking, mount namespace
and the rootfs are untouched, so Docker networks behave as they would anywhere else."""
import json
import os
import sys

RUNC = "/usr/bin/runc"
BINDS = {"proc": "/proc", "sysfs": "/sys", "cgroup": "/sys/fs/cgroup", "cgroup2": "/sys/fs/cgroup",
         "mqueue": "/dev/mqueue", "devpts": "/dev/pts"}
RO = {"sysfs", "cgroup", "cgroup2"}

args = sys.argv[1:]
if "create" in args or "run" in args:
    bundle = os.getcwd()
    for flag in ("--bundle", "-b"):
        if flag in args:
            bundle = args[args.index(flag) + 1]
    cfg = os.path.join(bundle, "config.json")
    with open(cfg) as f:
        spec = json.load(f)
    mounts = []
    for m in spec.get("mounts", []):
        t = m.get("type")
        if t in BINDS:
            if not os.path.exists(BINDS[t]):
                continue
            m = {"destination": m["destination"], "type": "bind", "source": BINDS[t],
                 "options": ["rbind", "nosuid", "noexec", "nodev"] + (["ro"] if t in RO else [])}
        mounts.append(m)
    spec["mounts"] = mounts
    linux = spec.setdefault("linux", {})
    linux["namespaces"] = [n for n in linux.get("namespaces", []) if n.get("type") != "pid"]
    with open(cfg, "w") as f:
        json.dump(spec, f)
os.execv(RUNC, [RUNC] + args)
