# ADR-0054: Remove agent visualization and retain research functionality

Status: accepted; supersedes ADR-0008, ADR-0009 and ADR-0032.

## Context

The maintainer requested removal of all agent visualization so development concentrates on execution,
conditions, monitoring and evidence. The browser observer had its own server, derived state, constraint
board, model-written status lines, pixel assets and demonstration path. These are independent of the
runtime's original record collection and monitor decisions.

## Decision

- Remove `mesocosm observe serve`, its server/state/constraint/summary modules, static assets and
  UI-specific tests. Remove the host-only observer demo and README graphics and their source files.
- Inspect runs through `report`, `diagnose`, `evaluate`, `audit`, original bundles and worker event APIs.
- Preserve model/harness/tool records, monitor review and intervention, activation recomputation,
  probes, policy training, snapshots and Inspect export.
- Keep the `observe` Python package, manifest fields and optional dependencies for activation analysis.
  Their names are not a browser UI interface. Shared gateway/worker web dependencies remain necessary.
- Update current documentation and README. Prior ADRs remain historical records, marked superseded
  in the decision index; they no longer describe an available observer command.

## Consequences

There is no browser observer, agent map, visual replay or model-generated UI status summary.
Existing research bundles remain readable through the retained analysis interfaces. This removal does
not improve the completeness of captured evidence or establish new real-agent acceptance.

Verify the CLI no longer exposes the removed command, retained analysis commands still work, source
imports contain no deleted modules, packaging contains no UI assets, and the remaining tests/type checks
pass. The README documents project functions and links detailed validation boundaries separately.
