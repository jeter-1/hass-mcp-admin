# Engineering 2.4.1-beta.33 acceptance

This contract covers HAMCP-155: governed deletion of UI-managed automations
and scripts, shipped **inactive**. It adds one tool,
`create_configuration_delete_plan`, and one runtime-only Core capability
profile, `core.configuration_delete_operation`. The signed registry
(sequence 12) does not reference that profile. The release activates nothing
new. It follows 2.4.1-beta.32 (HAMCP-220).

See also:
- Release notes:
  [`V2_4_1_BETA33_RELEASE_NOTES.md`](V2_4_1_BETA33_RELEASE_NOTES.md).
- Design, Core contract and Josh's 2026-10-23 decisions:
  [`HAMCP_155_GOVERNED_DELETION_DESIGN.md`](HAMCP_155_GOVERNED_DELETION_DESIGN.md).

**Release target.** `2.4.1-beta.33` is staged by one owner-authorized run of
`scripts/promote_next_release.py --apply` on a base that carries
2.4.1-beta.32. The run consumes the declaration. PRs #294 (HAMCP-132 slice 2)
and #303 (HAMCP-165) also stage beta.33. The tooling accepts only the strict
next beta, so if another release takes beta.33 first, this PR merges that
base with a merge commit and restages to the next free beta (beta.34 or
beta.35), renaming this document and the release notes.

The contract separates four stages:
1. source and offline acceptance;
2. release CI;
3. publication;
4. installed Engineering acceptance.

It authorizes none of the later stages.

## 1. Source and offline acceptance

Bind the following:
- Josh's HAMCP-155 objective and decisions;
- `2.4.1-beta.33` (or the restaged beta) materialized in all three version
  authorities, with exact acceptance and release-note resolution from
  `scripts/codex-context.py` and no staged declaration left over;
- the PR head SHA;
- the passing PR `validate` run for that exact head;
- the Evidence tier log named in the PR.

The review needs a security section covering:
- **New write path, unreachable at release.**
  - `DELETE /api/config/automation/config/<id>` and
    `DELETE /api/config/script/config/<object_id>`, with no body.
  - It is reachable only through an approved plan dispatched by the F3
    runtime under `delete_automation_configuration` or
    `delete_script_configuration`, and only for a profile admitted by a
    signed registry. Only the new gateway method sends DELETE. The generic
    configuration write still raises for any action other than create or
    update.
  - With the shipped sequence 12, the routing layer refuses before planning
    with `CoreCapabilityUnavailable` / `pending_signed_registry_admission`.
    The governance service refuses first, before any Home Assistant read or
    plan storage, for the tool and the generic and internal builders.
  - Dispatch re-checks admission on every F3 path: first apply, resume,
    recovery, retained or approved plans, and F3 composed without Core.
    Each refuses with zero writes. Preflight refuses before its live-state
    read, and observation refuses to verify without admission.
- **Closed vocabulary.** Reads are the config GET, the entity registry list
  and the entity state GET. `create_configuration_plan` still refuses
  `delete`; legacy `delete_automation` stays prohibited; upstream
  `ha_config_remove_*` stays unrouted.
- **Drift at dispatch.** Immediately before the DELETE the live config is
  compared with the bound baseline fingerprint. Drift, or an already absent
  item, is `stale_target_state` with zero writes.
- **Approval.**
  - The policy follows the removed effects. Protected or unresolved removed
    effects, incomplete dependency evidence, and protected or unresolved
    consumers need the existing `ELEVATED_ADMIN` acknowledgement with a
    plain-language consequence statement. No approval-core change.
  - A delete is never classified as having no effect. An ordinary delete is
    `standard_admin` at medium risk.
  - A tampered policy marker or basis, a protected effect relabelled
    standard, or a hash or pre-image mismatch is `PROHIBITED`.
- **Verification.** Absence is proven by config 404, registry entry absent
  and state absent, polled within a bound. Partial absence is
  `manual_review_required` (`config_removed_runtime_unverified`); a config
  still present is a readback mismatch.
- **Fallback.** There is no delegated provider, no direct fallback and no
  free-text input.

Preserve the following:
- stable v1.1.2;
- persisted formats;
- every existing descriptor and input schema. The catalog is **94 tools,
  95 with the optional YAML reader admitted**: 66 static plus 28 delegated.
- probe fingerprints, the 17 signed Core capability profiles (the governed
  configuration profile `sha256:d517fa73…` and the 069A profile
  `sha256:cc09630e…` are unchanged), owner-signed Core registry entries
  (sequence 12) and the signed ha-mcp registry;
