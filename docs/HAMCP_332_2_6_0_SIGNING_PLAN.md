# HAMCP-332: 2.6.0 signing plan (proposal)

Status: **proposal only, NOT FROZEN.** This document signs nothing. It does
not edit the registry, dispatch a workflow, or change `.release/`, any
version, or any runtime file. Signing a Core registry sequence is Josh's
owner action, done through the protected `core-release-registry-signing`
environment.

**Freeze rule.** This plan freezes only after both of these hold:

1. the Codex delta reviews of #389, #390, #392, #393 and #394 have settled;
2. the pending Josh choices in §8 that affect the contents of a 2.6 profile,
   or whether a profile is in the extension, are answered.

Until then the master table (§4) is the current proposal, not a signing
brief. The Codex delta review of this plan follows the five feature deltas.

- Register: HAMCP-332 (2.6.0 design milestone, signing-plan deliverable).
- Design base: `origin/release/2.6.0` at
  `7246f2d21693f039504542574569f03b71476489` (the PR base; Engineering
  `2.4.2-beta.7`, retired stable `1.1.2`).
- Current integration head, read 2026-11-08: `origin/release/2.6.0` at
  `1c4c32b3c2da152bd42f25178b077c62c13af7d4`. Between the base and this head,
  the signing, selection and evidence machinery cited below is unchanged. The
  only change under `ha_core_readmission/` is five lines in `routes.py`
  (HAMCP-229 maps one static tool to two existing profiles; no profile
  changes). Script line numbers below hold at both revisions.
- Canonical journal: `origin/registry` at
  `e486028ed0b06b8a5a737824c829705b403b34d8`, sequence 17.
- Authored 2026-11-07; corrected 2026-11-08 after the Codex design review
  (§9). US Central time.

## 1. Settled direction

**Josh direction (register 2026-11-07; confirmed in-session 2026-11-08):**

- Additive profiles only. No existing signed profile is version-bumped or
  replaced. Existing fingerprints stay byte-identical.
- Scene gets a separate, additive scene profile (HAMCP-329). This applies to
  HAMCP-298 too.
- New 2.6 profiles are signed for **Core 2026.11.0 only**, in **one combined
  2.6 signing**: one `extend-capabilities` extension of the existing
  2026.11.0 entry. No 10.x targets for new profiles. No separate or phased
  2.6 signings.
- HAMCP-134 and HAMCP-231 are delivered in **2.6.0**.
- HAMCP-295 rename **rewrites references with verified undo**. A
  disclose-only rename is not a deliverable.
- HAMCP-297 use cases: power-on behavior after outages, child or physical
  lock, and sensor tuning (sensitivity, timeouts). Not indicator LEDs.
- HAMCP-296 decisions stand as merged in #401.

Consequences, from source (§2.6):

- The extension must keep the exact prior reference list of the 2026.11.0
  entry as an ordered prefix and append the new references after it.
- No replacement operation, no relaxation of the unknown-contract check (C4)
  and no protected-workflow change is needed to sign this. The current
  utility already expresses it.
- The 2026.10.0, 2026.10.1 and 2026.10.3 entries are not changed. New 2.6
  capabilities stay unadmitted there and fail closed.

## 2. Authority, method and current state

### 2.1 Authority and method

- **Josh decides** product scope, profile contents where a design leaves a
  choice open, and every signing action (ADR-022). The target release, the
  batch shape and the additive rule are settled (§1).
- **Current source decides** what the signing tools can express. That means
  `scripts/prepare_core_release_registry.py`,
  `scripts/core_registry_one_tap.py`, `scripts/core_release_evidence.py`,
  `.github/workflows/core-release-registry-publish.yml`, and the runtime under
  `hass_mcp_engineering_beta/ha_mcp_engineering/ha_core_readmission/` (written
  `E/` below).
- **The signed journal decides** what is admitted today. It was read from the
  git object at `origin/registry`, not from a running system.
- **Nothing came from a live system.** No live Home Assistant instance,
  deployed endpoint, credential or signing key was used. The offline verifier
  result in §2.5 is the author's local evidence from 2026-11-07.
- **Unknown values stay unknown.** Where a value cannot be derived, this
  document writes `unknown` and names the missing source.

### 2.2 Journal tip

| Field | Value | Source |
| --- | --- | --- |
| Branch tip | `e486028e`, "Publish owner-approved Core 2026.11.0 registry journal", committed 2026-11-07 00:59:30 CST | `git log origin/registry` |
| File | `upstream-trust/ha-core-release-registry.json`, sha256 `3f79fce76484ff9f9a9c070defe4d1d13865ccbfd24fda0ab8724515c8cc6a5d` (recomputed 2026-11-08) | git object at the tip |
| Key and registry ids | `ha-core-release-registry-v1` / `ha-core-reviewed-releases` | journal fields |
| Envelopes | 17; `checkpoint_sequence` 1; 0 `revocation_sources` | journal fields |
| Sequence 17 validity | `generated_at` `2026-11-07T06:16:34Z` (00:16:34 CST); `expires_at` `2027-02-05T06:16:34Z` (2027-02-05 00:16:34 CST) | sequence 17 envelope |
| Copy on `main` | Frozen at sequence 15 (the cutover). It is not the current tip. | `docs/CORE_RELEASE_REGISTRY.md:354-360` |

Admitted Core releases at sequence 17 (every entry uses probe profile
`core-probes-child-device-name-parts-v1`): 2026.10.0 (22 references),
2026.10.1 (26), 2026.10.3 (31) and 2026.11.0 (32). Revocations (tombstones):
2026.9.2, 2026.9.3 (HAMCP-161) and 2026.9.4 (HAMCP-182). A revoked release can
never be re-added (`prepare_core_release_registry.py:169-170`).

