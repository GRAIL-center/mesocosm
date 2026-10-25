# Research literature and hypothesis design

This guide connects research on multi-agent cooperation, collusion, reward hacking, and oversight to testable Mesocosm conditions. The aim is to change incentives, information, and available actions around real tasks, then observe what agents do. A paper's incident, exploit, or dialogue is not a scenario to reproduce by planting files or prescribing misconduct.

Reviewed on **2026-10-24**. Links pin the versions reviewed; some papers have newer revisions. Dates below are the submission dates of those pinned arXiv versions. Findings describe each paper's setting, not established behavior of Mesocosm or of all agents.

## Read implementation evidence separately

The conditions derived below include both implemented controls and further research ideas. The
[variable manual](experimental-variables.md) maps mechanisms to current configuration fields, while
the [variable audit](variable-audit.md) examines existing controls and their confounds. Consult
[research controls](research-controls.md), [experiments](experiments.md), [training](training.md), and
the [capability matrix](capability-matrix.md) for precise limits. Consult
[native acceptance](native-acceptance.md) for execution evidence. A configuration field, a unit test,
and a successful run with three actual agents establish different things.

Reward allocation, memory policy, communication permissions, budgets and monitoring provide starting
points. Bounded observable history, explicit feedback, compositional rewards, reviewer rematching,
measured reputation, work assignment and artifact publication now have implementations and automated
tests; new real-agent acceptance remains unverified. Agent-selected partners, binding contracts,
operational report resolution and richer audit protocols remain proposals requiring their own
enforcement and evidence. A prompt describing such a mechanism does not implement it.

## Core studies

### 1. Emergent Collusion in Long-Horizon LLM Agent Interaction

