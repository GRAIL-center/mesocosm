# ADR-0086: The purpose is to produce new misaligned behaviors and release them for research

Status: accepted · Date: 2026-11-18 · Supersedes the purpose and the fourth stage of ADR-0078

## Context

ADR-0078 named the long-term use as red-teaming and gave it two halves: finding the conditions under which
a team behaves in misaligned ways, and measuring what monitoring misses. Summaries and reports written from
it presented the measurement of monitors as half of the goal. The maintainer corrected this on 2026-11-18:
the goal is the behaviors themselves and what others need to study them.

## Decision

- **Purpose.** Mesocosm keeps producing misaligned behaviors of teams of AI agents that have not been
  reported before, and makes them easy to study. It still reproduces reported behaviors, observes what real
  agents do on real tasks, and elicits behavior by varying enforced conditions; these serve that purpose.
- **What is released for each behavior.**
  - The conditions it needs, stated so that others can reproduce it: the behavior card with one state per
    lever, the tested values and the untested ones.
  - What happened: the records of the runs, with what each agent read and did.
  - The materials: the environment, the data and the tools to run it again and to vary it.
- **The fourth stage.** Red-teaming is the search that produces such behaviors. A case is complete when it
  is released with its conditions, its records and its materials. It is not a procedure that certifies one
  team before deployment.
- **Monitoring is one use of the cases.** Monitor misses and false alarms are measured where a study is
  about a monitor. They are not part of what completes a case, and a statement of the purpose does not
  present them as half of it.

## Consequences

- Unchanged from ADR-0078: the order of the four stages, agent teams as the builders, permissive building
  with strict labels and records, guidance as a skill before code, the separation of exploration and
  claims, the checkpoint as a condition, and the review of an elicitation recipe for a hazardous behavior
  before release.
- Unchanged: detectors are checked against the cases of stage 1 before their flags are read (stage 2).
- The README, the milestones and the agent instructions state the purpose in these words.
- Still to decide: the release review procedure and the first targets of the search.
