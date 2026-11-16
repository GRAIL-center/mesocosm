# ADR 0060: Verify prepared native images before reuse

Status: accepted

## Context

Each fresh native runtime previously rebuilt the task base and OpenCode harness.
The process-local image dictionary did not reuse a completed image across runs.
Recreating npm and filesystem-copy build snapshots after cache cleanup repeatedly
crossed the physical disk floor during a real multi-benchmark pilot.

## Decision

Keep the complete task environment identity, including read-only mounted task files.
Use its full SHA-256, the harness/startup recipe, OpenCode version and platform to
identify a prepared image. New builds record these values and the immutable original
base image ID in `org.mesocosm.native.*` labels. A new runtime inspects the candidate
and original base, verifies the labels and root-filesystem lineage, and reuses the
inspected immutable final image ID. A mismatched labeled image fails explicitly;
an unlabeled tag is not automatically trusted.

An operator may explicitly import an existing image using both
`MESOCOSM_PREPARED_IMAGE_RECEIPT` and `MESOCOSM_PREPARED_IMAGE_SHA256`. The receipt
uses `schema_version: 1`, `kind: mesocosm.native-image-import.v1`, and an `entries`
array. Each entry binds `environment_sha256`, exact `labels`, `agent_image_id`,
`base_image_id`, `agent_fingerprint`, `base_fingerprint`, and a relative `proof`
path/SHA-256. Fingerprints include immutable ID, Config, RootFS and platform fields;
mutable local tags are excluded. The receipt and its operator proof must be frozen
inputs of the execution plan. The runtime records both hashes in native evidence.

The operator is responsible for the import's build provenance. A benchmark-specific
producer may certify identical explicit build inputs for several distinct tasks and
list each complete task fingerprint separately. This is not a general Dockerfile
parser and does not change or omit any task-file identity. Untrusted task content
cannot authorize its own import through these host-side settings.

## Consequences and verification

The cache preserves the original base Config, native mounts, verifier separation,
resource checks and containment. Missing original images, source/recipe drift,
changed configuration or layer fingerprints, and broken lineage fail verification.
First preparation of a previously unseen task still needs build headroom unless an
explicit import proves reuse. This change does not pin historical transitive npm/apt
inputs or promise reproducible rebuilding; it pins the completed image bytes.

Focused cache and existing native-runtime tests pass, including rejection of altered
source/recipe/version/platform labels, image IDs, layer ancestry, imported Config and
receipt/proof hashes. Actual cached native execution remains unverified until a new
generation completes with its reuse receipt and ordinary containment/evaluator checks.
