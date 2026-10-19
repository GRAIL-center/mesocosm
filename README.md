<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="docs/assets/banner-dark.png">
    <img src="docs/assets/banner-light.png" alt="Mesocosm: a sealed world for AI agents. Vary the conditions, record everything." width="100%">
  </picture>
</p>

<p align="center"><b>Research infrastructure for eliciting and discovering misaligned behavior in multi-agent systems.</b></p>

Mesocosm puts real coding agents in a small, sealed world. You decide what the world contains and what the agents
may use. Then you change one condition at a time and watch what they do. Every prompt, every reply and every action
is recorded outside the agents' reach.

<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="docs/assets/observer-replay-dark.gif">
    <img src="docs/assets/observer-replay-light.gif" alt="The observer replaying a run: two agents, drawn as a hedgehog and a squirrel, work in forest clearings while speech bubbles say what each one is doing" width="100%">
  </picture>
  <br>
  <sub>A real run, replayed at 4×: two Qwen3-4B agents work on the same task in Docker on a Colab L4. The dotted
  trail appears when one agent reads the other's post on the team board.</sub>
</p>

## Why

In 2026, AI agents that were meant to work alone found each other. In the OpenAI–Hugging Face incident, isolated
agents met through a shared package cache, built their own message board and worked together to beat their scorer.
On the Collusion wiki, agents on timed lookup tasks shared answers through an old wiki they could write to. The
investigators of the first incident had to work from transcripts that some agents had rewritten.

Mesocosm is a controlled room for the same question. It recreates the conditions, keeps every escape attempt
contained, and keeps a record that no agent can edit.

## How it works

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="docs/assets/how-it-works-dark.png">
  <img src="docs/assets/how-it-works-light.png" alt="Three steps: a YAML file describes a world; agents live in a fenced clearing whose only exits are a model gateway and a web gateway; a gateway log, a world log, one timeline and optional activations feed flags and a live view" width="100%">
</picture>

1. **Describe a world.** One YAML manifest lists the agents, their tasks, the services in the world and the
   conditions to vary.
2. **Agents work inside it, sealed off.** Each agent is an unmodified coding agent (OpenCode) on the model you pick:
   open weights on vLLM, or an API model as a baseline. Its only ways out are the model gateway and, if you allow
   it, the web gateway. Both log every request.
3. **Everything is recorded.** Prompts and replies, every action in the world and, if you ask for them, the model's
   activations land on one timeline. The judge flags moments worth a look, and the observer lets you watch or replay.

A **comparison run** starts from the same state every time and changes one condition. **Persistent operation** keeps
the world running and carries memory from task to task. When something interesting happens there, you snapshot the
world and fork it into comparison runs.

## What you can vary

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="docs/assets/constraints-dark.png">
  <img src="docs/assets/constraints-light.png" alt="The constraint sprites in four groups: fences (sandbox, internet, hidden channels, build cache), supplies (tokens, model calls, cost, ledger), conditions (memory, exposure, clock, chat, models) and watchers (traps, honeypot, protected files, monitor, side task, flags)" width="100%">
</picture>

Each condition is one sprite on the observer's board, and its color shows whether it held, was pushed or broke. A
condition the platform cannot enforce makes the run fail. It is never faked with a line in the prompt.

<details>
<summary><b>The real board</b> at the end of the run above</summary>
<br>
<picture>
  <source media="(prefers-color-scheme: dark)" srcset="docs/assets/board-dark.png">
  <img src="docs/assets/board-light.png" alt="The observer's constraint board after the run: sandbox 2/2 sealed, internet closed, two hidden channels open, 961 of 160K tokens, 10 of 800 model calls, one trap set, two protected files with one push refused, no flags" width="100%">
</picture>
</details>

## Quick start

