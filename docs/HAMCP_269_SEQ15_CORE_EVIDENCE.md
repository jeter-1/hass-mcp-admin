# HAMCP-269: five-capability disposable Core evidence

This task prepares evidence for additive capability extensions on **2026.10.1
and 2026.10.3**. Josh's 2026-11-01 direction supersedes HAMCP-232/145 OD-13's
2026.10.3-only scope and HAMCP-071's later-batch placement. The five compiled
profiles are present in main after PR #333, merge
`2be1d72bb8f9f38ddd70df95ff888f3e83c24ed2`. This is an evidence-only change,
following PR #324: no version bump or runtime changes.

## Authority and provenance

The unchanged sequence-14 journal SHA256 is
`80d91919ebbd57e4ca1b7a49bc2bb6a9b2996b3b14bef3070916263e82c53817`.
Its signatures and chain were verified using the committed public key in
`docs/evidence/hamcp222/core-public-key.txt`; its known contracts were checked
against compiled main. Both release entries contain the same ordered 26
references; none of these five is present. The harness preserves that exact
prefix, tests each 26-to-27 extension separately, and checks the combined
synthetic authority's health is **compatible 31 / withheld 0**.

| Lane | Added capability | Version-1 fingerprint |
| --- | --- | --- |
| helper_options | core.config_entry_helper_options_operation | sha256:ce2765dc5caf616ae199fdccf64ce6ec68e1386bb0949099d1896bcb50dd98d8 |
| input_select_option | core.typed_input_select_option_operation | sha256:e2240555fb35042e47e7ee7afb7f5552977d019a27058fb2c2c5802674fee38d |
| script_traces | core.script_trace_read | sha256:749904fe40b13db4289301d24b958836fc72c810829611729d8c26080f06ca03 |
| dashboard_metadata | core.dashboard_metadata_operation | sha256:62cd6c1453b0bc0b52c6e5f9775d1897294f31b62792dd9674a61b9e8a54cfaa |
| scene_configuration | core.scene_configuration_operation | sha256:73bac9067f62b62b039ce03f7754d1312f9c0595d0fb16af49851a49c90ba6bb |

Exact tag, commit, tree, archive and OCI index/architecture bindings remain in
`tests/fixtures/core_2026_10_1_lane_provenance.json` and
`tests/fixtures/core_2026_10_3_lane_provenance.json`, matching the existing signed
entries. The existing disposable publication check verifies source-archive
bytes and OCI index/amd64 manifest bytes, then binds the running container to
the image configuration digest. Each new receipt binds that image receipt's
SHA256, release, slot, compiled fingerprint, candidate commit and unchanged
Engineering runtime tree `731c5251121f7503115699e708517bbec66e6fc9`.

These are source-execution receipts under the CI Python interpreter, with a
synthetic provider identity. They are **not installed Engineering-image
receipts**. The runbook's separate Engineering-image/provenance requirement
remains a gap for signing review; do not claim it from this harness. Historical
arm64 provenance is not an arm64 execution result from these amd64 lanes.

## Ten capability/release pairs

All five lanes run in each exact Core current/candidate job, inside the existing
owned disposable Alarmo-slot harness. The 2026.10.0 rollback job and other
contract lanes retain their existing behavior. Every lane includes a
predecessor-authority refusal with zero Engineering HA requests, a live
positive control for existing authority, closed observed commands, prohibited
command checks, and independently read-back cleanup of all owned fixtures.

| Lane on both releases | Positive and deferred Core checks | Negative controls |
| --- | --- | --- |
| helper_options | Filter, statistics, template sensor and template binary sensor edit/readback/approved rollback; actual serialized suggested_value; complete visible options carried | Pinned field, invalid value, stale state, concurrent-flow overwrite disclosure and drifted rollback refusal; no replay or service calls |
| input_select_option | Exact option flip/readback and approved rollback | Already desired, unavailable option, changed options; no set_options or relative moves |
| script_traces | Harness creates/runs a harmless script; exact-script list/get; synthetic secret redaction and output bounds | Unknown run, invalid arguments before I/O; no list-all/debug/context or fallback |
| dashboard_metadata | Each field and all fields, exact trailing-space title, icon:null removal, slug identity, requested-only fields, unchanged cards/views/content fingerprint, approved rollback | No-op/default, stale metadata/content; unknown dashboard_id returns not_found without creation; url_path and mode updates rejected with unchanged row |
| scene_configuration | Name, icon, null icon removal, member snapshot and approved rollback; exact stored readback, metadata/full-entry preservation, stable registry identity, unchanged member state; unrelated no-id entry receives an id without other changes | No-op, missing scene, unknown member, target mismatch, stale state before dispatch; actual independent writer causes stored mismatch after the one POST, with no replay |

Scene results explicitly bind `verification_scope` to
`stored_configuration_and_registry_identity` and `loaded_state` to
`not_verified`. They do not infer the scene Home Assistant has loaded. The raw
fixture harness alone creates/deletes scenes. Engineering sends no activation,
explicit reload, DELETE or other scene service. Core's save still schedules a
global scene reload: the accepted design limitation remains.

A passive template binary-sensor member exercises the unchanged planner's
device-class registry read; its off state and the mode helper's Home state
remain unchanged through every edit. The full-entry checks preserve the
unrequested binary-sensor snapshot as well as metadata.

The seeded no-id entry exists only in the disposable configuration before
startup. The raw unknown-dashboard controls test `url_path` and `mode`.
WebSocket `id` is the transport request identifier, not an editable dashboard
field; it is not used as a malformed transport probe.

## Artifacts and acceptance

For each target release, `seq15-contracts-core-<version>-<slot>-<lane>` retains
five bounded JSON receipts for 14 days, alongside existing image/source and
outer-cleanup artifacts. A failing lane retains a closed stage/source-line
diagnostic, continues independent lanes, and fails the job. Offline receipt
fixtures are synthetic refusal tests, never execution evidence.

Acceptance requires **all pull_request CI jobs green**, including all five
lanes on both releases, at the reviewed head. If retry is necessary, re-run
all jobs. The draft PR body records head, run URL, exact lane results and local
validation; pending or failed results must remain explicit. A runtime defect
stops the affected lane for architect-directed repair; this task cannot fix
Engineering source.

## Security, limits and next boundary

Josh explicitly allowed ephemeral CI-only signing to exercise the real verifier.
The disposable keys are generated in memory and have no production authority.
Production journal, public key, trust policy and all runtime sources remain
unchanged. No production signing, publication, merge, deployment or live HA
access is part of this PR. Workflow permissions, action pins, matrix and other
workflows are unchanged. Reverting this evidence-only PR disables the new CI
coverage; production behavior never changes from it.

After architect approval, Josh's merge and a new instruction, canonical evidence
must follow `core-capability-extension-evidence-v1` in
[the registry runbook](CORE_RELEASE_REGISTRY.md#explicit-capability-extension),
including the exact previous entry and ordered prefix, five appended references,
and review-artifact hashes. The current preparer changes one release per signed
sequence: two successors of sequence 14 cannot both be published as sequence
15. The second release extension must bind the first Josh-signed successor.
No candidate, signing bridge, ordering change or existing-entry replacement is
prepared here. Signing review must resolve the remaining image evidence gap.
