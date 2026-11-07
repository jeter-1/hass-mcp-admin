# HAMCP-332: 2.6.0 signing plan (proposal)

Status: **proposal only**. This document signs nothing. It does not edit the
registry, dispatch a workflow, or change `.release/`, any version, or any
runtime file. Signing a Core registry sequence is Josh's owner action, done
through the protected `core-release-registry-signing` environment.

- Register: HAMCP-332 (2.6.0 design milestone, signing-plan deliverable).
- Base: `origin/release/2.6.0` at `7246f2d21693f039504542574569f03b71476489`.
  At authoring time `origin/release/2.5.0` points at the same commit.
- Canonical journal: `origin/registry` at
  `e486028ed0b06b8a5a737824c829705b403b34d8`, sequence 17.
- Authored 2026-11-07, US Central time.

Inputs are the "Signing impact" sections of these design PRs into
`release/2.6.0`, each read at the head below:

| PR | Register | Head | Signing impact claimed |
| --- | --- | --- | --- |
| #390 | HAMCP-295 / HAMCP-109 | `e025778d13bd9f7bd54804832a3dc87e1d21df24` | New `core.entity_registry_rename_operation` |
| #393 | HAMCP-329 / HAMCP-298 | `a306b4d84287b33dc034abc2801651a4647bdb47` | `core.configuration_delete_operation` v1 to v2. `core.scene_configuration_operation` v1 to v2, only under its Q1 option A |
| #389 | HAMCP-297 | `c22af2252b164d9b5ed218ba9bc87dd9065d8c72` | New `core.device_configuration_entity_operation` |
| #392 | HAMCP-134 | `638510ff27c7c7765a9410194ea327d4cd44d76e` | New `core.security_metadata_read`, placement pending OQ-1 |
| #394 | HAMCP-231 | `6e4d05edd67421fe3bf4709d378f4cb7bd0c56dd` | New `core.thread_matter_topology_read`, release pending Q1 |
| #388 | HAMCP-232 | `873e94beb63e1a3dd41d3f4409ac6d61db669213` | None |
| #391 | HAMCP-104, 143, 149, 229, 267, 294, 296 | `61ae28481d77bf3ad48464fa2afc292aed17e1a4` | None for all seven notes |

Context only, not part of this plan's profile set:

- **HAMCP-319** (#385 into `release/2.5.0`, head
  `622c560394930303281fb7ae6a56f295ccbeb6fa`). It states "No new Core
  capability, profile or signing scope; seq 16/17 unchanged" (#385 §10).
- **HAMCP-290** (Marketplace read). #385 lists it as the later Phase 3 that
  restores `ha_get_hacs_info`. It is expected to add a Core contract, but no
  design exists yet. Its profile id, commands, versions and fingerprint are
  **unknown** (missing source: an HAMCP-290 design). This plan describes only
  how 2.6 signing orders against it.

## 1. Authority and method

- **Josh decides** product scope, the release each profile targets, which
  Core releases carry a profile, sequence order, and every signing action
  (ADR-022).
- **Current source decides** what the signing tools can express. That means
  `scripts/prepare_core_release_registry.py`,
  `scripts/core_registry_one_tap.py`, `scripts/core_release_evidence.py`,
  `.github/workflows/core-release-registry-publish.yml`, and the runtime under
  `hass_mcp_engineering_beta/ha_mcp_engineering/ha_core_readmission/` (written
  `E/` below). All line numbers are at the base commit.
- **The signed journal decides** what is admitted today. It was read from the
  git object at `origin/registry`, not from a running system.
- **Nothing came from a live system.** No live Home Assistant instance,
  deployed endpoint, credential or signing key was used. The journal check in
  §2.5 ran the base runtime verifier offline against the fetched journal bytes
  and the public key committed in source.
- **Unknown values stay unknown.** Where a value cannot be derived, this
  document writes `unknown` and names the missing source.

## 2. Current signing state (derived)

### 2.1 Journal tip

| Field | Value | Source |
| --- | --- | --- |
| Branch tip | `e486028e`, "Publish owner-approved Core 2026.11.0 registry journal", committed 2026-11-07 00:59:30 CST | `git log origin/registry` |
| Earlier registry commits | `cf415e29` (2026-11-05, sequence 16); `f95a8c1a` (branch created from main `c731ad8b`) | `git log origin/registry` |
| File | `upstream-trust/ha-core-release-registry.json`, sha256 `3f79fce76484ff9f9a9c070defe4d1d13865ccbfd24fda0ab8724515c8cc6a5d` | git object at the tip |
| Key and registry ids | `ha-core-release-registry-v1` / `ha-core-reviewed-releases` | journal fields |
| Envelopes | 17; `checkpoint_sequence` 1; 0 `revocation_sources` | journal fields |
| Sequence 17 validity | `generated_at` `2026-11-07T06:16:34Z` (00:16:34 CST); `expires_at` `2027-02-05T06:16:34Z` (2027-02-05 00:16:34 CST) | sequence 17 envelope |
| Copy on `main` | Frozen at sequence 15 (the cutover). It is not the current tip. | `docs/CORE_RELEASE_REGISTRY.md:354-360` |

### 2.2 Admitted Core releases at sequence 17

Every entry uses probe profile `core-probes-child-device-name-parts-v1`. The
groups are those in `ci/core-evidence-groups.json`.

| Core release | Source commit | References | Composition |
| --- | --- | --- | --- |
| 2026.10.0 | `6a811d33…` | 22 | 19 general, plus integration_inspection, automation_baseline, entity_metadata |
| 2026.10.1 | `abe1fdd7…` | 26 | the 22 above, plus availability, exposure, label_registry, configuration_delete |
| 2026.10.3 | `1f214b84…` | 31 | the 26 above, plus helper_options, input_select_option, script_traces, dashboard_metadata, scene_configuration |
| 2026.11.0 | `74cf5a49c63ed8d4d87717844bf14f79c28c9cea` | 32 | the 31 above, plus helper_value (added in sequence 17) |

