# ADR-0053: Expose native execution policy in worker and calibration entry points

Status: accepted · Date: 2026-10-24

## Context

The original Terminal-Bench `fix-git` task declares a storage quota and installs verifier
dependencies from the internet. Direct native execution already accepts explicit operator options
for these requirements, recording the resource adaptation and verifier network in runtime evidence.
The worker and reference-calibration entry points did not expose both settings. An unchanged task
could therefore fail for an entry-point configuration limitation even on a suitable Linux host.

## Decision

Expose `--verifier-network none|bridge` and `--allow-unenforced-resources` on `worker serve` and
`env calibrate`. Defaults remain `none` and false. Worker policy is operator-owned, applies to every
world in a job, and is retained in capabilities and the durable execution result. It is separate from
the submitted task and manifest. Calibration passes the same policy to both fresh diagnostic
environments and retains their existing native runtime receipts.

These switches neither implement storage quotas nor relax PID containment. The resource opt-in
permits explicitly recorded unsupported storage limits and unavailable host CPU/RAM enforcement;
available CPU/RAM controls still apply. Bridge networking only enables the existing snapshot
verifier path; it does not open agent egress or add internet support to separate live verifiers.
Required containment, original task bytes, three-agent worker admission and cleanup checks remain
unchanged. Refuse invalid network values before starting the worker or calibration.

## Verification and limits

Regression tests cover strict defaults, explicit forwarding through worlds and experiments,
persisted worker policy and calibration options. These are local interface and orchestration tests,
not a real Linux run. [Native acceptance](../native-acceptance.md) requires separate host, calibration,
three-agent, trajectory and lifecycle evidence. The historical Colab wrapper cannot provide required
private PID containment. Task failure or timeout is an observation; neither prompts nor verifier
criteria are changed to manufacture completion.
