# ADR-0025: Run bundles export to Inspect eval logs

Status: accepted · Date: 2026-10-18

## Context

The evaluation ecosystem has settled on Inspect (UK AISI): METR is moving Vivaria to it, ControlArena
builds its settings and analysis on it, and `inspect view` is the viewer people already have. Our run
bundle is richer than an Inspect log (full prompts with token ids, services and web logs, receipts,
activations), but a bundle nobody else's tools can open stays inside this repository.

## Decision

- `mesocosm export inspect <bundle>` writes an Inspect eval log, version 2, as JSON
  (`<bundle>/exports/<run id>.json`), the form `read_eval_log` and `inspect view` open.
- One **sample** per agent and task: `id` `<agent>/<task>`, `input` the task instruction, `messages`
  the agent's final model request (system, user, assistant with tool calls, tool results) plus its
  answer, `output` that answer, `scores.verifier` the verifier's reward, `model_usage` the gateway's
  token counts for the window, `metadata` the agent id, task, model-call count and the agent's flags.
- The **eval** carries the manifest name as task, `<provider>/<model id>` as model, the run's
  conditions, a summary of its receipts and its flag counts in `eval.metadata.mesocosm`, and a
  `not_exported` list naming what the bundle has that the log does not: token ids and activations,
  the services and web logs, receipts in full, the harness event stream, raw tool logs.
- The export is written by hand against Inspect's schema and has no dependency on `inspect_ai`;
  its shape is checked against the library outside the test suite (capability matrix).
- Import is not part of this decision: Harbor stays the task format. Inspect tasks and METR Task
  Standard families are a later adapter if a scenario needs them.

## Consequences

- Anyone with Inspect can open a Mesocosm run, compare it with ControlArena or Inspect runs, and use
  Inspect's analysis over our scores and transcripts; the bundle stays the record of truth.
- A sample's messages are the last request's view of the conversation. OpenCode compacts context, so
  a long task's early turns may be summarized in that view; the harness event stream in the bundle
  has them all.