**Revocations (tombstones):** 2026.9.2 and 2026.9.3 (HAMCP-161), 2026.9.4
(HAMCP-182). A revoked release can never be re-added
(`scripts/prepare_core_release_registry.py:169-170`).

**Sequence history:**

- Sequences 1-5: the 2026.9.x releases.
- Sequence 6 added 2026.10.0; sequence 7 added 2026.10.1.
- Sequences 8, 9 and 12 were revocations; sequences 10 and 11 were extensions.
- Sequence 13 extended 2026.10.1 to 26 references.
- Sequence 14 added 2026.10.3 with 26 references; sequence 15 extended it to 31.
- Sequence 16 added 2026.11.0 with 31 references. It was the first
  publication on the `registry` branch.
- Sequence 17 extended 2026.11.0 to 32 references (candidate branch
  `registry-candidates/2026.11.0-seq17`).

### 2.3 Normalized version set

The admitted set is **2026.10.0, 2026.10.1, 2026.10.3 and 2026.11.0**. No
other release is positive. Profiles touched by 2.6 are carried on these
releases today:

| Capability | Profile id / version | Adapter | Fingerprint at base (matches journal) | Signed for |
| --- | --- | --- | --- | --- |
| `core.configuration_delete_operation` | `core_configuration_delete_operation_v1` / 1 | `compiled-core-configuration-delete-operation-v1` | `sha256:5688af5d141b97ca33eadf251ecfbb8f3b1f833024b0ee1acea4973f558f887d` | 2026.10.1, 2026.10.3, 2026.11.0 (not 2026.10.0) |
| `core.scene_configuration_operation` | `core_scene_configuration_operation_v1` / 1 | `compiled-core-scene-configuration-operation-v1` | `sha256:73bac9067f62b62b039ce03f7754d1312f9c0595d0fb16af49851a49c90ba6bb` | 2026.10.3, 2026.11.0 |
| `core.governed_configuration_operation` | `core_governed_configuration_operation_v1` / 1 | `compiled-core-governed-configuration-operation-v1` | `sha256:d517fa73878f8dafea857b5306db750304d3c65522481898800cf86fb091f441` | all four |
| `core.entity_registry_metadata_operation` | `core_entity_registry_metadata_operation_v1` / 1 | `compiled-core-entity-registry-metadata-operation-v1` | `sha256:cc09630efbc0e1e6d0a587696406f6502c62fe730a767bc0a8cfc96a9988f767` | all four |
| `core.typed_power_operation` | `core_typed_power_operation_v1` / 1 | `compiled-core-single-power-v1` | `sha256:9242b795a2d5ed3deeb3b8ea46fba13fc299c14fb274e3b29ed97a30b607001a` | all four |
| `core.typed_helper_value_operation` | `core_typed_helper_value_operation_v1` / 1 | `compiled-core-single-helper_value-v1` | `sha256:d45ea23b2b7c0ade4c3d4b5ae28602a7d10af72db3dd103f20cffdcea8cc228d` | 2026.11.0 |

Consequences for the design PRs:

- #390's set (all four releases) equals the admitted set.
- #393's sets are per-profile carried sets, not the admitted set. The delete
  set is three releases; the scene set is two.
- #389 targets 2026.11.0 only.
- #392's set (2026.10.1 and 2026.10.3, "plus any later 2.6 target") omits
  2026.10.0 and 2026.11.0 (finding F5).
- #394 names 2026.11.0 and "any other supported Core version that is
  admitted".

### 2.4 Base profile inventory

- `CORE_RUNTIME_CAPABILITY_PROFILES` has 32 profiles (`E/ha_core_readmission/profiles.py:891-899`).
- 17 of them are probe-bound `CORE_CAPABILITY_PROFILES` (`profiles.py:156`).
- Every profile added or changed by 2.6 is runtime-only. So no signed probe
  fingerprint moves, and no existing entry's probe identity changes.

### 2.5 Offline verification result

The base runtime verifier ran offline in a scratch virtual environment against
the fetched sequence 17 bytes and the committed public key.

- Refresh was accepted, at sequence 17, with `positive_authority_current` true.
- Positive selections:
  - 2026.10.0: 22
  - 2026.10.1: 26
  - 2026.10.3: 31
  - 2026.11.0: 32
- 2026.9.4, 2026.10.2 and 2026.10.4 had no positive selections and no probe.

This is local evidence only. It says nothing about any deployed runtime.

### 2.6 Signing-machinery constraints (source facts)

These constraints govern every batching option in §5.