### 2.3 The current 2026.11.0 entry (the extension target)

Read from the sequence 17 envelope on 2026-11-08:

| Field | Value |
| --- | --- |
| `entry_id` | `ha-core-2026.11.0-reviewed-v1` |
| `source_commit` / `source_tree` | `74cf5a49c63ed8d4d87717844bf14f79c28c9cea` / `530241f5976abdfbea2fa1fa51e0ddc7a4d42560` |
| `source_archive_sha256` | `85f3c42c9f77aa45e01faff4471007beadf5cb50c4324f496a239139eec70ff1` |
| `image_index_digest` | `sha256:c6c1ca9931036bf3a2c4cc088d8db25dfea3b33935a5ecc83c40349462e2f69e` |
| `evidence_sha256` | `f194b7ea53dd80c9947b8c3a33a7fc009e8633b2f6175c2e68d7ec063faf93a8` |
| Added / last extended | sequence 16 (31 references) / sequence 17 (+ `core.typed_helper_value_operation`) |
| References | 32, in this order |

The 32 references, by `capability_id`, in signed order:

1. `core.basic_rest_read`
2. `core.basic_websocket_read`
3. `core.state_service_discovery`
4. `core.non_device_registry_read`
5. `core.direct_device_registry_read`
6. `core.delegated_device_effective_area`
7. `core.direct_entity_state_read`
8. `core.automation_configuration_read`
9. `core.automation_trace_read`
10. `core.dashboard_configuration_read`
11. `core.template_semantics`
12. `core.configuration_validation`
13. `core.dependency_helper_planning`
14. `core.typed_helper_operation`
15. `core.f3_mutation_verification`
16. `core.governed_configuration_operation`
17. `core.governance_observability`
18. `core.typed_fan_operation`
19. `core.typed_power_operation`
20. `core.integration_inspection_metadata_read`
21. `core.automation_baseline_metadata_read`
22. `core.entity_registry_metadata_operation`
23. `core.entity_registry_availability_operation`
24. `core.entity_assistant_exposure_operation`
25. `core.label_registry_operation`
26. `core.configuration_delete_operation`
27. `core.config_entry_helper_options_operation`
28. `core.typed_input_select_option_operation`
29. `core.script_trace_read`
30. `core.dashboard_metadata_operation`
31. `core.scene_configuration_operation`
32. `core.typed_helper_value_operation`

This list is today's prefix. It is **not** frozen as the prefix of the 2.6
candidate: the candidate uses the 2026.11.0 entry as it stands when the
candidate is prepared (§5). The `previous_entry_sha256` the extension
evidence binds is computed by the utility's `canonical_json` at preparation;
this plan does not state it.

### 2.4 Base profile inventory

- `CORE_RUNTIME_CAPABILITY_PROFILES` has 32 profiles
  (`E/ha_core_readmission/profiles.py:891-899`).
- 17 of them are probe-bound `CORE_CAPABILITY_PROFILES` (`profiles.py:156`).
- Every new 2.6 profile in §4 is runtime-only. No signed probe fingerprint
  moves, and no existing entry's probe identity changes.

### 2.5 Offline verification result (author, 2026-11-07)

The base runtime verifier ran offline in a scratch virtual environment against
the fetched sequence 17 bytes and the committed public key. Refresh was
accepted at sequence 17 with `positive_authority_current` true. Positive
selections: 2026.10.0: 22; 2026.10.1: 26; 2026.10.3: 31; 2026.11.0: 32.
2026.9.4, 2026.10.2 and 2026.10.4 had no positive selections and no probe.

This is the author's local evidence only. Codex independently parsed and
hashed the same journal but did not rerun the cryptographic verification. It
says nothing about any deployed runtime.

### 2.6 Signing-machinery constraints (source facts)

