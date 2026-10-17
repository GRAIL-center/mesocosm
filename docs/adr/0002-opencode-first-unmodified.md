# ADR-0002: OpenCode is the first harness and is used unmodified

Status: accepted · Date: 2026-10-17

## Context

The research purpose requires agents that work the way deployed coding agents work: real tools, real context management, real task pressure. Incidents reported in 2026 arose in such settings. Writing our own agent loop would make the environment less realistic and would make every result "about our harness". MARGIN Arena, the author's earlier environment, was a custom loop on Inspect AI; its designs (resource limits, separate logging of requested tool calls and actual results, selective activation storage) are kept, the loop is not.

OpenCode is open source, exposes an HTTP server with session management and an event stream, and accepts any OpenAI-compatible provider, which lets us route it through our gateway to vLLM without code changes.

## Decision

- The first supported harness is OpenCode in server mode (`opencode serve`), driven over HTTP and observed through its SSE event stream.
- We do not fork or patch the harness. All control is exerted through its configuration (provider, headers, permissions) and its API; all observation is from outside (gateway, services, events).
- Additional harnesses (OpenHands, Codex CLI) are added as adapters that declare their capabilities in `docs/capability-matrix.md`.

## Consequences

- Some controls will be impossible or coarse (for example, forcing a memory reset may require a new session rather than editing context). Such limits are documented as `unsupported` rather than worked around inside the harness.
- Mesocosm results describe agents as they are shipped, which is what the research question asks about.
