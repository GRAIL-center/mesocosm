# ADR-0009: Model-written status lines are derived artifacts kept beside the bundle

Status: accepted · Date: 2026-10-18 · Supersedes the "summaries are rule-based" clause of ADR-0008

## Context

Rule-based node summaries ("Running git reflog | head") are exact but read like logs. Researchers
watching many agents want one plain line per node ("Searching git history for the lost commits").
That line is an interpretation, so it must never be confused with the record.

## Decision

- `mesocosm observe serve --summarizer-url … --summarizer-model …` asks an OpenAI-compatible
  endpoint for one line (≤ 8 words, present tense, plain words; `--summary-lang en|ko`) whenever a
  session's activity signature changes. Calls are rate-limited per node and bounded in flight.
- Results are appended to `<runs>/<run_id>.observe/summaries.jsonl` with the signature, model and
  language, never inside the bundle. A replay reuses them without calling the model.
- On screen a model-written line carries a ✦ mark; the rule-based line remains the fallback and the
  model's input. A summarizer failure leaves the rule-based line in place.
- The summarizer calls the inference server directly, not through the gateway, so these calls never
  appear in any agent's exposure record.

## Consequences

- Judges (M4) may reuse the same derived-file convention for their outputs.
- The smoke-test model (ADR-0007) is good enough for status lines; the pilot can use a different one.