| # | Constraint | Source |
| --- | --- | --- |
| C1 | **One changed entry per positive sequence.** `add` and `extend-capabilities` refuse when more than one release entry changes ("positive transition must preserve all sibling entries"). The one-tap verifier also refuses a candidate that changes a Core version other than the requested one. | `scripts/prepare_core_release_registry.py:163-166`; `scripts/core_registry_one_tap.py:173-177` |
| C2 | **`extend-capabilities` is append-only, on one existing release.** The old references must remain an unchanged ordered prefix ("extension must append to the exact original references"). It cannot change release or probe identity. It needs a current authenticated predecessor and new review evidence. Several new references may be appended in the same extension; there is no one-new-profile limit. | `prepare_core_release_registry.py:180-197` |
| C3 | **`add` on an existing release is an identical renewal.** "Renewal cannot replace release identity or contracts". | `prepare_core_release_registry.py:174-179`, `:210-223` |
| C4 | **Every entry must match the compiled contracts of the checkout running the utility.** `require_known_contracts` checks every reference of every entry, unchanged siblings included, against `CORE_RUNTIME_CAPABILITY_PROFILES` (exact fields, and `auto_eligible`). Preparation calls it through `validate_transition`; signing revalidates and repeats the all-entry check. | `prepare_core_release_registry.py:88-98`, `:167-168`, `:257`, `:334-336` |
| C5 | **Signing runs only from `main`.** Every job of the one-tap workflow requires `github.ref == 'refs/heads/main'`. With C4, a profile can be signed only after its compiled code is on `main`. | `.github/workflows/core-release-registry-publish.yml:62`, `:95`, `:180` |
| C6 | **`revoke` withdraws one whole release per sequence, permanently.** It may not add or alter positives, and it does not run C4 (`:152-163`, `:325-333`), so an older writer can still withdraw. The revoked release can never be re-added. | `prepare_core_release_registry.py:152-163`, `:169-170`, `:325-333` |
| C7 | **Automated new-release evidence never adds or changes a profile.** `passing_subset` copies the predecessor's exact references for passing groups, and refuses a predecessor profile that is not mapped in `ci/core-evidence-groups.json` ("unmapped or duplicate predecessor profile: binding PR required"). | `scripts/core_release_evidence.py:195-207` |
| C8 | **Selection ignores references the runtime does not compile.** `selections()` walks the runtime's own profiles and admits one only when profile id, version, adapter, fingerprint and `auto_eligible` match its reference. An unknown reference is never matched, so it is ignored. | `E/ha_core_readmission/registry.py:58-100` |
| C9 | **`_contract()` derives the profile id and version.** `profile_id = capability_id.replace(".", "_") + "_v1"` and `"profile_version": 1`. | `E/ha_core_readmission/profiles.py:109-145` (the id rule is at `:119`) |
| C10 | **Extension evidence binds the exact transition.** Closed fields `model`, `version`, `previous_entry_sha256`, `previous_capabilities` (the complete old list), `added_capabilities` (the complete appended list) and `review_artifacts_sha256` (1-32 distinct sha256 values). | `prepare_core_release_registry.py:110-127` |
| C11 | **Bounds and expiry.** An entry holds 1-64 references. The runtime bound is 64 envelopes and 8 denial sources; the utility compacts to 32 positive-chain envelopes. Extension needs an unexpired predecessor; the tip expires 2027-02-05 00:16:34 CST. | `docs/CORE_RELEASE_REGISTRY.md:141`, `:236-238`; `prepare_core_release_registry.py:180-183` |
| C12 | **2026.11.0 lane binding.** A reviewed lane binding and provenance fixture exist for 2026.11.0. It is not a slot in the CI support window (current 2026.10.1, candidate 2026.10.3, rollback 2026.10.0). | `scripts/core_lane_bindings.py:53-63`; `ci/support-window.yaml:57-63`; `tests/fixtures/core_2026_11_0_lane_provenance.json` |

These are source-review conclusions. No candidate was prepared or dry-run for
this plan.

### 2.7 Unknowns

| Item | Status | Missing source |
| --- | --- | --- |
| Deployed Core and Engineering versions | unknown | Live observation is out of scope |
| Final `capability_id`, `profile_id`, adapter id and fingerprint of every new 2.6 profile | unknown | The reviewed implementation code on `main`; `_contract()` computes them (C9) |
| The 2026.11.0 reference list at preparation | unknown | Any signing between now and preparation (for example a separately reviewed 2.5 HAMCP-290 extension) changes it |
| Sequence number of the 2.6 extension | unknown | It is the predecessor's sequence plus one at preparation. No placeholder number is assigned |
| Final reference count of the extended 2026.11.0 entry | unknown | The predecessor at preparation plus the finally approved additions |
| HAMCP-290 contract | unknown | It is separate 2.5 scope and not part of this batch (§6) |

## 3. Profiles that must stay byte-identical

Every existing reference stays exactly as signed. These are the ones the 2.6
designs reuse or considered widening. Fingerprints were recomputed at the base
and match sequence 17.

| Profile (capability id) | Reused or considered by | Status in 2.6 | Fingerprint |
| --- | --- | --- | --- |
| `core.configuration_delete_operation` | #393 (automation and script create rollback and creation-identity capture, under reads it already signs) | Unchanged. Not version-bumped | `sha256:5688af5d141b97ca33eadf251ecfbb8f3b1f833024b0ee1acea4973f558f887d` |
| `core.scene_configuration_operation` | #390 (scene rewrites), #393 | Unchanged; `DELETE` stays never-sent | `sha256:73bac9067f62b62b039ce03f7754d1312f9c0595d0fb16af49851a49c90ba6bb` |
| `core.governed_configuration_operation` | #390 (automation and script rewrites), #393 (creates, helper recreate) | Unchanged | `sha256:d517fa73878f8dafea857b5306db750304d3c65522481898800cf86fb091f441` |
| `core.entity_registry_metadata_operation` | #390, #389 | Unchanged; `new_entity_id` is not added | `sha256:cc09630efbc0e1e6d0a587696406f6502c62fe730a767bc0a8cfc96a9988f767` |
| `core.typed_power_operation` | #389 | Unchanged; not reused for configuration entities | `sha256:9242b795a2d5ed3deeb3b8ea46fba13fc299c14fb274e3b29ed97a30b607001a` |
| `core.typed_input_select_option_operation` | #389 | Unchanged | `sha256:e2240555fb35042e47e7ee7afb7f5552977d019a27058fb2c2c5802674fee38d` |
| `core.typed_helper_value_operation` | #389 | Unchanged | `sha256:d45ea23b2b7c0ade4c3d4b5ae28602a7d10af72db3dd103f20cffdcea8cc228d` |
| `core.basic_websocket_read` | #392, #394 | Unchanged; widening it would silently extend existing tools | `sha256:e1c15f0d838c44f40c2bcf7ae9df408c9cd186cea7874ab7976945be405294cc` |
| `core.automation_baseline_metadata_read` | #392 | Unchanged | `sha256:42ea1b1731aa0d73e01717aabfa99639c29af2755d8d341c39b4789256a3c540` |
| `core.delegated_device_effective_area` | #392 | Unchanged | `sha256:b1c2352fe0e87b59a4f9451113d650ac474a56d9135f715d17d2ca31d25645ad` |
| `core.config_entry_helper_options_operation` | #388 | Unchanged (recheck only) | `sha256:ce2765dc5caf616ae199fdccf64ce6ec68e1386bb0949099d1896bcb50dd98d8` |
| `core.automation_trace_read`, `core.dependency_helper_planning`, `core.automation_configuration_read` | #391 (229, 296) | Unchanged | `sha256:7f45584511fb1069561ba4ef7a43c4cd6e9a5f41cb9b2f2aac5bdadcd6511c9c`; `sha256:4e981b75c8bf81ef24c3fa123827786ca57762db0d2e09108e499a5e8312efe9`; `sha256:ec4344e7dc0a0f4b1a84a2dfbbeae1b412432cf787bcdf41d58ea21a6edb45ad` |
| `core.basic_rest_read` | #391 (294) | Unchanged | `sha256:3c76c2789952f16340ec51c83122e43cceb1de9581b4dfdf15821d9fc8282cc8` |
| `core.dashboard_configuration_read`, `core.dashboard_metadata_operation` | #391 (143) | Unchanged | `sha256:134fa8fa48bdd085a491d2f311ee69c48f53c844a651c334974899e8b79f6424`; `sha256:62cd6c1453b0bc0b52c6e5f9775d1897294f31b62792dd9674a61b9e8a54cfaa` |

