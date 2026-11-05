# HAMCP-280: Core 2026.11.0 binding review

This is preparer evidence for a draft binding PR, pending architect final check and
Josh's merge. It supports carrying all 31 seq-15 profiles forward for the intended
seq-16 publication. It is not a candidate, signed admission, CI attestation, or
approval to upgrade a live system. The manual publication flow applies; the
HAMCP-309 automated detector is outside this change.

## Exact source and image

The [provenance fixture](../../../tests/fixtures/core_2026_11_0_lane_provenance.json)
binds stable tag `2026.11.0` to commit
`74cf5a49c63ed8d4d87717844bf14f79c28c9cea`, tree
`530241f5976abdfbea2fa1fa51e0ddc7a4d42560`, and source archive SHA-256
`85f3c42c9f77aa45e01faff4471007beadf5cb50c4324f496a239139eec70ff1`.

- OCI index: `sha256:c6c1ca9931036bf3a2c4cc088d8db25dfea3b33935a5ecc83c40349462e2f69e`.
- linux/amd64 manifest: `sha256:078222f9ea47cf47b172a301c2c45a1d2344b9d666827729b71fc3f7d7a57592`.
- linux/amd64 config: `sha256:c0f062fa32399e6634896e396726287ed238589a7726ed247ac60ad4e76fdb2f`.
- linux/arm64 manifest: `sha256:d486ced003d3b45b4e55fc4684fed76dccd2ce583b3618a92efe842abb26c3dc` (provenance only; not executed).

The predecessor source is Core 2026.10.3 commit
`1f214b8461dd1fbbd66e10765c6905f49d3c1d86`. Strict full-file O3 comparison of
all 337 contract-read files found 161 modified files, removed
`homeassistant/helpers/condition.py`, and 175 byte-identical files.
[Source comparison](source-comparison.json) records every hash;
[source judgments](source-judgments.json) assess all 162 differences using the
HAMCP-251 Part A §7 / Part B1 analysis and pinned source.
[Moved-source receipts](moved-source-receipts.json) account for 32 supplemental
implementation files, including the condition implementation move.
`ci/core-contract-read-files.json` is unchanged; no narrowing is proposed.

## HTTP storage expectation and upgrade consequence

The existing assertion required HTTP store major version 2 for all post-August
bindings. Core 2026.11.0 actually migrates it to major 3. The binding now supplies
`http_storage_version=3` only for 2026.11.0; prior 2026.8.0–2026.10.x expectations
remain 2. All other HTTP assertions are unchanged. Tests cover both correct
versions, rejection of the opposite versions, unchanged field assertions, and
refusal of an unreviewed release.

