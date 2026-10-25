# External task sources

Reviewed on **2026-10-24** against upstream repositories, task configurations, dataset cards and
licenses. This is a compatibility review; no new benchmark images were installed or executed.

## Recommended sequence

Keep the pinned Terminal-Bench 2 task as a runtime regression check. For the next substantive software
engineering evaluation, select a small **SWE-bench Verified** subset and preserve its original grading
protocol. For experiments where agents change the same running application, investigate a single-app
**WebArena-Verified** subset. These serve different research needs.

A collection of single-agent tasks does not become a shared world by running three copies. Independent
copies can compare conditions or policies. A shared-world study additionally needs explicit identities,
a common mutable environment, access controls, reset boundaries and outcome attribution. Preserve each
original benchmark score and report multi-agent adaptations separately.

## Comparison

| Source | What it provides | Fit and work needed |
|---|---|---|
| **[Terminal-Bench 2](https://github.com/harbor-framework/terminal-bench-2)** | Executable terminal tasks with original environments, reference solutions and verifiers in Harbor format | Lowest migration cost for the existing runtime. Keep a pinned, audited subset for integration checks; compatibility with one task does not establish support for the collection. |
| **[SWE-bench Verified](https://huggingface.co/datasets/SWE-bench/SWE-bench_Verified)** | 500 human-reviewed issues from real repositories, base commits, patches and executable tests | Preferred next software-repair evaluation. The [Harbor adapter](https://github.com/harbor-framework/adapters/tree/main/src/swebench) is useful, but its current schema and original grading semantics need explicit support and calibration. |
| **[SWE-bench Pro V2](https://github.com/scaleapi/SWE-bench_Pro-os/tree/main/v2)** | 642 validated tasks across 11 repositories, published Harbor directories and AMD64 images | Strong subsequent candidate for longer development tasks. More integration work: environment/verifier metadata, network policy and pristine-image patch regrading. Do not confuse it with the older 731-task V1 release. |
| **[SWE-Gym](https://github.com/SWE-Gym/SWE-Gym)** | Real Python issue tasks and executable environments, with a Lite subset | Training candidate once evaluation and rollout collection are stable. Start with a calibrated Lite subset and check overlap with held-out evaluation. Adapter issues prevent assuming every exported task is valid. |
| **[WebArena-Verified](https://github.com/ServiceNow/webarena-verified)** | 812 reviewed browser tasks over self-hosted applications, browser/network traces and deterministic evaluators | Preferred shared-application investigation. Requires browser control, environment lifecycle and evaluator integration; neither Harbor import nor shared three-agent execution is already available in Mesocosm. |
| **[TheAgentCompany](https://github.com/TheAgentCompany/TheAgentCompany)** | Actual GitLab, Plane, ownCloud and RocketChat services with company tasks and coworker interactions | Later integration reference. The company content is constructed and coworkers include LLM NPCs; replacing them with research agents changes the benchmark protocol. Its setup is also substantial. |
| **[AppWorld](https://github.com/StonyBrookNLP/appworld)** | Local API implementations of nine applications, synthetic people/activity data, databases and state-based evaluators | Useful for API-agent tooling, but a weaker first match for this project's focus on actual application services. Its simulated APIs should not be described as access to the named production services. |

For larger-scale training, [SWE-rebench V2](https://huggingface.co/datasets/nebius/SWE-rebench-V2) is a
later candidate: 32,079 instances across 20 languages with environment and quality metadata. The
[execution framework](https://github.com/SWE-rebench/SWE-rebench-V2) and generated installation/test
parsers need inspection on a small fixed subset before any collection-wide commitment.

## Immediate compatibility gaps

Mesocosm supports an explicit subset of Harbor task configuration. Importing a directory is not
proof that its environment, network or verifier protocol will execute faithfully.

- The current [Verified adapter template](https://github.com/harbor-framework/adapters/blob/main/src/swebench/src/swebench_adapter/task-template/task.toml)
  declares `verifier.network_mode = "public"`. The current snapshot verifier rejects that field.
- An inspected [Pro V2 task configuration](https://github.com/scaleapi/SWE-bench_Pro-os/blob/main/v2/tasks/instance_NodeBB__NodeBB-00c70ce7b0541cfc94afe567921d7668cdc8f4ac-vnan/task.toml)
  declares `environment.os`, `environment.network_mode`, `verifier.network_mode` and `verifier.collect`.
  These are outside the current native runtime's accepted fields.
- [Pro V2's protocol](https://github.com/scaleapi/SWE-bench_Pro-os/blob/main/v2/README.md) restricts agent
  internet while allowing model access and regrades the submitted patch in a pristine environment.
  Grading a snapshot of an agent-modified filesystem does not implement that protocol.

Implement supported policy mappings and original artifact/grading behavior explicitly. Do not delete
unsupported metadata or rewrite a task's tests to make validation pass. Retain unsupported cases as
such until their requirements can be enforced.

The Verified adapter lists [known task exclusions and environment issues](https://github.com/harbor-framework/adapters/tree/main/src/swebench#known-issues-and-excluded-tasks).
The SWE-Gym adapter also has a reported [verifier-output failure](https://github.com/harbor-framework/adapters/issues/12)
and [incomplete task accounting](https://github.com/harbor-framework/adapters/issues/25). These are
reasons to inspect and calibrate each selected task, not evidence that every task is broken.

## Small initial candidate set

These are intake candidates, not validated Mesocosm tasks:

| Candidate | Purpose | Required check |
|---|---|---|
| Existing pinned Terminal-Bench 2 `fix-git` | Retain an unchanged integration regression | Its two-file content comparison does not establish correct Git history. Preserve oracle hashes and inspect Git state separately; see [native acceptance](native-acceptance.md). |
| Terminal-Bench 2 `build-cython-ext` | Extend the current native path with a real package repair/build: `pyknotid` 0.5.3 against NumPy 2.3.0 | Needs a C compiler and GitHub/PyPI access; the verifier clones original tests and explicitly excludes some. [Pinned task](https://github.com/harbor-framework/terminal-bench-2/tree/2fd12b88aafdd04a52c298e3940bcb189f9766d6/build-cython-ext). |
| Verified `django__django-13741` | A real Django repair task explicitly used in the Harbor adapter's example | Map verifier policy, pin the original environment and confirm reference/untouched outcomes. [Original change](https://github.com/django/django/pull/13741). |
| SWE-Gym Lite `python__mypy-15413` | A small real type-checker repair for later training-path checks | Confirm installed tests, parser output and train/evaluation separation. [Original change](https://github.com/python/mypy/pull/15413). |
| Pro V2 `instance_NodeBB__NodeBB-00c70ce7b0541cfc94afe567921d7668cdc8f4ac-vnan` | Subsequent multi-module repair involving cache/slug handling | Implement the original fresh-image patch regrade first. [Original instructions](https://github.com/scaleapi/SWE-bench_Pro-os/blob/main/v2/tasks/instance_NodeBB__NodeBB-00c70ce7b0541cfc94afe567921d7668cdc8f4ac-vnan/instruction.md). |

Fixing an application's shared-cache implementation does not itself mean the research agents share
live application state. Likewise, a short coding issue need not require cooperation. Those properties
must be measured in the actual configured environment.

At the pinned Terminal-Bench revision, `build-cython-ext` uses environment fields within Mesocosm's
current accepted subset, requests no GPU and has no Compose sidecars. Its 10 GB storage declaration
still needs the explicit unsupported-resource adaptation, and its verifier needs internet access.
This is a lower-migration candidate while the SWE adapters are developed, not a passed execution test.
Image identity and original-reference calibration remain to be checked.

Other inspected Terminal-Bench tasks are weaker first choices: `cancel-async-tasks` is a small authored
function exercise; `log-summary-date-ranges` generates synthetic logs; `sqlite-db-truncate` has a loose
row-matching check; `fix-code-vulnerability` deliberately removes validation from its upstream code;
and `git-multibranch` checks live SSH/Nginx processes that the current filesystem snapshot grader does
not preserve. These observations refer to the same [pinned task collection](https://github.com/harbor-framework/terminal-bench-2/tree/2fd12b88aafdd04a52c298e3940bcb189f9766d6).

## Shared application direction

WebArena-Verified is the strongest candidate among the reviewed sources for a bounded shared-service
pilot. Its applications execute real reads and writes against persistent state, although the benchmark
uses prepared data and modified environments. [BrowserGym](https://github.com/ServiceNow/BrowserGym)
provides browser-agent integration; its parallel experiments do not establish simultaneous actors in
one shared world.

Start by reviewing a few single-site Magento administrator tasks. The official [environment inventory](https://github.com/ServiceNow/webarena-verified/blob/main/docs/environments/index.md)
lists the optimized administrator image at 2.9 GB, versus 31.6 GB for GitLab. GitLab is a natural later
choice for code/repository permissions, but its environment is much larger. These are published image
sizes, not complete host disk requirements.

Two boundaries matter before choosing any task:

1. **Environment changes:** the [Magento image](https://github.com/ServiceNow/webarena-verified/blob/main/docs/environments/shopping_admin.md)
   includes automatic login and disables product/review bulk deletion. Those restrictions affect which
   permissions and destructive actions an experiment can study.
2. **What a score proves:** [network-event evaluation](https://github.com/ServiceNow/webarena-verified/blob/main/docs/evaluation/network_event_based_evaluation.md)
   checks recorded requests and responses. It does not fully establish final persistent database state.
   For example, a successful product-disable request can be followed by another actor re-enabling it.
   Preserve the original result and independently record final state, actor attribution and later writes.

A faithful shared-world adapter needs separate agent accounts/sessions, one world reset before all
participants start, concurrent access, bounded quiescence before evaluation and records linked to the
acting agent. Reusing a task across multiple actors changes the evaluation setting; report that
adaptation rather than presenting it as an unchanged leaderboard run. Task instructions must not
prescribe cooperation, collusion or rule-breaking.

TheAgentCompany's [setup](https://github.com/TheAgentCompany/TheAgentCompany/blob/main/docs/SETUP.md)
requires substantial service storage, and its [evaluation](https://github.com/TheAgentCompany/TheAgentCompany/blob/main/docs/EVALUATION.md)
includes environment LLM/NPC dependencies. It is a useful later source, but adopting its authored
coworker interactions would introduce behavior assumptions beyond the present system baseline.
AppWorld's [parallelization guide](https://github.com/StonyBrookNLP/appworld/blob/main/guides/parallelizing_worlds.md)
requires separate worlds/output paths for parallel runs; that is not shared-state concurrency.

## License and provenance inventory

Keep source references and hashes instead of copying entire benchmark collections into this repository.
A benchmark harness license does not replace the licenses of task repositories, tests, images or assets.

| Source | Upstream license observations |
|---|---|
| Terminal-Bench 2 | [Repository Apache-2.0](https://github.com/harbor-framework/terminal-bench-2/blob/main/LICENSE); record third-party task/image provenance separately. |
| SWE-bench Verified | [Harness MIT](https://github.com/SWE-bench/SWE-bench/blob/main/LICENSE). The reviewed [data card](https://huggingface.co/datasets/SWE-bench/SWE-bench_Verified) did not declare an independent SPDX data license; retain the source repository licenses for code, patches and tests. |
| SWE-bench Pro V2 | Tooling MIT; [task statements, patches and tests retain their upstream repository licenses](https://huggingface.co/datasets/ScaleAI/SWE-bench_Pro#license). Do not adopt a conflicting license claim from a third-party adapter. |
| SWE-Gym | [Code Apache-2.0](https://github.com/SWE-Gym/SWE-Gym/blob/main/LICENSE); [dataset card MIT](https://huggingface.co/datasets/SWE-Gym/SWE-Gym). Original repositories have their own licenses. |
| SWE-rebench V2 | [Execution code MIT](https://github.com/SWE-rebench/SWE-rebench-V2); [data CC-BY-4.0](https://huggingface.co/datasets/nebius/SWE-rebench-V2#license), with upstream license metadata per instance. |
| WebArena-Verified / BrowserGym | [WebArena-Verified Apache-2.0](https://github.com/ServiceNow/webarena-verified/blob/main/LICENSE) and [BrowserGym Apache-2.0](https://github.com/ServiceNow/BrowserGym/blob/main/LICENSE); hosted applications and container contents require their own inventory. |
| TheAgentCompany | [Repository MIT](https://github.com/TheAgentCompany/TheAgentCompany/blob/main/LICENSE); service applications and bundled content are separate components. |
| AppWorld | [Apache-2.0 with additional protected-content redistribution terms](https://github.com/StonyBrookNLP/appworld#lock_with_ink_pen-license); app/API/task bundles cannot be treated as ordinary unrestricted plaintext redistributions. |

## Acceptance before model spending

For each chosen task, retain its source revision, task files/modes, image digest, network policy,
resource requirements and original verifier. On Linux CPU infrastructure, run the original reference
solution or patch and the untouched baseline in fresh environments. Inspect test collection, logs and
actual test outcomes; a zero score from a broken installation is not a measured task failure.

Only after this calibration should the fixed three-agent model run begin. Keep task scores, interaction
records, conditions, monitor judgments and missing evidence separate. Failure and noncompletion remain
observations; do not change prompts or deadlines after seeing the outcome to manufacture success.

Still unverified: image installation on a Mesocosm worker, real three-agent compatibility, shared-state
reset/concurrency semantics, and complete per-image third-party license inventories. The review did not
inspect every task in any collection and does not certify any collection as fully supported.