Reusing an existing contract for reference rewrites (#390) or for
create-rollback of automations and scripts (#393) does not by itself justify
widening that contract. Any genuinely new provider or permission must be
designed and reviewed explicitly before it can join the extension.

## 4. Master table: the one-extension 2026.11.0 plan

All rows below are candidate references for the **one** `extend-capabilities`
extension of the 2026.11.0 entry. No other entry changes. Each design was read
at the corrected head named in the row (`git fetch origin <branch>` then
`git show <sha>:<path>`).

**Identifier kinds.** `_contract()` takes a dotted `capability_id` (for
example `core.thread_matter_topology_read`) and derives the underscore,
versioned `profile_id` (`core_thread_matter_topology_read_v1`) (C9). The
designs do not all name the same kind. The "Named by the design" column says
which kind each design names. The "Derived `profile_id`" column applies the C9
rule to the proposed capability id. It is a proposal, not a final value.
**Final `profile_id`, adapter id and fingerprint are unknown for every row
until the implementation is built and reviewed.**

| # | Source PR, register, corrected head, document | Named by the design (kind) | Derived `profile_id` (proposal) | Tool(s) | Core commands and contract (summary) | Exact 2026.11.0 lane evidence named by the design | Proving tests named by the design (future evidence; none run) | Persisted state | Final id / fingerprint | Inclusion |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| N1 | #390, HAMCP-295 / HAMCP-109, `786f4a4d5c9af0c29d2bd92688d4b4d55daa16d2`, `docs/HAMCP_295_109_ENTITY_RENAME_DEPENDENT_EXECUTION_DESIGN.md` §5 | Both: capability id `core.entity_registry_rename_operation` and profile id `core_entity_registry_rename_operation_v1` | `core_entity_registry_rename_operation_v1` (the design states the same) | `create_entity_rename_plan`; `rollback_change` for verified rename plans | WS `config/entity_registry/update` with exactly `{entity_id, new_entity_id}`; `config/entity_registry/get` for old and new ids; new-id state absence read. Dispatch `at_most_once_entity_registry_rename`; readback `authoritative_exact_entity_registry_entry_renamed`; recovery `read_only_no_redispatch`. Dependency-bound execution and rewrites add **no** profile; they use the unchanged governed and scene profiles (§3) | **None named at this head.** Required by this plan (§5 G4, G5) | Exact admission on 2026.11.0 from a signed reference; N30 pre-plan refusal and zero dispatch on 2026.10.0/10.1/10.3; N31 metadata fingerprint pinned; N1 `new_entity_id` still refused by the metadata plan; N20 old reader refuses or holds new plans | New: `execution_mode`, `compensation`, compensation child and decision records. §8 of the design requires a two-direction writer-provenance downgrade proof | unknown | Conditional on #390 J3 (2.6.0 or 3.0). If J3 keeps 3.0, or the contract is not frozen in time, N1 is not in the extension and there is no second 2.6 signing |
| N2 | #393, HAMCP-329 / HAMCP-298, `3dd4fdf4749864cc53f474d9188631b3cf83ebc3`, `docs/HAMCP_329_298_SCENE_CREATE_AND_CREATE_ROLLBACK_DESIGN.md` §3.1, §5 | Capability id `core.scene_create_delete_operation`, with proposed profile id `core_scene_create_delete_operation_v1` and adapter `compiled-core-scene-create-delete-operation-v1` | `core_scene_create_delete_operation_v1` | `create_scene_configuration_plan` `action: create`; `rollback_change` (created-object rollback only) | REST `POST /api/config/scene/config/{config_id}` to an absent id; `GET` of that config; `DELETE` of it (rollback only); WS `config/entity_registry/list`; `GET /api/states/{entity_id}`. Create and delete targets, polled readbacks, concurrency disclosure, `never_sent` `scene.*` services | **None named** (acceptance uses a test-signed registry and fake Core). Required by this plan (G4, G5) | Proving tests 1-5 of design §5: existing 32 references identical; old runtime plus extended registry keeps existing admission; new runtime plus sequence-17 registry refuses only the new identities with zero reads; never admitted on 2026.10.x; probe fingerprints unchanged | New: `created_identity` evidence, rollback plan form, delete intent. Design §8 persistence and downgrade gate | unknown | Current proposal, pending Josh (the two-profile split, §8). Alternative: one profile carrying both N2 and N3 |
| N3 | #393 (as N2) | Capability id `core.created_helper_delete_operation`, with proposed profile id `core_created_helper_delete_operation_v1` and adapter `compiled-core-created-helper-delete-operation-v1` | `core_created_helper_delete_operation_v1` | `rollback_change` (created-object rollback only) | WS `input_boolean/list`, `input_number/list`, `input_boolean/delete`, `input_number/delete` (exact object id), `config/entity_registry/list`; REST `GET /api/states/{entity_id}`. Identity correspondence, absence readback (runtime, not disk durability) | **None named.** Required by this plan (G4, G5) | As N2 | As N2 | unknown | As N2. Under the one-profile alternative, N3 merges into N2 |
| N4 | #389, HAMCP-297, `d020118a18af9faf29b8dd5d08a0974a75217b31`, `docs/HAMCP_297_DEVICE_CONFIGURATION_ENTITIES_DESIGN.md` §5.4, §6, §9.2 | Capability id `core.device_configuration_entity_operation` (its §6 column is headed "Profile id" but holds the dotted capability id), adapter `compiled-core-device-configuration-entity-operation-v1` | `core_device_configuration_entity_operation_v1` | `create_device_configuration_plan` | REST `GET /api/states/{entity_id}`; WS `config/entity_registry/get`, `config/device_registry/list`; `call_service` `select.select_option` (`option` only), `switch.turn_on` / `switch.turn_off` (no data), **`number.set_value` (`value` only)**, one exact `entity_id` target. Numeric exactness contract, identity-bound undo, never-sent list. All numeric semantics are in this one profile before first signature | **Yes:** existing 2026.11.0 lane binding (`scripts/core_lane_bindings.py:62`, image index `sha256:c6c1ca99…e2f69e`, the same as the registry entry); new evidence group `device_configuration_entity`; test-only fixture component; a workflow overlay step (a protected path its build task must name). PASS receipt bound to the final fingerprint is one of the extension's `review_artifacts_sha256` | §6 proving check (every existing id and fingerprint identical; `previous_capabilities` equals the current list; this profile added once); lane tests per §9.2 (D4), N22-N26, C1-C7, R1-R8 | New: the verification record (target identity, post-apply observation fingerprint, restorable prior value) and undo plans. **The design names no downgrade evidence.** Required by this plan (§7.3) | unknown | In the extension once its contract is frozen (§8 choices) |
| N5 | #392, HAMCP-134, `e052cee86b218ecdaf3e64ed7c61d90693a3ee9b`, `docs/HAMCP_134_SECURITY_METADATA_FEASIBILITY.md` §7, §8, §9 | Capability id `core.security_metadata_read` ("final id at build"; HAMCP-222 plan name `core.security_posture_metadata_read`). A new `CoreCapabilityClass` value | `core_security_metadata_read_v1`, if that capability id stands | `get_security_metadata` | Projected reads: `auth/current_user`, `config/auth/list`, `http/config` (active-slot projection), `get_config` (URL posture), `homeassistant/expose_new_entities/get`, `homeassistant/expose_entity/list` (stored-true counts, only if choice 3 includes it), `assist_pipeline/pipeline/list`, `conversation/agent/list`, `get_panels`. Large `never_sent` | **Yes, per command:** fixtures from Core 2026.11.0 source and disposable-lane evidence against an exact 2026.11.0 Core. No evidence group named; required by this plan (G5) | §9: exact-version evidence (M1); HTTP active-slot cases (M2); connection-identity cases (M3); ingress cases only if Slice D (M4); exposure counts if included (M5); no-echo in errors, audit and truncation; drift tests; refused before Core I/O on 2026.10.x; existing fingerprints and entries byte-identical | None beyond audit (counts, statuses, reason codes). The build must show no new persisted type is introduced | unknown | In the extension once its contract is frozen (§8 choices). Supervisor `S` reads are outside the Core registry and not in this profile |
| N6 | #394, HAMCP-231, `8917c19a89fb98337f49973d8b54df72b06b564b`, `docs/HAMCP_231_THREAD_MATTER_TOPOLOGY_INVESTIGATION.md` §5.1, §7 | Both: capability id `core.thread_matter_topology_read` and profile id `core_thread_matter_topology_read_v1` (cited to `profiles.py:119`) | `core_thread_matter_topology_read_v1` (the design states the same) | `get_thread_matter_topology` | `thread/list_datasets`; `matter/network_topology` with `refresh: false` fixed; only if J1 approves them, `otbr/info` (projected) and `matter/node_diagnostics` (one registry-validated `device_id`). Projection allowlists, identifier policy, activity classes, caps, typed reasons, `never_sent`, `principal_origin: existing_configured_authenticated_origin_only` | **Partly:** "the existing exact-Core lane and registry process for Core 2026.11.0", plus activity tests against an exact-version Matter Server in a disposable lane (§8 item 6). No evidence group named; required by this plan (G5) | §8 items (refusal, canary privacy, link resolution, network name by type, activity, freshness); §9 acceptance; §11 step 3 candidate preserves every prior reference in order; §11 step 6 rollback proving check | "No persisted state is added" (design). The build must confirm it | unknown | In the extension once its contract is frozen (§8 choices). Direct Matter Server or OTBR access (J4) has no Core profile and cannot be hidden inside this one |

**Count.** The current proposal appends **6** references (N1-N6), or **5**
under the #393 one-profile alternative, and one fewer if #390 J3 keeps
HAMCP-295/109 in 3.0. "32 to 33" in #389 §7 is a base-local delta for that
profile alone, not the combined count. The final count of the extended entry
is the predecessor's count at preparation plus the finally approved additions.
It is unknown.

**Work that needs no new signed profile** (as the designs state):

- #390: the dependency-bound execution mode and the reference rewrites,
  compensation and rollback steps. They run under the unchanged
  `core.governed_configuration_operation` and
  `core.scene_configuration_operation`.
- #393: automation and script create rollback, and their creation-identity
  capture. They run under the unchanged `core.configuration_delete_operation`
  with reads it already signs. The design adds new runtime route identities
  that map to that capability. It asks Codex to confirm this reading; the
  build must prove the fingerprint stays byte-identical.
- #392: the related existing tools (`ha_get_system_health`,
  `ha_get_entity_exposure`, `ha_get_integration`) keep their current profiles.
  Optional Supervisor reads need their own provider review and are outside the
  Core registry.
- #394: the delegated `thread_network` section keeps its current path. A
  direct Matter Server or OTBR provider (J4) would need a new kind of trust
  artifact, not a Core profile.
- #388 (HAMCP-232) and #391 (HAMCP-104, 143, 149, 229, 267, 294, 296): no
  signing requirement.

## 5. The combined extension

### 5.1 Candidate shape

One candidate, prepared from `main` after every included implementation has
merged:

- operation `extend-capabilities`, Core version `2026.11.0`, entry
  `ha-core-2026.11.0-reviewed-v1`;
- `previous_capabilities`: the complete reference list of the 2026.11.0 entry
  in the predecessor current at preparation, in order (today the 32 in §2.3);
- `added_capabilities`: the finally approved new references from §4, each
  exactly once, appended after that prefix;
- `review_artifacts_sha256`: 1-32 distinct sha256 values, including each new
  profile's exact 2026.11.0 lane PASS receipt and the reviewed candidate
  artifacts (C10);
- every other entry, every tombstone and every revocation source unchanged
  (C1).

The sequence number is the predecessor's plus one at preparation. It is not
assigned here.

### 5.2 Gates before the candidate exists

| Gate | Requirement |
| --- | --- |
| G1 | The corrected designs are frozen: Codex delta reviews settled and the §8 choices that affect contents or inclusion answered. A narrower profile is never frozen first on the assumption that a later signing widens it silently |
| G2 | Each included implementation is merged to `main` with its profile compiled and `auto_eligible` (C4, C5). Changes under `E/ha_core_readmission/` are protected-path changes that the build task names |
| G3 | Fingerprints come from the reviewed code that will be on signing `main`. A regression test compares **all then-current retained references** of every entry, not only the historical 32, with the compiled profiles, and shows them byte-identical; probe fingerprints unchanged |
| G4 | Each new contract has passing exact Core 2026.11.0 disposable-lane evidence for its actual command, projection and refusal behavior, with its receipt bound to the final fingerprint and lane image. A binding or provenance fixture, or green docs CI, is not lane evidence. Each implementation contract documents its concrete lane path; that is routine evidence plumbing, not a new product decision |
| G5 | Each new capability is mapped in `ci/core-evidence-groups.json`, with its contract read paths in `ci/core-contract-read-files.json`, before signing. Otherwise the next automated new-release run refuses the predecessor (C7) |
| G6 | The persisted-state gate of §7.3 passes for every included profile |
| G7 | The actual registry predecessor is re-fetched at preparation. Any intervening signing (for example a separately reviewed 2.5 extension) is preserved: its additions become part of the prefix and its tombstones are retained. Predecessor, hash and time drift refusals stay in force |
| G8 | Candidate and evidence bytes are reviewed and hashed; the actual combined transition is dry-run; public signing runs from `main` through the protected environment with Josh's approval. Passing source tests do not substitute for that approval |

### 5.3 Proving tests (required implementation and candidate evidence; none run)

1. Every retained signed reference is identical before and after: same
   `profile_id`, `profile_version`, `adapter_id` and `contract_fingerprint`.
2. Old automation and script deletion and scene editing stay available on
   every release where they are signed today.
3. Each unsigned new operation refuses with
   `pending_signed_registry_admission`, before any Home Assistant I/O and with
   no fallback.
4. The candidate changes exactly one release entry (2026.11.0); all other
   entries are byte-identical; all original references are preserved as the
   ordered prefix.
5. A candidate that replaces, removes or reorders an old reference is
   refused, and the unknown-contract check (C4) is not relaxed.
6. Every finally approved new contract is covered by exact-11.0 evidence
   (G4).
7. The new capabilities remain unadmitted on 2026.10.0, 2026.10.1 and
   2026.10.3, even with the extended journal.

### 5.4 Expiry

The extension needs a current predecessor (C2, C11). If preparation falls
after 2027-02-05 00:16:34 CST, positive authority has already lapsed and
failed closed, and an identical renewal (C3) is needed first. That renewal is
registry maintenance, not a 2.6 profile signing. This plan does not analyse
renewal further.

## 6. Interaction with 2.5.0 and automated releases

- **HAMCP-319 (#385)** adds no Core signing. Its only overlap is the shared
  `main` that C4 and C5 sign from.
- **HAMCP-290** is separately reviewed 2.5 scope. It is **not** pulled into
  this 2.6 batch. If it is signed first, its references are part of the
  predecessor the 2.6 extension preserves (G7). Its contract is unknown here.
- **Automated new Core releases** (for example a 2026.11.x) consume sequence
  numbers and copy only predecessor references whose groups pass (C7). Once a
  2.6 profile is signed on 2026.11.0, the next automated run refuses unless
  `ci/core-evidence-groups.json` maps it (G5).

## 7. Rollback, recovery and persisted state

Selection compatibility, persisted-state compatibility and future signing are
separate properties. C8 establishes only the first.

### 7.1 Runtime or image rollback

Deploying an older Engineering image while the journal carries the new
references:

- **Selection.** The older runtime does not compile the new profiles, so it
  ignores their references and still selects every reference it knows (C8).
  This is source behavior, not yet tested for the specific older image.
- **Persisted state.** C8 says nothing about plans, tasks or durable execution
  records written under the new profiles. A runtime rollback is safe only for
  an older image for which §7.3 has been verified.
- A runtime rollback is a separately authorized release or deployment action.
  It does not change the journal.

### 7.2 Reverting the signing checkout

Removing a signed profile's compiled contract from signing `main` breaks
future signing. C4 checks every reference of every entry, so every later
positive transition (extension, automated add or renewal) would refuse while
the 2026.11.0 entry still carries that reference. `revoke` would still work
(C6).

- The writer must keep every signed contract compiled. A feature revert on
  `main` disables or removes the tool and its routing, but keeps the profile
  contract that the journal references.
- The reference stays signed. Neither cache deletion nor restoring an old
  journal removes it, and both are forbidden as rollback
  (`docs/CORE_RELEASE_REGISTRY.md:287`, `:296-297`).
- There is no operation that removes one reference from an admitted release
  (C2, C3). A "remove one capability" operation would be new authority
  (`docs/CORE_RELEASE_REGISTRY.md:291-293`) and is not proposed.

### 7.3 Persisted-state gate (required before signing)

For each included profile, using the exact shipped image of the preceding
release (derived at build time) and fixtures generated by the exact shipped
writer (root `AGENTS.md`, Persisted Compatibility Fixtures):

| Row | Persisted state | Required evidence | Design coverage |
| --- | --- | --- | --- |
| N1 rename | Dependency-bound plans and tasks, compensation records | Old reader refuses or holds new records and never runs them as `ordered_stop_on_failure`; old records load with identical hashes in the new reader | #390 §8, N20 |
| N2, N3 scene and helper | Creation identity evidence, created-object rollback plans, delete intents | Older runtime rejects new rollback plans and delete intents, never executes them, and still loads records carrying the new evidence keys; old records keep their hashes | #393 §8 |
| N4 device configuration | Verification records and undo plans | Older runtime refuses or holds new plans and records, with no redispatch; restart tests around intent and readback | **Not in #389 at its head.** Required here |
| N5 security metadata | Audit only | Shown that no new persisted record type is introduced | #392 §7.3 (states audit content) |
| N6 Thread/Matter | None stated | Shown that no persisted state is added | #394 §11 step 6 |

**Proving tests (required evidence; none run):**

1. An older runtime with the extended journal keeps every old operation and
   refuses every new one.
2. Historical and new stored records survive or refuse truthfully, without
   redispatch, across restart.
3. The selected recovery leaves the next positive candidate preparable (C4
   passes on the writer).

### 7.4 Whole-release revocation

`revoke` of 2026.11.0 withdraws **every** capability on that release and can
never be undone (C6). It is a separate, permanent owner decision, not a
feature rollback, and is not proposed here.

## 8. Choices that block the freeze — pending Josh

None is answered here. One line each; the designs give the options.

- **#389:** Q5 per-plan bounds (one entity, or up to N of one domain on one
  device).
- **#389:** Q6 whether some settings need the safety-critical acknowledgement
  in the profile.
- **#389:** Q7 final tool and profile names (blocks only if Josh objects to
  the proposed names).
