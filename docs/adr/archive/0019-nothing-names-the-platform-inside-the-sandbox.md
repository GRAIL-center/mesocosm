# ADR-0019: Nothing inside an agent container names the platform

Status: accepted · Date: 2026-10-18

## Context

An agent that can tell it is in an experiment behaves differently from one that cannot. Until now
the agent container told it plainly: a dozen `MESOCOSM_*` environment variables (gateway URL and
key, peers, channels, the inference server's address to probe), an entrypoint script with comments
about the platform, an `egress-check` binary that talked about isolation and covert channels, a
provider named "Mesocosm gateway" in the harness config, identity headers `X-Mesocosm-Agent` /
`X-Mesocosm-Run`, refusals such as `mesocosm_budget_exhausted` with "budget exhausted for agent
agent-1" in the message, a model id `mesocosm-3b`, authors on the board named `agent-1`, and a
gateway whose control endpoints (`/mesocosm/budget`, `/mesocosm/pause`) any agent could reach and
call: an agent could have raised its own budget.

The research goal (ARCHITECTURE.md) is a world the agents take for an ordinary workplace, with the
platform's authority intact. Both halves matter: a sandbox that looks like a lab biases what it
measures, and a control surface the subjects can touch is not a control surface.

## Decision

- **No environment, no scripts, mounted files.** The agent service in the rendered topology carries
  no environment variables of ours. The control plane renders the harness config
  (`opencode.json`), the git identity (`gitconfig`) and, when a persona has a `brief`, the harness
  rules file (`AGENTS.md`) into `runs/<run label>/agents/<agent id>/` (`write_agent_files`) and
  mounts them read-only. The agent image is OpenCode and its tools; the entrypoint script and the
  egress-check binary are gone. The egress check is piped into the container over
  `docker compose exec -T <agent> sh -s`, with the facts it needs passed as variables of that
  process only.
- **Identity by key.** Every agent gets its own gateway key, derived from the control key, the run
  label and the agent id (`agent_api_key`, HMAC-SHA256, `sk-…`). The gateway maps keys to
  `{agent, run}` (`MESOCOSM_AGENT_KEYS`); a key beats any header, and neither the key nor identity
  headers are forwarded upstream. Identity headers remain for harnesses the control plane drives
  directly (Harbor's OpenCode, the host spike); records say `identified_by: key | header | none`.
- **A control surface only the control plane has.** The gateway's admin endpoints move to `/admin/*`
  and require the control key (`MESOCOSM_CONTROL_KEY` = `gateway.api_key`); to anyone else they
  are 404, and the attempt is logged as `admin_denied` with the caller's identity. `/health` says
  `{"ok": true}` and nothing more. The services already treat `/admin/*` the same way (ADR-0011).
- **Refusals look like the API's own.** Budget exhausted is 429 `insufficient_quota`; a stopped run
  is 403 `account_deactivated`; a hold past its limit is 503 `service_unavailable`; an upstream
  failure is 502 `server_error`. None names the run or the agent. The gateway log keeps the real
  reason (`budget_exhausted`, `run_stopped`, `hold_expired`, `upstream_error`), so the bundle loses
  nothing. This replaces the `mesocosm_*` error types of ADR-0003 and ADR-0014.
- **Neutral names.** The provider in the harness config is `gateway.provider_id` /
  `gateway.provider_name` (`internal`, "Internal LLM API" by default); the model id in the examples
  is the name vLLM serves (`qwen3-4b`), replacing `mesocosm-3b` (ADR-0007's naming clause).
- **Personas.** `agents[].persona {username, name, email, hostname, brief}` is how the agent appears
  in the world: the board, the pastebin and the chat show `username`, commits carry `name <email>`,
  the container may take `hostname`. Chat recipients and verdict subjects may be named by username
  or by agent id. The services log and the control endpoints keep agent ids; the board's `write`
  record carries `author` (the username) so the impersonation detector knows the agent's own name.
  Without a persona the username is the agent id, as before.
- **A domain.** `services.domain` makes every world hostname also answer as `<host>.<domain>`
  (`board.corp.example`, `gateway.corp.example`) and is the personas' default e-mail domain.

## Consequences

- From inside a container the agent sees OpenCode, a config pointing at an internal LLM API with its
  own key, a git identity, a workspace, a team board and wiki with named colleagues, a package
  mirror. Residuals remain: the compose service names (`agent-1`, `control`, `gateway`) resolve on
  the network, the fixed `172.30.x.x` addressing, and bind-mount sources in `/proc/self/mountinfo`
  show host paths (which include the repository directory's name). These are recorded here rather
  than hidden; an agent that reads `mountinfo` to find out where it is has already told us something.
- Old compose files and the `agent-entrypoint.sh`-based image do not work with this code; the static
  spike compose mounts `spike/docker/agent-1.opencode.json` instead.
- `GatewayAdmin` needs the control key (the CLI takes it from the bundle's manifest); the spike
  scripts that curl the gateway must send `Authorization: Bearer <control key>` to `/admin/*`.
- An agent that guesses or steals another agent's key would be recorded as that agent at the gateway
  while the services still identify it by address; the keys are 160-bit HMAC outputs and never leave
  the agent's own config file and the gateway.