| # | Constraint | Source |
| --- | --- | --- |
| C1 | **One changed entry per positive sequence.** `add` and `extend-capabilities` refuse when more than one release entry changes, with "positive transition must preserve all sibling entries". | `scripts/prepare_core_release_registry.py:163-166` |
| C2 | **`extend-capabilities` is append-only, on one existing release.** The old references must remain an unchanged ordered prefix ("extension must append to the exact original references"). It cannot change release or probe identity. It needs a current authenticated predecessor and new review evidence. The doc adds: "This operation is additive; it introduces no capability-removal or production recovery authorization". | `prepare_core_release_registry.py:180-197`; `docs/CORE_RELEASE_REGISTRY.md:259-293` |
| C3 | **`add` on an existing release is an identical renewal.** "Renewal cannot replace release identity or contracts". `add` of a new release is one entry per sequence. | `prepare_core_release_registry.py:174-179`, `:210-223` |
| C4 | **Every entry must match the compiled contracts of the checkout running the utility.** `require_known_contracts` runs on every entry of the candidate, siblings included, at preparation and again at signing. It keys by `capability_id` against the checkout's `CORE_RUNTIME_CAPABILITY_PROFILES` and compares every reference field exactly. | `prepare_core_release_registry.py:88-98`, `:167-168`, `:334-336` |
| C5 | **Signing runs only from `main`.** Every job of the one-tap workflow requires `github.ref == 'refs/heads/main'`. So by C4, a profile can be signed only after its compiled code is on `main`. | `.github/workflows/core-release-registry-publish.yml:62`, `:95`, `:180` |
| C6 | **`revoke` withdraws one release per sequence, permanently.** It may not add or alter positives. The revoked release can never be re-added. | `prepare_core_release_registry.py:152-163`, `:169-170` |
| C7 | **Automated new-release evidence never adds or changes a profile.** `passing_subset` copies the predecessor's exact references for passing groups. It refuses a predecessor profile that is not mapped in `ci/core-evidence-groups.json` ("binding PR required"). | `scripts/core_release_evidence.py:195-208`; `docs/CORE_RELEASE_REGISTRY.md:646-656` |
| C8 | **One version per capability at runtime.** The coordinator refuses a duplicate capability, profile identity or adapter. An entry refuses a duplicate `capability_id`. `selections()` matches profile id, version, adapter and fingerprint exactly, and silently skips a reference it does not compile. | `E/ha_core_readmission/coordinator.py:182-187`; `registry_models.py:85`; `registry.py:58-100` |
| C9 | **`_contract()` hard-codes version 1.** It sets `profile_id = capability_id.replace(".", "_") + "_v1"` and `"profile_version": 1`. | `E/ha_core_readmission/profiles.py:109-126` |
| C10 | **Bounds and expiry.** The utility compacts to 32 positive-chain envelopes. The runtime bound is 64 envelopes and 8 denial sources. An entry holds 1-64 references (2026.11.0 has 32). Extension needs an unexpired predecessor; the tip expires 2027-02-05 00:16:34 CST. | `docs/CORE_RELEASE_REGISTRY.md:141`, `:236-238`; `prepare_core_release_registry.py:180-182` |
| C11 | **CI window and bindings.** The window lists Core current 2026.10.1, candidate 2026.10.3 and rollback 2026.10.0. 2026.11.0 is not a window slot. Reviewed bindings exist for 2026.10.0, 10.1, 10.3 and 11.0, and so do lane provenance fixtures (`tests/fixtures/core_2026_1{0_0,0_1,0_3,1_0}_lane_provenance.json`). | `ci/support-window.yaml:57-63`; `scripts/core_lane_bindings.py:53-62` |

Three consequences follow directly from source:

- **Additive profiles work today, one release per sequence.** Appending new
  profiles is possible, but each release entry costs one sequence (C1, C2).
  Several new profiles can share the sequence for the same release.
- **The delete v1 to v2 switch cannot be expressed today.** Nothing replaces an
  existing reference inside an entry: C2 is append-only and C3 is an identical
  renewal. Nothing changes more than one entry per sequence (C1). Revoking and
  re-adding is impossible (C6). C4 also creates a deadlock:
  - Once `main` compiles delete v2 in place of v1, every positive transition
    refuses while any sibling entry still references v1. That includes
    additive extensions and automated new-release adds.
  - While `main` compiles v1, no candidate can reference v2.

  A new utility operation, or a change to C4's sibling check, is required.
  That is a `scripts/` change, which needs explicit scope (§5.2).
- **Rollback cannot remove a signed reference.** No operation removes a
  reference from a still-admitted release short of revoking the whole release
  permanently (C2, C6; §7).

### 2.7 Unknowns

| Item | Status | Missing source |
| --- | --- | --- |
| Deployed Core and Engineering versions | unknown | Live observation is out of scope. The CI window's "current 2026.10.1" is CI metadata, not runtime truth. |
| Fingerprints of every new or changed 2.6 profile | unknown | The merged contract code; `_contract()` computes them (C9) |
| Adapter ids for the #390, #392 and #394 profiles | unknown | Not specified by those designs |
| HAMCP-290 contract | unknown | No HAMCP-290 design |
| Final sequence numbers | unknown | Depends on what is signed first (2.5.x, automated new releases, renewals). "Seq 18+" below is a placeholder. |

## 3. Master table

There is one row per profile. "Dormant" means the runtime compiles the
profile, but `selections()` does not select it until a signed reference
matches (C8). The tool refuses with `pending_signed_registry_admission` and
does not fall back.

