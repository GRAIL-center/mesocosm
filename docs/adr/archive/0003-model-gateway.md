# ADR-0003: A model gateway is the enforcement and exposure-logging point

Status: accepted · Date: 2026-10-17

## Context

Three requirements point at the same place:

1. We must know what the model actually received (the proposal's "actual information exposure"), not what was available in the environment.
2. Resource conditions (token budgets, rate limits) must be enforced where they take effect, and leave receipts.
3. Activations captured at the inference worker must be joined to the request that produced them.

Harnesses manage their own context, so the only place where the exact model input is visible without modifying the harness is between the harness and the inference server.

## Decision

- All model traffic from experimental agents goes through an OpenAI-compatible reverse proxy, the **model gateway** (`mesocosm.gateway`).
- The gateway stamps every request with `X-Mesocosm-Request-Id`, reads `X-Mesocosm-Agent` and `X-Mesocosm-Run` from headers set in the harness's provider configuration, logs the full request and response, enforces per-agent budgets, and returns explicit errors when a budget is exhausted.
- Where the inference server supports it, the gateway requests prompt and completion token ids so that records contain exact token sequences.
- Commercial baseline models are reached through a separate gateway instance outside the sandbox, at fixed endpoints.

## Consequences

- The gateway is on the critical path; it must stream and add little latency. It is a small service with no dependencies beyond an HTTP client.
- A gateway record is evidence of availability to the model, not of attention or use. Interpretation stays with the analysis.
- Agents hold a gateway API key only; it grants model access and nothing else.
