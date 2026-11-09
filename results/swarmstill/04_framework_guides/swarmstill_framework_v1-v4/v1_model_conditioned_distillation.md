# Model-conditioned distillation: a guide for harness designers

Use this guide when you design a harness meant to make a different (usually smaller) model reproduce a
collective behavior that was observed with another model. The central idea: **the same behavior can rest on
different causes in different models, so the target model's harness is usually not the original harness.**

## 1. Describe the target as a signature, not a number

A rate alone is easy to hit for the wrong reason. Write down, from the observed traces:

- **Rate**: how often the behavior occurs, per episode and per trajectory.
- **Shape over time**: where it starts (is the first episode clean?), how fast it rises, whether it stabilizes
  or relapses.
- **Pathway**: how each onset happens. Typical pathways: explicit agreement in messages; one agent relaxes after
  feedback says its strict verdict was wrong and the peer follows; both relax at once after both were "wrong".
- **Dependencies you can see**: what agents themselves cite when they change (feedback, rewards, earlier
  episodes, the peer's messages).

A reproduction should match the signature, not just the rate. A model that does the behavior from episode 1 for
no stated reason reproduces the rate but not the behavior.

## 2. Profile the target model before designing

Read the target model's profile (supplied separately) and answer:

1. **Baseline disposition.** Without any accumulated history, how often does it already do the behavior? A high
   baseline means the original harness may overshoot and the learned component is hidden under noise.
2. **What moves it.** Does it react to feedback about its own verdicts? To rewards? To what the peer says? To
   its own earlier episodes?
3. **What it cannot do.** Can it negotiate an explicit agreement, keep a commitment over episodes, or use long
   histories without losing the signal? Small models often cannot hold a long context or follow subtle
   incentives; strong models often coordinate explicitly and need less outside pressure.
4. **Capability floor.** Does it still do the underlying tasks well enough for verdicts to mean something? If
   it fails the task often, "correct" and "incorrect" verdicts become noise.

## 3. Map the original pathway onto what the target model can do

- If the original pathway needs a capability the target model lacks, replace it with a lever that delivers the
  same information in a form the model can use: shorter or more focused history, clearer feedback, fewer
  messages to track.
- If the target model's baseline is already high, look for settings that make the clean start (and therefore
  the rise) possible again, instead of pushing the behavior further.
- If the target model's baseline is near zero, look for the minimum added pressure that lets the pathway start,
  not pressure that forces the behavior directly.
- Never produce the behavior by instruction, by rewarding it directly, or by any setting whose only effect is to
  make the behavior compulsory. The behavior must stay a choice the agents make under the conditions you set.

## 4. Design for the causal structure, not only the outcome

- For each setting you choose, say which part of the pathway it supports and what should happen if it were
  removed.
- Prefer harnesses whose behavior depends on the same kind of conditions as the original, so that interventions
  on the small system tell you something about the original one.
- Write down a falsifiable prediction for at least one intervention on your harness.

## 5. Iterate on evidence from the target model

Run the candidate on the target model, compare the signature (rate, shape, pathway, cited dependencies) with the
target, and change one thing at a time. A match on rate with a mismatched shape or pathway is a failed
reproduction, not a near miss.

