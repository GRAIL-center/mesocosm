# Model-conditioned distillation, v4: a general guide for predicting and designing collective behavior

Use this guide to predict how a collective behavior of interacting language-model agents changes when the
environment changes, and how the answer depends on the model. Version 4 removes everything specific to the peer-review
collusion study that produced versions 1–3 and keeps what transferred. It was frozen before any data from the
behaviors it is tested on were collected.

A behavior is B = F(M, P, S, E): the model (M), its instructions and incentives (P), the social structure (S: who talks
to whom, in what order, with what memory) and the environment (E: tasks, information, feedback). Most prediction
errors come from reasoning about P alone.

## 1. Agents respond to meaning, not arithmetic

- **Pressure that openly asks for a violation is often refused.** Agents keep a stated rule when the request to break
  it is explicit or is paid for directly. Do not assume that adding pressure or payment for the unwanted act raises it.
- **Violations and failures need a legitimate-sounding reason.** A behavior spreads when another stated objective or a
  plausible authority lets the agent call it the right thing to do. Look for the sentence in the environment that
  makes the behavior look correct.
- **What an agent sees is what it acts on.** An agent cannot keep, resist or leak information it never received, and
  it tends to copy what it received last. Before predicting, trace exactly which messages each agent sees at each turn.

## 2. Write the behavior as a chain of necessary links

Write the behavior as the links that must all hold, for example: the information or belief is introduced AND it reaches
the next agent AND that agent keeps it (or adopts it) AND it reaches the output. For each change, ask which link it
removes or strengthens.

- A change that removes a link the model depends on predicts a collapse, not a partial drop.
- A change that touches a link the model does not use predicts little change.
- Apply a measured effect to every other change that shares the same link.

## 3. Structure decides exposure

- **Topology sets how many hops a message travels and how many sources each agent hears.** In a chain, content must
  survive every hop and each agent hears one voice. With many incoming edges, an agent hears several voices: a single
  dissenting or false voice is diluted, but a shared false voice is reinforced, and agents receive more text to copy.
- **Turn order and repetition decide what is fresh.** The last message received dominates small models more than large
  ones.
- **Separate the structural effect from the content effect.** A topology change alters both who hears a message and how
  much text each agent must process; say which of the two you expect to matter for this model.

## 4. Identify the model's own pathway before predicting

The same behavior can rest on different pathways in different models.

- **Deliberate pathway** (stronger models): the agent weighs the rule, the request and the evidence; it changes when
  the reasons change.
- **Copying pathway** (weaker models): the agent echoes or summarizes its inputs; it changes when what it receives
  changes, and it is insensitive to reasons.
- **Collapse pathway** (models below the task's capability floor): output quality is low regardless of condition;
  apparent differences between conditions are noise or format failures.

Read the profile for signs: how far each model's baseline is from 0% and 100%, how its baseline differs across
structures, and its task competence. A model near a floor or a ceiling cannot move further in that direction.

## 5. Capability floor and direction

Corrective or contrary information changes behavior only if the model can recognize it as relevant. Below that level,
adding correct information or warnings does little, and removing them does little. Check competence and format failures
before reading behavior. When a model is weak, predict small and noisy effects, not reversed ones.

## 6. Size and family

- Within one family, larger models usually follow instructions and keep constraints better, but they also integrate
  more of what they read, which can carry more of a false or private input forward. Do not assume monotone effects:
  predict the direction from the chain in section 2.
- Across families of similar size, instruction-following style differs more than raw ability. Use each model's
  baseline as the anchor and predict changes relative to it.

## 7. Predicting

- Start from each model's measured baseline; predict the change, not the level.
- For each change: name the link it acts on, say which models depend on that link, predict a direction, then a size.
- Prefer "little change" for a model at a floor or ceiling, and for changes that touch a link the model does not use.
- State which models you expect to respond most and why.

## 8. Designing a harness for a target model

- Keep the original's links if the target model can use them; restore a missing link before adding pressure.
- Never produce the behavior by instructing it; that is not a reproduction.
- Match the signature (baseline level, how it varies with structure, which interventions move it), not only the rate.
- Probe the most likely necessary link on the target model before committing to a design.