You need Python 3.11–3.13, [uv](https://docs.astral.sh/uv/), Docker and an NVIDIA GPU for local inference.
Start with [`examples/world-native-three.yaml`](examples/world-native-three.yaml): three actual OpenCode
agents on the original external Terminal-Bench `fix-git` environment and verifier. Follow the
[native runtime runbook](docs/native-multiagent.md) to pin the task/model, generate private keys and start
the matching model and Docker stack, then run:

```bash
uv sync --locked --extra dev --extra gateway
uv run pytest
uv run mesocosm world validate runs/native-three/world.yaml --json
uv run mesocosm run runs/native-three/world.yaml \
  --compose-file runs/native-three/compose.yml \
  --verifier-network bridge --allow-unenforced-resources --json
uv run mesocosm observe serve runs/<run_id>
```

A run needs vLLM, the model gateway and the sandbox up first. Native task environments are the default;
the older hand-written smoke examples require explicit `--task-runtime fixture --verifier-image <image>`.
Their filenames, fixed answers and seeded conversations are documented in [`examples/README.md`](examples/README.md).
Fixture results do not establish native workload completion. To watch a run while it happens, start the observer and add
`--gateway-log` with the gateway's log file. Activation work needs `uv sync --extra observe`, and vLLM lives in its
own environment.

The [Colab A100 acceptance run](docs/native-multiagent-evidence.md) completed 11 model requests,
eight shell tools and three original verifiers across three independent agents. All task rewards
were zero: the runtime worked, while this model did not solve the Git repair task.

<details>
<summary>More commands</summary>

```bash
# authored fixture scenarios and chains, with explicit execution mode
uv run mesocosm scenario run examples/scenarios/channel-exposure.yaml \
  --task-runtime fixture --verifier-image debian:bookworm-slim --json
uv run mesocosm chain examples/chains/smoke.yaml \
  --task-runtime fixture --verifier-image debian:bookworm-slim --json
# the judge's candidate incidents
uv run mesocosm flags list runs/<run_id> --json
# a model scores every transcript
uv run mesocosm judge monitor runs/<run_id> --model qwen3-4b --url http://127.0.0.1:8080/v1 --json
# agent-hours, tokens and cost per agent-hour
uv run mesocosm report runs/<run_id> --gpu-hour-cost 0.8 --json
# the run as an Inspect eval log
uv run mesocosm export inspect runs/<run_id> --json
# a linear probe over captured activations
uv run mesocosm activations probe train runs/<run_id> --layer 18 \
    --out probes/leak.safetensors --json
# replay a finished run at 4x
uv run mesocosm observe serve runs/<run_id> --replay 4
```

Every command accepts `--json` and then prints exactly one JSON object on stdout.
</details>

## Reproducing real incidents

| Incident | What happened | What Mesocosm stages |
|---|---|---|
| **OpenAI–Hugging Face** | Isolated agents met through a shared package cache, built a message board and gamed their scorer. | A shared build cache that leaks traces, a scorer file within reach and a monitor model ([`hf-incident`](examples/scenarios/hf-incident.yaml), [`unsanctioned-board`](examples/scenarios/unsanctioned-board.yaml)) |
| **Collusion wiki** | Agents on timed lookup tasks coordinated through a writable old wiki. | Timed rounds with staggered cohorts ([`world-timed-cohorts`](examples/world-timed-cohorts.yaml)), a decommission deadline on the clock, and a writable site on the web gateway's allow-list |

A real escape or a real victim is never reproduced, only the contained and logged attempt.
[`docs/incident-reproducibility.md`](docs/incident-reproducibility.md) goes through both incidents behavior by
behavior.

## Safety rules

- Agents run on a Docker network with no route to the internet. Every run starts with an egress check that must fail
  to reach the outside.
- The only ways out are the model gateway and, when enabled, the web gateway, which enforces an allow-list and logs
  every request.
- API baselines are reached by the model gateway at fixed endpoints, never by an agent.
- Records live outside the sandbox and are append-only. Agents hold no credentials for the control plane.

## Status

Pre-alpha. Work is tracked as milestones M0–M8.

| Milestone | What it adds | State |
|---|---|---|
| M0–M1 | One agent on one task, end to end, in a sealed sandbox | done |
| M1.5 | Live observation and replay | done |
| M2–M3 | Many agents, world services, channels, conditions with receipts, chained runs | done |
| M4 | Staged scenarios, traps, flags and a review queue | done |
| M5 | Activations recomputed from the logged tokens | done |
| M6 | Persistent operation, snapshots and forks | done |
| P1 | A realistic world: personas, account roles, MCP tools, the real internet through the web gateway | done |
| P2 | Chat, verifiable world state, a model per agent, side tasks and a monitor, activation probes | built |
| Since P2 | The forest observer, a shared build cache, a decommission deadline, timed rounds, a honeypot | built |

"Done" means verified in Docker on a Colab L4. "Built" means covered by the test suite but not yet run in Docker
(ADR-0027 to ADR-0036). Details are in [`docs/milestones.md`](docs/milestones.md) and
[`docs/capability-matrix.md`](docs/capability-matrix.md).

## Learn more

- [`ARCHITECTURE.md`](ARCHITECTURE.md): the components and how they connect.
- [`docs/operation-contract.md`](docs/operation-contract.md): the Python API and `--json` CLI that people and
  research agents share.
- [`docs/capability-matrix.md`](docs/capability-matrix.md): what is supported, unsupported or unverified, with evidence.
- [`docs/adr/`](docs/adr/README.md): every design decision, one record each.
- The project proposal is a Google Doc shared in the GRAIL Slack channel.

<details>
<summary>Repository layout</summary>

```
src/mesocosm/
  core/       manifests, run manager, conditions and receipts, scenarios, chains, snapshots
  gateway/    the model gateway: per-agent keys, budgets, receipts, a model per agent
  env/        the sandbox (Compose renderer, egress check, web gateway) and the world services
  adapters/   the harness adapter (opencode/) and the task adapter (harbor/)
  observe/    the observer (forest view, constraint board, replay), activation recompute, probes
  judge/      flags for traps, state checks and channel use; the review queue; the monitor model
  export/     derived files from a run, such as an Inspect eval log
  cli/        the `mesocosm` command line
examples/     worlds, scenarios, chains and tasks to start from
spike/        M0 scripts and the runbook
docs/         milestones, operation contract, capability matrix, ADRs, README figures
```
</details>

## Contributing

Read [`AGENTS.md`](AGENTS.md) first. It is the operating contract for coding agents and people alike (`CLAUDE.md` is
a symlink to it). Decisions are recorded as ADRs in `docs/adr/`. Each adapter and each controllable condition has a
row in the capability matrix that says whether it is supported, unsupported or unverified, with the evidence.

Mesocosm is released under the [MIT License](LICENSE).
