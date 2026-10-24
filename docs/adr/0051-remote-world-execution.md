# ADR-0051: Separate experiment submission from whole-world execution

Status: accepted. Extends ADR-0042, ADR-0044 and ADR-0050.

## Context

The local CLI combines experiment orchestration with Docker construction, fixed network addresses
and host PID inspection. Running that controller on a Mac against a remote Docker daemon cannot
provide the Linux host evidence required by native task containment. A dropped laptop connection
must not determine which observations survive, and moving only agent containers leaves service,
verifier and cleanup ownership ambiguous.

## Decision

Keep experiment definitions independent of execution location. A client submits a finite research
world or experiment to a durable worker through an authenticated, versioned API. The worker owns
the complete world, including gateways, harnesses, task services, original verifiers, records and
cleanup. Docker on Linux is the first backend. Inference can remain on a separate GPU host. The
existing direct local commands share execution composition with this backend.

The client materializes declared task and file inputs into a size-bounded indexed archive. Task bytes
and permissions are preserved; file hashes and the package digest are verified again at extraction.
Unreferenced local files, symlinks and special files are excluded or refused. Client gateway addresses
and control credentials are removed. The worker creates a separate resolved configuration with fresh
control credentials and managed endpoints, preserving the submitted package. All condition variants
are validated, including relocated paths, before execution.

A job ID binds one input digest. Repeating that submission returns the existing job; a conflicting
digest is rejected. The client saves its ID and exact package before submission so an uncertain HTTP
response can be resolved without starting a second observation. Accepted jobs outlive client
connections. Stopping is explicit and cleanup completes before another job can own the world.

One worker holds a host lease and runs jobs serially because the current topology uses fixed subnets.
Ownership is persisted before Docker creation. Recovery removes recorded project resources, including
temporary runtime/verifier containers, then records the interruption. It never reruns the observation.
Failed cleanup blocks new execution. This is recovery on worker startup, not an independent watchdog
that operates while the worker or host is down. Manually launched stacks remain operator-owned.

The API is for one trusted research operator. Every route requires a bearer token; the default bind
address is loopback. Clients use an SSH tunnel or HTTPS. The worker requires a private state directory,
a complete source checkout for Docker build assets and access to its local Linux Docker daemon.
Remote jobs require at least three agents, a finite comparison workload, the research profile and
required containment. Persistent streams and policy-training jobs are not remote job kinds.

Model credential environment names must be explicitly allowed by the worker and present on its host.
An allowed name does not restrict the model upstream a trusted submitter can select. This API is not
a multi-tenant credential or workload sandbox. Explicit task/MCP credentials can be input data.
Result downloads contain original run bundles without secret redaction, including recorded run
credentials; they are private research artifacts. Generated Compose files, workspace trees and
unrelated worker files are not exported.

## Consequences and verification

A laptop no longer needs Docker or Linux PID inspection to submit and inspect a world. The current
backend still needs a Linux host with private PID containment; capability preflight alone does not
verify a particular task. No cloud scheduler, second backend or cross-host training controller is
introduced.

Package, API, lifecycle, client and Docker command-boundary tests verify local contracts. They use
injected backends or controlled command responses where Docker is unavailable. They do not establish
acceptance with three real agents, actual tools, native services and the original verifier on a Linux
worker. That integration remains unverified until an appropriate host is available. See the
[execution guide](../execution.md) and [capability matrix](../capability-matrix.md).