- **#389:** E1 household entities (evidence, not intent); it may adjust the
  numeric contract before freeze.
- **#390:** J3 delivery of HAMCP-295/109 in 2.6.0 or 3.0 (decides whether N1
  is in the extension).
- **#390:** J1 rewrite coverage and acceptable incomplete coverage (gates the
  design's contract freeze, S0, before the rename fingerprint is frozen).
- **#390:** J2 rename policy class and acknowledgement (also gates S0).
- **#392:** choice 1, privacy and detail of the projection.
- **#392:** choice 2, coverage and session-default reporting.
- **#392:** choice 3, optional reads (stored-true exposure counts and the
  satellite mapping change the command list).
- **#393:** two profiles (N2, N3) or one combined scene and helper profile.
- **#393:** Q-A ownership strictness when the registry entry changed after
  creation.
- **#393:** Q6 legacy contract-1 `CREATE_AUTOMATION` plans.
- **#393:** Q7 a created scene stored but never registered.
- **#394:** J1 initial read subset (A1-A4).
- **#394:** J2 active discovery, `refresh` and ping.
- **#394:** J3 LAN-identifier policy across all aliases.

Not blocking this plan's freeze: #389 Q3 (default (a) changes nothing; (b) or
(c) would need its own additive profile reviewed before the combined signing),
#394 J4 (outside the Core registry) and #394 J5 (delegated section reason
codes).

