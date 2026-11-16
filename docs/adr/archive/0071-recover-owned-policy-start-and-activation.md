# ADR-0071: Recover owned policy start and activation

Status: accepted

## Context

ADR-0069 reserved a designer training attempt before native admission. The native queue
persisted its job and completed its idempotency record before starting its supervisor.
A transient supervisor-launch failure therefore left an existing queued job, but retrying
the public request only returned the saved designer response. Activation had a similar
boundary: persisting the local activation key followed by an exception before native
admission permanently required reconciliation even though there was no deployment to verify.

## Decision

Retain the original public training request identity, native job identity, dataset,
configuration, source pins and full budget reservation. Record supervisor launch state
separately as `worker_start: pending | started | failed`; `started` acknowledges supervisor
launch, not an optimizer update. Repeating the exact public training request after a
known launch failure starts the existing queued job. It never resubmits native training.
Add `policy_status(action="start")` for explicit session-owned queue recovery, including
a crash after native job persistence but before either request completion or designer
acknowledgement. Recover such a job only through its exact native request digest,
deterministic native job ID and matching dataset/configuration/source identity.

Serialize competing designer starts. Recheck native state and start only queued jobs;
running and terminal jobs retain their existing attempt. Native supervisor/child queue
leases and atomic claims continue to prevent duplicate optimizer attempts, including a
crash after supervisor creation but before acknowledgement. Unresolved native admission
cannot create a replacement job. Failed reservations are never refunded. `start` does not
resume an interrupted optimizer or allocate cloud resources.

For activation, inspect the durable native request table and owned alias/training-job
inventory under the native serving lease. If neither contains admission evidence, return
`activation_not_started`. A normal retry can then call native deployment with the exact
same key and alias. `reconcile=true` remains verification-only and explains that no native
activation was admitted in this case. If any native request or deployment exists, treat
activation as admitted or uncertain: recover its unique owned job when possible, require
explicit reconciliation and never reload. Incomplete request-only admission, ambiguous
jobs and a busy native lease fail closed. An activation key alone is not evidence that
a load occurred.

## Consequences

Transient worker-launch and pre-admission activation failures are recoverable without
charging another attempt, creating another checkpoint, changing serving aliases or
weakening uncertain-load protection. Public diagnostics remain bounded and omit native
IDs, paths, credentials and exception text. Existing policy/session ownership checks and
post-final mutation restrictions remain in force.

Policy lifecycle contracts inject both reported
failures, verify exact identities and budget preservation, exercise concurrent retries,
recover persisted-but-unacknowledged jobs, and verify that admitted or ambiguous activation
never invokes another load. These are software contracts; they do not establish live vLLM
availability, GPU training effectiveness or behavioral improvement. See the
operator recovery guide.
