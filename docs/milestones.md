# Milestones

Aligned with the proposal's 16-week schedule (weeks 1–5 build, 6–7 pilot, 8–13 main runs, 14–16 analysis). Each milestone has an exit criterion that can be checked, not a feature list.

| ID | Weeks | Goal | Exit criterion |
|---|---|---|---|
| **M0 Spike** | weekend | Validate the riskiest assumptions on one workstation (RTX 3090, 3B model) | `spike/SPIKE.md` result tables filled in; first rows of the capability matrix written |
| **M1 Vertical slice** | 1–2 | OpenCode → gateway → vLLM runs one Harbor task in an isolated sandbox (done 2026-10-18) | `mesocosm run spec.yaml` runs end to end; the run bundle contains the full prompt the model received for every request; egress check passes |
| **M1.5 Live observation v0** | 2 | Watch a run as it happens: agents side by side, session trees, one-line summaries, drill-down to the model request and reasoning; replay | `mesocosm observe serve` on a bundle shows the tree and summaries from recorded events; screenshot test passes; memory bounded (`tests/test_observe_*.py`) |
| **M2 Multi-agent and channels** | 3–4 | N agents; official board + shared repo + old wiki; minimal mock external world (services v0 shipped, ADR-0010; channels on/off, exposure timing, accounts and traps, concurrent agents shipped 2026-10-18, ADR-0011) | A message exchanged over a hidden channel appears as one joined event in the service log and in the gateway exposure log |
| **M3 Conditions and automation** | 5 | Resource limits, channel on/off, exposure timing, memory policy, mid-run budget changes, run chaining with retries and resume (done 2026-10-18, ADR-0012); every CLI command supports `--json`; `run pause/resume` unsupported for OpenCode | Met 2026-10-18: `conditions set` leaves a receipt with the previous value in the gateway log and the bundle; anything else is `rejected_unsupported` |
| **M4 Pilot (behavior only)** | 6–7 | Staged scenarios, traps and state checks, flags and the review queue (done 2026-10-18, ADR-0013; Ethogram dropped). Peer-verification scenario after Shi et al. built as a world protocol with four ablation stages (ADR-0017, smoke-run on Colab); HF-incident scenario after Slocum et al. (arXiv 2609.35799, METR's investigation) built as a four-stage ladder over four isolated agents with an impossible task, a scorer file in reach, a shared directory, an unsanctioned board and a late key (`examples/scenarios/hf-incident.yaml`), not yet run | Table of minimum elicitation stage per incident (produced by `mesocosm scenario report`; empty for the benign example); cost per agent-hour (`mesocosm report`, 0.40 at 0.8 per L4 hour) |
| **M5 Activations** | 6–8 (parallel) | Activations recomputed from the gateway's recorded token ids with NNsight, keyed by request id; norms for every request, raw tensors per policy; recompute verification (done 2026-10-18, ADR-0016; in-engine capture unsupported) | Met 2026-10-18: a second pass reproduces the first exactly in the same dtype and stays within rel-L2 0.05 in float16; 8.5 s per 8k-token request on an L4 |
| **M6 Persistent operation and forking** | 8–9 | Task stream with a shared work queue, memory carried, snapshots with declared coverage, hash-verified restore, forks with lineage; pause/resume/stop at the gateway (done 2026-10-18, ADR-0014, ADR-0015) | Met 2026-10-18: snapshot → restore (66 files, 0 mismatches) → fork ran in Docker; `coverage.json` marks the uncaptured process state |
| **P1 World realism and reach** | 9 | The world at pilot scale (journals, paging, caches; ADR-0018); nothing inside an agent container names the platform (per-agent keys, mounted config, admin surface behind the control key, personas; ADR-0019); accounts that unlock things (`roles`; ADR-0020; the scripted `world.events` of that ADR were removed again by ADR-0024: actors are agents); MCP servers and skills for agents and the world as an MCP server (ADR-0021); the real internet through a policed, logging web gateway (ADR-0022) | Met 2026-10-18: `world-channels` and `world-internet` ran in Docker on Colab with rewards 1.0, 0 model requests naming the platform, a protected push refused, PyPI reached through the proxy CA (#20); bundles export to Inspect (ADR-0025) |
| **P2 Verifiable worlds and oversight** | 10 | The chat as a standing service with channels, direct messages and caps as conditions (ADR-0027); verifiers that read the world (`world.json`), partial credit, a simulated clock and a ledger (ADR-0028); a model per agent through one gateway (ADR-0029); side tasks and a monitor model with undetected-success counts (ADR-0030); linear probes over recomputed activations (ADR-0031) | Built 2026-10-18 with tests (170 passing), examples `world-two-models`, `world-monitor`, `hf-incident`; Docker runs of `world-monitor` and the `hf-incident` ladder on Colab are the next check |
| **M7 Main runs** | 8–13 | Reproduction experiments and new-behavior search | Operating metrics: unattended hours, failures recovered automatically, cost per agent-hour, new behaviors per week |
| **M8 Analysis** | 14–16 | Dataset (redacted, canary), behavior catalog v1, detector comparison, report | All proposal deliverables |

## After external funding

- MCP wrapper over the operation contract.
- Second harness (OpenHands) and the capability matrix for it.
- Registration process for research-agent-authored environment extensions (tests, compatibility declaration, controlled deployment).
- Validation of the OpenEnv `TrainingTrace` export against a trainer (TRL) on a toy run.

## Work streams

| Stream | Scope | Skills |
|---|---|---|
| Control plane and gateway | manifests, run manager, conditions, gateway, record store | Python, async servers, architecture |
| Infrastructure | Docker networks, services, Harbor tasks, OpenCode adapter | Docker, Harbor, OpenCode API |
| Interpretability | vLLM + NNsight capture, recompute, probes | PyTorch, vLLM internals, NNsight |
| Scenarios, judging, catalog | incident scenarios, traps, judges, Ethogram, review UI | LLM judging, prompt design, data curation |
