# Architecture decision records

One file per decision, numbered. Status is `proposed`, `accepted`, `superseded by ADR-n`. Keep each record short: context, decision, consequences.

| ADR | Title | Status |
|---|---|---|
| [0001](0001-record-decisions.md) | Record architecture decisions | accepted |
| [0002](0002-opencode-first-unmodified.md) | OpenCode is the first harness and is used unmodified | accepted |
| [0003](0003-model-gateway.md) | A model gateway is the enforcement and exposure-logging point | accepted |
| [0004](0004-training-ready-records.md) | Records are training-ready; no trainer in scope | accepted |
| [0005](0005-behavior-only-default.md) | Behavior-only runs are the default; activations are selective | accepted |
| [0006](0006-smoke-test-hardware.md) | Smoke tests run on a local RTX 3090 with a 3B model | accepted (model choice superseded by ADR-0007) |
| [0007](0007-smoke-test-model.md) | Smoke-test model is Qwen3-4B-Instruct-2507, served with a 32k context | accepted |
| [0008](0008-live-observation.md) | Live observation reads the run bundle; it never writes to it | accepted (summary clause superseded by ADR-0009) |
| [0009](0009-model-written-status-lines.md) | Model-written status lines are derived artifacts kept beside the bundle | accepted |
| [0010](0010-world-services.md) | The world is a set of ordinary services that identify callers by address | accepted |
