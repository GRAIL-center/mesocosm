# ADR-0031: Linear probes over recomputed activations

Status: accepted · Date: 2026-10-18

## Context

Activations are recomputed from recorded token ids and stored per request (ADR-0016): per-layer norms
for every request, raw residual-stream tensors where the policy says. Nothing read them. The proposal's
detector comparison needs a first activation-based detector to set next to the behavioral ones, and the
cheapest honest one is a linear probe: a direction at one layer, trained on labelled requests, scored on
new ones. It has to be reproducible: a probe is meaningless away from the model, revision, dtype, layer
and pooling it was trained on.

## Decision

- **A probe is logistic regression on pooled raw tensors.** `mesocosm activations probe train
  <bundles> --layer N --out probe.safetensors` takes every captured request with a raw tensor at that
  layer, pools it to one vector (`mean` over positions, or the `last` position), standardizes, and fits a
  regularized logistic regression with numpy. Labels come from the judge's flags by default (a flagged
  agent's requests and requests named in evidence are positives) or from a `{request_id: 0 | 1}` file
  the researcher wrote. Training refuses a single class and bundles captured differently from one another.
- **The probe file carries its provenance.** The safetensors metadata names the model, revision, dtype,
  positions, layer, pooling, hidden size, counts, training accuracy and loss, label source, bundles and
  time. `probe score <bundle> --probe` refuses a bundle whose captures differ in model, revision, dtype
  or positions, and writes one line per request (score, logit, agent, time) to
  `activations/probes/<name>.jsonl`, reporting the mean per agent and the top requests.
- **`observe.capture.probes` scores at capture time.** After `activations capture`, the probes the
  manifest lists are scored over what was just captured and reported with the capture.
- **numpy and safetensors join the dev extra** so the probe tests run without a GPU; torch stays in
  `observe`.

## Consequences

- A probe's score is a number about a request, not a flag: no detector turns it into one yet, and the
  review queue does not see it. Turning scores into `judge.flag` events with a threshold is a later
  decision, after a probe has been trained on real captures.
- Labels from flags make the probe learn "requests of agents the judge flagged", which is agent-level,
  not request-level, supervision; the labels file is the way to request-level labels.
- Verified on synthetic tensors in tests; the first real probe waits for a captured run with flags.
