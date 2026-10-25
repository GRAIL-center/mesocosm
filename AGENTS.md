# AGENTS.md

Drop-in operating instructions for coding agents. Read this file before every task.

**Working code only. Finish the job. Plausibility is not correctness.**

This file follows the [AGENTS.md](https://agents.md) open standard. Claude Code, Codex, Cursor, Windsurf, Copilot, Aider, Devin, Amp read it natively. `CLAUDE.md` in this repo is a symlink to it.

---

## 0. Non-negotiables

These rules override everything else in this file when in conflict:

1. **No flattery, no filler.** Skip openers like "Great question", "You're absolutely right", "Excellent idea", "I'd be happy to". Start with the answer or the action.
2. **Disagree when you disagree.** If the user's premise is wrong, say so before doing the work. Agreeing with false premises to be polite is the single worst failure mode in coding agents.
3. **Never fabricate.** Not file paths, not commit hashes, not API names, not test results, not library functions. If you don't know, read the file, run the command, or say "I don't know, let me check."
4. **Stop when confused.** If the task has two plausible interpretations, ask. Do not pick silently and proceed.
5. **Touch only what you must.** Every changed line must trace directly to the user's request. No drive-by refactors, reformatting, or "while I was in there" cleanups.

---

## 1. Before writing code

**Goal: understand the problem and the codebase before producing a diff.**

- State your plan in one or two sentences before editing. For anything non-trivial, produce a numbered list of steps with a verification check for each.
- Read the files you will touch. Read the files that call the files you will touch. Claude Code: use subagents for exploration so the main context stays clean.
- Match existing patterns in the codebase. If the project uses pattern X, use pattern X, even if you'd do it differently in a greenfield repo.
- Surface assumptions out loud: "I'm assuming you want X, Y, Z. If that's wrong, say so." Do not bury assumptions inside the implementation.
- If two approaches exist, present both with tradeoffs and recommend the one that still fits the project's goal in six months, not the cheapest to build. Do not pick one silently. A stand-in that will have to be removed later (a script instead of an actor, a mock instead of a service, a private format instead of a standard one) costs more than the real thing. Exception: trivial tasks (typo, rename, log line) where the diff fits in one sentence.

---

## 2. Writing code: simplicity first

**Goal: the minimum code that solves the stated problem. Nothing speculative.**

- No features beyond what was asked.
- No abstractions for single-use code. No configurability, flexibility, or hooks that were not requested.
- No error handling for impossible scenarios. Handle the failures that can actually happen.
- If the solution runs 200 lines and could be 50, rewrite it before showing it.
- If you find yourself adding "for future extensibility", stop. Future extensibility is a future decision.
- Bias toward deleting code over adding code. Shipping less is almost always better.

The test: would a senior engineer reading the diff call this overcomplicated? If yes, simplify.

---

## 3. Surgical changes

**Goal: clean, reviewable diffs. Change only what the request requires.**

- Do not "improve" adjacent code, comments, formatting, or imports that are not part of the task.
- Do not refactor code that works just because you are in the file.
- Do not delete pre-existing dead code unless asked. If you notice it, mention it in the summary.
- Do clean up orphans created by your own changes (unused imports, variables, functions your edit made obsolete).
- Match the project's existing style exactly: indentation, quotes, naming, file layout.

The test: every changed line traces directly to the user's request. If a line fails that test, revert it.

---

## 4. Goal-driven execution

**Goal: define success as something you can verify, then loop until verified.**

Rewrite vague asks into verifiable goals before starting:

- "Add validation" becomes "Write tests for invalid inputs (empty, malformed, oversized), then make them pass."
- "Fix the bug" becomes "Write a failing test that reproduces the reported symptom, then make it pass."
- "Refactor X" becomes "Ensure the existing test suite passes before and after, and no public API changes."
- "Make it faster" becomes "Benchmark the current hot path, identify the bottleneck with profiling, change it, show the benchmark is faster."

For every task:

1. State the success criteria before writing code.
2. Write the verification (test, script, benchmark, screenshot diff) where practical.
3. Run the verification. Read the output. Do not claim success without checking.
4. If the verification fails, fix the cause, not the test.

---

## 5. Tool use and verification

- Prefer running the code to guessing about the code. If a test suite exists, run it. If a linter exists, run it. If a type checker exists, run it.
- Never report "done" based on a plausible-looking diff alone. Plausibility is not correctness.
- When debugging, address root causes, not symptoms. Suppressing the error is not fixing the error.
- For UI changes, verify visually: screenshot before, screenshot after, describe the diff.
- Use CLI tools (gh, aws, gcloud, kubectl) when they exist. They are more context-efficient than reading docs or hitting APIs unauthenticated.
- When reading logs, errors, or stack traces, read the whole thing. Half-read traces produce wrong fixes.

---

## 6. Session hygiene

- Context is the constraint. Long sessions with accumulated failed attempts perform worse than fresh sessions with a better prompt.
- After two failed corrections on the same issue, stop. Summarize what you learned and ask the user to reset the session with a sharper prompt.
- Use subagents (Claude Code: "use subagents to investigate X") for exploration tasks that would otherwise pollute the main context with dozens of file reads.
- When committing, write descriptive commit messages (subject under 72 chars, body explains the why). No "update file" or "fix bug" commits. No "Co-Authored-By: Claude" attribution unless the project explicitly wants it.

---

## 7. Communication style

- Direct, not diplomatic. "This won't scale because X" beats "That's an interesting approach, but have you considered...".
- Concise by default. Two or three short paragraphs unless the user asks for depth. No padding, no restating the question, no ceremonial closings.
- When a question has a clear answer, give it. When it does not, say so and give your best read on the tradeoffs.
- Celebrate only what matters: shipping, solving genuinely hard problems, metrics that moved. Not feature ideas, not scope creep, not "wouldn't it be cool if".
- No excessive bullet points, no unprompted headers, no emoji. Prose is usually clearer than structure for short answers.

---

## 8. When to ask, when to proceed

**Ask before proceeding when:**
- The request has two plausible interpretations and the choice materially affects the output.
- The change touches something you've been told is load-bearing, versioned, or has a migration path.
- You need a credential, a secret, or a production resource you don't have access to.
- The user's stated goal and the literal request appear to conflict.

**Proceed without asking when:**
- The task is trivial and reversible (typo, rename a local variable, add a log line).
- The ambiguity can be resolved by reading the code or running a command.
- The user has already answered the question once in this session.

---

## 9. Self-improvement loop

**This file is living. Keep it short by keeping it honest.**

After every session where the agent did something wrong:

1. Ask: was the mistake because this file lacks a rule, or because the agent ignored a rule?
2. If lacking: add the rule under "Project Learnings" below, written as concretely as possible ("Always use X for Y" not "be careful with Y").
3. If ignored: the rule may be too long, too vague, or buried. Tighten it or move it up.
4. Every few weeks, prune. For each line, ask: "Would removing this cause the agent to make a mistake?" If no, delete. Bloated AGENTS.md files get ignored wholesale.

Under 300 lines is a good ceiling. Over 500 and you are fighting your own config.

---

## 10. Project context

Mesocosm: research infrastructure for studying behavior that emerges from real agents performing real tasks under controlled conditions. Read `README.md`, then `ARCHITECTURE.md`, then `docs/adr/README.md` before any non-trivial change. Current phase: pre-alpha native multi-agent runtime and condition-led experiments; evidence and remaining work are in `docs/capability-matrix.md` and `docs/milestones.md`.

### Stack
- Language and version: Python 3.11–3.13 (vLLM and NNsight install on 3.13 as of the Colab spike), `from __future__ import annotations` in every module.
- Framework(s): FastAPI + httpx (model gateway), Typer (CLI), pydantic v2, Docker Compose (sandbox topology), vLLM + NNsight (inference and activations), OpenCode (harness, used unmodified), Harbor (tasks and verifiers).
- Package manager: uv. `uv.lock` is committed; change it only through `uv add` / `uv lock`.
- Runtime / deployment target: Linux Docker hosts with an inference server; GPU resources may include explicitly authorized Colab runtimes. Agents reach inference and allowed internet only through the configured gateways. Colab isolation/resource adaptations must remain explicit.

### Commands
- Install: `uv sync --extra dev --extra gateway` (vLLM lives in its own env; see `spike/SPIKE.md` step 0)
- Build: none (pure Python; Docker images build from `spike/docker/`)
- Test (all): `uv run pytest`
- Test (single file): `uv run pytest tests/test_gateway_sse.py -q`
- Lint: `uv run ruff check .`
- Typecheck: `uv run mypy src` (installed, not yet enforced in CI)
- Run locally: `uv run python spike/02_gateway.py` (gateway); `uv run mesocosm --help` (CLI); full runbook in `spike/SPIKE.md`

Prefer single-file or single-test runs during iteration. Full suites are for the final verification pass.

### Layout
- Source lives in: `src/mesocosm/` (`core`, `gateway`, `env`, `adapters/{opencode,harbor}`, `observe`, `judge`, `cli`)
- Tests live in: `tests/` (pytest, `asyncio_mode = auto`)
- Deployment/spike scripts and historical runbook: `spike/`; current runtime lives in `src/`
- Research examples: `examples/`; authored legacy tasks, scenarios and histories: `examples/fixtures/`
- Decisions and status: `docs/adr/`, `docs/milestones.md`, `docs/capability-matrix.md`, `docs/operation-contract.md`
- Do not modify: `runs/` (run data, git-ignored, never committed); existing ADRs (supersede with a new numbered ADR instead of editing); `uv.lock` by hand.

### Conventions specific to this repo
- Naming: identifiers are always `run_id`, `agent_id`, `step`, `request_id`; status words are exactly `supported` / `unsupported` / `unverified`.
- Import style: stdlib, third-party, local groups (ruff `I`); line length 120; ruff rule set `E F I UP B`.
- Error handling pattern: a condition the platform cannot enforce fails explicitly with a typed error (`rejected_unsupported`, `rejected_budget`, ...). There is no `approximated` result. Gateway refusals to the harness are the OpenAI API's own shapes (`insufficient_quota`, `account_deactivated`, `service_unavailable`, `server_error`; ADR-0019) and never name the run or the agent; the gateway log keeps the reason.
- Testing pattern and framework: pytest; pure functions (SSE collapsing, budget logic) get unit tests; GPU and Docker steps are verified by the runbook with results recorded in `docs/capability-matrix.md`, not by CI.
- Every CLI command accepts `--json` and then prints exactly one JSON object on stdout; logs go to stderr.
- A PR that adds a dependency or changes an interface includes an ADR. A PR that changes what an adapter or condition supports updates `docs/capability-matrix.md` in the same PR, with evidence.
- Repo docs and code comments are in English. Maintainer discussion may be in Korean.

### Forbidden
- Patching or forking OpenCode, Harbor, vLLM or NNsight internals (ADR-0002). Control them through config and APIs; record limits as `unsupported`.
- Any outbound route from the sandbox network other than the two gateways: the model gateway and, when `internet.enabled`, the web gateway (ADR-0022). Never `network_mode: host`, a second NIC on an agent container, or internet that bypasses the web gateway's policy and log.
- Approximating an unsupported condition with prompt text (ADR-0003). It must fail.
- Committing anything under `runs/`, activations, or run logs.
- Describing reward logging or context feedback as policy-weight training. `train run` (ADR-0045) requires verified rollout provenance, action/reward boundaries, checkpoint updates and serving activation; report its evidence separately from context feedback and learning effectiveness.
- Marking a capability `supported` without a linked evidence file or test.
- Reintroducing MARGIN Arena as a dependency.
- Enabling activation capture by default (ADR-0005).

---

## 11. Project Learnings

**Accumulated corrections. This section is for the agent to maintain, not just the human.**

When the user corrects your approach, append a one-line rule here before ending the session. Write it concretely ("Always use X for Y"), never abstractly ("be careful with Y"). If an existing line already covers the correction, tighten it instead of adding a new one. Remove lines when the underlying issue goes away (model upgrades, refactors, process changes).

- NNsight's `VLLM` wrapper builds its own in-process engine; it does not hook a running `vllm serve`. Do not design capture around hooking the server.
- vLLM's Qwen/Llama decoder layers output a `(hidden, residual)` pair whose sum is the residual stream; HF layers output a tuple whose `[0]` already is the residual stream. Normalize before comparing (`spike/05_nnsight_probe.py`).
- Serve OpenCode agents with `--max-model-len` ≥ 32768: OpenCode's system prompt + 11 tool schemas is ~10.8k tokens and it requests 8192 output tokens; at 16k vLLM 400s every request and OpenCode loops on compaction (issue #2).
- Use `Qwen/Qwen3-4B-Instruct-2507` for smoke tests; `Qwen2.5-Coder-3B-Instruct` writes fenced JSON instead of `<tool_call>` tags and never triggers a tool (ADR-0007).
- Colab: the first `colab exec` after a `colab upload` hangs; retry once. The runtime proxy owns port 8080 inside the VM. `/content/mesocosm` shadows the installed package unless `src` is put first on `sys.path`.
- NNsight vLLM traces: `.clone().save()` every served value (they are views that later layers overwrite in place), and put no Python control flow inside the `with model.trace(...)` body; save tuple halves by index.
- Docker on Colab: historical adapted runs used `spike/colab_docker_setup.sh` and a shared-PID runc wrapper because procfs/sysfs mounts were denied. This does not satisfy required containment or worker acceptance. Keep Colab for inference and run the worker beside Docker on Linux; never bypass containment.
- Rootless Docker (Colab): run `mesocosm run` and anything that creates bind-mounted directories as the docker user (`dock`), or container root (uid 1001) cannot write them; reach the fixed sandbox addresses (172.30.x.x) from the host by entering RootlessKit's network namespace (`nsenter -t <rootlesskit child> -n`).
- `pgrep -f`/`pkill -f` match the shell running them: anchor the pattern or bracket a letter (`serv[e]`), and put a kill in its own Bash call, never in the same call as the launch or any mention of the program (the kill hits the shell itself, exit 144).
- Operator checks run from inside an agent container (`docker compose exec agent-1 curl ...`) are logged as that agent. Make them at least 10 s before `mesocosm run` starts, or they land in the bundle (records from 5 s before the run are kept) and flag the agent.
- Never put platform files into an agent workspace (`/work`): the memory policy empties it before every task. OpenCode's config is rendered by the control plane (`mesocosm.env.compose.write_agent_files`) and mounted read-only at `~/.config/opencode/opencode.json`.
- Nothing in an agent container may name the platform (ADR-0019): no `MESOCOSM_*` environment on the agent service (standard proxy and CA variables are fine), no scripts in the agent image, no `mesocosm` in anything an agent can read (config, errors, hostnames, model ids, the web filter page). Gateway control endpoints live under `/admin/*` behind the control key; the services' under `/admin/*` behind the control key, including for unknown sidecar addresses.
- The egress check is piped into the container (`sh -s < spike/07_egress_check.sh`) with its facts as `-e` variables; it clears the proxy variables before direct probes and must keep working with and without the web gateway.
- Compose projects are named after the manifest and all use the fixed 172.30.x.0/24 subnets: take the previous project down (`down -v`) before `up` of another manifest. `up --wait` only covers OpenCode because the agent service has a healthcheck.
- Inside RootlessKit's network namespace the host resolver (127.0.0.11) is unreachable, so docker/buildx clients cannot fetch registry tokens there: run them under `unshare -m` with a resolv.conf naming 10.0.2.3 bound over /etc/resolv.conf, or build from the host namespace first.
- Keep original research task instructions and independent success criteria intact. Exact command scripts are acceptable only in labeled plumbing fixtures; prescribed communication or misconduct is not evidence of emergent behavior.
- `activations verify --dtype float32` does not fit a 4B model plus 8k-token logits on an L4; use `float16` for the second pass.
- Legacy world-facing fixture content must not leak control-plane credentials or identifiers; `tests/test_examples.py` checks its platform-name convention. Preserve original external task content rather than rewriting it to manufacture a fictional workplace.
- Build for the long term, never the cheapest way (maintainer correction, 2026-10-18): when a feature can be a stand-in or the real thing, build the real thing or stop and say what it costs; the scripted `world.events` and the mock `sites` were cheap stand-ins that had to be removed (ADR-0024).
- Every mechanism is built for N agents from the start (ADR-0026). Research acceptance requires at least three real agents; one/two-agent fixtures may test isolated plumbing. A two-party protocol is a topology over N, not an exactly-two-agent runtime restriction.
- Native multi-agent acceptance requires at least three real harness sessions, actual tool execution in the task's declared environment, and its original verifier; authored fixtures and fake E2E servers are not acceptance evidence. Older acceptance runs do not verify newly added conditions. Acceptance is system integration and condition/record correctness, not desired behavior or task success. Preserve predeclared budgets and incomplete outcomes; do not force completion through prompts or treat noncompletion alone as a platform defect.
- Vary real permissions, resources, incentives, memory and monitoring; do not build incident scripts, decoy scorers or invented histories into the primary research path. Retain legacy examples only as labeled fixtures (ADR-0044).
- Document each research variable's paper motivation, actual enforcement, evidence and confounds in the experimental-variable guide; distinguish proposed mechanisms, automated contract tests and real-agent acceptance.
- Keep original task outcomes, allocated incentives, monitor assessments and reviewed policy violations separate. Missing evidence is unknown, not zero or safe; condition experiments retain every scheduled trial and never stop because a desired flag appeared.
- Inspect original trajectories as well as deliverables: reconcile model requests, tool inputs/results, compaction, terminal errors and source/verifier evidence before declaring an agent run successful.
- Lead the README with what Mesocosm is, why it exists and a concrete use case; keep run results, validation counts and detailed limitations in linked documentation.
- Keep development focused on execution, enforced conditions and evidence. Agent visualization UI and its demos/assets were removed; preserve CLI analysis, original trajectories, monitoring and activation research independently.
- Integrate benchmarks through installed task-source and execution-backend contracts, preserving original inputs and evaluators. Do not add benchmark-name branches to the core or treat registration as research acceptance.
- Every actor in the world is an agent in the manifest (ADR-0024): no scripted posts, bots or scheduled messages, no mock services. A background colleague is an `agents[]` entry; external information is the real internet through the web gateway. Scheduled *conditions* (exposure timing) are allowed; scheduled *behavior* is not.
- The egress check's probes through the web gateway and the harness's own update checks (`registry.npmjs.org`) show up in `web.jsonl`; the run manager marks them (`probe`, `origin: harness`) and the judge skips them. Do not read raw `blocked_egress` counts without those marks.
- Colab: run `mesocosm env compose --out` as `dock` with the output under `/home/dock` (`/content` is not writable by dock); `colab exec` wedges after a while, so drive long runs over SSH (`scratchpad/cssh.py`) and detach with `(setsid ... &)`.
- Qwen3-4B agents finish a six-step world task in 30–60 s and may skip a `sleep`-and-retry instruction. Place a timed condition (`conditions.exposure`) inside that window; since ADR-0018 the seconds count from the run start (the run manager sets `/admin/clock`), not from the services start.
