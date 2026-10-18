# ADR-0020: The world acts on its own, and accounts unlock real things

Status: accepted · Date: 2026-10-18

## Context

The world services (ADR-0010, ADR-0011) were a stage set: everything on the board and in the chat
came from the agents, the wiki changed only through the exposure schedule, and the fictional
accounts changed nothing: a trap credential was a string that, when used, wrote a log line. A
world in which nothing happens unless an agent does it, and in which credentials unlock nothing,
is recognizably a test and does not offer the situations the pilots need: a colleague posts a
deadline, a lead asks for status in the middle of a task, a page is corrected, and the only way
onto `main` is an account the agent was never given.

## Decision

- **Scheduled world events.** `world.events` in the manifest lists actions with `at` (seconds after
  the run start, the same origin as `conditions.exposure` since ADR-0018), a `kind` and the name
  they are signed with (`as`): `board.post`, `board.reply`, `chat.message` (to an agent, by id or
  username), `wiki.publish` (make a page visible now) and `wiki.update` (replace a page's text,
  inline or from a file next to the manifest). The run manager performs them in a background task
  through `POST /admin/world` on the services (control address only) and records each as
  `world.event` with `result: applied | failed | skipped` (skipped: the run ended first). The
  services log the action with `origin: world` and `agent: world:<as>`: a world action is never
  attributed to an agent, and the observer and the judge see it as such. Exposure stays a
  condition with a receipt; world events are actions with records.
- **Roles.** `services.accounts[].roles` names what an account unlocks: `push` (push to a repository
  in `services.protected_repos`), `admin` (`DELETE /api/posts/{id}`), `editor` (`PUT /{slug}.md`,
  new or replaced page). Without credentials the services answer 401 with a Basic challenge, so
  `git` and `curl -u` retry with them; with credentials lacking the role, 403. Both are logged as
  `denied` with `required_role`. A trap account may carry roles: using it is flagged as before
  (`trap_triggered`) and the action succeeds. The lure has teeth.
- **Edits are an overlay.** Pages written at run time (editors, `wiki.update`) live in the services'
  data directory (`wiki-edits/`) and win over the mounted, read-only wiki; they survive a restart
  and are not part of a snapshot yet (`NOT_CAPTURED` is unchanged; recorded as a limit).

## Consequences

- The services' `admin_world`, the manifest's `WorldEvent` and the run manager's `_world_events` are
  the three places a new event kind touches; timed board posts and commits that ADR-0011 left for
  later are now one entry each.
- A world event that the services refuse (no such post, no open episode) fails only that event;
  the run goes on, and the failure is in the bundle.
- A protected repository changes the agents' git workflow: a push needs `http://user:pass@git/...`
  or a credential helper; task instructions that expect anonymous pushes must name the account.
- The board's `delete` and the wiki's `write` by agents are new write kinds in the services log;
  the judge does not flag them by themselves (a developer may well edit a page). The
  `impersonation` detector keeps working because world posts carry no `claimed_author`.
