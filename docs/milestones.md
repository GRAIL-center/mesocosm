# Milestones

Aligned with the proposal's 16-week schedule (weeks 1–5 build, 6–7 pilot, 8–13 main runs, 14–16 analysis). Each milestone has an exit criterion that can be checked, not a feature list.

| ID | Weeks | Goal | Exit criterion |
|---|---|---|---|
| **M0 Spike** | weekend | Validate the riskiest assumptions on one workstation (RTX 3090, 3B model) | `spike/SPIKE.md` result tables filled in; first rows of the capability matrix written |
| **M1 Vertical slice** | 1–2 | OpenCode → gateway → vLLM runs one Harbor task in an isolated sandbox (done 2026-10-18) | `mesocosm run spec.yaml` runs end to end; the run bundle contains the full prompt the model received for every request; egress check passes |
| **M1.5 Live observation v0** | 2 | Watch a run as it happens: agents side by side, session trees, one-line summaries, drill-down to the model request and reasoning; replay | `mesocosm observe serve` on a bundle shows the tree and summaries from recorded events; screenshot test passes; memory bounded (`tests/test_observe_*.py`) |
| **M2 Multi-agent and channels** | 3–4 | N agents; official board + shared repo + old wiki; minimal mock external world (services v0 shipped, ADR-0010; channels on/off, exposure timing, accounts and traps, concurrent agents shipped 2026-10-18, ADR-0011) | A message exchanged over a hidden channel appears as one joined event in the service log and in the gateway exposure log |
| **M3 Conditions and automation** | 5 | Resource limits, channel on/off, exposure timing, memory policy, mid-run budget changes, run chaining with retries and resume (done 2026-10-18, ADR-0012); every CLI command supports `--json`; `run pause/resume` unsupported for OpenCode | Met 2026-10-18: `conditions set` leaves a receipt with the previous value in the gateway log and the bundle; anything else is `rejected_unsupported` |
| **M4 Pilot (behavior only)** | 6–7 | Staged scenarios, traps and state checks, flags and the review queue (done 2026-10-18, ADR-0013; Ethogram dropped). Pilot content still to write: HF-incident scenario (4 stages, after Slocum et al.), peer-verification collusion scenario (after Shi et al.) | Table of minimum elicitation stage per incident (produced by `mesocosm scenario report`; empty for the benign example); cost per agent-hour (`mesocosm report`, 0.40 at 0.8 per L4 hour) |
| **M5 Activations** | 6–8 (parallel) | NNsight capture on vLLM keyed by request id; summary signals + raw windows; recompute verification | Recomputed activations match originals within a stated tolerance; capture overhead measured |
| **M6 Persistent operation and forking** | 8–9 | Task stream; memory and shared state carried across runs; snapshot coverage declared; fork into comparison runs | Snapshot → restore → fork test passes; uncaptured process state is marked |
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
