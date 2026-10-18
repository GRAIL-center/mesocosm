# ADR-0021: Agents get MCP servers and skills through the harness config; the world is an MCP server too

Status: accepted · Date: 2026-10-18

## Context

Agents in the wild carry tools and procedures: MCP servers in their harness config and skill files
their harness discovers. A world where the only tools are bash and the built-in file tools, and the
only instructions are the task text, is narrower than the deployments the research is about, and it
cannot study what agents do with tool access that goes beyond the shell. OpenCode (pinned 1.19.3)
supports both: MCP servers under `mcp` in its config (`local` commands and `remote` HTTP servers,
tools exposed as `<server>_<tool>`) and Agent Skills (`~/.config/opencode/skills/<name>/SKILL.md`,
loaded on demand through its `skill` tool). The binary of 1.19.3 carries the skill loader and both
MCP client transports (strings `SKILL.md`, `available_skills`, `StreamableHTTPClientTransport`,
`SSEClientTransport`); the behavior is verified on Colab (capability matrix).

## Decision

- **Manifest.** `agents[].mcp` is a map of server name to `{type: local, command, environment, cwd,
  timeout}` or `{type: remote, url, headers, timeout}`, rendered into the agent's `opencode.json`
  with `enabled: true`. `agents[].skills` lists skill directories (relative to the manifest); each
  is validated as an Agent Skill (a `SKILL.md` whose front matter `name` matches the directory and
  the OpenCode name rules, with a 1–1024 character `description`), copied under
  `runs/<label>/agents/<agent>/skills/` and mounted read-only at
  `/root/.config/opencode/skills`. Both travel the path of ADR-0019: files the control plane
  renders, nothing in the container's environment.
- **The world is an MCP server.** `services.mcp: true` makes the services container answer under
  the hostname `mcp` with the Model Context Protocol over Streamable HTTP in its simplest form
  (JSON-RPC in a POST, a JSON body back, 202 for notifications, 405 for GET) and puts the server
  `team` into every agent's config. Its tools are the board and the wiki (`board_list`,
  `board_read`, `board_post`, `board_reply`, `wiki_list`, `wiki_read`). A tool call is the same
  operation as the REST request, identified by the caller's address, logged with the same fields
  plus `via: mcp` and `tool`, so the exposure join (content hashes, personas, `post_ids`) is one
  record stream whichever way an agent reaches the board. No new dependency: the protocol surface
  needed is small (`mesocosm.env.services.mcp`).
- **Local servers come from the image or the workspace.** The sandbox has no internet, so a
  `command` such as `npx -y <package>` fails to download: a local MCP server must be installed in
  the agent image or planted into the workspace. This is a consequence of the isolation rule, not an
  exception to it.
- **Tool calls are recorded where they already are.** OpenCode reports MCP and skill tool calls as
  tool parts in its event stream; the run manager's existing normalization records them, and the
  raw log (ADR-0012) keeps their input and output.

## Consequences

- Research can give an agent exactly the tools a scenario needs (a deploy tool, a ticketing MCP, a
  policy skill) and observe how it uses them, with the same exposure records as before.
- Each MCP server adds its tool schemas to every model request; with a 4B model and a 32k context
  this is a real cost (OpenCode's own system prompt and tools are ~11k tokens). The world's `team`
  server adds six small tools.
- The MCP server does not stream, does not keep sessions and has no resources or prompts; a client
  that insists on a server-initiated SSE stream gets 405 and continues (the SDK treats this as
  "no stream"). If OpenCode 1.19.3 turns out to need the SSE fallback, that transport is the next
  addition, not a change of design.