Pinned source establishes version 2/minor 2 at
[2026.10.3 config.py:74](https://github.com/home-assistant/core/blob/1f214b8461dd1fbbd66e10765c6905f49d3c1d86/homeassistant/components/http/config.py#L74)
and version 3/minor 1 at
[2026.11.0 config.py:75](https://github.com/home-assistant/core/blob/74cf5a49c63ed8d4d87717844bf14f79c28c9cea/homeassistant/components/http/config.py#L75).
The migration versions the SSL profiles in stable and pending while preserving
the stable/pending/YAML migration/port/error semantics; see
[config.py:724](https://github.com/home-assistant/core/blob/74cf5a49c63ed8d4d87717844bf14f79c28c9cea/homeassistant/components/http/config.py#L724).
Older Core cannot read this migrated v3 HTTP store: its Store defaults the maximum
readable major to its own version and rejects higher stored versions; see
[2026.10.3 storage.py:277](https://github.com/home-assistant/core/blob/1f214b8461dd1fbbd66e10765c6905f49d3c1d86/homeassistant/helpers/storage.py#L277)
and [storage.py:437](https://github.com/home-assistant/core/blob/1f214b8461dd1fbbd66e10765c6905f49d3c1d86/homeassistant/helpers/storage.py#L437).
A binary downgrade alone is therefore not a store rollback. No live upgrade or
store migration is performed by this PR.

## Executed evidence

One fresh attempt-6 ran on scriptbox after a host-idle check found another
validation job and Josh explicitly directed concurrent execution, from
**2026-11-05T05:56:37Z to 2026-11-05T06:04:02Z**, including cleanup.
Each verdict below comes from its own results JSON, not an outer exit code.
**WITHHELD: none. All 31 profiles have passing group evidence.**

| Group | Profiles | Result |
|---|---:|---|
| general | 19 | PASS |
| integration_inspection | 1 | PASS |
| automation_baseline | 1 | PASS |
| entity_metadata | 1 | PASS |
| availability_exposure | 2 | PASS |
| label_registry | 1 | PASS |
| configuration_delete | 1 | PASS |
| helper_options | 1 | PASS |
| input_select_option | 1 | PASS |
| script_traces | 1 | PASS |
| dashboard_metadata | 1 | PASS |
| scene_configuration | 1 | PASS |

[Lane summary](lane-summary.json) binds the groups to the local run context and
exact tested source hashes. [Receipt hashes](receipt-hashes.json) cover the
original bounded result, cleanup, source/image binding, context, and image-chain
receipts in [receipts/](receipts/). Cleanup returned `clean:true`; owned containers,
network, config and synthetic token were removed. All 9,921 prior evidence files
remained unchanged.

The image proof **PASSED**. Observed Docker identities equal the pinned index,
with linux/amd64 platform and identical before/after container IDs. The local
export supplies the hash-exact manifest/config and unique platform descriptor.
Only the original index is absent locally: its link is established by the
unchanged committed verifier's pinned-hash-verified index bytes, retained exactly
as base64 in [the identity receipt](receipts/image-identity.json). No image-store
cause is asserted. This is the architect-authorized Checkpoint-12 conditional
proof, not acceptance of an index identity alone.

The lane run tested clean committed checkout
`4161ea3b23a1299a59870ef1fa17c62cb6a3ddd3`, Engineering **2.4.2-beta.2**,
after integration of main `7aa4c92db3b8f76cd8a38f011937dd3905d5c04a`.
The [before](receipts/checkout-before.json) and
[after](receipts/checkout-after.json) checkout receipts record `tested_checkout_head` and
`uncommitted_changes:false`; all group receipts bind the same head and context.
No tracked file changed during the run. The later evidence-only commit replaces
these documents without changing the tested implementation.

This run supersedes attempt-5's pre-integration evidence. The old run is dropped
from the current PR documents; its exact bytes remain in Git history and the
untouched local `attempt-5/` directory. All 12 group verdicts match its pass.
The [owner concurrency override](receipts/owner-host-concurrency-override.json)
records the observed busy host and the direction to proceed.
Final-head repository Evidence already passed: 10,204 tests, 25 skipped, four
workers. Per the architect's correction, Evidence is not rerun for this
evidence-document-only replacement. CI on the updated head is reported separately
in the PR and handoff. Architect final check remains pending.

## Local rehearsal differences

All runner helpers remain untracked under `.artifacts/hamcp280-lanes/`; they are
not product, workflow, or registry changes. The four authorized substitutions
are unchanged from Checkpoint 12 except fresh attempt paths:

1. Public codeload downloads without tokens, retaining exact SHA-256 pins and
   missing/mismatched-pin refusal.
2. Owned synthetic-file cleanup using the pinned fixture image with networking
   disabled and only the owned config mounted, followed by the unchanged
   `core_evidence_lanes.py` cleanup check.
3. Alarmo fixture staging using that image, no network, only the owned config and
   specific fixture mounted; exact destination, mode 600 and equal byte hashes.
4. The image-identity term uses the authorized chain proof described above;
   all other committed verifier terms and container continuity remain intact.

The copied step 9 invokes a local orchestrator implementing the latest direction:
profile failures record `passed:false`/WITHHELD and continue; setup, binding,
identity and cleanup failures stop. No original contract assertion is removed.
The general-failure continuation path was unit-tested but was not needed here.
Every output uses fresh attempt-6 paths; an exclusive invocation marker prevents
reuse. The static receipt hashes the exact workflow, copied-step diff, original
verifier, helpers and branch sources. Six offline identity tests, three local
orchestration tests, ten Python parses and twelve shell syntax checks passed.
The complete local review/diffs remain with the handoff; the
[static receipt](runner-static-review.json) is included for audit.

## HAMCP-309 observation (no change)

`core_evidence_lanes.py` explicitly records a child group's failure and returns
zero; its specialized loop continues independent groups. This is consistent with
the intended continue-on-WITHHELD design. The candidate assembler validates group
bindings and `passing_subset` includes only results that are exactly `True`:
`passed:false` and missing groups cannot contribute their profiles.
It does **not** reject every reduced candidate: the all-31 guard applies only
when `state.acceptance` is true; otherwise it can accept a nonempty passing
subset. The empty subset is refused. No assembler or candidate command was run.

## Scope and next boundary

No add-on path, runtime schema, provider route, stable-v1 file, workflow permission,
release declaration, version, support window, or contract-read manifest changes
relative to main. This is not a release PR and needs no version bump. The binding
and test changes can be reverted without changing any live store or admission.
The evidence is a preparer assessment, not independent review or publication
approval. No signing key, secret, environment, ruleset or approval was accessed.

Architect final check and Josh's merge are next. Only after that merge may preparation
produce local seq-16 candidate/evidence bytes against the freshly verified seq-15
journal. Architect written approval of their exact SHA-256 values must precede
any permanent candidate-branch push. Josh alone runs signing dry-run and real
signing dispatch. No candidate, registry-candidates push, signing dispatch,
deployment, live Home Assistant access, or Core upgrade occurred here.
