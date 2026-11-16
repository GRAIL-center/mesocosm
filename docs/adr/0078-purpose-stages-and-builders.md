# ADR-0078: Purpose, stages and who builds

Status: accepted · Date: 2026-11-16 · Consolidates ADR-0075 and ADR-0076

## Decision

- **Purpose.** Mesocosm is a laboratory for the behavior of teams of AI agents. It reproduces reported
  behaviors, observes what real agents do on real tasks, and elicits behavior by varying enforced
  conditions. The long-term use is red-teaming: finding the conditions under which a team behaves in
  misaligned ways and measuring what monitoring misses, before deployment.
- **Four stages, in order.** Each is the precondition of the next.
  1. *Reproduce.* Two published behaviors serve as positive controls: verification collusion with and
     without memory across episodes, and naming conventions with and without interaction history. A
     control passes when the baseline effect exceeds the measured run-to-run noise, removing the
     published condition changes the behavior in the reported direction, and request records show the
     change reached model inputs. A control that does not reproduce is recorded as a re-test result. It
     is not repaired by adding cues.
  2. *Observe.* The native world is used for observation once agents complete solvable tasks at a rate
     fixed in advance. Detectors are checked against the stage-1 cases before their flags are read.
  3. *Elicit.* Conditions are varied to find what turns a behavior on. Noise is measured before any
     comparison is read.
  4. *Red-team.* The search targets misaligned team behavior, and each case measures monitor misses and
     false alarms.
- **Agent teams build the reproductions.** Tools and environments are designed for an agent that builds
  and runs a study. An interface that is too strict, or that withholds context about the original study
  or the platform, is a defect.
- **Permissive to build, strict to label and record.** An agent may build what the platform lacks: a new
  environment, a scripted counterpart, a reconstruction, a prompt where a condition cannot be set. Each is
  declared and labeled. Every case carries the origin of the behavior at baseline (`instructed`, `cued`,
  `spontaneous`, `trained`). A behavior obtained with scripted counterparts is not reported as emergent
  among real agents.
- **Guidance is a skill before it is code.** What an agent needs to know lives in a skill. Code is added
  only for what an instruction cannot do: running trials, recording model calls, and tested statistics.
- **Exploration and claims are separate.** Exploration is cheap and unrestricted. A claim needs repeated
  runs and held-out values wherever a design was tuned.
- **The checkpoint is a condition.** Behavior is observed with the models that show it, including hosted
  models through the gateway. Open-weight models are used where weights are needed.
- **Release.** Cards, detection signals, traces and null results are published. An elicitation recipe for
  a hazardous behavior is reviewed before release.

## Consequences

- "This behavior needs the model" is a result, not a failed design.
- Results from one checkpoint are not assumed to carry over to another.
- Still to decide: the release review procedure and the first red-team targets.