| # | Capability / profile id | New or changed | Core commands and contract added | Source PR and register | Core releases needing entries | Fingerprint | Conditional on (pending Josh) | Target release | Dormancy before signing |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| P1 | `core.entity_registry_rename_operation` / `core_entity_registry_rename_operation_v1` | New | WS `config/entity_registry/update` with exactly `{entity_id, new_entity_id}`; `config/entity_registry/get` for the old and new ids; new-id state absence read. Dispatch `at_most_once_entity_registry_rename`; readback `authoritative_exact_entity_registry_entry_renamed`; recovery `read_only_no_redispatch` | #390; HAMCP-295, HAMCP-109 | 2026.10.0, 2026.10.1, 2026.10.3, 2026.11.0 (four extension sequences under C1) | unknown until the build computes it from merged code (`_contract()`, C9) | #390 Q4 (single-step rename in 2.6.x, or only with 109 in 3.0). Q1 decides whether the scene v1 and governed-config profiles are also used; it does not change P1's contract | 2.6 (the design lists the base binding set) or 3.0, pending Q4 | Dormant: the tool refuses; no fallback to the metadata profile or ha-mcp (#390 N7) |
| P2 | `core.configuration_delete_operation` / `core_configuration_delete_operation_v2` (adapter `compiled-core-configuration-delete-operation-v2`) | Changed: from v1 (`sha256:5688af5d…887d`) to v2 | REST `DELETE /api/config/scene/config/{config_id}`; WS `input_boolean/delete`, `input_number/delete`; absence reads (scene config 404, `input_boolean/list`, `input_number/list`, `config/entity_registry/list`, `GET /api/states/{entity_id}`). `resource_types` grows to 5. Scene and helpers are reachable only through created-object rollback. `never_sent` grows | #393; HAMCP-329, HAMCP-298 | 2026.10.1, 2026.10.3, 2026.11.0 (all carried releases, switched together) | unknown until merged code computes it. Also needs `_contract()` to accept a version (C9) | #393 Q2 (helper types), Q3 (window and order), Q4 (298 under v1 first), Q7 (config-only scene delete would change the contract). The version change itself is recorded in #393 §5 as "Josh decision 2026-11-07", and the HAMCP-329 register outcome states it | 2.6 | **Not dormant.** A v2 runtime with a v1 registry withdraws all deletion. A v1 runtime with a v2 registry loses the same (#393 §5.1). Not expressible with the current utility (§2.6, F1) |
| P3 | `core.scene_configuration_operation` / `core_scene_configuration_operation_v2` (adapter "…-v2" per #393) | Changed: from v1 (`sha256:73bac906…ba6bb`) to v2, **only under #393 Q1 option A** | Create target `one_exact_absent_config_id_with_no_scene_registry_entry` on the existing `POST /api/config/scene/config/{config_id}`; create arguments; absence check; polled registry readback; concurrency disclosure. No new route | #393; HAMCP-329 | 2026.10.3, 2026.11.0 (switched together) | unknown until merged code computes it (C9) | #393 Q1 = A | 2.6 | Not dormant: under a version mismatch, scene edit is withdrawn (#393 §5.1). Same utility gap as P2 |
| P3′ | Scene-create sibling profile, id **unknown** | New, **only under #393 Q1 option C** | The scene create POST and create contract, as a new capability. #393 names the option but specifies no id or contract | #393; HAMCP-329 | unknown (expected 2026.10.3 and 2026.11.0, the scene v1 set; not stated by the design) | unknown | #393 Q1 = C | 2.6 | Dormant (additive) |
| P4 | `core.device_configuration_entity_operation` (proposed name) / `core_device_configuration_entity_operation_v1` (adapter `compiled-core-device-configuration-entity-operation-v1`) | New | REST `GET /api/states/{entity_id}`; WS `config/entity_registry/get`, `config/device_registry/list`; `call_service` `select.select_option` (`option` only), `switch.turn_on`, `switch.turn_off`, each on a single exact `entity_id`. F3 identity `set_exact_device_configuration_value`; provider boundary `direct_ha_api` | #389; HAMCP-297 | 2026.11.0 only (32 to 33). The 10.x entries are not extended | unknown until merged code computes it | #389 Q1 (use cases; blocks the build). Q2 (`number.set_value` would join this profile before first signing). Q3 (`control_power` options b and c may change `core.typed_power_operation`, a separate impact). Q4 (batch placement). Q7 (names enter the contract) | 2.6 | Dormant: refuses with `pending_signed_registry_admission` |
| P5 | `core.security_metadata_read` (#392 calls the final id "at build"; HAMCP-222 plan name `core.security_posture_metadata_read`) | New | Projected reads: `config/auth/list`, `auth/current_user` (`mfa_modules`), `http/config`, `get_config` (URL posture), `homeassistant/expose_new_entities/get`, `homeassistant/expose_entity/list` (counts; OQ-7), `assist_pipeline/pipeline/list`, `conversation/agent/list`, `get_panels`. Large `never_sent`. A new `CoreCapabilityClass` value | #392; HAMCP-134 | The design names 2026.10.1 and 2026.10.3. The admitted set adds 2026.10.0 and 2026.11.0, which the design did not verify (F5). Josh chooses the set | unknown until merged code computes it | #392 OQ-1 (dormant in 2.6, or wait for 3.0). OQ-3, OQ-5, OQ-6 and OQ-7 change the projection or command list, and so the fingerprint | 3.0 per the row cited by the design, unless OQ-1 brings a dormant signing into 2.6 | Dormant |
| P6 | `core.thread_matter_topology_read` / profile id `core_thread_matter_topology_read_v1` by `_contract()` naming (name pending Josh) | New | `thread/list_datasets`; `matter/network_topology` with `refresh=false` fixed; optional `otbr/info` (projected; Q3); optional `matter/node_diagnostics` (one validated `device_id`). Projection allowlists, caps, typed reasons, `never_sent`, `principal_origin: existing_configured_authenticated_origin_only` | #394; HAMCP-231 | 2026.11.0, plus any admitted release Josh chooses. The design checked 2026.10.3 and 2026.11.0 only | unknown until merged code computes it | #394 Q1 (2.6 or 3.0). Q2 (approve the profile and its A1-A4 subset). Q3, Q4, Q5 and Q6 change the command list. Q7 changes the projection | 3.0 per the row cited by the design, pending Q1 | Dormant |

No row's fingerprint can be derived before its contract code merges: the
material includes the exact command list, response contract and `never_sent`
text, which are open until the design questions above are answered.

## 4. Profiles considered and deliberately not widened

Fingerprints were recomputed at the base and match sequence 17.

| Profile | Considered by | Why it stays unchanged | Fingerprint |
| --- | --- | --- | --- |
| `core.entity_registry_metadata_operation` | #390, #389 | `new_entity_id` is deliberately not added to the closed fields. #389's adapter is not this one | `sha256:cc09630efbc0e1e6d0a587696406f6502c62fe730a767bc0a8cfc96a9988f767` |
| `core.governed_configuration_operation` | #390 (reused under Q1 B or C), #393 | Creates are already signed. Rollback of a create sends a delete under P2, never under this profile | `sha256:d517fa73878f8dafea857b5306db750304d3c65522481898800cf86fb091f441` |
| `core.scene_configuration_operation` v1 | #390 (reused under Q1 B), #393 (under Q1 B or C) | Unchanged unless #393 Q1 = A (P3) | `sha256:73bac9067f62b62b039ce03f7754d1312f9c0595d0fb16af49851a49c90ba6bb` |
| `core.typed_power_operation` | #389 | Not reused for config entities. Q3 options b and c could change it later; that would be a separate impact | `sha256:9242b795a2d5ed3deeb3b8ea46fba13fc299c14fb274e3b29ed97a30b607001a` |
| `core.typed_input_select_option_operation` | #389 | Names `input_select.select_option` only | `sha256:e2240555fb35042e47e7ee7afb7f5552977d019a27058fb2c2c5802674fee38d` |
| `core.typed_helper_value_operation` | #389 | Helper domains only | `sha256:d45ea23b2b7c0ade4c3d4b5ae28602a7d10af72db3dd103f20cffdcea8cc228d` |
| `core.basic_websocket_read` | #392, #394 | Widening `get_config` or another generic read would silently extend existing tools | `sha256:e1c15f0d838c44f40c2bcf7ae9df408c9cd186cea7874ab7976945be405294cc` |
| `core.automation_baseline_metadata_read` | #392 | `auth/current_user` stays limited to the baseline contract | `sha256:42ea1b1731aa0d73e01717aabfa99639c29af2755d8d341c39b4789256a3c540` |
| `core.delegated_device_effective_area` | #392 | `ha_get_entity_exposure` keeps its current path | `sha256:b1c2352fe0e87b59a4f9451113d650ac474a56d9135f715d17d2ca31d25645ad` |
| `core.config_entry_helper_options_operation` | #388 | Recheck only. utility_meter is deferred | `sha256:ce2765dc5caf616ae199fdccf64ce6ec68e1386bb0949099d1896bcb50dd98d8` |
| `core.automation_trace_read`, `core.dependency_helper_planning`, `core.automation_configuration_read` | #391 (229, 296) | Reused unchanged. Routes live in code | `sha256:7f45584511fb1069561ba4ef7a43c4cd6e9a5f41cb9b2f2aac5bdadcd6511c9c`; `sha256:4e981b75c8bf81ef24c3fa123827786ca57762db0d2e09108e499a5e8312efe9`; `sha256:ec4344e7dc0a0f4b1a84a2dfbbeae1b412432cf787bcdf41d58ea21a6edb45ad` |
| `core.basic_rest_read` | #391 (294) | The logbook keeps its REST route. Name resolution uses the existing states read | `sha256:3c76c2789952f16340ec51c83122e43cceb1de9581b4dfdf15821d9fc8282cc8` |
| `core.dashboard_configuration_read`, `core.dashboard_metadata_operation` | #391 (143) | Dashboard compilation uses the ha-mcp provider. No Core profile is touched | `sha256:134fa8fa48bdd085a491d2f311ee69c48f53c844a651c334974899e8b79f6424`; `sha256:62cd6c1453b0bc0b52c6e5f9775d1897294f31b62792dd9674a61b9e8a54cfaa` |

The 104, 149 and 267 notes in #391 touch no Core profile.

## 5. Proposed batching (options; Josh chooses)

### 5.1 Track A: additive profiles (P1, P3′, P4, P5, P6)

Track A is expressible today. Each release entry takes one
`extend-capabilities` sequence, which appends **every** 2.6 additive
reference chosen for that release. Each sequence needs:

- its own review evidence (model `core-capability-extension-evidence-v1`);
- a candidate branch `registry-candidates/<version>-seq<N>`;
- a dry run;
- Josh's environment approval.

The maximum sequence counts are:

| Release | Appended (if every conditional row is chosen) | Without P5/P6 |
| --- | --- | --- |
| 2026.10.0 | P1 (+P5, P6 if Josh admits them there) | P1 |
| 2026.10.1 | P1 (+P5, P6) | P1 |
| 2026.10.3 | P1, P3′ if Q1 = C (+P5, P6) | P1 (+P3′) |
| 2026.11.0 | P1, P3′ if Q1 = C, P4 (+P5, P6) | P1, P4 (+P3′) |

Under #390's version set that is four sequences. Options:

- **A-1, newest first:** 2026.11.0, then 10.3, 10.1, 10.0. The newest releases
  gain capability first.
- **A-2, oldest first:** 10.0, then 10.1, 10.3, 11.0.
- **A-3, narrowed set:** sign only 2026.10.3 and 2026.11.0 (two sequences).
  2026.10.0 and 10.1 then refuse P1 and fail closed. This narrows #390's
  stated set, so it is Josh's call.
- **A-4, retire first:** revoke 2026.10.0 and/or 10.1 before 2.6 signing
  (one sequence each), if they are no longer needed. Revocation is permanent
  (C6) and withdraws every positive on that release. This is an owner
  lifecycle decision, not a signing-plan default.

Dependencies within Track A:

- Each sequence is prepared against the then-current tip and must name it.
  Any intervening sequence forces re-preparation (drift refusal).
- Each sequence needs an unexpired predecessor. If 2.6 signing falls after
  2027-02-05 00:16:34 CST, an identical renewal must come first (C3, C10).

### 5.2 Track B: version changes (P2, and P3 under Q1 = A)

Track B is **not expressible with the current utility** (§2.6). Every option
below needs Josh's explicit scope decision before any build:

- **B-1, multi-entry replace (matches #393's intent).** Add a new reviewed
  operation (name unknown; for example "replace-capability"). In one sequence
  it would:
  - replace one capability's reference with its next version in every
    carried release entry;
  - bind per-release v2 evidence;
  - refuse any other change.

  C4 would also have to accept unchanged sibling references that the signed
  predecessor authenticates, even when they are no longer compiled. Otherwise
  the deadlock in §2.6 remains for every later positive transition.
  The change touches `scripts/prepare_core_release_registry.py`,
  `scripts/core_registry_one_tap.py` (its approval summary would need to show
  replaced references), and their tests. If the workflow's summary or inputs
  change, it also touches the protected workflow. Scope, negative tests and
  focused review are needed. B-1 could optionally also append Track A
  references in the same sequence. That widens the operation, and Josh must
  choose it explicitly.
- **B-2, per-release replace.** A single-entry replace operation, used once
  per release. It needs the same utility and C4 changes. It also lengthens a
  mixed state, because a v2 runtime selects nothing on releases still at v1.
  It has no advantage over B-1 for the window.
- **Excluded by recorded direction or by source:**
  - A sibling capability id in place of a version would be additive and
    avoid the window. #393 §5.1 excludes it, citing Josh's version decision.
    It is listed only so the record shows it was considered.
  - Revoke and re-add is impossible (C6).
  - Signing v2 only on a future Core release cannot work: the deadlock in C4
    remains, and C7 copies predecessor references, which would be v1.

**Window and order (#393 Q3).** By C4 and C5, v2 can be signed only after v2
code is on `main`. Two orders are possible:

- **Sign first:** merge to `main`, sign B-1, then release and deploy. The
  deployed v1 runtime loses all deletion until the deploy.
- **Deploy first:** merge, release and deploy, then sign. The deployed v2
  runtime loses all deletion until the signing.

The window cannot be zero. Josh chooses the order (#393 Q3).

**#393 Q4 interaction.** If 298 for automations and scripts ships under v1
first, that slice needs no signing.

### 5.3 Ordering between Track A and Track B

- **Constraint 1 (C4).** After `main` compiles delete v2, Track A sequences
  and automated new-release adds refuse unless B-1 (with its C4 change) has
  landed. So either:
  - **O-1:** sign every Track A sequence while `main` still compiles delete
    v1, then land v2 code and the B-1 sequence; or
  - **O-2:** land the B-1 utility change first, then sign in any order.
- **Constraint 2 (#393).** All carried releases switch v1 to v2 in one
  sequence (B-1).
- **Constraint 3 (#393 §5.1, Q3).** The v1/v2 window withdraws **all**
  deletion (automation and script delete, the HAMCP-155 recreate source, 298
  rollbacks), and scene edit under Q1 = A. P3 must switch in the same B-1
  sequence as P2, or in a further B-1 sequence with its own window.

### 5.4 Interaction with 2.5.0 (HAMCP-290 and HAMCP-319)

- **HAMCP-319 (#385)** adds no Core signing. Its only overlap is the shared
  `main` that C4 and C5 sign from.
- **HAMCP-290** has an unknown contract. The same mechanics apply: its code
  must be on `main` (C5), and each release entry takes one append sequence
  (C1). `release/2.6.0` is staged behind 2.5.0, so 290's code is expected on
  `main` first. Options:
  - **H-1:** 290 signs in its own 2.5.x sequences, and 2.6 Track A follows
    from that tip.
  - **H-2:** 290's references join the 2.6 Track A sequence for the same
    release. #394 §7 suggests this. It needs both codes on `main` at signing
    and couples 2.5.x timing to 2.6.
  - **H-3:** 2.6 signs first. This is not available if 2.6 code reaches
    `main` after 2.5.0.
- **Automated new Core releases** (for example a 2026.11.x) also consume
  sequence numbers. They carry only predecessor references whose lane groups
  pass (C7). Once a 2.6 profile is signed on a release, the next automated
  run refuses until `ci/core-evidence-groups.json` maps it ("binding PR
  required").

### 5.5 3.0-pending profiles (P5, P6)

- **D-1:** defer. Nothing is signed for P5 or P6 in 2.6.
- **D-2:** sign dormant in 2.6, inside the Track A sequences. The profile code
  must then ship on `main` in 2.6 (C4, C5), even if the tool ships in 3.0.
  That makes the build placement (#392 OQ-1, #394 Q1) a 2.6 code decision,
  not only a signing one.

### 5.6 Candidate sequence plans (for comparison only)

| Plan | Sequences after the tip (placeholders) | Needs a utility change | Delete window |
| --- | --- | --- | --- |
| I: additive first, then B-1 (O-1) | [290 per H-1] → 2-4 Track A → B-1 | yes, before the B-1 step | one B-1 window |
| II: B-1 first, combined (O-2, B-1 appends Track A) | [290] → 1 | yes, wider | one window, and Track A rides with it |
| III: narrowed (A-3), then B-1 | [290] → 2 Track A → B-1 | yes | one window over 3 releases (or fewer, if A-4 retires 10.1) |
| IV: Track A only; P2/P3 deferred | [290] → 2-4 Track A | no | none; #393 slices 3-4 and 6 wait |

## 6. Per-profile prerequisites before any signing

These apply to every row:

1. **Merged code on `main`.** The compiled profile must be on `main`, not
   only on `release/2.6.0` (C4, C5). Any change under
   `E/ha_core_readmission/` is a protected-path change that the build task
   must name.
2. **Computed fingerprint.** The fingerprint comes from the merged code. A
   regression test must prove that all 32 existing fingerprints stay
   byte-identical. That matters most where `_contract()` is changed for
   versions (P2, P3; C9).
3. **Exact-tag verification at every release to be signed** (ADR-021), with
   per-release evidence bound in the extension or replace evidence:

   | Row | Releases checked by the design | Gap for the proposed set |
   | --- | --- | --- |
   | P1 | 2026.10.0, 10.1, 10.3 (files byte-identical) and 2026.11.0 (#390 preamble and §2.4) | none |
   | P2 | 2026.10.3 and 2026.11.0. 2026.10.1 "was not re-read" (#393 preamble) | 2026.10.1 |
   | P3 | 2026.10.3 and 2026.11.0 | none |
   | P3′ | none (no contract) | all |
   | P4 | 2026.11.0 | none |
   | P5 | 2026.10.1 and 2026.10.3 only (#392 §2.3) | 2026.10.0 and 2026.11.0, if admitted there |
   | P6 | 2026.10.3 and 2026.11.0 (#394 §2) | 2026.10.0 and 2026.10.1, if admitted there. The Matter Server and `matterjs-server` schemas are outside the Core registry |

4. **Evidence lanes.**
   - Add a new group per profile in `ci/core-evidence-groups.json`, and add
     its paths to `ci/core-contract-read-files.json`.
   - Run a passing lane at each release to be signed.
   - 2026.11.0 is not a CI window slot (C11). Josh decides how its lane
     evidence is produced; bindings and provenance fixtures for it already
     exist.
   - Without a group, later automated runs refuse (C7).
5. **Fixtures.** Generate them from pinned source shapes, not hand-built.
   Any persisted-format compatibility fixture comes from the exact shipped
   code (root `AGENTS.md`). P1 needs #390's downgrade fixture (N20); P2
   needs #393's v1/v2 test-signed registries.
6. **Candidate review.** Byte-review each candidate and its evidence, record
   both SHA-256 values, do a dry run, then Josh approves the environment
   (`docs/CORE_RELEASE_REGISTRY.md:372-400`).
7. **Utility scope (P2, P3 only).** The B-1 operation and the C4 change are
   reviewed and merged first (§5.2).

## 7. Rollback and revocation

- **Additive rows (P1, P3′, P4, P5, P6).**
  - A runtime without the profile ignores the reference (C8). Rolling
    Engineering back to a build without the profile is safe; the reference
    stays dormant.
  - **No operation removes a signed additive reference** from a
    still-admitted release. The only withdrawal is revoking the whole
    release, which is permanent (C6) and removes every positive on it.
  - The practical revert is an Engineering build that does not compile the
    profile.
  - If a signed contract is found unsafe, the choice is between a code
    revert and a release revocation. A "remove one capability" operation
    would be new authority (`docs/CORE_RELEASE_REGISTRY.md:291-293`).
- **Version rows (P2, P3).**
  - Rolling the runtime back to v1 while the registry references v2
    withdraws deletion (and scene edit under Q1 = A).
  - #393 §9 proposes "a re-signed sequence referencing v1". That needs the
    same B-1 operation in reverse, with v1 code back on `main` (C4). It is
    not expressible today (F2).
  - Josh decides whether to accept the withdrawal or to sign a reverse
    replacement.
- **Do not roll a published journal backwards.** Do not restore an old
  journal or delete a cache as rollback
  (`docs/CORE_RELEASE_REGISTRY.md:287`, `:296-297`).
- **Expiry.** If no sequence is signed before 2027-02-05 00:16:34 CST,
  positive Core registry authority lapses and fails closed. This plan did not
  analyse per-entry renewal beyond C3 and C10.
- **Withdrawal by older writers** stays available. `revoke` does not need to
  understand a newer profile (`docs/CORE_RELEASE_REGISTRY.md:299-302`).

## 8. Findings against design PRs

These are factual conflicts between a design's signing material and current
source. They are recorded here only; the design PRs are not edited.

| # | Severity | PR | Evidence | Conflict | Correction for the design owner |
| --- | --- | --- | --- | --- | --- |
| F1 | High | #393 | #393 §5 table (delete row: "must switch its reference from v1 to v2 **in the same sequence**"; §9 slice 6: "one sequence **after 17** re-referencing every carried release" | The current utility cannot express this. It allows one changed entry per sequence (C1), append-only extension (C2) and identical renewal (C3), and C4 deadlocks once `main` compiles v2 while siblings reference v1 | Name the required utility operation and C4 change as a prerequisite build slice, with scope for `scripts/` and possibly the protected workflow (§5.2 B-1). State that signing slice 6 is blocked until then |
| F2 | Medium | #393 | #393 §9, "Rollback of the implementation" ("It needs a re-signed sequence referencing v1") | Not expressible (C2, C3, C4). A reverse replace needs B-1 and v1 code on `main` | Make the rollback depend on B-1, or state that the accepted withdrawal is the only rollback until B-1 exists |
| F3 | Medium | #393 | #393 §2.1 (lines 101-115): "The journal tip is sequence 15 … carrying Core 2026.10.0, 2026.10.1 and 2026.10.3"; "Planned sequence 16"; "Sequence 17 is the next planned batch"; the preamble calls 2026.10.3 "the newest Core release in the signed journal"; the §2.1 delete row lists 2026.10.1 and 2026.10.3 only; §5 scene row "carried to 2026.11.0 in planned seq 16"; the governed-config row lists 2026.10.0, 10.1, 10.3 only | It read the frozen `main` copy. The canonical tip is sequence 17 on `registry`. 2026.11.0 is signed (sequences 16 and 17) and carries delete, scene and governed config | Re-derive §2.1 from `origin/registry` at `e486028e` |
| F4 | Medium | #390 | #390 "Signing impact" P1 row: "These fit in one envelope with the other 2.6 profiles" | Four release entries need four sequences (C1, C2) | Say "one extension sequence per release entry (four), each shareable with other 2.6 additions for that release" |
| F5 | Medium | #392 | #392 §8: entries "for each admitted Core version (2026.10.1 and 2026.10.3, plus any later 2.6 target)" and "a new owner-signed registry envelope"; §2 checks only 2026.10.1 and 2026.10.3 | The admitted set is 2026.10.0, 10.1, 10.3 and 11.0 (§2.3). There is no exact-tag verification at 2026.10.0 or 2026.11.0, and each release takes its own sequence | Name the version set as Josh's choice and verify at every chosen tag. Change "a new envelope" to one extension per release |
| F6 | Low | #394 | #394 §7 row: "(capability `core_thread_matter_topology_read_v1` …)"; "a new reviewed entry on Core 2026.11.0"; §11 item 6: "Remove the capability from the next signed entry" | `core_…_v1` is the profile-id form (`profiles.py:119`); the capability id is `core.thread_matter_topology_read`. Adding to 2026.11.0 extends the existing entry. Removing a reference is not expressible (C2, C3); only revocation of the whole release withdraws it | Fix the id label and the operation name. Rollback is a code revert or a release revocation |
| F7 | Low | #389 | #389 §6: "`core.typed_operation` family `power` (`sha256:9242b795…001a`)"; §6 note "the `core.typed_operation` fingerprint may change" | The signed capability id is `core.typed_power_operation` (journal and `profiles.py`). There is no `core.typed_operation` capability | Use `core.typed_power_operation` |
| F8 | Low | #393 | #393 §2.1 (line 111) governed-config row, adapter column: "(signed probe profile)" | Its adapter is `compiled-core-governed-configuration-operation-v1` (computed at base; matches the journal) | Use the adapter id |

No conflict was found in #388 or #391: "None" is consistent with source for
all eight notes. #389's citation of the utility constraint
(`prepare_core_release_registry.py:165-166`, `:184-185`) is accurate.

## 9. Open questions — pending Josh

None of these is decided by this plan.

**Raised by this plan:**

1. **Version-change path (P2, P3).** Authorize a B-1 utility operation and the
   C4 sibling-check change as a scoped build, or choose another path (§5.2)?
2. **Release set for additive profiles.** All four admitted releases (A-1 or
   A-2), only 2026.10.3 and 2026.11.0 (A-3), or retire releases first (A-4)?
3. **Track order.** O-1 (Track A before v2 code reaches `main`) or O-2 (utility
   first)? Should B-1 also append Track A references (Plan II)?
4. **2.5.0 ordering.** H-1, H-2 or H-3 against HAMCP-290 (§5.4).
5. **3.0-pending profiles.** D-1 or D-2 for P5 and P6 (§5.5).
6. **Evidence for 2026.11.0.** It is not a CI window slot (C11). How should
   its per-profile lane evidence be produced?

**From the designs (signing-relevant only):**

| Source | Question | Effect on signing |
| --- | --- | --- |
| #393 Q1 | Scene-create home: A (scene v2), B (delete v2 carries the POST), C (new sibling) | Selects P3, P2's contract, or P3′ |
| #393 Q2 | Helper types in delete v2 | P2 command list and fingerprint |
| #393 Q3 | Accept the window; sign first or deploy first | B-1 timing (§5.2) |
| #393 Q4 | 298 for automations and scripts under v1 first | Whether an earlier slice ships without signing |
| #393 Q7 | Config-only scene delete with a weaker readback | P2 contract and fingerprint |
| #390 Q1 | Rename only, or with dependent rewrites (A, B1, B2, C) | Reuse of scene v1 and governed config. No new profile |
| #390 Q4 | Single-step rename in 2.6.x, or only with 109 in 3.0 | Whether P1 signs in 2.6 |
| #389 Q1 | Household use-case list | Blocks the build, and so P4 |
| #389 Q2 | `number.set_value` in the first slice | P4 command list before first signing |
| #389 Q3 | `control_power` (a), (b) or (c) | A possible separate `core.typed_power_operation` change |
| #389 Q4 | Batch P4 with other 2.6 profiles at 2026.11.0, or sign alone | Track A packing |
| #389 Q7 | Final tool and profile names | Enter the signed contract |
| #392 OQ-1 | Dormant in 2.6, or wait for 3.0 | P5 placement (D-1, D-2) |
| #392 OQ-3, OQ-5, OQ-6, OQ-7 | Identity detail, proxy detail, satellites, exposure counts | P5 projection and command list |
| #394 Q1 | 2.6 or 3.0 | P6 placement |
| #394 Q2 | Approve the profile; which A1-A4 subset | P6 existence and command list |
| #394 Q3, Q4, Q5, Q6, Q7 | `otbr/info`, `discover_routers`, `refresh`, `ping_node`, identifiers | P6 command list and projection |

#394 Q8 and Q9 (direct Matter Server and OTBR boundaries) are outside the
signed Core registry. They would need a new kind of trust artifact (#394 §7).

## 10. Signing impact

None. This document changes no Core profile, contract, registry entry,
workflow or fingerprint.

| Profile id | New or changed (from version X) | Core commands / contract added | Why | Fingerprint impact | Can it batch with other 2.6 profiles |
| --- | --- | --- | --- | --- | --- |
| None | n/a | n/a | Planning document only | none | n/a |

## 11. Non-actions

- No signing, registry or candidate-branch write, or workflow dispatch.
- No change to `.release/`, versions, `config.yaml`, the Dockerfile, any
  protected path, runtime code, tests, workflows, or any other document.
- No live Home Assistant, deployed endpoint, credential or signing key was
  used. Upstream facts come only from the design PRs' exact-tag citations.
- The design PRs were not edited. Findings F1-F8 are for their owners and
  for review.
