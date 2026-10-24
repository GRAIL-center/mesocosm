# ADR-0049: Enforced research controls and independently inspectable evidence

Status: accepted

## Context

Research needs controllable interactions between real agents, complete trial accounting and independent
measurements. Scripted incidents do not supply these. Existing model admission, session abort, public
services and time-window joins leave important execution and measurement boundaries implicit.

## Decision

1. Inspect owned Docker containers before agent work. Standard native execution requires verified
   private PID namespaces, unprivileged configuration and disabled restart. An explicitly `adapted`
   manifest retains unsupported/unverified isolation evidence. Interrupt active turns on a stop request.
   On failure, separately record gateway refusal, harness abort and owned-container termination.
   Full container IDs and Compose project/service labels must match before any kill. Confirmed descendant
   termination additionally requires cached pre-action namespace evidence for that container/start time.
   Successful Compose teardown must leave an empty project container inventory.
2. Add optional default-deny internal service access grants, directed chat edges and channel memberships.
   Require separate agent networks and remove raw-packet/network-administration capabilities. Apply
   policy to the logical resource, including Git smart HTTP and underlying MCP tools, rather than trusting
   the Host header. Reject shared-directory bypasses. An allowed shared resource or internet destination
   remains a potential relay; this is an access graph, not a noninterference proof.
3. Give new events stable IDs, explicit execution identities and evidence-backed links. Unknown identities
   and missing links stay unknown. Exact message/object versions establish exposure, not behavioral
   causation. Detached bundle anchors verify exact bytes against a separately retained reference;
   merely putting an anchor outside the bundle does not prove independent custody or truthful capture.
4. Enforce shared model-request, output-token, active gateway-request and service-request pools at their
   actual admission points. Reserve output caps before dispatch, normalize both token-limit fields, and
   retain unknown accounting on failures. Reviewer calls have their own existing budget. These are real
   request controls, not GPU allocation, monetary accounting or simulated ledger balances.
5. Allow joint before-tool assessment over actual agent sessions and optional shared-service/web
   evidence. Store the exact assessed input and hash. Collection spans an explicit interval and is not
   an atomic snapshot. Configured decision delay consumes the deadline. Missing/oversized evidence and
   exhausted budgets fail closed. Assessment, denial acknowledgement and workload termination remain
   separate observations; this does not mediate syscalls inside an approved tool.
6. Pin materialized experiment inputs, check drift before repetitions, and offer a strict three-agent
   native baseline profile. Record declared model revisions separately from verified serving identities.
   Preserve failures and all unrun trials. Environment hashes do not make remote image tags immutable.
7. Report original task outcomes, allocated incentives, reviewed behavior, monitor confusion and costs
   separately. Only explicit reviewed assessment IDs enter confusion matrices, including negative
   predictions; unreviewed/unclear evidence is not safe. Optional held-out training worlds run before
   and after updates without an optimizer step, with disjoint task content and preserved failures.

These decisions refine ADR-0010/0014/0043/0044/0046. They do not change the original task verifier,
introduce desired-behavior scripts or establish any misalignment or learning-effect result.

## Consequences and evidence

See [research controls](../research-controls.md) for configuration and the
[capability matrix](../capability-matrix.md) for the tested boundary. New controls have local plumbing
and regression tests. They do not inherit real-agent acceptance from earlier A100 runs. Standard Linux
containment, new joint monitoring and a repeated held-out real-agent study require new live evidence.

Docker's [kill operation](https://docs.docker.com/reference/cli/docker/container/kill/) signals the
container's main process. [Inspect](https://docs.docker.com/reference/cli/docker/inspect/) provides
state/identity evidence; neither API alone guarantees containment when a runtime removes PID isolation.
The existing Colab wrapper is therefore not standard containment evidence.
