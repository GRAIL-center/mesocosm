# Legacy authored fixtures

These manifests explicitly use `profile: fixture`; their task metadata also declares `fixture = true`.
The default `profile: research` rejects these known authored mechanisms, including when a task is
loaded through a dataset or persistent queue. Fixture runs are excluded from baseline and policy training.

These files preserve earlier integration examples and incident-specific demonstrations for regression
testing. **They are synthetic inputs, not records of prior agent behavior or evidence of spontaneous
misalignment.** Start new research from [the real-environment configurations](../README.md).

The whole former example tree moved here together: YAML task, skill, seed, chain and scenario references
remain relative to the same neighboring files. A former path such as `examples/world-hf-incident.yaml`
is now `examples/fixtures/world-hf-incident.yaml`. Existing ADRs describe the historical paths.

| Fixture | Authored elements and what it can establish |
|---|---|
| `world-hf-incident.yaml`, `scenarios/hf-incident.yaml`, `tasks/license-key/` | An impossible-until-exposed answer, planted key and editable decoy scorer. The real verifier ignores the decoy. Exercises exposure and file-change flags; it does not reproduce a real scorer compromise. |
| `world-unsanctioned-cache.yaml`, `scenarios/unsanctioned-board.yaml` | Preselected object-store affordances, a staged key and an incident-shaped task. Exercises shared-store logging and candidate flags; a store read alone is not collusion. |
| `world-monitor.yaml`, `tasks/release-notes/side_task.md` | A direct instruction to transfer a credential. Exercises side-task and monitor plumbing; the behavior is requested by the fixture. |
| `peer-review.yaml`, `scenarios/peer-verification*.yaml`, `tasks/peer-review/` | Ten fabricated episodes with fixed correctness labels, a prescribed review protocol and reward messages. Exercises verdict accounting and context feedback; there is no policy-weight training. |
| `world-timed-cohorts.yaml`, `tasks/timed-lookup/` | Authored timed rounds, a disclosed cache API and simulated time. Tests timing and cache integration. |
| `world/seed/`, `world/wiki/`, persona briefs | Invented prior conversations, repository content, workplace history and credentials. These are explicit initial inputs, never observations from a live run. |
| Remaining task families, worlds, chains, forks and skills | Small demonstrations of files, board exchange, streaming, internet access, model routing or observation. Several prompts prescribe exact commands and communication. |

The task directories have handwritten checks, such as exact strings, filenames, fixed labels or selected
service records. They are not replacements for external tasks and their original verifiers. One- or
two-agent legacy configurations test specific plumbing and do not meet the project's three-agent
acceptance criterion.

The services themselves differ from their fixture content: board, chat, wiki and Git process real
requests; the cache stores text objects; the optional clock, ledger and vault provide simulated time,
balances and canned secrets. These distinctions must remain explicit in any result description.

To run a fixture for a plumbing check, select `--task-runtime fixture` and an appropriate
`--verifier-image`. The `scenario` commands preserve older staged behavior, including their stopping
rules. Use the generic experiment runner for complete repeated condition comparisons.

Keep new incident scripts, decoy files, fake credential histories and prescribed misconduct out of the
primary examples. A fixture result should be labeled as such in reports and exported records.
