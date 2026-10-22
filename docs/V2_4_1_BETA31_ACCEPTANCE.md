# Engineering 2.4.1-beta.31 acceptance

This contract covers HAMCP-069B: governed entity enable/disable and voice
assistant exposure, shipped **inactive**. It adds one tool,
`create_entity_availability_exposure_plan`, and two runtime-only Core
capability profiles that the signed registry (sequence 12) does not
reference. The release activates nothing new. It follows 2.4.1-beta.30
(HAMCP-131).

See also:
- Release notes:
  [`V2_4_1_BETA31_RELEASE_NOTES.md`](V2_4_1_BETA31_RELEASE_NOTES.md).
- Design and owner decisions OD-1..OD-12, OD-2c (i) and the OD-7 amendment
  (at most 8 entities per plan):
  [`HAMCP_069B_ENABLE_EXPOSE_DESIGN.md`](HAMCP_069B_ENABLE_EXPOSE_DESIGN.md).

**Release target.** `2.4.1-beta.31` is staged by one owner-authorized run of
`scripts/promote_next_release.py --apply` on a base that carries
2.4.1-beta.30. The run consumes the declaration. HAMCP-132 slice 2 (PR #294)
also needs a new beta after beta.30. If another release takes beta.31 first,
this PR merges that base with a merge commit and takes the next free beta.

The contract separates four stages:
1. source and offline acceptance;
2. release CI;
3. publication;
4. installed Engineering acceptance.

It authorizes none of the later stages.

## 1. Source and offline acceptance

Bind the following:
- Josh's HAMCP-069B objective and decisions;
- `2.4.1-beta.31` materialized in all three version authorities, with exact
  acceptance and release-note resolution from `scripts/codex-context.py` and
  no staged declaration left over;
- the PR head SHA;
- the passing PR `validate` run for that exact head;
- the Evidence tier log named in the PR.

The review needs a security section covering:
- **New write paths, unreachable at release.**
  - `config/entity_registry/update` with exactly `entity_id` and
    `disabled_by`.
  - `homeassistant/expose_entity` with one assistant (`conversation` or
    `cloud.google_assistant`), one entity and `should_expose`.

  Both are reachable only through an approved plan dispatched by the F3
  runtime, and only for a profile admitted by a signed registry. With the
  shipped sequence 12, the routing layer refuses before planning with
  `CoreCapabilityUnavailable` / `pending_signed_registry_admission`. The
  governance service refuses again before any Home Assistant read or plan
  storage, for every entry point.
- **Closed vocabulary.**
  - The gateway sends only the two writes and four reads:
    `config/entity_registry/get`, `config/entity_registry/list`,
    `config_entries/get_single` and
    `homeassistant/expose_new_entities/get`.
  - `cloud/status`, `homeassistant/expose_entity/list`,
    `homeassistant/expose_new_entities/set` and both cloud entity-update
    commands are never sent. A static check pins where `cloud/status`
    appears.
  - Alexa is refused (OD-3).
- **Approval.**
  - Protected entities need the existing `ELEVATED_ADMIN` acknowledgement,
    with a plain-language consequence statement (OD-9). No approval-core
    change.
  - A tampered policy marker is `PROHIBITED`.
- **Verification.**
  - Exact registry readback decides every child; the empty
    `expose_entity` result is never proof.
  - The only tolerated difference is a non-target `cloud.alexa` options key
    going from absent to present. It is reported with
    `tolerated_new_exposure_key_cloud_alexa`.
- **Fallback.** There is no delegated provider, no direct fallback and no
  free-text input.

Preserve the following:
- stable v1.1.2;
- persisted formats;
- every existing descriptor and input schema. The catalog is **93 tools,
  94 with the optional YAML reader admitted**: 65 static plus 28 delegated.
- probe fingerprints, the 17 signed Core capability profiles (the 069A
  entity-metadata profile fingerprint is unchanged), owner-signed Core
  registry entries (sequence 12) and the signed ha-mcp registry;
- the HOLD and the support window;
- workflows, add-on permissions and dependencies.

### Offline proof

No test reaches a live system. Every fixture is synthetic.

- `tests/test_hamcp069b_entity_availability_exposure_execution.py`
  (51 tests). It uses the real service, the F3 runtime and the gateways
  over a synthetic registry (`tests/hamcp069b_fixtures.py`) and covers:
  - end to end for each operation: plan, approve, dispatch, registry
    readback;
  - elevated approval for protected cases;
  - every planning refusal, including the 8-entity cap and one config entry
    per enable plan;
  - stale state, readback mismatch and an empty `expose_entity` result;
  - the tolerated Alexa key;
  - partial plans;
  - never-sent commands, statically and at the gateway `_send`;
  - policy tampering;
  - rollback.
- `tests/test_hamcp069b_entity_availability_exposure_admission.py`
  (20 tests) covers:
  - both profiles runtime-only and referenced by no signed envelope;
  - the 069A fingerprint unchanged;
  - refused while unadmitted, through the compiled Core gateway, with exact
    pending details and zero app calls;
  - capabilities and health showing `pending_signed_registry_admission`;
  - a TEST signed registry fixture that references them, where each
    reference admits only its own type and a tampered fingerprint keeps the
    profile withheld;
  - real prepared-target binding;
  - the closed tool schema.
- Count and catalog pins in the existing suites are updated from 64 to 65
  static tools.
- Mutations: `docs/evidence/hamcp069b-mutations/`. All 15 mutants are
  killed, and the 71-test baseline passes. `results.json` records each
  mutant and the tests that killed it. The mutants:
  - skip readback;
  - tolerate any new exposure key;
  - drop the protected floor, safety-sensor protection or
    feeds-protected-automation;
  - the planner never elevates;
  - ignore stale state (a constant fingerprint, killed only at plan creation;
    the targeted mutant skips the preflight stale check);
  - the wrong assistant in the command;
  - planner or policy allowing several config entries;
  - accept an unregistered entity;
  - allow Alexa;
  - a cap of 9;
  - drop the `_send` allow-list.

## 2. Release CI and publication

Require exact-head CI before release acceptance. Pending or unavailable jobs
are not passing evidence.

Publication needs Josh's Ready authorization and the protected merge handoff.
Verify the following with the shipped verifiers:
- the merged source;
- v2.4.1-beta.31;
- the immutable version and commit images;
- provenance and SBOM.

Never invent future image identities. Retain the beta.30 artifacts and the
known-good earlier artifacts for recovery.

No stored format changes. Rollback is reinstalling the beta.30 artifact.

## 3. Installed Engineering

All installed checks in this section are read-only. None of them writes,
reloads, restarts or calls a service.

1. **Installed-image binding.** Separately authorize installation of the
   published Engineering artifact. Bind the running add-on to:
   - the published image index digest;
   - the v2.4.1-beta.31 tag;
   - the protected `main` merge commit that contains this PR.
2. **Catalog.** `tools/list` shows 93 tools (94 with the YAML reader
   admitted), including `create_entity_availability_exposure_plan`.
3. **Inactive.**
   - `list_capabilities` and server health show
     `entity_registry_availability` and `entity_assistant_exposure` as
     `pending_signed_registry_admission`.
   - One `create_entity_availability_exposure_plan` call is refused with
     `CoreCapabilityUnavailable`, `plan_created: false` and
     `provider_dispatch_occurred: false`. `list_change_plans` shows no new
     plan.
4. **Unchanged 069A.** `create_entity_metadata_plan` remains admitted
   (signed sequence 12), with no change in behavior.

## 4. Live canaries: to do after signed sequence 13

These canaries **cannot run on beta.31**. Each one waits for a separate,
reviewed, owner-signed Core registry sequence 13 that references both
profiles. Josh approves each canary separately, on one harmless entity:
- expose, then unexpose, one harmless entity to Assist (`conversation`),
  each verified by registry readback;
- disable, then re-enable, one harmless entity whose config entry's reload is
  harmless, each verified by registry readback. The enable discloses the
  coalesced config-entry reload.

Running a canary is a live change, and this document does not authorize it.
