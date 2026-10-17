# Mesocosm

Research infrastructure for eliciting and discovering misaligned behavior in multi-agent systems.

Mesocosm is a continuously operating, controllable research world in which open-weight coding agents work on real benchmarks and tasks inside an isolated sandbox. Researchers (and, later, authorized research agents) vary conditions such as resource limits, hidden communication channels, information exposure and memory, and the platform records what each agent actually received, what it did, and the model's internal activations at the moment of the behavior, all on one timeline. The research purpose is to reproduce misaligned behavior reported in real incidents and to discover behavior that has not been reported yet.

Project proposal: see the Mesocosm proposal (Google Doc, GRAIL Slack channel). This repository holds the platform.

## Status

Pre-alpha. We are at **M0 (spike)**: validating the riskiest technical assumptions on a single workstation before writing the platform. See [`spike/SPIKE.md`](spike/SPIKE.md) and [`docs/milestones.md`](docs/milestones.md).

## What the platform does (target)

| Capability | Meaning |
|---|---|
| Real work | Tasks come from executable benchmarks (Terminal-Bench, SWE-bench) through Harbor; agents are public coding agents (OpenCode first) running open-weight models on vLLM. |
| Two run modes | *Comparison runs* restore the initial state every run and randomize conditions. *Persistent operation* keeps supplying tasks and carries memory and shared state across runs. A behavior found in persistent operation is forked from the saved state into comparison runs that change one condition. |
| Enforced conditions | Resource limits, channels, exposure and memory policies are applied at the point where they take effect (the model gateway, the environment services), and every applied condition leaves a receipt. Unsupported conditions fail explicitly. |
| Joint evidence | Actions, messages, what each agent actually read, CoT, activations, memory, resource use and environment state, keyed to the same run/agent/step identifiers. |
| Training-ready records | Token sequences, action masks, reward version and policy version are kept so that SFT/RL can consume the records later. Applying training is out of scope. |
| Agent-operable | One operation contract (Python API + `--json` CLI, later MCP) that both people and authorized research agents can call. Experimental agents never hold control-plane credentials. |

See [`ARCHITECTURE.md`](ARCHITECTURE.md) for components and [`docs/operation-contract.md`](docs/operation-contract.md) for the interface.

## Repository layout

```
src/mesocosm/
  core/       world manifests, run manager, scheduler, conditions, snapshots, record store
  gateway/    OpenAI-compatible model gateway: exposure logging, budgets, request tagging
  env/        sandbox networks, shared services (board, repo, wiki), mock external world
  adapters/   harness adapters (opencode/), task adapters (harbor/)
  observe/    activation capture (vLLM + NNsight), recompute verification, dashboards
  judge/      traps, environment-state checks, LLM judges, behavior catalog (Ethogram)
  cli/        `mesocosm` command line
spike/        M0 scripts and runbook (host-only day 1, Docker isolation day 2)
docs/         milestones, operation contract, capability matrix, ADRs
```

## Development setup

Python 3.11 or 3.12 (vLLM and NNsight constraints), [uv](https://docs.astral.sh/uv/), Docker, an NVIDIA GPU for anything that runs a model. Smoke tests target a single RTX 3090 (24 GB) with a 3B model.

```bash
uv sync --extra dev
uv run pytest
```

Model-dependent extras:

```bash
uv sync --extra gateway            # FastAPI gateway
uv sync --extra observe            # torch, transformers, nnsight
# vLLM is installed separately in its own environment; see spike/SPIKE.md
```

## Safety rules (apply from the first run)

- Experimental agents run on a Docker network with no route to the internet. Every run starts with an egress check that must fail to reach the outside.
- The only way out of the sandbox is the model gateway, which reaches the inference server and nothing else.
- Commercial baseline models are called by the control plane outside the sandbox, at fixed API endpoints.
- Records live outside the sandbox and are append-only. Agents have no credentials for the control plane.

## Contributing

Read [`AGENTS.md`](AGENTS.md) first; it is the operating contract for coding agents and people alike (`CLAUDE.md` is a symlink to it). Decisions are recorded as ADRs in `docs/adr/`. Each adapter and each controllable condition has a row in `docs/capability-matrix.md` stating whether it is supported, unsupported or unverified, with the evidence. Work is tracked as GitHub milestones M0–M8.
