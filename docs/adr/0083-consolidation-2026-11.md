# ADR-0083: Consolidation of November 2026

Status: accepted · Date: 2026-11-16

## Context

The repository had 30,600 lines of source in 147 files, 55 commands and 77 decision records, and no
reproduced behavior. Much of it served directions that had been given up.

## Decision

- **Removed.** The research workbench and the restricted designer interface with their skill, which
  ADR-0076 had identified as the bottleneck of the SwarmStill studies. The authored fixture tree and the
  deprecated staged scenarios. The legacy peer-review protocol. In the study package: a second comparison
  engine, the reference schema, the event commands and the budget estimate.
- **Kept.** The native runtime, the gateways, training, activation recomputation, the execution worker and
  the integration contracts.
- **Records.** ADR-0001 to ADR-0077 are in [archive/](archive/README.md). ADR-0078 to ADR-0082 restate
  the decisions that still hold. From here on a record is superseded by a new numbered record, not edited.
- **The removed code** remains on the `main` branch at the time of this record and in the history of this
  one. The SwarmStill results are on the branches `results` and `swarmstill/idr-loop-1`.

## Consequences

- Source is 21,600 lines in 111 files. The test suite is 20,200 lines.
- Not done. The package `core` still holds both the shared types and the run manager, so it and the
  packages `env`, `gateway`, `judge`, `learning` and `adapters` import each other. Six files are longer
  than 800 lines. Separating them is its own piece of work.
- Not done. The mechanisms that only the fixture profile admits (ADR-0081) are still in the code.
