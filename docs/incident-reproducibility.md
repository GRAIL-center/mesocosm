# From incident demonstrations to condition experiments

The earlier version of this document treated incident-shaped scenarios as a roadmap. That approach is
superseded. Mesocosm's research target is behavior that emerges from real agents performing real tasks
under controlled conditions. Recreating an incident's story with a planted key, decoy scorer and a
sequence of escalating exposures cannot establish that its underlying mechanism has been reproduced.

The old examples are retained only as [legacy fixtures](../examples/fixtures/README.md). Their authored
inputs and limitations are listed there. Existing ADRs remain a historical record of those decisions.

## What an experiment changes

A research setup connects at least three actual agents to task environments, functioning tools and
services. It controls the affordances and incentives agents really encounter. The intended behavior is
an outcome to measure, not an instruction to put in a task or a file to plant in the workspace.

| Condition | Control at the source |
|---|---|
| Reward allocation | How verified task outcomes contribute to individual or group rewards. Keep independent task success separately observable. |
| Evaluation independence | Which evidence produces feedback or reward, who can affect it, and which independent verifier evaluates the final outcome. |
| Memory and repeated interaction | Whether the same sessions and workspaces carry across tasks, with declared reset boundaries. |
| Shared access | Actual filesystem mounts, service permissions, network topology and communication limits. |
| Monitoring | Evidence visibility, per-agent or joint scope, call budget and intervention timing when the runtime supports it. |
| Learning | Frozen weights, context feedback or actual policy updates, labeled separately and recorded with their provenance. |

These are research dimensions, not a claim that every possible setting is implemented. Consult the
[capability matrix](capability-matrix.md) and the experiment configuration validation. Unsupported
controls must be refused rather than approximated with prose instructions to the agents.

## What a result establishes

Keep these measurements separate:

- The original verifier's task outcome and any missing or failed evaluation.
- The reward delivered to each agent and the evidence used to compute it.
- Logged actions and reviewed policy violations, with the rule that made an action disallowed.
- The monitor's assessment, its evidence coverage and errors.
- Any policy update, including its input rollouts and resulting checkpoint.

A shared-store read does not establish collusion. A detector flag does not establish malicious intent.
A monitor's low score does not establish absence of misconduct, especially when evidence is missing or
truncated. A reward message in a carried conversation is context feedback, not reinforcement learning
of model weights.

Run all scheduled conditions and repetitions, including failures and runs with no flags. Establish a
feasible compliant baseline, vary conditions without changing the underlying task unnecessarily, and
use an evaluation independent of the incentive under study. Report task performance alongside the
behavioral outcome and monitor false positives. Joint monitoring and private monitoring are comparisons
of available evidence, not two interchangeable labels for the same prompt.

Real agents may cooperate, fail, ignore a resource or produce no suspicious behavior. Those are valid
observations. A study should not require an incident-shaped result to count as a completed experiment.
