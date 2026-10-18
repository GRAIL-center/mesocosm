# ADR-0029: One gateway, a model per agent

Status: accepted · Date: 2026-10-18

## Context

A world had one model: `model.id`, served by the vLLM instance the gateway was started with. The
proposal compares open-weight agents with commercial baselines and mixes models in one world (does a
weaker agent follow a stronger one into a scheme? does collusion need the same model on both sides?),
and ADR-0003 answered that with "a separate gateway instance outside the sandbox" for commercial
models. A second gateway means a second log, a second budget table, a second identity scheme and a
second thing to keep ADR-0019-clean. The chokepoint is valuable because it is one.

## Decision

- **The manifest carries a model catalog.** `model` stays the default; `models` lists further
  `ModelSpec`s, and `agents[].model` picks one by id. A `ModelSpec` may name an `upstream` (the
  OpenAI-compatible server's root; the harness's `/v1/...` path is forwarded as is), an `api_key_env` (the name of the variable holding its key) and its
  `kind`: `vllm` (token ids recorded, activations recomputable) or `openai` (any other API, behavior
  only). It also carries the context and output limits the harness is told.
- **The gateway routes by the model the request names.** `MESOCOSM_UPSTREAMS` maps model ids to
  `{url, key_env, kind}`; a request for a model not in the map goes to the default inference server
  with `MESOCOSM_UPSTREAM_KEY`. Each upstream has its own HTTP client; the record carries `upstream` and
  `model_kind`; `return_token_ids` is asked of vLLM only. Budgets, holds, stops and refusals are per
  agent as before, whatever the upstream. `/admin/health` lists the upstreams and whether a key is
  present, never the key.
- **Secrets stay in the environment.** The compose renderer writes
  `KEY_NAME: ${KEY_NAME:?... required by model ...}` on the gateway service, so `docker compose up`
  interpolates the key from the operator's shell and fails loudly without it; no rendered file, no
  agent file, no record holds it. Validation warns when the variable is unset.
- **An agent's config names its model and nothing else of the catalog**: one provider, one model
  entry, the agent's own key. The run manager sends the agent's model id to the harness; the Inspect
  export reports each sample under the agent's model with usage per model; `activations capture
  --served-model` recomputes the requests of one served model with its own weights.

## Consequences

- ADR-0003's separate-gateway clause is superseded. Commercial baselines are ordinary agents in the
  same world, logged and refused the same way; they reach the internet through the gateway container's
  own network (`edge`), never through the sandbox.
- A `kind: openai` model yields no token ids: no activations, and the manifest warns when
  `observe.activations` is on. Its responses are still full records.
- `examples/world-two-models.yaml` puts a vLLM agent and a Claude agent on one board; it validates
  without the key (a warning) and starts only with it.
