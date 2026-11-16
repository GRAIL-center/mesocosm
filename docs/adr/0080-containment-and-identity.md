# ADR-0080: Containment, identity and unmodified components

Status: accepted · Date: 2026-11-16 · Consolidates ADR-0002, ADR-0003, ADR-0019, ADR-0022, ADR-0029 and ADR-0043

## Decision

- **Components are used unmodified.** OpenCode, Harbor, vLLM and NNsight are controlled through
  configuration and APIs. Their internals are never patched or forked. A limit is recorded as
  `unsupported`.
- **Two gateways are the only way out of the sandbox.** Agents reach inference through the model gateway
  and, when the internet is enabled, the outside through the web gateway, which applies a policy and logs
  every flow. No host networking, no second interface on an agent container, no route around the web
  gateway.
- **The model gateway enforces and records.** It authenticates each agent by its own key, enforces
  budgets, routes each request to the server of the model it names (vLLM or another OpenAI-compatible
  API), and logs the exact request and response. Agents never hold a model server's key.
- **Nothing inside an agent container names the platform.** No platform-named environment variables,
  scripts, hostnames, model ids or error texts. Refusals to the harness are the OpenAI API's own shapes
  and never name the run or the agent; the gateway log keeps the reason. Control endpoints live under
  `/admin/*` behind the control key.
- **Platform files stay out of the agent workspace.** The harness configuration is rendered by the control
  plane and mounted read-only.

## Consequences

- Operator checks made from inside an agent container are logged as that agent.
- A study's command environment runs outside this containment (ADR-0079).