- the HOLD and the support window;
- workflows, add-on permissions and dependencies.

The new profile's contract fingerprint is
`sha256:5688af5d141b97ca33eadf251ecfbb8f3b1f833024b0ee1acea4973f558f887d`.
Signed sequence 13 must reference exactly this value.

### Offline proof

No test reaches a live system. Every fixture is synthetic.

- `tests/test_hamcp155_configuration_delete_execution.py` (21 tests): the
  real service, F3 runtime and gateways over a synthetic config, registry
  and state store (`tests/hamcp155_fixtures.py`). It covers end to end
  delete and recreate rollback, drift at dispatch, stale and already absent
  targets, a 400 rejection, an uncertain send, partial plans, partial
  runtime absence (registry entry only, state only, both), a still-present
  config, and storage of every pre-intent refusal code.
- `tests/test_hamcp155_configuration_delete_admission.py` (31 tests): the
  profile is runtime-only and referenced by no signed envelope, and its
  contract fingerprint (and the unchanged governed configuration
  fingerprint) is pinned; refusal
  while unadmitted with zero reads and nothing stored, through the tool and
  the generic and internal builders; admission re-checked at preflight,
  immediately before the DELETE, at observation, after restart, on resume
  and recovery mid-plan (lease or route withdrawn after the first delete),
  after an uncertain delete, through the legacy routes and without Core; a
  TEST signed
  registry fixture that admits it; the closed tool schema.
- `tests/test_hamcp155_configuration_delete_policy.py` (17 tests):
  removed-effect classification, elevation and acknowledgement, the
  unlock floor, incomplete consumer evidence, tamper refusals, removed rows
  with an empty after side, and planning refusals before any read.
- `tests/test_hamcp155_configuration_delete_lane.py` (9 tests): the dormant
  seq-13 delete lane `scripts/configuration_delete_contract_acceptance.py`.
  Refusals are expected outcomes with exact codes, every claimed read
  (config 404, registry entry gone, state gone) is asserted, the lane is
  unwired, and the shipped lanes exclude the inactive profile.
- Count and catalog pins in the existing suites are updated from 65 to 66
  static tools.
- Mutations: `docs/evidence/hamcp155-mutations/`. 35 of 35 mutants are
  killed over the four suites above (baseline 78 tests OK), covering the
  four HAMCP-165 lessons (admission at every layer, drift at dispatch,
  every claimed read asserted, removed-effect classification including
  "not create/update means no effect"), rollback, adapter expected
  effects, executor refusal storage and the closed gateway.
  `results.json` records each mutant and the tests that killed it.

## 2. Release CI and publication

Require exact-head CI before release acceptance. Pending or unavailable jobs
are not passing evidence.

Publication needs Josh's Ready authorization and the protected merge handoff.
Verify the following with the shipped verifiers:
- the merged source;
- the release tag;
- the immutable version and commit images;
- provenance and SBOM.

Never invent future image identities. Retain the beta.32 artifacts and the
known-good earlier artifacts for recovery.

No stored format changes. Rollback is reinstalling the beta.32 artifact.

## 3. Installed Engineering

All installed checks in this section are read-only. None of them writes,
reloads, restarts or calls a service.

1. **Installed-image binding.** Separately authorize installation of the
   published Engineering artifact. Bind the running add-on to:
   - the published image index digest;
   - the release tag;
   - the protected `main` merge commit that contains this PR.
2. **Catalog.** `tools/list` shows 94 tools (95 with the YAML reader
   admitted), including `create_configuration_delete_plan`.
3. **Inactive.**
   - `list_capabilities` and server health show the configuration delete
     capability as `pending_signed_registry_admission`.
   - One `create_configuration_delete_plan` call is refused with
     `CoreCapabilityUnavailable`, `plan_created: false` and
     `provider_dispatch_occurred: false`. `list_change_plans` shows no new
     plan.
4. **Unchanged create and update.** `create_configuration_plan` remains
   admitted, with no change in behavior, and still refuses `delete`.

## 4. Live canary: to do after signed sequence 13

This canary **cannot run on this release as shipped**. It waits for a
separate, reviewed, owner-signed Core registry sequence 13 that references
`core.configuration_delete_operation` with the fingerprint above. Josh
approves it separately, on one test automation:
1. delete it under an approved plan and read back its absence (config 404,
   registry entry gone, state gone);
2. roll it back with `rollback_change` under a separately approved plan and
   read back that the recreated config matches the pre-image exactly.

Running the canary is a live change, and this document does not authorize
it.
