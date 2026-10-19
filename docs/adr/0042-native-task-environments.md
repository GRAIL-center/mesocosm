# ADR-0042: Execute and verify tasks in their native environments

Status: accepted · Date: 2026-10-19

## Context

Importing a benchmark directory did not import its execution environment. Every agent still ran the generic
OpenCode image, a bind mount concealed any source already in the task image, and the verifier used Debian
regardless of the task's dependencies. Loading real instructions was insufficient to run real tasks.

## Decision

- The CLI defaults to a native task runtime. A task must declare `[environment].docker_image` or provide
  `environment/Dockerfile`; the latter builds with `environment/` as its context. Missing environments,
  task Compose sidecars, GPU tasks and separate verifier environments fail explicitly. The first supported
  harness layer targets Debian/Ubuntu images; it installs the released OpenCode package and retains the
  task's user, working directory and startup command. It adds Node 22 and curl/CA/libstdc++ packages,
  recorded as an adaptation; this can replace `/usr/local/bin/node` and is not a promise of identical
  dependency versions. A native WORKDIR that would conceal the root filesystem is rejected.
- At a task boundary the runtime copies the image's working tree to the empty host workspace *before*
  mounting that workspace at the original WORKDIR. Existing snapshot workspaces are preserved. The agent's
  service is recreated on its existing sandbox network, using its own config/key. The original application
  command is supervised alongside OpenCode; a failed application exits the container. A mount that is not
  writable by the original native user fails explicitly rather than substituting root. Carrying a harness
  session across different native environments is unsupported. Egress is checked again after replacement.
- Verification snapshots the completed agent container's filesystem into a private, temporary image. The
  original task script runs with its shebang, original WORKDIR and user, the agent's resulting working tree,
  and its installed dependencies. Tests are mounted read-only only in this verifier, and outputs go to a
  dedicated log directory. The controller enforces timeout; nonzero exit, missing reward and nonfinite
  reward are infrastructure failures. Native verifier stdout/stderr and image IDs are retained.
- A filesystem snapshot does not capture running applications, in-memory databases or process state.
  Tasks whose verifier requires live processes are unsupported by this runtime. This is not a general
  replacement for Harbor's multi-service orchestration; the first live target is a filesystem/Git task.
- Verifiers default to `--network none`. `--verifier-network bridge` explicitly permits a verifier's own
  dependency installation after the agent has finished. It never joins the sandbox; agent proxy, model
  credential and OpenCode config variables are cleared, and control/config mounts are absent. The original
  benchmark scorer remains unchanged. This verification network is recorded as an adaptation.
- CPU/memory limits are applied to agent and verifier containers when the Docker daemon supports cgroups.
  Storage quotas are unsupported. Requested limits that cannot be enforced reject the task by default;
  `--allow-unenforced-resources` permits an explicitly adapted run with requested, applied and unsupported
  resources recorded separately. This is needed on Colab rootless Docker without cgroup delegation. No
  unenforced limit is reported as applied.
- `--task-runtime fixture --verifier-image <image>` is the explicit compatibility path for hand-written
  smoke fixtures. There is no implicit Debian fallback. Native operation also rejects known default control
  keys; credentials must be generated in a control-only manifest.

## Verification

`tests/test_task_runtime.py` covers native-source staging, workdir/user/network/resource preservation,
original Bash verifier invocation, private filesystem snapshots, truthful failures, cleanup on cancellation,
unsupported resources, task Compose rejection, environment change detection and native startup failure.
Harbor adapter tests cover explicit verifier images and task provenance. A live three-agent benchmark run
is separate evidence; mocked Docker command tests do not establish that a Docker image actually builds.
