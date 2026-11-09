# Iterative environment design study (running; results pending)

Can a frontier coding agent, testing its own designs on real runs, redesign a multi-agent environment so that a
small model's team reproduces a larger model's behavior signature? Full protocol: `PROTOCOL.md` (frozen before any
designer session; amendment recorded in it). Summary: behavior AgentCollabBench RTD; teacher Qwen3-8B team in the
original environment; students Qwen3-4B and Gemma-4-E4B-it; designers Claude Opus 5.5 and GPT-6 Astra; one spec over
four levers (topology, turns, salience, distractors); 20 real tests per session on 18 loop tasks with a numeric
prediction before each; primary outcome is the finalized design's alignment on 17 held-out tasks.

Tooling (snapshot taken while sessions run):

- `acbdesign` the designer's only test tool; `daemon.py` the operator-side service that validates, budgets, runs and
  scores each test and keeps the ledger; `design_eval.py` applies a spec to the official task JSON and runs the
  official harness and scorer; `build_ws.py` builds a designer workspace and its fixed prompt; `run_codex.sh` launches
  a Codex designer; `pick_short.py` / `pick_heldout.py` draw the task sets; `make_reference.py` builds the reference
  signatures; `track.py` and `audit.py` follow each session and audit its tool use.
- `freeze.sha256` is the first freeze, taken before the 02:50 UTC amendment that moved the study to short task sets
  (see `PROTOCOL.md`). `PROTOCOL.md`, `daemon.py` and `build_ws.py` changed in that amendment, so they do not match
  it; `acbdesign` and `design_eval.py` do. Pod host and port are also redacted here (`<POD_HOST>`, `<POD_PORT>`) in
  `daemon.py` and `make_reference.py`. The held-out task list is withheld until the sessions finish.

Results, per-test trajectories and the held-out evaluation will be added when the sessions finish.
