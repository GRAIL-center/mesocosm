# ADR-0005: Behavior-only runs are the default; activations are selective

Status: accepted · Date: 2026-10-17

## Context

Compute is shared (GRAIL allocation at RCAC) until external funding arrives. Activation capture adds latency and storage, and most runs are for measuring occurrence rates, where behavior records suffice. The proposal already specifies a behavior-only pilot.

## Decision

- A run records behavior (events, exposure, messages, environment state, cost) unless the manifest enables activation capture.
- With capture enabled, the default policy stores per-step summary signals and raw tensors only for suspicious windows; everything else is recomputed on demand from recorded tokens.
- Cost per agent-hour is measured from M1 and reported with every milestone.

## Consequences

- The recompute path (ADR-0003, M5) is a first-class component, and its numerical difference from original capture is measured and reported.
- CI runs a two-agent toy world on CPU with a very small model, with capture disabled.
