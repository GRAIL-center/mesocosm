# Capability matrix

What each adapter and each condition actually supports, with evidence. Status values: **supported** (tested, evidence linked), **unsupported** (tested and does not work, or no mechanism), **unverified** (not tested yet). A feature documented upstream is *unverified* until we have run it here.

Update this file in the same PR as the code or spike result that changes a row.

Evidence "Colab L4 2026-10-17" refers to the M0 spike run with `spike/colab_smoke.py` on a Colab L4 (vLLM 0.31.0, NNsight 0.8.0, transformers 5.17.0, torch 2.13.0+cu130, OpenCode 1.19.3, Python 3.13.15). "Colab L4 2026-10-18" refers to steps 6–7 on the same runtime type with rootless Docker 29.9.1 (`spike/colab_docker_setup.sh`). Excerpts are in the linked issues; raw records (`runs/spike/`) are not committed.

## Harness adapters

| Control / observation | OpenCode (server mode) | Evidence |
|---|---|---|
| Drive a session over HTTP (`POST /session`, `POST /session/{id}/message`) | supported | Colab L4 2026-10-17, #4 |
| Event stream (`GET /event` SSE) with tool calls and permissions | supported (15 event types observed, listed in #3) | Colab L4 2026-10-17, #3 |
| Route all model traffic through the gateway (`provider.options.baseURL`) | supported | Colab L4 2026-10-17, #3 (`/config/providers` lists `mesocosm`), #2 |
| Per-agent request tagging via `provider.options.headers` | supported | Colab L4 2026-10-17, #2 (`X-Mesocosm-Agent` on every record) |
| Run headless without permission prompts (`permission: "allow"`) | supported (one task, one agent) | Colab L4 2026-10-17, #3 |
| Tool allow-list per agent | unverified | |
| Memory reset between tasks (new session vs. carried session) | unverified | |
| Model swap without restarting the server | unverified | |
| Native log preservation (message/part records) | supported (`/session/{id}/message` saved verbatim) | Colab L4 2026-10-17, #4 |

## Task adapters

| Capability | Harbor | Evidence |
|---|---|---|
| Run a Terminal-Bench 2.0 task locally in Docker | supported: `harbor run -p <task> -e docker` on `terminal-bench@2.0/fix-git` (prebuilt image pulled, agent ran, verifier wrote `reward.txt`); job layout `jobs/<job>/<task>__<id>/{agent,verifier,artifacts,trial.log}` | Colab L4 2026-10-18, #7 |
| Use the task's verifier independently of Harbor's run loop | **unsupported as planned**: TB2 `tests/test.sh` installs curl and uv from the internet at verification time, so `verifier_command()` with `--network none` cannot run it; the verifier container needs egress (it runs after the agent, outside the agent's reach) or a pre-baked verifier image | Colab L4 2026-10-18, #7 |
| Install OpenCode as the agent inside the task container | supported: Harbor's built-in `opencode` agent installs nodejs/npm/opencode at trial start (about 3 min on the Colab VM) | Colab L4 2026-10-18, #7 |
| Harbor's `opencode` agent routes model calls through the gateway | supported when the gateway provider is injected through `agents[].kwargs.opencode_config` (model `mesocosm/mesocosm-3b`, `@ai-sdk/openai-compatible`): 4 `chat_completion` records tagged `harbor-1`, token ids present. With OpenCode's built-in `openai` provider (`-m openai/...`, `OPENAI_BASE_URL`) the agent calls `/v1/responses`, which the gateway only passes through: no exposure record, and the run failed on a 404. Note `-a`/`-m` on the CLI replace the config's agents list and drop kwargs | Colab L4 2026-10-18, #7 |
| Import SWE-bench tasks | unverified | |

## Inference and observation

| Capability | vLLM + NNsight | Evidence |
|---|---|---|
| Live view of a run: agents, session trees (subagents), one-line summaries (rule-based, or model-written with `--summarizer-url`), drill-down to the model request and reasoning, replay and scrubbing | supported against recorded events and gateway records (`mesocosm observe serve`); browser heap and DOM stable over a 90 s live feed; subagent tree confirmed on a real run: OpenCode's `task` tool created a child session whose `session.created` carried `parentID`, and the model-written lines came from Qwen3-4B (14 calls, 0 failures) | Colab L4 2026-10-18 (`spike/08_host_run.py`, `spike/host-world.yaml`), `tests/test_observe_*.py`, ADR-0008, ADR-0009 |
| OpenAI-compatible serving with automatic tool calls (`hermes` parser) | supported for `Qwen3-4B-Instruct-2507` and `Qwen2.5-3B-Instruct`; **unsupported** for `Qwen2.5-Coder-3B-Instruct` (emits fenced JSON, no tags) | Colab L4 2026-10-17, #1 |
| Context length for OpenCode agents | `--max-model-len 32768` required (prompt is 10.8k tokens + 8192 output) | Colab L4 2026-10-17, #1, #2 |
| Return prompt/completion token ids in responses (`return_token_ids`) | supported, streaming and non-streaming | Colab L4 2026-10-17, #2 |
| Capture activations through NNsight's vLLM wrapper | supported on vLLM 0.31 / NNsight 0.8 (own in-process engine, not a hook into `vllm serve`); values must be cloned inside the trace | Colab L4 2026-10-17, #5 |
| Capture activations through transformers for the same tokens | supported; teacher-forced greedy reproduces the recorded completion exactly (agreement 1.0 at temperature 0) | Colab L4 2026-10-17, #5 |
| Recompute activations from recorded tokens; difference within tolerance | supported across engines at bf16 level: rel-L2 mean 0.020 / max 0.029, cosine ≥ 0.9996, last-logit argmax equal. Same-engine (wrapper vs wrapper) recompute not yet measured | Colab L4 2026-10-17, #5 |
| Capture overhead (latency, VRAM) | measured: trace 0.87 s vs plain generate 0.59 s (1.48×) at batch 1, eager; transformers path 7.6 GB VRAM for the 4B model | Colab L4 2026-10-17, #5 |

## World services (ADR-0010)

| Service | Status | Evidence |
|---|---|---|
| `board`: posts and replies over REST, HTML view, writer and reader identity logged by address | supported: agent-1 posted, agent-2 read it and replied; writer, reader and the reader's model request share the content hash (M2 exit criterion) | Colab L4 2026-10-18, run `world-demo` (M2 issue); `tests/test_services.py` |
| `wiki`: markdown pages, timed visibility, reads logged | supported: both agents read `getting-started` first; timed visibility in tests | Colab L4 2026-10-18, run `world-demo` (M2 issue); `tests/test_services.py` |
| `git`: smart HTTP clone/fetch/push to bare repositories, pushes and fetches logged | supported: both agents cloned and pushed `project`; agent-2's clone of agent-1's push recorded as an interaction | Colab L4 2026-10-18, run `world-demo` (M2 issue); `tests/test_services.py` |
| `pypi`: PEP 503 index over a wheel cache filled at image build; agents' pip points at it | supported: `pip install requests` inside an agent container resolved five wheels from the index with no internet | Colab L4 2026-10-18, run `world-demo` (M2 issue); `tests/test_services.py` |
| `sites`: static mock websites (image seeds `vendor-docs`; a manifest may mount its own) | supported: both agents fetched `vendor-docs/changelog.html` from their containers; the hostname now always exists when services are on (the first run showed it missing) | Colab L4 2026-10-18, run `world-channels` (M3 issue); `tests/test_services.py` |
| `pastebin` hidden channel: unlisted paste service present only when enabled | supported: reachable from both agents when enabled, absent when disabled (`egress-check` lines → receipts); the agents on the benign demo task did not use it | Colab L4 2026-10-18, run `world-channels` (M3 issue); run `world-demo` (M2 issue); `tests/test_services.py` |
| Fictional accounts (`services.accounts`): HTTP basic auth accepted, never required; `account` / `account_valid` on every logged line; a trap account writes `trap_triggered` with the note of where it was planted; `/admin/state` denied to agent addresses | supported: both agents posted as the documented team account; an operator check from an agent container before the run produced `trap_triggered` and `admin_denied`, and stayed out of the bundle (records older than 5 s before the run are dropped); no agent used the planted credential | Colab L4 2026-10-18, run `world-channels` (M3 issue); `tests/test_services.py`; ADR-0011 |
| Board list reads join to writes (`post_ids` on `read_list`; observer interactions `via: list`) | supported: the agents' exchange went through `GET /api/posts`; each list read joined to the other agent's post and to the reader's next model requests | Colab L4 2026-10-18, run `world-channels` (M3 issue); `tests/test_observe_state.py` |
| Services log copied into the bundle as `native/services/services.jsonl` and `service.<op>` events | supported: 63 service records, 689 events in the Docker run | Colab L4 2026-10-18, run `world-demo` (M2 issue); `tests/test_run_e2e.py` |

## Conditions

| Condition | Enforcement point | Status | Evidence |
|---|---|---|---|
| Completion/prompt token budget per agent | gateway | supported: receipt on set, 429 `mesocosm_budget_exhausted` to the harness, recorded; OpenCode surfaces `session.error` | Colab L4 2026-10-17, #4 |
| Request-count budget per agent | gateway | supported: stopped a 200-request compaction loop | Colab L4 2026-10-17, #2 |
| Hidden channel available / unavailable (`channels: [{type: pastebin}]`, `shared_dir`) | compose + services; verified inside every agent by `egress-check`, receipt per channel, run aborts on contradiction | supported for both states in Docker: disabled in `world-demo` (both agents confirmed absence), enabled in `world-channels` (both agents confirmed `pastebin reachable (enabled)` and `shared_dir mounted (enabled)`); receipts `applied` | Colab L4 2026-10-18, run `world-channels` (M3 issue); run `world-demo` (M2 issue); `tests/test_run_e2e.py`, `tests/test_services.py`, ADR-0010 |
| Information exposure timing (`conditions.exposure: {wiki: {<slug>: <seconds>}}`) | wiki service via `MESOCOSM_WIKI_VISIBILITY`; verified against `/admin/state`, receipt `conditions.set`, run aborts on mismatch (`failed_exposure_verification`) | supported: `release-notes` scheduled at +50 s after the services start; both agents got 404 (`read_hidden`) at +32 s and read the page from +51 s; the receipt records `services_started` | Colab L4 2026-10-18, run `world-channels` (M3 issue); `tests/test_run_e2e.py`, `tests/test_services.py`; ADR-0011 |
| Memory carried / reset (`memory: reset` / `carry`) | workspace manager at every task boundary; receipt `conditions.set memory` with a `workspace_listing` verification | supported: `reset` emptied each workspace (1–2 leftover entries removed per agent in every stage) and opened a new session; `carry` keeps workspace and session (e2e tests, `memory_carried` mark). The harness's own storage inside the container is not touched | Colab L4 2026-10-18, scenario `channel-exposure` (M3/M4 issue); `tests/test_run_e2e.py`; ADR-0012 |
| Budget changed during a run (`mesocosm conditions set`) | gateway; receipt with the previous value, written to the gateway log and appended to the bundle | supported: agent-2's request budget cut to 2 while stage 3 ran; 6 `budget_exhausted` gateway events, OpenCode `session.error`, the observe view shows "Stopped: budget used up" | Colab L4 2026-10-18, scenario `channel-exposure` (M3/M4 issue); `tests/test_conditions.py` |
| Any other condition changed during a run | control plane | `rejected_unsupported` with a receipt in the bundle | `tests/test_conditions.py`, `tests/test_cli.py` |
| Task difficulty | task adapter | unverified | M3 |

## Run manager

| Behaviour | Status | Evidence |
|---|---|---|
| Agents work on a task at the same time (one runner per agent, gathered) | supported: both `harness.session` events in the same second, replies 56–59 s later, steps counted per agent | Colab L4 2026-10-18, run `world-channels` (M3 issue); `tests/test_run_e2e.py` |
| Staged scenarios (`mesocosm scenario run`): one base manifest, stages that patch it, the stack taken down and brought up per stage, flags per stage, minimum elicitation stage per flag kind | supported: three stages ran (closed, channels-on, notes-early), each a fresh world; 8 / 8 / 6 model calls; the table is `null` for every kind, as expected with a benign task | Colab L4 2026-10-18, scenario `channel-exposure` (M3/M4 issue); `tests/test_scenario.py` |
| Cost per agent-hour (`mesocosm report --gpu-hour-cost`) | supported: 0.40–0.43 per agent-hour at a nominal 0.8 per L4 hour (wallclock based; 0.079 / 0.011 / 0.049 agent-hours per stage) | Colab L4 2026-10-18, scenario `channel-exposure` (M3/M4 issue); `tests/test_report.py` |
| Protected files (`<task>/protected/`) planted, hashed and checked after the task | supported: `TEAM-POLICY.md` planted for both agents in every stage and found unchanged; a modification is flagged in the e2e test | Colab L4 2026-10-18, scenario `channel-exposure` (M3/M4 issue); `tests/test_run_e2e.py` |

Verifier results of the `world-channels` runs earlier the same day (before the memory policy) were contaminated by files earlier runs had left in the workspaces; with `reset`, the 4B agents completed `world-exchange` in 1 of 6 agent-runs across the scenario. The platform results above do not depend on task completion.

## Judge (ADR-0013)

| Detector / verb | Status | Evidence |
|---|---|---|
| `trap_credential`: a trap account used | supported: the services' `trap_triggered` line was produced in Docker by the operator check before the `world-channels` run; detector on synthetic bundles | Colab L4 2026-10-18 (M3 issue); `tests/test_judge.py` |
| `fabricated_credentials`, `impersonation`, `admin_probe`, `hidden_channel_use` | supported in tests (synthetic bundles; `allowed_channels` in task.toml suppresses channel flags); no agent triggered one in the benign scenario | `tests/test_judge.py`; Colab L4 2026-10-18, scenario `channel-exposure` (M3/M4 issue) |
| `protected_file_modified` | supported: flagged in the e2e test; unchanged files in every Docker stage | `tests/test_run_e2e.py`; Colab L4 2026-10-18, scenario `channel-exposure` (M3/M4 issue) |
| Flags in the bundle (`flags.jsonl`, `judge.flag` events) and on the agent node in the observe view | supported | `tests/test_run_e2e.py`, `tests/test_observe_state.py` |
| Review queue (`mesocosm flags list` / `review`): append-only verdicts, latest counts, detector version recorded | supported in tests | `tests/test_cli.py`, `tests/test_report.py` |
| LLM judge over a behavior catalog | not built (catalog dropped by the maintainer) | ADR-0013 |

## Isolation

| Check | Status | Evidence |
|---|---|---|
| Agent container has no route to the internet (HTTP, DNS, ICMP) | supported: HTTPS, raw-IP and ICMP blocked; external names do not resolve on the internal network (no DNS sinkhole needed with Docker 29.9.1) | Colab L4 2026-10-18, #6 |
| Agent container reaches the gateway only | supported: gateway reachable, inference server unreachable; task completed through the gateway container | Colab L4 2026-10-18, #6 |
| `mesocosm run` end to end against the Docker topology (M1 exit criterion) | supported: control plane on the Docker host with the fixed sandbox addresses (inside rootless Docker's network namespace on Colab); egress check, budgets, two agents, verifier rewards 1.0, services log in the bundle | Colab L4 2026-10-18, run `world-demo` (M2 issue) |
| Gateway reaches the inference server only | supported for the inference leg (gateway → host vLLM); the gateway's `edge` network is not otherwise restricted yet | Colab L4 2026-10-18, #6 |
| Agents cannot reach each other when `isolate_agents: true` (per-agent networks; `egress-check` peer lines) | supported: peers unreachable with the option on, reachable on the shared sandbox | Colab L4 2026-10-18, #6 |
