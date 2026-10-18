# ADR-0026: Every mechanism takes N agents; peer review is a topology over them

Status: accepted · Date: 2026-10-18

## Context

The peer-verification protocol (ADR-0017) was built for the paper's two agents and the manifest
validation said so ("exactly two agents"); its task instances were planted from directories named
after the two agent ids. This is a multi-agent platform: a mechanism that only works for a pair is
a prototype, and the maintainer's direction is that anything that fits two must fit many working
together. Colosseum's finding that collusion depends on who talks to whom (topology) makes the
review graph a research variable, not an implementation detail.

## Decision

- **Peer review runs over any number of agents.** `services.peer_review.topology` says who reviews
  whom: `all` (every agent judges every other; the default) or `ring` (each judges the next agent in
  manifest order). `reviewers()` in the manifest module is the one definition of the graph; the
  episode synchronizer scores every assigned pair against the reviewed agent's actual correctness,
  the shared reward is the per-verdict reward times the number of right verdicts, `mutual_accept`
  means every assigned verdict was ACCEPT, and the runner's feedback tells each agent about each peer
  it judged and each judge that judged it. Verdicts outside the topology are recorded, never scored.
  The chat already accepts any member as a recipient and lists the members on its usage page.
- **Instances come from a pool.** A task provides `instances/<name>/` and the agent's position in the
  manifest selects one, round robin, so two instances serve two, three or ten agents; `agents/<id>/`
  still works when a task really means a specific agent. `task.planted` records which instance an
  agent got.
- **The example has three agents** (`examples/peer-review.yaml`, topology `all`, ten messages per
  episode: five per peer as in the paper's two-agent setting); the task text speaks of peers, one
  verdict per peer.
- **The rule generalizes** (AGENTS.md): a new mechanism is designed for N agents from the start; a
  two-party protocol is expressed as a topology over N, never as a special case.

## Consequences

- ADR-0017's two-agent clause is superseded; its metrics (EC/TC/CC over `mutual_accept`) keep their
  meaning as "every assigned pair accepted".
- The paper's exact setting is `topology: all` with two agents; results on three or more agents are
  a new experiment, not a replication, and the matrix says so.
- The pool wraps around, so with more agents than instances two agents hold the same instance; a
  task that needs distinct instances for every agent provides that many.
