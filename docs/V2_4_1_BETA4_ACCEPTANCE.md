# Engineering 2.4.1-beta.4 acceptance

This contract covers governed entity-registry metadata administration
(HAMCP-069A-1) on top of beta.3. It distinguishes source/disposable acceptance,
release CI, publication, installed Engineering acceptance and Core admission of
the new profile. This document authorizes none of the latter operations.

## 1. Source, review and CI

Bind the design approval (`53954b9`, 2026-10-11), the base `main` and every
later main merge into the branch, and the final reviewed commit and tree.
Record each implementation deviation (V1-V11) and its owner disposition from
the design's §15. Earlier review does not cover changed code.

Materialize 2.4.1-beta.4 in all three version authorities through the existing
staging tool. Require exact acceptance/release-note resolution and no remaining
staged declaration. Complete Full/Evidence on clean source and the bounded
independent review, including a security section covering the new write path,
the closed dispatch payload, provider admission and rollback. Require
exact-head CI before release acceptance. Pending or unavailable jobs are not
passing evidence.

Preserve stable v1.1.2, historical persisted fixtures, locks/holds/uncertainty,
production journals, the probe fingerprints, the 21 existing Core capability
contracts, every signed registry entry, dependency locks, build inputs,
workflow permissions and the support window. The public catalog is 84 tools;
every beta.3 descriptor is unchanged and only `create_entity_metadata_plan` is
added. Lifecycle, backup and operation-status remain withheld; no generic
forwarding, upstream write or fallback is added.

Offline source evidence must prove, through the shipped service, F3 runtime and
registry gateway:

- useful authorized success for every supported field, with exactly one closed
  update carrying only the changed fields and exact readback;
- refusal of every unsupported field and undeclared argument before any Home
  Assistant read, and of unknown references, missing entities, integration
  hidden conflicts, no-op requests and unavailable consumer evidence;
- standard and elevated policy, including the required acknowledgement, and
  prohibition of mixed or incomplete plans;
- stale-state refusal before dispatch, distinct authorization outcomes at
  planning, preflight, dispatch and readback, and uncertain-result preservation
  without redispatch;
- the already-present outcome and its replay property (terminal plan, no
  dispatch after the state changes back, no re-approval, no rollback);
- rollback success, refusal after a later edit and refusal of a not-dispatched
  forward plan;
- fail-closed `CoreCapabilityUnavailable` with zero transport while the profile
  is unadmitted, admission only through an exact owner-signed reference with
  the planner's lease bound to its exact entity target (design §15 V11), and
  that upstream `ha_set_entity` is never invoked.

## 2. Publication and recovery preparation

Publication needs Josh's applicable Ready authorization and the protected merge
handoff. Verify the merged source, v2.4.1-beta.4, immutable version/commit
images, provenance and SBOM with the shipped verifiers. Never invent future
image identities. Local commits or disposable PASS do not establish
publication. Retain the known-good beta.3 artifacts and options for recovery.
How beta.3 handles retained beta.4 entity metadata records is established by
the F12 load-path result in the design's §15. Beta.3 loads every record
without quarantine and refuses every action on 069A plans without dispatch,
but a non-terminal entity metadata task faults beta.3's F3 readiness. Downgrade
to beta.3 only after every entity metadata execution task is terminal; if one
is not, return to beta.4 so that it reconciles the task by readback. Never
clear journals or locks to force recovery.

## 3. Installed Engineering

1. Separately authorize installation of the published Engineering artifact and
   bind it to the published index and protected source.
2. Compare all 84 public descriptors, Core signed selection and
   compatible/withheld profiles, storage/audit health, F3 readiness and zero
   fallback. `core.entity_registry_metadata_operation` must be reported
   withheld, and `create_entity_metadata_plan` must fail closed without any
   Home Assistant request.
3. Retain beta.3 health, diagnostic and upstream acceptance obligations
   unchanged.

## 4. Core admission of the entity metadata profile

Admission is a separate owner decision through an owner-signed Core registry
change, with the container evidence run from `main` described in the design's
§9. Signing selects reviewed compiled behavior; it cannot create a contract.
After authorized admission, any first live entity metadata change needs a
separately approved target. Synthetic success is not household acceptance.

No push, CI trigger, signing, publication, installation, Core admission or
household operation is authorized by source completion.
