# Engineering 2.4.1-beta.34 acceptance

This contract covers HAMCP-165: governed label create, edit and entity
assignment, shipped **inactive**, and the HAMCP-069B profile read-vocabulary
fix. It adds one tool, `create_label_plan`, and one runtime-only Core
capability profile, `core.label_registry_operation`, that the signed registry
(sequence 12) does not reference. The release activates nothing new; it
switches on with the sequence-13 signing (seq-13 batch = HAMCP-069B +
HAMCP-165 + HAMCP-155). It follows 2.4.1-beta.33 (HAMCP-132 slice 2).

See also:
- Release notes:
  [`V2_4_1_BETA34_RELEASE_NOTES.md`](V2_4_1_BETA34_RELEASE_NOTES.md).
- Design and owner decisions (Josh, 2026-10-23):
  [`HAMCP_165_LABELS_DESIGN.md`](HAMCP_165_LABELS_DESIGN.md).
- The HAMCP-222 signed coverage audit (OD-5, the 069B fix):
  [`HAMCP_222_SIGNED_COVERAGE_AUDIT.md`](HAMCP_222_SIGNED_COVERAGE_AUDIT.md).

**Release target.** `2.4.1-beta.34` is staged by one owner-authorized run of
`scripts/promote_next_release.py --apply` on a base that carries the published
2.4.1-beta.33 (HAMCP-132 slice 2, PR #294). The run consumes the declaration.
This PR first staged beta.33; #294 merged first, so this PR merged the new
`main` with a merge commit and restaged as beta.34.

The contract separates four stages:
1. source and offline acceptance;
2. release CI;
3. publication;
4. installed Engineering acceptance.

It authorizes none of the later stages.

## 1. Source and offline acceptance

Bind the following:
- Josh's HAMCP-165 objective and the 2026-10-23 decisions;
- `2.4.1-beta.34` materialized in all three version authorities, with exact
  acceptance and release-note resolution from `scripts/codex-context.py` and
  no staged declaration left over;
- the PR head SHA;
- the passing PR `validate` run for that exact head;
- the Evidence tier log named in the PR.

The review needs a security section covering:
- **New write paths, unreachable at release.**
  - `config/label_registry/create` with exactly `name`, `color`, `icon` and
    `description`.
  - `config/label_registry/update` with `label_id` and only the changed
    fields.

  Both are reachable only through an approved plan dispatched by the F3
  runtime, and only for a profile admitted by a signed registry. With the
  shipped sequence 12, the routing layer refuses before planning with
  `CoreCapabilityUnavailable` / `pending_signed_registry_admission`. The
  governance service checks admission first, before validation reads or plan
  storage, for every entry point.
- **Closed vocabulary.**
  - The label gateway sends only `config/label_registry/list`, `create` and
    `update`. `config/label_registry/delete` is never constructed and is
    refused at the gateway before any I/O.
  - Entity assignment uses the signed 069A `config/entity_registry/update`
    path unchanged.
- **Approval.**
  - Label children are `standard_admin`, physical effect none.
  - Assignment children keep their 069A classification; the plan takes the
    highest child class. No approval-core change.
  - A tampered plan shape or label proposal is `PROHIBITED`.
  - The planning caller cannot approve.
- **Verification.**
  - Exact label-registry readback decides every label child; a differing
    entry or a different generated `label_id` is never verified.
  - Stale state, a newly existing create target and any namespace change are
    refused immediately before the write.
- **Fallback.** There is no delegated provider and no direct fallback.
- **069B fix.** Only the two unsigned 069B profiles change (two named reads).
  No signed profile or signed registry entry changes.
- **CI.** `ci.yml` adds the dormant seq-13 lane definitions only (§1.2).

Preserve the following:
- stable v1.1.2;
- persisted formats;
- every existing descriptor and input schema. The catalog is **95 tools,
  96 with the optional YAML reader admitted**: 67 static plus 28 delegated;
- probe fingerprints, every signed Core capability profile (the 069A
  entity-metadata profile fingerprint is unchanged), owner-signed Core
  registry entries (sequence 12) and the signed ha-mcp registry;
- the HOLD and the support window;
- add-on permissions and dependencies.

### 1.1 Fingerprints

| Profile | Fingerprint |
| --- | --- |
| `core.label_registry_operation` (new) | `sha256:b2f0dd11ff243aa2559c27738d962785e37455d37b836cb5a6997aeabb26813c` |
| `core.entity_registry_availability_operation` (corrected) | `sha256:ae292fbc16ad82c8d8347b7e2eb1c3a7e8c7644d95783f5bf2ae9f9cc63a76b2` |
| `core.entity_assistant_exposure_operation` (corrected) | `sha256:474c8ccaaae83c12f56a2848995ff089097936568a2ad5dc8d25ce855d51dbbd` |

These are the values the sequence-13 signing must reference.

### 1.2 Evidence lanes (defined, dormant)

`scripts/seq13_contract_acceptance.py` defines the `label_registry` (165) and
`availability_exposure` (069B) lanes. `ci.yml` sets
`REAL_HA_SEQ13_LANES: dormant` on the `real-ha-contract-tests` job and adds
two steps (stage the lane's evidence paths, upload the bounded evidence)
that run only when that value is `enabled`. No existing step, condition or
artifact changes. The seq-13 batch evidence run flips that one value.

The lanes are verified offline only, against synthetic receipts
(`tests/test_seq13_contract_acceptance.py`). They have not run against a
real Core.

### 1.3 Offline proof

No test reaches a live system. Every fixture is synthetic.

- `tests/test_hamcp165_label_registry.py` (62 tests). It uses the real
  service, the F3 runtime and the label gateway over a synthetic label and
  entity registry (`tests/hamcp165_fixtures.py`) and covers:
  - the dormant profile, its pinned fingerprint and the unchanged signed
    siblings;
  - refused while unadmitted at every entry point (route, compiled Core,
    service, tool, internal builder, generic configuration plan), with zero
    Home Assistant reads and nothing stored, including admission withdrawn
    during planning, at the persist boundary and across restart;
  - a TEST signed registry fixture admitting the profile; a tampered
    reference keeps it withheld;
  - end to end: create with every field, edit (only changed fields sent),
    create then assign, edit and create then assign, audit records;
  - every validation refusal before any read; duplicate name, rename onto
    another name, in-plan duplicates, slug collision, unknown label, no-op
    edit;
  - stale state, namespace change and a newly existing create target at
    dispatch; readback mismatch and a different generated id never
    verified; error after send indeterminate; truthful partial results;
  - rollback refused; no self-approval; policy tampering; the never-sent
    boundary.
- `tests/test_seq13_contract_acceptance.py` (21 tests): both lanes' source
  binding, receipts (every window slot, every refusal), fixture cleanup by
  independent absence readback, dormancy unless exactly `enabled`, and the
  pre-client lane guards. `tests/test_alarmo_interval_observer.py` pins the
  dormant `ci.yml` gate and the unchanged existing steps.
- `tests/test_hamcp069b_entity_availability_exposure_admission.py`: each
  gateway read command is bound to a named profile check, and both corrected
  fingerprints are pinned.
- Count and catalog pins in the existing suites are updated from 66 to 67
  static tools (beta.33 added `get_configuration_definitions`).
- `tests/test_hamcp165_entry_admission.py` (7 tests): an unadmitted first
  apply of a label (create, edit, create then assign) or 069B (disable,
  expose) plan refuses before any persistence on every path (withdrawn
  profile, F3 without Core, a Core that cannot report status, no F3
  runtime): the plan, task, child, manifest and lock stores stay
  byte-identical and Home Assistant receives no request; the refused plan
  applies once admission returns.
- Mutations: `docs/evidence/hamcp165-mutations/` (`run_mutations.py`,
  `results.json`). The baseline (98 tests) passes and all 30 mutants are
  killed by the label, review1 and entry-admission suites:
  - the service-layer gate removed, not checked first, or not rechecked at
    the persist boundary; the internal builder accepting no persist lease;
  - readback skipped; a different generated `label_id` accepted;
  - a duplicate name accepted on create or on rename; a case-sensitive name
    compare; an in-plan duplicate create accepted;
  - stale state ignored at dispatch (targeted), and a constant fingerprint;
    a namespace change ignored; the dispatch preflight evidence skipped;
  - the gateway send allowlist dropped; the 8-operation cap raised to 9; the
    policy execution contract always complete;
  - review1: the dispatch admission removed, no Core or a status failure
    treated as admitted, the dispatch-boundary and recovery rechecks removed,
    a held verification delegated, the builder admission removed, the final
    read's target comparison removed;
  - entry admission: the F3 entry check moved after persistence or removed,
    and the no-F3 service refusal moved after task resolution or removed.

## 2. Release CI and publication

Require exact-head CI before release acceptance. Pending or unavailable jobs
are not passing evidence.

Publication needs Josh's Ready authorization and the protected merge handoff.
Verify the following with the shipped verifiers:
- the merged source;
- v2.4.1-beta.34;
- the immutable version and commit images;
- provenance and SBOM.

Never invent future image identities. Retain the beta.33 artifacts and the
known-good earlier artifacts for recovery.

No stored format changes. Rollback is reinstalling the beta.33 artifact.

## 3. Installed Engineering

All installed checks in this section are read-only. None of them writes,
reloads, restarts or calls a service.

1. **Installed-image binding.** Separately authorize installation of the
   published Engineering artifact. Bind the running add-on to:
   - the published image index digest;
   - the v2.4.1-beta.34 tag;
   - the protected `main` merge commit that contains this PR.
2. **Catalog.** `tools/list` shows 95 tools (96 with the YAML reader
   admitted), including `create_label_plan`.
3. **Inactive.**
   - `list_capabilities` and server health show `label_registry_entry`,
     `entity_registry_availability` and `entity_assistant_exposure` as
     `pending_signed_registry_admission`.
   - One `create_label_plan` call is refused with
     `CoreCapabilityUnavailable`, `plan_created: false` and
     `provider_dispatch_occurred: false`. `list_change_plans` shows no new
     plan.
4. **Unchanged 069A.** `create_entity_metadata_plan` remains admitted
   (signed sequence 12), with no change in behavior.

## 4. Live canary: to do after signed sequence 13

This canary **cannot run on beta.34**. It waits for the separate, reviewed,
owner-signed Core registry sequence 13 that references
`core.label_registry_operation` (and, for step 3, keeps the signed 069A
profile). Josh approves each plan separately:

1. Create the test label `HAMCP Canary` (no entity). Verify the
   `config/label_registry/list` readback shows exactly the planned
   `label_id` (`hamcp_canary`), name, colour, icon and description.
2. Edit its colour and description in a new plan. Verify the readback shows
   only those two fields changed.
3. Assign it to one harmless entity (no physical effect, not protected) in a
   new plan. Verify the entity registry readback shows the label added and
   no other change.

The label is removed later by HAMCP-155 or manually by Josh. Running a
canary is a live change, and this document does not authorize it.
