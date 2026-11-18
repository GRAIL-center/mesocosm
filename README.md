# Mesocosm

**An experimental environment for reproducing, observing and eliciting multi-agent behavior.**

Mesocosm is a laboratory for the behavior of teams of AI agents. It reproduces behaviors that have been
reported, observes what independent agents do on real tasks, and elicits behavior by varying enforced
conditions instead of instructions. The long-term use is red-teaming: finding the conditions under which
a team behaves in misaligned ways, and measuring what monitoring misses, before deployment. The
reproductions are meant to be built and run by teams of AI agents, so the tools are designed for an agent
builder: room to build new environments, context about the original study, and checks that return answers.

Reported multi-agent behaviors depend on conditions that look incidental, such as one prompt sentence,
an adjacent model version or memory between episodes, and a report rarely says which conditions matter.
Mesocosm therefore enforces each condition in the environment and records what reached every agent.
You configure access, budgets, memory, incentives and monitoring; agents choose their actions. Task
success, rewards, monitor assessments and reviewed behavior remain separate measurements.

A study can rerun a published collusion experiment with and without memory between episodes. It can rerun
a published training experiment, with one training run as one trial, to see whether a behavior that grows
under reinforcement learning appears again. Or it can give three agents an unchanged external task, expose
selected shared resources, and compare outcomes when incentives or oversight change.

[Run three agents](docs/native-multiagent.md) · [Submit a job](docs/execution.md) ·
[Choose variables](docs/experimental-variables.md) · [Compare conditions](docs/experiments.md) ·
[Task sources](docs/task-sources.md) ·
[Add an integration](docs/integrations.md) ·
[Study a behavior](skills/behavior-study/SKILL.md) ·
[Architecture](ARCHITECTURE.md) · [Direction](docs/adr/0078-purpose-stages-and-builders.md)

## What it does

| Function | What Mesocosm provides |
|---|---|
| **Execute real tasks** | Independent OpenCode processes, sessions and workspaces; native Docker task environments and original verifiers. Research worker jobs require at least three agents. |
| **Connect other systems** | Installed task-source and execution-backend plugins; original task formats, environment lifecycles and evaluators can be integrated without editing the core. |
| **Control conditions** | Agent and team model budgets, service permissions, communication access, memory policy and incentive allocation. Supported controls are enforced by the relevant component. |
| **Study repeated interaction** | Bounded records from prior tasks, selected and delayed feedback, measured reward components, reviewer matching, observed reputation, work assignment and publication of actual outputs. |
| **Run comparisons** | Named condition variants, repeated trials, fixed input records and explicit failed or missing observations. |
| **Monitor and intervene** | Posthoc review or assessment before supported tool calls, with recorded allow, deny or termination decisions. |
| **Update policies** | An optional REINFORCE/LoRA training loop using validated action/reward records and checkpoint activation between worlds. |
| **Inspect evidence** | Original model and harness records, tool events, verifier results, enforcement receipts, CLI reports, diagnostics and integrity checks. |
| **Study a behavior** | An agent brings an environment as a manifest and a command, measures the unchanged baseline, runs comparisons that return the effect next to the run-to-run noise, and confirms claims as behavior cards. The environment can be a reconstruction or a study's published code, and a trial can be one training run ([ADR-0085](docs/adr/0085-training-runs-as-study-trials.md)). Three commands and a [skill](skills/behavior-study/SKILL.md). Three environments have real-model runs: a naming game ([evidence](docs/naming-game-evidence.md)), the published Emergent Collusion code ([evidence](docs/agent-collusion-evidence.md)) and the published steganographic-collusion training code ([evidence](docs/steganographic-collusion-evidence.md)). |

The built-in Docker/OpenCode runtime supports a defined subset of Debian/Ubuntu tasks and original Compose services.
Shared-service mode retains separate agent workspaces and records one team measurement. Compatibility
with a task directory must be checked against its actual environment and verifier requirements.
See [native task support](docs/research-runtime.md).

## How a run works

With the built-in Docker/OpenCode backend:

1. **Select an external task.** Preserve its instructions, initial environment and independent verifier.
2. **Define a world in YAML.** Configure agents, models, resources, access, rewards and monitoring.
3. **Execute on Linux.** The Docker worker owns task services, agent execution, verification and cleanup.
   Model inference can run on a separate GPU host or compatible endpoint.
4. **Inspect the run bundle.** Review the complete recorded trajectory alongside outputs and original
   verifier results. Use the same inputs and declared changes for a condition comparison.

A client can submit jobs from a Mac or Linux machine and disconnect while an accepted job runs.
The worker retains lifecycle state and supports status, cancellation and result retrieval.
Direct execution on a Linux host is also available. [Execution guide →](docs/execution.md)

## Get started

```bash
git clone https://github.com/GRAIL-center/mesocosm.git
cd mesocosm
uv sync --locked --extra gateway
uv run mesocosm --help
uv run mesocosm capabilities --json
```

This installs the control software; the built-in runtime also requires a supported Linux Docker environment,
a model endpoint and an external task.