**Signing-specific choices: none new.** The additive architecture, the scene
profile, the 2026.11.0-only target, the single batch and the placement of
HAMCP-134 and HAMCP-231 are settled (§1). Final signing approval is a later,
concrete owner action, not requested here.

## 9. Codex design review 2026-11-08 — disposition

Review of `7246f2d2..2248224f` (verdict: ready after corrections; not a build
or signing approval).

| ID | Severity | Finding (short) | Correction |
| --- | --- | --- | --- |
| 1 | High | The proposed path contradicted the additive-only decision and expanded signing authority (delete and scene v2, Track B, C4 relaxation, withdrawal window) | §1 records the direction; §3 keeps every existing profile byte-identical; §4 gives scene and helper delete their own additive profiles; Track B, the replacement operation, the C4 relaxation, the withdrawal window and the reverse replacement are removed from the actionable plan; the old diagnosis is kept only under "Superseded (historical)". Proving tests in §5.3 |
| 2 | Medium | The master table and questions did not describe the agreed combined release | §1 settled decisions; §4 rewritten as the one-extension 2026.11.0 table at the corrected design heads, with identifier kinds, lane evidence, proving tests, persisted state and unknown final ids and fingerprints; "32 to 33" restated as a base-local delta; 10.x targets, multi-sequence options, 134/231 placement and 295/297 intent questions removed; §2.3 records the current entry; §5 candidate shape and gates; §8 lists only open feature choices |
| 3 | Medium | Runtime rollback called safe without separating selection, persisted state and future signing | §7: runtime or image rollback (§7.1) separated from reverting the signing checkout (§7.2); the writer keeps signed contracts compiled; §7.3 persisted-state gate per profile with proving tests; §7.4 revocation as a separate permanent decision |
| Source conclusions | n/a | No replacement or C4 change needed; one-tap rejects other versions; evidence groups; compare all then-current references; 1-32 artifact hashes; re-fetch the predecessor; no invented sequence number; HAMCP-290 not pulled in; #388 and #391 add nothing; no Supervisor, Matter Server or OTBR authority inside a Core profile; lane path in the implementation contract | §1 consequences; §2.6 C1, C4, C7, C10; §4 notes; §5.2 G3-G8; §2.7 and §5.1 (sequence unknown); §6 |
| Remaining questions | n/a | No new signing-policy choice; only unresolved feature choices hold freeze | §8 |

