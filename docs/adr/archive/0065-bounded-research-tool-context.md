# ADR-0065: Bounded research tool context and recoverable navigation

Status: accepted

## Context

The workbench preserved original evidence but returned entire transcripts and factor catalogs to
research agents. Generic CLI errors hid input mistakes; live trainer progress was absent from job
inspection. A download interrupted between directory creation and receipt persistence could not
be fetched again. Agents therefore needed repeated large reads, source inspection or manual repair.

## Decision

Use `ResearchWorkbench.call` as the agent-facing boundary, shared by the JSON CLI. Preserve full
direct Python results for trusted internal callers and evaluation. Validate declared argument
schemas before dispatch; expose selected nested schemas through `describe_tool`. Return bounded,
redacted error details without claiming that unstored diagnostics exist.

Bound dispatched results to 24 KiB of JSON UTF-8. Retain oversized complete results by immutable
reference. Transcript imports return counts and IDs; `read_events` and `read_record` provide bounded
pages, exact text continuations and explicit omission markers. `list_factors` defaults to a filtered,
paged index, with detail on request. `list_records` supports recovery after agent context loss.
Declared held-out gates apply to these new read paths and linked records.

Read bounded progress from the owned trainer output, and support state-version-based unchanged
responses with a suggested polling interval. Keep training metrics separate from behavioral evidence.
Journal downloaded result identity before admission, retain unadmitted directories, verify recovered
bytes and original job/input identity, and never create a replacement experiment during retrieval.

## Consequences

Original evaluation/export evidence remains complete. Existing direct Python consumers retain full
result access; LLM adapters must use the dispatcher. Pagination and default factor discovery change
the agent response contract, documented as tool-index schema version 2. This bounds individual tool
responses, not arbitrary client prompt history, total campaign costs or tokenizer-dependent tokens.
No scientific search strategy, intermediate checkpoint deployment or native acceptance is implied.

See the workbench guide and its regression tests.