| Next step | Guide |
|---|---|
| Configure a first three-agent world | [Native setup](docs/native-multiagent.md) · [Example manifest](examples/world-native-three.yaml) |
| Choose compatible external tasks | [Task source comparison](docs/task-sources.md) · [Runtime requirements](docs/research-runtime.md) |
| Add a benchmark, simulator or agent runtime | [Integration contracts and registration](docs/integrations.md) |
| Submit and manage execution | [Client and worker](docs/execution.md) |
| Vary conditions across trials | [Experiments](docs/experiments.md) · [Research controls](docs/research-controls.md) |
| Build a research hypothesis | [Variable manual](docs/experimental-variables.md) · [Paper notes](docs/research-literature.md) · [Variable audit](docs/variable-audit.md) |
| Train from verified rollouts | [Policy training](docs/training.md) |
| Reproduce a behavior with an agent | [Behavior-study skill](skills/behavior-study/README.md) |
| Rerun a study's published code | [Emergent Collusion](examples/agent-collusion/README.md) · [Steganographic collusion under training](examples/steganographic-collusion/README.md) |

## Add an integration

A **task source** reads a benchmark's original inputs. An **execution backend** owns its environment,
agent interfaces, evaluator and cleanup. Existing Harbor-compatible tasks use the built-in backend;
different environments can supply both plugins as a separate Python package.

```bash
uv run mesocosm integrations list --json
uv run mesocosm integrations check /path/to/world.yaml --json
```

Selected plugins are checked before execution and pinned in portable inputs. Backend registration does
not automatically provide every condition, monitoring or RL feature; unsupported combinations must be
rejected. See the [integration guide](docs/integrations.md) for contracts and a deployment example.

## Inspect results

Run bundles retain configurations, native model/harness records, events and original verifier artifacts
outside agent workspaces. With `RUN_BUNDLE` set to a collected bundle directory:

```bash
uv run mesocosm report "$RUN_BUNDLE" --json
uv run mesocosm diagnose "$RUN_BUNDLE" --json
uv run mesocosm evaluate "$RUN_BUNDLE" --json
uv run mesocosm audit graph "$RUN_BUNDLE" --json
```

Reports summarize the evidence; diagnoses help distinguish model/tool failures, incomplete turns and
verifier problems. Audit commands check recorded links and preserved bytes. None establishes that
uncaptured activity did not occur. The [operation contract](docs/operation-contract.md) documents the
CLI and worker API. Optional activation analysis and monitor intervention remain separate research
functions.

## Direction

Work proceeds in four stages, and each is the precondition of the next. Agent teams do the building,
under one rule: permissive to build, strict to label and record
([ADR-0078](docs/adr/0078-purpose-stages-and-builders.md)).

| Stage | What it establishes |
|---|---|
| 1. Reproduce | The laboratory can obtain a published behavior, and lose it when the published condition is removed. |
| 2. Observe | What teams of agents do on real tasks, read with detectors checked against stage 1. |
| 3. Elicit | Which enforced conditions turn a behavior on, against measured run-to-run noise. |
| 4. Red-team | Under which conditions a team behaves in misaligned ways, and what monitoring misses. |

The project is at stage 1. One positive control has small real-model runs: with Qwen3-4B, populations
settled on a shared name without an instruction when the prompt asked for brief reasoning, and stopped
doing so without interaction history; Qwen3.5-9B did not ([evidence](docs/naming-game-evidence.md)). The other
control, the published Emergent Collusion code, has one exploration run: Qwen3.5-9B agents accepted each
other's work against the verification rule with and without memory across episodes
([evidence](docs/agent-collusion-evidence.md)). A behavior that grows under training has one run: the
published steganographic-collusion code, trained for ten hours on one seed, reached the paper's result, a
sender and a receiver that pass on information the sender is told to leave out in a form the overseer does
not flag. The first word of the sender's text carries it, and no control arm has completed
([evidence](docs/steganographic-collusion-evidence.md),
[report](docs/reports/steganographic-collusion/report.tex)).
[Milestones](docs/milestones.md) give the evidence and exit criteria for each stage.

## Project status

Mesocosm is **pre-alpha**. Implementation, automated tests and real-agent validation have different
coverage. The newer Linux worker and shared-service execution paths still need full three-agent
acceptance, as do the new repeated-interaction controls. A completed policy update does not establish
improved behavior, and task failure alone does not establish a platform defect.

The [capability matrix](docs/capability-matrix.md) tracks verified scope and remaining limits.
The [acceptance procedure](docs/native-acceptance.md) defines the next integration checks.
[Milestones](docs/milestones.md) and [design decisions](docs/adr/README.md) explain the development direction.

## Development

Read [AGENTS.md](AGENTS.md) before contributing. Source is in [`src/mesocosm/`](src/mesocosm/),
tests in [`tests/`](tests/), and configurations in [`examples/`](examples/).

```bash
uv sync --locked --extra dev --extra gateway
uv run pytest
uv run mypy src
uv run ruff check .
```

Optional learning tests need the separate `learning` dependencies. GPU inference and Docker acceptance
require their documented execution environments.

## License

Copyright (c) 2026 **Seunghyun Yoo**. Mesocosm is source-available under the
[PolyForm Noncommercial License 1.0.0](LICENSE). Noncommercial use is free under its terms,
including permitted personal and research uses. Commercial use requires a separate agreement.

For commercial licensing, research partnerships or project-specific infrastructure, contact
Seunghyun Yoo through the [repository's issues](https://github.com/GRAIL-center/mesocosm/issues).
Third-party components retain their respective licenses.