**Cross-document notes for the delta reviews** (the design PRs are not edited
here):

- #390 §9 says "HAMCP-290 and the other new 2.6 profiles share the one
  combined 2.6 signing". This plan follows the review: HAMCP-290 is separate
  2.5 scope and is not pulled into the batch (§6).
- #390 and #393 name no exact 2026.11.0 lane evidence or evidence group, and
  #392 and #394 name no evidence group. This plan requires both (G4, G5).
- #389 names no persisted-state downgrade evidence. This plan requires it
  (§7.3).
- #389 §6 labels its id column "Profile id" but gives the dotted capability
  id.

## 10. Superseded (historical)

The revision at `2248224f` was written before the Josh direction in §1 was
confirmed. Its actionable parts are withdrawn and must not be built or used as
a signing brief:

- **Track B (delete and scene v1 to v2).** It proposed a new
  multi-entry "replace-capability" utility operation (B-1) or a per-release
  replace (B-2), a change to C4 so it accepts uncompiled signed siblings, a
  v1/v2 withdrawal window with a sign-first or deploy-first order, and a
  reverse replacement as rollback. Its source diagnosis was sound: the
  current utility cannot replace a reference, and once `main` compiles v2 in
  place of v1, C4 refuses every positive transition while any entry still
  references v1. That diagnosis is now moot, because no existing profile
  changes.
