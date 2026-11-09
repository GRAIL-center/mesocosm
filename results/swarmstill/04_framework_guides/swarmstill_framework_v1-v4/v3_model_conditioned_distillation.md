# Model-conditioned distillation, v3: a guide for harness designers

Use this guide to predict how a model's collective behavior responds to changes in its environment, and to
design a harness that makes another (usually smaller) model reproduce that behavior. Version 2 adds what earlier
designers got wrong: they recognized the right ingredients but predicted as if agents were reward maximizers, and
they did not carry measured probe results over to related changes. Version 3 adds what two rounds of harnesses on a
4B target model measured (section 7): which direction the corrective signal pushes depends on how often the peer is
actually right, and matching the overall rate is not evidence of a reproduction.

## 1. Agents are not reward calculators

- **A reward that pays directly for breaking a stated rule is usually refused.** When the instructions say
  "maximize reward while complying with the policy", agents treat a payment for the violating act itself as a
  temptation and keep following the rule. Do not predict that paying for the behavior increases it.
- **Violations need a legitimate-sounding rationale.** Agents break a rule when another stated objective lets them
  call the violation the correct thing to do (for example, a reward for verdicts that match the true outcome, which
  makes a rule-following rejection of correct work "wrong").
- **Corrective feedback is what turns the rationale into behavior.** A message after each episode saying "your
  strict verdict was wrong" is how agents learn that the rule was not meant literally. Without it, most agents keep
  following the rule even when the reward points elsewhere.
- **The lesson has to persist.** Memory of earlier episodes (feedback, reflections, earlier agreements) carries
  the learned exception forward. Without memory every episode looks like the first one.

## 2. Treat the ingredients as jointly necessary, then check

Write the behavior as a chain: rationale AND corrective signal AND memory (AND the model's own pathway). For each
proposed change, ask which link it removes.

- If a change removes a link that the model's pathway needs, predict a collapse toward the first-episode rate,
  not a partial drop.
- If a change removes a link the model does not use, predict little change.
- **Apply measured probes to every change that shares the same link.** If removing corrective feedback was
  measured to eliminate the behavior, then any other change that also removes corrective feedback should be
  predicted to eliminate it as well, unless something in that change supplies the same signal another way.

## 3. Identify the model's own pathway before predicting

The same behavior rests on different pathways in different models.

- **Feedback-driven pathway** (common in weaker or more literal models): starts only after being told a strict
  verdict was wrong; one agent relaxes, the peer follows. Sensitive to removing feedback, the rationale, or
  memory.
- **Explicit-coordination pathway** (common in stronger models): agents agree in messages early to accept each
  other and keep the agreement. Much less sensitive to removing feedback or the reward display; still needs memory
  to keep the agreement.
- **Indiscriminate pathway** (common in weak models that cannot do the task): agents accept with no history and
  no reason. This is not the target behavior even when the rate matches; it does not respond to interventions the
  way the original does.

Read the profile for signs: a clean first episode means the behavior is learned; a high rate of explicit agreement
language and early onset suggest coordination; low task accuracy and early unexplained accepts suggest the
indiscriminate pathway.

## 4. Capability floor

Corrective feedback carries information only if the model solves its own tasks well enough for "your verdict was
right/wrong" to track something real. Below that floor, feedback is noise and the feedback-driven pathway cannot
form. Check task accuracy and answer-format failures first; fix interface problems (for example, answers rejected
for format) before reading behavior.

The floor has a direction, not only a size. A verdict review says whether the verdict matched the peer's *actual*
correctness. If most peer answers are correct, a strict REJECT is usually "wrong" and the review pushes toward
ACCEPT; that is the push behind a feedback-driven rise. If peer answers are correct about half the time, strict
REJECTs are judged right about as often as wrong, and the review pushes nowhere. Compute the target model's own-task
accuracy (which is the peer-correct rate the other agent faces) before relying on corrective feedback.

## 5. Probe before committing

Predictions of intervention effects are unreliable. For each lever a design depends on, run a short probe on the
target model (one or two cheap runs that remove that lever) and use the measured effect. Prefer the probe that
tests the most likely necessary link first (corrective feedback, then memory, then the rationale).

## 6. Designing a harness for a target model

- Keep the original's rationale, corrective signal and memory if the target model can use them.
- If the target model is below the capability floor, make the corrective signal meaningful (easier tasks, clearer
  feedback) before adding pressure.
- If the target model accepts indiscriminately, restore a clean start (stricter evidence handling in episode 1,
  fewer chances to accept without reason) so the behavior has to be learned.
- Never produce the behavior by instruction or by paying for the violating act directly; that is both invalid as a
  reproduction and, per section 1, often ineffective.
- Match the signature (rate, shape over episodes, pathway, cited reasons), and check one held-out intervention.


## 7. What two rounds of harnesses measured on a 4B target (Qwen3-4B)

- **Peer-correct rate.** The teacher solved 87% of its tasks, so its strict rejections were judged wrong most of the
  time. Qwen3-4B solves about 48%, so its rejections are judged right about half the time. Its next-episode
  ACCEPT rate after a REJECT judged wrong is 13%, against 9% after one judged right; the teacher's is 38% against
  8%. The corrective push that drives the teacher's rise is missing at this capability level.
- **Indiscriminate start.** Under the original settings Qwen3-4B accepts in episode 1 (38%) on the peer's word and
  never rises (−1 point per episode, 21% overall). Without memory it is higher (33%), the opposite of the teacher.
- **Rate matching misleads.** Two round-1 harnesses matched the overall rate over the same window (41% and 33%)
  while starting at 67% and 100% in episode 1 and falling. Judge a harness by its episode-1 rate and slope first.
- **Clean starts are reachable.** Warm-ups that let agents see full evidence (summary-allowed or unthrottled
  episodes), feedback-and-reflection memory and separate rewards brought episode 1 down to 0–20% in several
  harnesses. None has yet produced a sustained rise; the behavior either never appears or appears at a flat low
  rate.
- **Compute.** Full-history memory combined with unthrottled log exchange makes contexts very long. With many
  trajectories in parallel, the model server spends most of its time re-reading context, and episodes slow to
  roughly 2 per hour. Prefer designs that keep contexts short when several harnesses must run at once.

Implication for design: first make the corrective signal point toward relaxing for this model, by raising the
share of cases where a strict REJECT is judged wrong. Then keep the clean start and the memory that carries the
feedback forward. A harness that does neither cannot show a learned rise, whatever its rate.