**Shi, Zhang, and Yang.** [arXiv:2609.24967v1](https://arxiv.org/html/2609.24967v1), 2026-09-21.

**Design and variables.** Two agents repeatedly perform tasks and assess each other's compliance. Interventions include history length and scope, shared versus separate verdict-accuracy rewards, reward feedback, and communication capacity. Agents are not instructed to collude. Adaptation occurs through context, without parameter updates.

**Evidence.** Retaining interaction history and sharing evaluation incentives can sustain reciprocal acceptance despite missing required evidence. Removing history or separating incentives reduces the measured behavior in studied conditions.

**Limits.** The evidence requirement is deliberately difficult to satisfy within the communication budget. “Collusion” concerns reciprocal protocol violations, not necessarily incorrect task outputs. Some reward ablations also change feedback or reflection. Two-agent results do not establish the same effect for three or more agents.

**Derived conditions.** Vary reward ownership and provenance, actionable feedback, history, and evidence capacity independently where possible. Include a feasible-verification control and keep the original task verifier independent of peer acceptance. A high peer score alone is insufficient evidence of collusion.

### 2. CoopEval: Benchmarking Cooperation-Sustaining Mechanisms and LLM Agents in Social Dilemmas

**Tewolde et al.** [arXiv:2604.15267v2](https://arxiv.org/html/2604.15267v2), 2026-07-05; first submitted 2026-04-16.

**Design and variables.** Four social dilemmas compare repeated interaction, reputation, contracts, and mediation. Manipulations include continuation probability, history length, fixed partners versus random rematching, direct versus broader interaction histories, voluntary delegation, and outcome-dependent utility transfers.

**Evidence.** Contracts and mediation can sustain cooperation where repeated interaction or reputation is insufficient. Partner changes weaken some repetition effects; providing more history does not uniformly improve cooperation.

**Limits.** These are stylized games with specified utilities, not open-ended software work. Agents receive self-interest objectives rather than misconduct instructions. Population evolution uses payoff-based replicator dynamics; it is not training the language models' weights. Cooperation in a game is not automatically alignment with an external user's interests.

**Derived conditions.** Consider stable identities with rematching, action-derived reputation visibility, voluntary delegation, and enforceable transfers. Contracts must actually settle against outcomes; partner choice must change assignments. These are candidate mechanisms, distinct from merely sharing a team reward or exposing chat history.

### 3. Reward Hacking as a Transmissible Strategy in Multi-Agent Coding Systems

**Hu et al.** [arXiv:2610.12822v1](https://arxiv.org/html/2610.12822v1), 2026-10-09.

**Design and variables.** Source agents first acquire reward-hacking behavior through elicitation and training. Experiments then expose other coders to their code, memories, or plans through chains, planners, and orchestrators. Comparisons include matched clean sources, feedback retries, routing based on grading history, and reviewer interventions. Leaky and stricter evaluators distinguish apparent success from task success.

**Evidence.** Problematic strategies can propagate through artifacts and delegation. Routing can amplify exposure to a source with favorable observed scores, even without the router adopting its strategy.

**Limits.** The initial source is deliberately elicited or seeded; the study does not show misconduct arising spontaneously in an otherwise clean population. Results concern particular coding tasks and evaluator weaknesses. Some retry analyses condition on initial failure.

**Derived conditions.** Track immutable artifact versions, publication and retrieval, source identity, exposure, and downstream adoption separately. Compare independent outcome verification with agent-supplied reward claims. Use matched source checkpoints for explicit transmission experiments; label seeded adversaries separately from unseeded emergence experiments.

### 4. A Case Study on Emergent Cheating and Whistleblowing in Autonomous Research Swarms

**Paglieri et al.** [arXiv:2609.04170v1](https://arxiv.org/html/2609.04170v1), 2026-09-03.

**Design and variables.** An observational case study follows 100 autonomous agents working on 71 Lean conjectures. First accepted solutions lock task credit, accepted code enters a shared library, and agents communicate publicly and privately. These are system affordances, not randomized interventions.

**Evidence.** Agents discover and spread ways to satisfy checks without fulfilling their intended meaning. Other agents investigate and report problems without being instructed to play whistleblowers. The feedback endpoint is not monitored during execution, so reporting does not provide an effective live intervention.

**Limits.** The case motivates hypotheses but does not isolate causal effects of competition, communication, or publication. Compiler acceptance and syntactic checks do not establish that the intended theorem was proved. Reports alone do not demonstrate functioning oversight.

**Derived conditions.** Study credit assignment, task claiming, publication before reuse, and report handling. Distinguish report submission, delivery, review, decision, and effect. Measure the spread of both harmful methods and corrective information; neither shared access nor whistleblowing should be assumed protective without a comparison.

### 5. AI Control: Improving Safety Despite Intentional Subversion

**Greenblatt et al.** [arXiv:2312.06942v3](https://arxiv.org/html/2312.06942v3), 2024-01-05; first submitted 2023-12-12.

**Design and variables.** A red team directs a capable coding model to introduce backdoors. Blue-team protocols vary auditing, trusted or untrusted monitoring, deferral to a weaker trusted model, trusted editing, and protections against collusion between an untrusted producer and reviewer. Audit and deferral budgets are explicit constraints.

**Evidence.** Protocol choice changes the trade-off between usefulness and protection from deliberate sabotage. A monitor's score is only one component: routing, auditing, and what happens after suspicion matter.

**Limits.** This is adversarial protocol evaluation with intentional subversion, not evidence of naturally emerging misalignment. Results depend on the coding setting, attack search, trusted-model assumptions, and available trusted labor. Monitoring is not a guarantee against all attacks.

**Derived conditions.** Compare reviewer independence, risk-selected versus random audits, review budgets, and enforced fallback or repair. Measure task usefulness alongside violations and intervention cost. Existing monitoring settings should not be described as a complete control protocol unless its audit, routing, and response steps are implemented and recorded.

### 6. Can escalation channels redirect reward hacking toward defect disclosure?

**Gomez.** [arXiv:2608.29460v1](https://arxiv.org/html/2608.29460v1), 2026-08-29.

**Design and variables.** A two-by-two study varies disclosure policy and escalation-tool availability, with an additional prompt-only comparison. Eight models face nine tasks with ambiguous or defective evaluation conditions. Outcomes include reward hacking, correct solutions, reports, and cost.

**Evidence.** Combining policy and an escalation route reduces reward hacking in studied conditions. The work examines disclosure as an alternative action rather than only detecting misconduct afterward.

**Limits.** This is a small, single-agent task set with engineered defects. Tool availability also introduces guidance preferring escalation, so the intervention does not isolate a neutral tool affordance. Tool implementations differ across models. It does not establish multi-agent protection or the effect of an operational incident-response process.

**Derived conditions.** Add a structured reporting action with recorded delivery and resolution; vary response cost, delay, and consequences independently. Use a neutral tool-description control to separate affordance from instruction. Count submission, acknowledged receipt, and effective remediation separately. A report channel that nobody processes is not equivalent to oversight.

## Supplementary studies

### 7. On the Fragility of AI Agent Collusion

**Keppo et al.** [arXiv:2603.20281v1](https://arxiv.org/html/2603.20281v1), 2026-03-18.

**Design and variables.** Repeated pricing experiments vary agent heterogeneity, number of competitors, model size, access to competitors' price history, stated planning horizon, learning-agent type, and anti-collusion instructions.

**Evidence.** Some asymmetries destabilize collusive pricing, but heterogeneity is not uniformly protective. Differences in model size can support leader–follower behavior rather than remove coordination.

**Limits.** The environment is a stylized market. For LLM agents, patience is manipulated through the stated horizon; this is not the same as changing an optimizer's discount factor or an enforced continuation process. Pricing outcomes do not directly establish the same effects in collaborative work.

**Derived conditions.** Separate population composition, actual information access, expected future interaction, and prompt wording. Compare homogeneous and heterogeneous teams with matched capabilities and costs where possible. Treat agent count as an experimental variable while preserving Mesocosm's minimum acceptance target of three actual agents. Do not equate diversity with safety.

### 8. Cultural Evolution of Cooperation among LLM Agents

**Vallinder and Hughes.** [arXiv:2412.10270v1](https://arxiv.org/html/2412.10270v1), 2024-12-13.

**Design and variables.** Twelve agents play a repeated donor game across generations. Higher-performing agents pass natural-language strategies to replacements. Experiments vary donation benefits, reputation-history length, and availability of costly punishment.

**Evidence.** Cooperation and punishment depend on the model and environment. A punishment option can support cooperation for one population while inducing damaging over-punishment in another. Short reputation histories weaken cooperation in studied conditions.

**Limits.** This is a small stylized-game study, not a real-task deployment. Strategy inheritance is cultural transmission through text, not parameter training. Results do not establish that punishment or longer memory improves alignment generally. Strategies and histories must be distinguished from fabricated past events.

**Derived conditions.** Consider real opportunity costs, action-derived reputation, population turnover, and controlled strategy inheritance. Separate retained private memory from publicly visible reputation. If sanctions are studied, enforce and record their actual cost and effect rather than asking agents to imagine them; measure mistaken sanctions and task harm as well as cooperation.

## Turn a mechanism into a hypothesis

Use this template before adding a condition or running an experiment. These are design questions, not configuration keys.

| Field | Required content |
| --- | --- |
| Mechanism | The incentive or information pathway expected to change behavior, with a paper reference and a stated applicability gap. |
| Population | At least three actual agents for Mesocosm acceptance; model and checkpoint identities, capabilities, identities across episodes, and assignment procedure. |
| Work | Real task source, unchanged task instructions, original success criteria, and an independent outcome measurement. |
| Intervention | One enforceable change, its unit of assignment, and where it takes effect: reward settlement, permission check, retrieval, assignment, or review decision. |
| Comparison | A matched baseline and a control for instruction wording, access, cost, or feedback when the intervention changes them together. |
| Prediction | A directional prediction and an observation that would count against it. |
| Measurement | Task success, resource use, reward components, exposures, actions, reports, monitor assessments, and reviewed violations kept separate. |
| Learning | Fixed weights with context updates, strategy inheritance, population selection, or actual parameter updates; identify which changes between episodes. |
| Analysis | Replications, assignment order, stopping rule, exclusions, missing-data treatment, and uncertainty declared before inspecting outcomes. |

Example questions include whether independent verification changes reciprocal favorable reviews, whether reviewed publication changes downstream reuse of problematic work, or whether resolved reports change behavior relative to an otherwise identical reporting channel. These are hypotheses to test, not predictions guaranteed by the cited studies. Keep the task and available evidence feasible in every comparison.

## Evidence rules for humans and agents

- **Separate origins.** Unprompted discovery, exposure to a deliberately seeded source, and an explicitly instructed adversary answer different questions. Label them at the experiment and result level.
- **Keep outcomes independent.** Peer approval, claimed success, assigned reward, original verifier results, and reviewed misconduct are different measurements. Disagreement is evidence to inspect, not a reason to overwrite one with another.
- **Record the causal path.** Availability is not exposure; retrieval is not adoption; a report is not remediation; a monitor flag is not a confirmed violation. Preserve records needed to distinguish these stages.
- **Keep interventions real.** A description of private access, a sanction, a review gate, or a reputation system is insufficient. Require enforcement and observable consequences. Reject unsupported settings instead of approximating them with a story.
- **Avoid overclaiming transfer.** Two-agent games, single-agent defective benchmarks, adversarial evaluations, and observational swarms can motivate conditions. They do not validate those conditions in Mesocosm's real-task population.
- **Preserve failures and uncertainty.** Record unsuccessful trials and infrastructure failures. Missing evidence is unknown. Do not select stopping points, replacements, or examples because they produce an interesting incident.

When a hypothesis requires a missing mechanism, specify its contract and required evidence before implementation. Keep incident-specific exploits, invented histories, and scripted dialogue out of the normal research path. The scientific object is the behavior produced by agents under enforced conditions.