- **Track A options.** Extending all four admitted releases (one sequence
  each, newest or oldest first), a narrowed 10.3 plus 11.0 set, and retiring
  releases first. Replaced by the 2026.11.0-only single extension.
- **Ordering and placement options.** O-1/O-2 (Track A versus Track B), H-1 to
  H-3 (HAMCP-290 ordering), D-1/D-2 (134 and 231 in 3.0 or dormant in 2.6),
  and candidate plans I to IV.
- **Old open questions 1-6** and the design-question table (#393 Q1-Q4 and
  Q7, #390 Q1 and Q4, #389 Q1, Q2 and Q4, #392 OQ-1, #394 Q1). They are
  answered by the direction or withdrawn by the corrected designs. Old
  question 6 (how to produce 2026.11.0 lane evidence) is now an
  implementation-contract item (G4).
- **Findings F1-F8 against the earlier design heads.** F1 and F2 (#393
  version switch and its rollback) and F4 and F5 (#390 and #392 release sets)
  are superseded by the direction. F3 (#393 journal state), F6 (#394
  identifier labels), F7 (#389 `core.typed_power_operation`) and F8 (#393
  adapter id) are corrected at the heads in §4, as each design's own
  disposition section states.

## 11. Signing impact

None. This document changes no Core profile, contract, registry entry,
workflow or fingerprint.

| Profile id | New or changed (from version X) | Core commands / contract added | Why | Fingerprint impact | Can it batch with other 2.6 profiles |
| --- | --- | --- | --- | --- | --- |
| None | n/a | n/a | Planning document only | none | n/a |

## 12. Non-actions

- No signing, registry or candidate-branch write, dry run, or workflow
  dispatch. The signing tooling was not run.
- No change to `.release/`, versions, `config.yaml`, the Dockerfile, any
  protected path, runtime code, tests, workflows, or any other document.
- No live Home Assistant, deployed endpoint, credential or signing key was
  used. Upstream facts come from the design PRs' exact-version citations.
- The design PRs were not edited. The cross-document notes in §9 are for
  their owners and for review.
