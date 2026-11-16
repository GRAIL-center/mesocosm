# ADR-0043: Authenticate model calls and expose only the observed inference surface

Status: accepted · Date: 2026-10-19

## Context

The gateway accepted unknown keys and unauthenticated identity headers. An agent could drop its key to
escape its budget or a stopped run, and those calls no longer belonged to the run's records. The generic
proxy also exposed `/v1/completions`, `/v1/responses` and upstream management endpoints without the chat
path's controls or full exposure record. Concurrent requests checked usage before any call completed,
so even a request budget of one admitted an arbitrary number of calls.

## Decision

- Every proxied call requires a configured agent key or the gateway control key. Only the control key
  may supply identity headers; an agent key still takes precedence over headers. Missing and unknown
  keys receive the API's ordinary `401 invalid_api_key`, and an `auth_denied` record is kept.
- The proxy exposes `POST /v1/chat/completions` and `GET`/`HEAD /v1/models` only. All other upstream
  paths return 404 and are recorded as `endpoint_denied`. `/health` stays public; `/admin/*` keeps its
  existing control-key check. A new inference API must implement the same controls and full recording
  before it is exposed.
- Requests from one agent are serialized through response completion, including the final streaming
  chunk. Different agents proceed concurrently. Every admitted upstream attempt consumes a request
  before the upstream call is awaited, including attempts that fail to connect or return an error.
- A completion-token budget caps the request's output limit at the remaining allowance (divided by
  `n` for multiple choices). Both legacy `max_tokens` and `max_completion_tokens` callers are supported.
  Prompt-token usage is checked at response boundaries: exact preflight token counts are unavailable
  across arbitrary OpenAI-compatible servers, so one prompt can cross that threshold. Serialization
  prevents additional concurrent calls from multiplying the overshoot.
- A response without usage makes further token-budgeted requests fail closed. Known usage remains
  separate from missing usage; token counts are never invented. Stream cleanup records partial exposure
  and accounts for usage before awaiting connection cleanup, including downstream disconnects.
- A request released from one pause checks for any remaining agent or whole-run pause before proceeding.

## Consequences

- OpenCode's configured OpenAI-compatible provider continues to use the supported chat endpoint.
  Control-plane harnesses must authenticate their identity headers with the control key. Anonymous
  inference and generic upstream passthrough are no longer supported.
- Same-agent parallel subagents share an ordered budget; three or more different agents retain parallel
  model access. A provider must respect the forwarded output cap and report usage for token accounting.
- `tests/test_gateway.py` checks invalid-key/header bypasses, denied inference/management paths, three
  concurrent agent identities with streaming and non-streaming responses, shared output allowance,
  partial streams and overlapping holds. These CPU tests verify enforcement, not real-model behavior.
