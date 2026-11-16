# ADR-0072: Native structure graphs and designer composition

Status: accepted

## Context

ADR-0070 identified three separate S controls: information visibility, work/review
relationships, and execution order/final authority. Selecting a channel or an `all`/`ring`
review topology did not express “two workers finish, then one reviewer alone submits.”
Prompt instructions cannot enforce these controls. Designer-controlled population also
needs approved role templates rather than arbitrary persona or tool instructions.

## Decision

Add native `structure.work`, `structure.reviews` and `structure.execution`:

- Work maps a source agent to artifact readers. Its exact edge set must equal
  `services.access.artifacts`, in addition to ordinary board read permission.
- Review maps each reviewer to subjects. The actual verdict endpoint admits only assigned
  verdicts, and the stage checks exactly one accept/reject per subject before completion.
- Execution declares an acyclic `depends_on` relation and nonempty sink `finalizers`.
  Actual predecessor stage completion admits each successor; failures do not satisfy a
  dependency. Every subordinate precedes every designated finalizer. Review subjects and
  granted work sources must precede their reviewers/readers.

Native work completion quiesces and cleans the subordinate task runtime, preserves its
completion record and publishes only declared workspace artifacts. It does not invoke the
original verifier. Only designated finalizers proceed to original task verification.
Subordinate work remains `completed_ungraded`, with no invented success or zero score.
Record admissions, completions, review checks, publication and finalization with source
links. A failure terminates owned workloads; it cannot advance dependent stages.

The first supported profile is Docker/OpenCode isolated comparison, reset memory,
all-agent assignment and `as_ready` task starts. Reject shared task services, shared
mounts, staggered starts, side-task verification and reward/feedback/history/reputation
episode barriers. Work exchange requires declared artifacts, completed eligibility and
no original-score exposure. These restrictions preserve native task and verifier
contracts; they are not prompt approximations.

An optional private `structure_space` exposes a bounded designer `StructureInput` and
`structure_catalog`. Operators approve role templates, public team labels, service/channel/
repository domains and a 3–32 agent range. Explicit population lists contiguous ordinal
slots; clone only approved base-agent templates while preserving their original role
instructions, tools, skills, models and per-agent budgets. Resolve duplicate persona
usernames deterministically without changing instruction text. Omitted population keeps
the base agents while public ordinal slots map privately to their IDs.

Compile visibility directly into native service, channel, direct-message, repository and
artifact ACLs. Approved `team:<id>` selectors expand to exact member grants, and team groups
are retained as role metadata. This supplies real team permission grouping; team-specific
reward allocation remains unsupported. No arbitrary persona, source paths, task text,
evaluator, model configuration or executable extension is accepted through S.

Apply approved typed global S/E changes and compiled graphs before final native
validation, so temporary invalid intermediate settings cannot block a valid combination.
The compiler owns graph/access fields, service enabling, isolation and the graph review
protocol; it enables chat when reviews require it. Advertise these prerequisites and
retain exact before/after receipts. Other incompatible conditions remain errors. Preserve
original source-relative task, skill and observation paths by absolutizing against the
original manifest directory before writing a derived manifest.

Bind the base manifest and private space bytes to the designer session. Keep an immutable
private composition receipt containing original-source hash, normalized public input,
approved changes, actual manifest, role/member map and composition hash. Native experiment
configuration separately identifies the compiled source and its own frozen package;
never label that compiled hash as the original source hash.

## Consequences

A designer can construct approved populations and independently enforce communication,
artifact, review and scheduling relationships. P selection can bind to the resulting agent
IDs. Explicit unsupported combinations still fail before native execution. Role/team
names alone grant nothing; only compiled native edges and existing backend admission do.

Evidence: [native graph access](../../../tests/test_structure_access.py),
[native scheduler](../../../tests/test_structure_runtime.py),
[quiescent work completion](../../../tests/test_structure_native_completion.py), and
designer composition. The composer tests include
3, 6 and 32 agents, private template preservation, path relocation, exact artifact ACLs,
team expansion, graph dependencies and safe diagnostics. These software tests do not replace
three-agent Docker/OpenCode acceptance with original tasks and trajectory inspection.
See the designer structure guide.
