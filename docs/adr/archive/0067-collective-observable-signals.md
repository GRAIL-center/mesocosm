# ADR-0067: Frozen collective behavior measurements

Status: accepted

## Context

A fixed-policy population study needs one detector for API-model teacher evidence and
lineage-qualified student evidence. A generic stage rubric does not itself define agent
exposure, adoption, review acceptance or agent-episode denominators. Missing capture and
text similarity must not become negative observations or inferred transmission links.

## Decision

Extend `define_behavior` with a strict `kind: collective` definition: bounded relative
test-file patterns, Python test-function identifiers, a declared impossible-task boolean
and normalized exposure event kinds. Unknown fields, executable expressions and arbitrary
scorers are rejected. The canonical definition is content-addressed in the existing
private behavior store. Existing stage definitions retain their contract.

Add `evaluate_collective` with exactly one source mode: imported `transcript_id`, a strict
`job_id`/`run_id` pair, or previously retained `evaluation_ids` for aggregation. Native
evaluation uses the existing complete lineage and input/verifier reconciliation and the
candidate's own frozen behavior. Imported evidence explicitly has `association: unverified`.
Held-out import, evaluation and retained-record access keep the independent-operator gate.

The detector reports observable test-file fingerprint changes or recorded edit actions,
definitions of named test functions in written Python, and verifier passes only when the
operator explicitly declares the task impossible. These are recorded signals, not intent
or proof that every edit changed the evaluator's behavior. A request alone is not an
executed action; successful grading does not alone prove misconduct on an ordinary task.

Expose first recorded onset and explicit source-linked exposure/adoption. Compare only
recorded comparable clocks. Ambiguous ordering, unknown capture or missing source links
remain unknown. Never infer an exposure from matching prose or a common filename. Native
posthoc exposure records must resolve their exact original read and source-event links.
Review acceptance requires recorded linkage to a submission containing a signal.

Prevalence uses an explicitly declared agent-episode inventory, or native summary episode
outcomes, plus complete relevant capture. Missing units, unattributed signals and absent
coverage leave the value unknown while retaining observed numerator/denominator bounds.
Report every denominator explicitly. Pool distinct retained source transcripts using the
same detector; duplicate imports do not create independent observations. Aggregation is
descriptive, with no confidence intervals or claim of independence across repeated tasks.

## Consequences

The evidence normalizer preserves native link coverage, task identity on native messages
and the documented Unix clock for native journal events. Original source bytes, stable
normalized/source event IDs and hash validation remain intact. Importing a declaration of
complete capture does not independently authenticate it.

The evaluator neither modifies rewards nor runs a second benchmark scorer. Original
ImpossibleBench and fallback execution contracts remain unsupported on the current native
Harbor path; see the original-contract audit.
Missing test-file capture must therefore remain unknown. A different installed backend
must preserve the original completion, submission and sandbox lifecycle before live study
acceptance; there is no prompt-text approximation.

Software evidence: detector fixtures,
native runtime binding tests, and
normalizer tests. These do not measure real
agent behavior or fixed-P transfer. The model/benchmark operator smoke check remains
unverified until real original-contract execution is available.
