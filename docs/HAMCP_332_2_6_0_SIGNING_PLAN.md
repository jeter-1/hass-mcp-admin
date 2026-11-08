# HAMCP-332: 2.6.0 signing plan (proposal)

Status: **proposal only, NOT FROZEN.** This document signs nothing. It does
not edit the registry, dispatch a workflow, or change `.release/`, any
version, or any runtime file. Signing a Core registry sequence is Josh's
owner action, done through the protected `core-release-registry-signing`
environment.

**This plan is NOT FROZEN.** Josh answered the 2.6.0 design choices
in-session on 2026-11-08 (items 1-25; §9A). Those answers settle the
extension's membership (§4), but freezing still waits on all of these:

1. the Codex delta review of each design at the head cited in §4: #389,
   #390, #392, #393 and #394;
2. a **full** (not delta) independent review of #389, whose change path was
   rewritten by item 25 into switch-like ordinary control with a new direct-HA
   provider contract (§4 N4);
3. ~~for HAMCP-297: Josh's answers to #389 Q8 and Q9 and the registry
   confirmation of the first-build targets~~. **Done:** Josh answered Q8, Q9
   and Q10 in-session on 2026-11-08, and the `translation_key` values were
   confirmed from integration source (§8, §9A);
4. the Codex delta review of this plan, which follows the feature deltas.

Until then the master table (§4) is the current proposal, not a signing
brief. A freeze of this plan is not a freeze of any fingerprint: final ids
and fingerprints stay unknown until the reviewed implementation is on
`main` (§2.7).

**Design heads cited** (each read with `git show <sha>:<path>` on
2026-11-08):

| PR | Register | Head | Document |
| --- | --- | --- | --- |
| #389 | HAMCP-297 | `84f0c5bed569d2bd2c49c64c302118fb083225f1` | `docs/HAMCP_297_DEVICE_CONFIGURATION_ENTITIES_DESIGN.md` |
| #390 | HAMCP-295 / HAMCP-109 | `2eae7a6bc956b8b68be72b42c8f9923a8589bbc7` | `docs/HAMCP_295_109_ENTITY_RENAME_DEPENDENT_EXECUTION_DESIGN.md` |
| #392 | HAMCP-134 | `924d09b0e6451468e9a32a30c96206d7d7524866` | `docs/HAMCP_134_SECURITY_METADATA_FEASIBILITY.md` |
| #393 | HAMCP-329 / HAMCP-298 | `fe83111c7f1d2b608832284412f4b3ea5effc4c9` | `docs/HAMCP_329_298_SCENE_CREATE_AND_CREATE_ROLLBACK_DESIGN.md` |
| #394 | HAMCP-231 | `c807b370be8ce880d7f4e40aae7081237c57dbe3` | `docs/HAMCP_231_THREAD_MATTER_TOPOLOGY_INVESTIGATION.md` |
| #391 | wave-1 notes (HAMCP-104, 143, 149, 229, 267, 294, 296) | `258f1e74c7cfda174df822215a67c8920759a599` | cited only for "no signing requirement" (§4) |

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
  (§9); Josh decisions of 2026-11-08 recorded and design heads refreshed the
  same day (§9A). US Central time.

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
  - **Superseded in part** by the Josh decision (in-session 2026-11-08), late-row rule (§8): the combined
    signing waits for N2-N6 only until the hard cutoff of 2027-01-22. A row
    that is not frozen by then gets its own later additive
    `extend-capabilities` signing of the same 2026.11.0 entry; it does not
    move to 3.0. The rest of this bullet stands: Core 2026.11.0 only, no 10.x
    targets, and no phasing by choice.
- HAMCP-134 and HAMCP-231 are delivered in **2.6.0**.
- HAMCP-295 rename **rewrites references with verified undo**. A
  disclose-only rename is not a deliverable.
- HAMCP-297 use cases: power-on behavior after outages, child or physical
  lock, and sensor tuning (sensitivity, timeouts). Not indicator LEDs.
- HAMCP-296 decisions stand as merged in #401.

**Josh decisions (in-session 2026-11-08) that bear on signing** (full list
and dispositions in §9A):

- HAMCP-295/109 target 2.6.0 with a hard cutoff to 3.0 if the rename
  contract is not frozen when the combined signing is assembled (Josh
  decision (in-session 2026-11-08), item 6). The cutoff never delays the
  combined signing, and N1 gets no second 2.6 signing (§4 N1).
- **Late-row rule** (Josh decision (in-session 2026-11-08), late-row rule). The combined signing waits for
  every row, with a hard cutoff of **2027-01-22**, the HAMCP-139 renewal
  checkpoint. At the cutoff, what is frozen is signed, and that signing
  doubles as the renewal of the Core authority. A late row gets its own
  later additive signing; it is not moved to 3.0. This plan reads the rule
  as answering its §8 question, which was about N2-N6; item 6 still governs
  N1 (§4 Count, §8).
- #393 uses **two** additive profiles, scene create/delete and created-helper
  delete (Josh decision (in-session 2026-11-08), item 12).
- HAMCP-297 is switch-like ordinary typed control with exact readback and
  undo, not a plan; its signed profile stays additive (Josh decision
  (in-session 2026-11-08), item 25). Its first-build targets are those of
  item 1, and its profile name `core.device_configuration_entity_operation`
  is accepted (Josh decision (in-session 2026-11-08), items 1 and 5).
- HAMCP-134's first build has no optional reads (Josh decision (in-session
  2026-11-08), item 11), and HAMCP-231's first build sends A1 + A2 only with
  nothing beyond Core (Josh decision (in-session 2026-11-08), items 16 and
  19). Both narrow the command lists in §4.

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
  batch shape (one combined signing, with the late-row rule of §8) and the
  additive rule are settled (§1).
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
| `core.configuration_delete_operation` | #393 (automation and script create rollback and creation-identity capture, under reads it already signs; the item 13 option E elevation) | Unchanged. Not version-bumped. Its compiled contract (`E/ha_core_readmission/profiles.py:603-631`) carries target, resource types, dispatch, absent-target, readback, recovery and predispatch fields, and no policy or elevation field, so the option E elevation basis `created_object_registry_modified_since_create` is governance-side and should not move this fingerprint. #393 §5 proving test 1 must prove it | `sha256:5688af5d141b97ca33eadf251ecfbb8f3b1f833024b0ee1acea4973f558f887d` |
| `core.scene_configuration_operation` | #390 (scene rewrites), #393 | Unchanged; `DELETE` stays never-sent | `sha256:73bac9067f62b62b039ce03f7754d1312f9c0595d0fb16af49851a49c90ba6bb` |
| `core.governed_configuration_operation` | #390 (automation and script rewrites), #393 (creates, helper recreate) | Unchanged | `sha256:d517fa73878f8dafea857b5306db750304d3c65522481898800cf86fb091f441` |
| `core.entity_registry_metadata_operation` | #390, #389 | Unchanged; `new_entity_id` is not added | `sha256:cc09630efbc0e1e6d0a587696406f6502c62fe730a767bc0a8cfc96a9988f767` |
| `core.typed_power_operation` | #389 | Unchanged; not reused for configuration entities. `control_power` keeps config-category switches, serialized with the new tool by the shared `entity:` lock (Josh decision (in-session 2026-11-08), item 4) | `sha256:9242b795a2d5ed3deeb3b8ea46fba13fc299c14fb274e3b29ed97a30b607001a` |
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
| N1 | #390, HAMCP-295 / HAMCP-109, `2eae7a6bc956b8b68be72b42c8f9923a8589bbc7`, design §5, §6.1, §9 | Both: capability id `core.entity_registry_rename_operation` and profile id `core_entity_registry_rename_operation_v1` | `core_entity_registry_rename_operation_v1` (the design states the same) | `create_entity_rename_plan`; `rollback_change` for verified rename plans | WS `config/entity_registry/update` with exactly `{entity_id, new_entity_id}`; `config/entity_registry/get` for old and new ids; new-id state absence read. Dispatch `at_most_once_entity_registry_rename`; readback `authoritative_exact_entity_registry_entry_renamed`; recovery `read_only_no_redispatch`; predispatch `core_identity, capability_contract, target_fingerprint, session_fingerprint, generation`. Dependency-bound execution and the mandatory rewrites add **no** profile; they use the unchanged governed and scene profiles (§3). The item 7 not-rewritten acknowledgement and the item 8 actuator elevation are plan and policy behavior, not contract fields | **Not named by the design.** Its F2 freeze criterion requires "the non-live Core 2026.11.0 lane evidence that the combined signing plan requires" and defers the evidence group to this plan. This plan requires it (G4, G5) | §6 N7, N8, N30, N31 and the rest of §6; §6.1 C1-C10 and the release checks R1 (signing request lists the profile only if F1-F4 hold) and R2 (cutoff fired: no rename tool, F3 identity or compensating planner in the 2.6.0 candidate); A1-A11 | New: `execution_mode`, compensation candidates, ownership classes, decision records. Design §8 and slice S2 require a two-direction writer-provenance downgrade proof | unknown | **In the extension only if F1-F4 (design §9) all hold when the combined signing request is assembled** (Josh decision (in-session 2026-11-08), item 6). Otherwise the cutoff applies (Count, below) |
| N2 | #393, HAMCP-329 / HAMCP-298, `fe83111c7f1d2b608832284412f4b3ea5effc4c9`, design §3.1, §5, §7, §8 | Capability id `core.scene_create_delete_operation`, with proposed profile id `core_scene_create_delete_operation_v1` and adapter `compiled-core-scene-create-delete-operation-v1` | `core_scene_create_delete_operation_v1` | `create_scene_configuration_plan` `action: create`; `rollback_change` (created-object rollback only) | REST `POST /api/config/scene/config/{config_id}` to an absent id; `GET` of that config; `DELETE` of it (no body; created-object rollback only); WS `config/entity_registry/list`; `GET /api/states/{entity_id}`. Create and delete targets, polled readbacks, loaded-state and concurrent-create disclosures, `never_sent` `scene.apply`, `scene.create`, `scene.delete`, `scene.reload`, `scene.turn_on`; recovery `read_only_no_redispatch`. A created scene stored but never registered within the bound is non-success and not rollback-eligible (Josh decision (in-session 2026-11-08), item 15) | **Not named.** Acceptance (§7) uses a test-signed registry and a fake Core. This plan requires exact-11.0 lane evidence and a group (G4, G5) | §5 proving tests 1-5: existing references identical; old runtime plus extended registry keeps existing admission; new runtime plus sequence-17 registry refuses only the new identities with zero reads; never admitted on 2026.10.x; probe fingerprints unchanged. §6 negative and option E tests | New: `created_identity` evidence, rollback plan form, delete intents. §8 persistence and downgrade gate with exact-historical-writer fixtures | unknown | In the extension. Decided as one of **two** profiles (Josh decision (in-session 2026-11-08), item 12); the one-profile alternative is withdrawn |
| N3 | #393 (as N2) | Capability id `core.created_helper_delete_operation`, with proposed profile id `core_created_helper_delete_operation_v1` and adapter `compiled-core-created-helper-delete-operation-v1` | `core_created_helper_delete_operation_v1` | `rollback_change` (created-object rollback only) | WS `input_boolean/list`, `input_number/list`, `input_boolean/delete {input_boolean_id}`, `input_number/delete {input_number_id}`, `config/entity_registry/list`; REST `GET /api/states/{entity_id}`. Identity correspondence; dispatch `at_most_once_ws_helper_delete_by_object_id`; absence readback (runtime, not disk durability); `never_sent` every create, update and other `{type}/delete` | **Not named.** As N2 | As N2 | As N2 | unknown | In the extension, as the second of the two profiles (Josh decision (in-session 2026-11-08), item 12) |
| N4 | #389, HAMCP-297, `84f0c5bed569d2bd2c49c64c302118fb083225f1`, design §4.1, §5.3-§5.13, §6, §8, §9.2, §12, §13 | Capability id `core.device_configuration_entity_operation` (name accepted, Josh decision (in-session 2026-11-08), item 5); adapter `compiled-core-device-configuration-entity-operation-v1`. Operation class is the typed ordinary operation, with an **explicit** `_contract` because the name does not fit the `core.typed_<family>_operation` generator (`profiles.py:425-462`) | `core_device_configuration_entity_operation_v1` (C9 still applies to the explicit `_contract`) | `control_device_configuration` (the design's proposed name; `create_device_configuration_plan` is superseded by item 25). Direct typed control with an `undo` action; **no plan, no F3 identity, no approval panel** (Josh decision (in-session 2026-11-08), item 25) | Provider boundary `direct_ha_api`, **new provider contract `direct-ha-exact-device-configuration-v1`**, new `ProviderCapability` member and `CoreIndependence` entry. REST `GET /api/states/{entity_id}`; WS `config/entity_registry/get`, `config/device_registry/list`; `call_service` `select.select_option` (`option` only), `switch.turn_on` / `switch.turn_off` (no data), `number.set_value` (`value` only); one exact `entity_id` per change (item 2). Signed contract also carries: actions including `undo`; the compiled target-kind allowlist of exactly three `(domain, platform, translation_key)` triples, (`select`, `matter`, `startup_on_off`), (`switch`, `matter`, `child_lock`) and (`number`, `matter`, `sensitivity_level`), confirmed from Matter source at Core 2026.11.0 (#389 §4.1; Josh decision (in-session 2026-11-08), Q9); changing the list changes the fingerprint; an empty production `confirmation_required` trigger (Q8); at-most-once dispatch and read-only recovery; exclusive lock `entity:<entity_id>` shared with `control_power`; the numeric exactness contract with the embedded 2026.11.0 `UNIT_CONVERTERS` set; identity-bound undo; predispatch checks; never-sent list. Signed-only authority: refuses `device_configuration_core_authority_unavailable` before HA I/O | **Yes** (§9.2): existing 2026.11.0 lane binding (`scripts/core_lane_bindings.py:62`, image index `sha256:c6c1ca99…e2f69e`, the same as the registry entry); new group `device_configuration_entity` naming exactly this capability; a new `run_disposable` module in `scripts/core_evidence_lanes.py`; a test-only fixture component; a workflow overlay step (protected). PASS receipt bound to the final fingerprint and lane image, listed in `review_artifacts_sha256`. **Residual:** under Q9 (a) the fixture kind is not admitted, so the lane proves the mechanism through a build-time test seam and proves the production list refuses the fixture kind; household kinds are proven only at deployed acceptance (§5.2 G4) | §6 proving check; §8 N-series refusals, T1-T6 (with T1a), S-series (with S7-S8, the Q8 test-only trigger proof), C1-C8, R1-R12 and P1-P9; §9.2 lane tests (Codex D4) | Shipped receipt, execution, lock and hold-release formats (unchanged writers) plus two **new** sidecars: `device-configuration-verified-observation-v1` and `device-configuration-undo-claim-v1`. Downgrade expectations and required proof in design §5.13, tests P1-P9 (§7.3) | unknown | In the extension once its contract is frozen. Q8, Q9, Q10 and the target confirmation are decided (§8, §9A); contract freeze waits only on the full #389 review |
| N5 | #392, HAMCP-134, `924d09b0e6451468e9a32a30c96206d7d7524866`, design §7, §8, §9 | Capability id `core.security_metadata_read` ("final id at build"; HAMCP-222 plan name `core.security_posture_metadata_read`). A new `CoreCapabilityClass` value | `core_security_metadata_read_v1`, if that capability id stands | `get_security_metadata` | Projected reads: `auth/current_user` (identity corroboration and `mfa_modules` only), `config/auth/list` (conservative projection, item 9), `http/config` (active-slot projection), `get_config` (URL posture fields only), `homeassistant/expose_new_entities/get`, `assist_pipeline/pipeline/list`, `conversation/agent/list`, `get_panels`. **Not admitted in the first build:** `homeassistant/expose_entity/list`, `assist_satellite/get_configuration` and every Supervisor read (Josh decision (in-session 2026-11-08), item 11). Source-bounded omissions are explicit `not_provided`, with no session defaults (item 10). Large `never_sent` | **Yes, per command:** fixtures from Core 2026.11.0 source and disposable-lane evidence against an exact 2026.11.0 Core. **No evidence group named;** required by this plan (G5) | §9: exact-version evidence (M1); HTTP active-slot cases (M2); connection-identity cases (M3); required-now negatives for the removed optional reads (refused before I/O, no Supervisor request, `supervisor/*` and `hassio/*` refused); no-echo in errors, audit and truncation; drift tests; refused before Core I/O on 2026.10.x; existing fingerprints and entries byte-identical | None beyond audit. The build must show no new persisted record type is introduced | unknown | In the extension once its contract is frozen. Nothing in #392 is pending Josh |
| N6 | #394, HAMCP-231, `c807b370be8ce880d7f4e40aae7081237c57dbe3`, design §5.1, §7, §8, §11 | Both: capability id `core.thread_matter_topology_read` and profile id `core_thread_matter_topology_read_v1` (cited to `profiles.py:119`) | `core_thread_matter_topology_read_v1` (the design states the same) | `get_thread_matter_topology` | Only `thread/list_datasets` and `matter/network_topology` with `refresh: false` fixed (A1 + A2, Josh decision (in-session 2026-11-08), item 16). `otbr/info` and `matter/node_diagnostics` are in `never_sent`. A2's possible border-router discovery start is accepted with disclosure; no `refresh: true`, `thread/discover_routers` or `matter/ping_node` (item 17). Opaque per-response ids for every identifier class (item 18). Projection allowlists, activity classes, caps, typed reasons, `principal_origin: existing_configured_authenticated_origin_only` | **Partly:** "the existing exact-Core lane and registry process for Core 2026.11.0", plus activity tests in a disposable lane with synthetic nodes (§8 item 6). **No evidence group named;** required by this plan (G5) | §8 items (refusal, A1/A2 alias canary privacy, link resolution, network name by type, activity disclosure and absence of class R reads, freshness); §9 acceptance; §11 step 3 candidate preserves every prior reference in order; §11 step 6 rollback proving check | "No persisted state is added" (design §11 step 6). The build must confirm it | unknown | In the extension once its contract is frozen. Nothing beyond Core is in 2.6 (item 19), so no Matter Server or OTBR authority can be hidden inside this profile |

**Count.** The extension appends **6** references (N1-N6) when every
included contract is frozen in time. Josh's item 12 fixes N2 and N3 as two
profiles, so there is no five-profile #393 alternative. The only variable
is the HAMCP-295 cutoff (Josh decision (in-session 2026-11-08), item 6):

- If any of #390's F1-F4 fails when the combined signing request is
  assembled, N1 is left out and the extension appends **5** references
  (N2-N6). The whole of HAMCP-295/109 moves to 3.0. S1-S3 do not merge into
  the 2.6.0 line before F1-F4 can be met, so 2.6.0 carries no registered or
  reachable rename behavior (#390 R2).
- The cutoff never delays the combined signing, N1 gets no second 2.6
  signing, and no 10.x target is added.
- **N2-N6 follow the late-row rule** (Josh decision (in-session 2026-11-08), late-row rule, §8). The
  combined signing waits until N2-N6 are all frozen, but never past
  2027-01-22. If one is late at the cutoff, the combined signing appends
  only the frozen rows (plus N1 if F1-F4 hold), and each late row is
  appended later by its own additive `extend-capabilities` signing of the
  2026.11.0 entry. Over all 2.6 signings the total stays 6 references, or 5
  under the item 6 cutoff. Each signing has its own sequence number (the
  predecessor's plus one at preparation, unknown here) and its own review
  evidence (`prepare_core_release_registry.py:189-193`).
- No row is narrowed to fit the cutoff: a late row is signed whole, later
  (G1).

"32 to 33" in the earlier #389 §7 was a base-local delta for that profile
alone, and #389 still labels its §7 counts as base-local deltas; neither is
the combined count. The final count of the extended
entry is the predecessor's count at preparation plus 6 or 5. It is unknown.

**Work that needs no new signed profile** (as the designs state):

- #390: the dependency-bound execution mode and the mandatory reference
  rewrites, compensation and rollback steps. They run under the unchanged
  `core.governed_configuration_operation` and
  `core.scene_configuration_operation`.
- #393: automation and script create rollback, their creation-identity
  capture and the option E elevation (Josh decision (in-session 2026-11-08),
  item 13). They run under the unchanged `core.configuration_delete_operation`
  with reads it already signs (§3). The design adds new runtime route
  identities that map to that capability; the build must prove the
  fingerprint stays byte-identical. Legacy contract-1 `CREATE_AUTOMATION`
  plans keep `unavailable_for_create` and need nothing new (item 14).
- #389: undo is the `undo` action of the same tool under N4, not a
  `rollback_change` plan, so it adds no second profile. `control_power` keeps
  its signed profile unchanged (item 4).
- #392: the related existing tools (`ha_get_system_health`,
  `ha_get_entity_exposure`, `ha_get_integration`) keep their current profiles.
  Supervisor reads are not in the first build (item 11); any later one needs
  its own provider review and is outside the Core registry.
- #394: the delegated `thread_network` section keeps its current path and is
  unchanged by HAMCP-231 (item 20). Aligning its ids with the opaque policy
  is a separate item after 2.6, HAMCP-341 (Josh decision (in-session
  2026-11-08)). Nothing beyond Core is in 2.6 (item 19).
- #388 (HAMCP-232) and #391 at `258f1e74` (HAMCP-104, 143, 149, 229, 267, 294,
  296): no signing requirement. Items 21-24 (an acceptance-time read-only 296
  measurement, the 229/294 shipped defaults, no 143 expansion beyond the limit
  diagnostics, and deferral of the listed follow-ups past 2.6) add none.

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
| G1 | The corrected designs are frozen: each Codex delta review settled, the full review of #389 complete, and the §8 items that affect contents or inclusion answered. A narrower profile is never frozen first on the assumption that a later signing widens it silently. For N1, "frozen" is #390's F1-F4 at assembly (§4 Count) |
| G2 | Each included implementation is merged to `main` with its profile compiled and `auto_eligible` (C4, C5). Changes under `E/ha_core_readmission/` are protected-path changes that the build task names. The designs name further protected paths their build tasks must name: for #389, `E/approval_web.py` (`TYPED_HOLD_KINDS`), `E/providers/routing.py` (new `ProviderCapability`), `E/capabilities.py` (new provider contract `direct-ha-exact-device-configuration-v1`) and a `.github/workflows/` lane overlay step (#389 §9.2, §12). #389's new direct-HA provider contract is a provider-boundary change and needs the security section of its build review |
| G3 | Fingerprints come from the reviewed code that will be on signing `main`. A regression test compares **all then-current retained references** of every entry, not only the historical 32, with the compiled profiles, and shows them byte-identical; probe fingerprints unchanged |
| G4 | Each new contract has passing exact Core 2026.11.0 disposable-lane evidence for its actual command, projection and refusal behavior, with its receipt bound to the final fingerprint and lane image. A binding or provenance fixture, a test-signed registry with a fake Core, or green docs CI, is not lane evidence. Each implementation contract documents its concrete lane path; that is routine evidence plumbing, not a new product decision. **Open for Codex (#389 Q9 seam):** under Q9 option (a) the signed allowlist admits only household `(domain, platform, translation_key)` kinds, so the lane proves the generic mechanism through a build-time test seam that adds the fixture kind (unreachable in a production build, asserted by a test) and proves separately that the production list refuses the fixture kind; the admitted household kinds are proven only at deployed acceptance (#389 §9.2, §9.3). Whether that satisfies G4 is a judgment the #389 review must make before the contract freezes. This plan does not accept it in advance |
| G5 | Each new capability is mapped in `ci/core-evidence-groups.json`, with its contract read paths in `ci/core-contract-read-files.json`, before signing. Otherwise the next automated new-release run refuses the predecessor (C7). This plan defines the rule that #390 F2 defers to it: every new capability is in exactly one group; no new capability joins `general`; each design's build contract names its group, its lane module in `scripts/core_evidence_lanes.py` and its read paths, following the base pattern of one group per feature (`ci/core-evidence-groups.json` at the base: `scene_configuration`, `helper_value`, and `availability_exposure` for a two-capability feature). #389 names `device_configuration_entity` (§9.2). #390, #392, #393 and #394 name no group at their cited heads; group names are build-contract plumbing, not a product decision |
| G6 | The persisted-state gate of §7.3 passes for every included profile |
| G7 | The actual registry predecessor is re-fetched at preparation. Any intervening signing (for example a separately reviewed 2.5 extension) is preserved: its additions become part of the prefix and its tombstones are retained. Predecessor, hash and time drift refusals stay in force |
| G8 | Candidate and evidence bytes are reviewed and hashed; the actual combined transition is dry-run; public signing runs from `main` through the protected environment with Josh's approval. Passing source tests do not substitute for that approval |

### 5.3 Proving tests (required implementation and candidate evidence; none run)

1. Every retained signed reference is identical before and after: same
   `profile_id`, `profile_version`, `adapter_id` and `contract_fingerprint`.
2. Old automation and script deletion and scene editing stay available on
   every release where they are signed today.
3. Each unsigned new operation refuses before any Home Assistant I/O and
   with no fallback, using the typed reason its design names:
   `pending_signed_registry_admission` for #393's new identities (N2, N3;
   #393 §5 test 3) and `device_configuration_core_authority_unavailable` for
   #389's ordinary control (N4; #389 §5.13, §8 N1). #390 refuses before plan
   creation (N1; #390 §6 N7), and #392 and #394 refuse before Core I/O (N5,
   N6). Their exact reason codes are fixed in each build contract.
4. The candidate changes exactly one release entry (2026.11.0); all other
   entries are byte-identical; all original references are preserved as the
   ordered prefix.
5. A candidate that replaces, removes or reorders an old reference is
   refused, and the unknown-contract check (C4) is not relaxed.
6. Every finally approved new contract is covered by exact-11.0 evidence
   (G4).
7. The new capabilities remain unadmitted on 2026.10.0, 2026.10.1 and
   2026.10.3, even with the extended journal.

### 5.4 Expiry and the 2027-01-22 cutoff

The extension needs a current predecessor (C2, C11;
`prepare_core_release_registry.py:181-183`). The sequence 17 tip expires
2027-02-05 00:16:34 CST.

Under the Josh decision (in-session 2026-11-08), late-row rule, the combined signing is prepared no later
than **2027-01-22**, the HAMCP-139 renewal checkpoint, 14 days before that
expiry. Every positive signing, `extend-capabilities` included, writes a new
sequence whose `expires_at` is its `generated_at` plus 90 days
(`prepare_core_release_registry.py:247-248`, checked at `:322-323`), so the
cutoff signing also renews the authority. This is source behavior at
`1c4c32b3`.

- **Nothing frozen at the cutoff.** An extension must append at least one
  reference (`:189-191`), so it cannot serve as the renewal. The plan reads
  "sign what's ready" as an identical `add` renewal of the 2026.11.0 entry
  (C3, `:176-180`), registry maintenance with no 2.6 profile. This is a
  plan reading, not a Josh decision.
- **An earlier signing renews first** (for example an automated 2026.12
  Core add, HAMCP-139). The cutoff still applies to the 2.6 rows; that
  signing becomes part of the predecessor (G7).
- **Preparation after expiry.** If the cutoff is missed and preparation falls
  after the tip's expiry, positive authority has lapsed and failed closed.
  An identical renewal (C3) comes first.
- **Later late-row signings** each need a current predecessor too, and each
  renews the authority again.

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
| N1 rename | Dependency-bound plans and tasks, compensation records | Old reader refuses or holds new records and never runs them as `ordered_stop_on_failure`; old records load with identical hashes in the new reader | #390 §8, §6 N20, slice S2 (two-direction writer-provenance downgrade proof) |
| N2, N3 scene and helper | Creation identity evidence (including the option E basis), created-object rollback plans, delete intents | Older runtime rejects new rollback plans and delete intents, never executes them, and still loads records carrying the new evidence keys; old records keep their hashes | #393 §8 (persistence and downgrade gate with exact-historical-writer fixtures) |
| N4 device configuration | No plans (item 25). Shipped receipt, execution, lock and hold-release formats under the new `ordinary-device-configuration-v1` namespace, plus the new `device-configuration-verified-observation-v1` and `device-configuration-undo-claim-v1` sidecars | The exact preceding shipped image, started on a state directory with writer-generated device-configuration records, reads none of them, redispatches nothing, and does not release, transfer or redispatch the unknown-owner `entity:` lock; roll forward resumes read-only with the hold present; restart at each dispatch and undo boundary; registry-reverted refusal; existing power, fan and helper records load with identical hashes | **Covered by #389 §5.13 and §8 P1-P9** (the gap recorded at `d020118a` is closed in design). Still to be proven, not assumed: the preceding image's F3, fan and power stale recovery never releases, transfers or redispatches a lock whose owner it does not recognize (#389 states this is unknown at its head; P1 must prove it). Disclosed fail-closed residual: after a downgrade the older `control_power` refuses `lock_conflict` on a held or in-flight switch, and the older Ingress page cannot release a `device_configuration` hold, until roll-forward |
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

## 8. What still blocks the freeze

Josh answered items 1-25 in-session on 2026-11-08 (§9A). Every choice that
§8 listed at `e1dfe1da` is decided; they are recorded in §9A and in each
design's own disposition section. What remains:

**Decided since `ed00e5cc` (HAMCP-297; #389 §13.1, §15):** Josh answered
in-session on 2026-11-08.

| Item | Decision | Effect on N4 |
| --- | --- | --- |
| Q8 | (a) no safety confirmation in the first build; the mechanism ships with an empty trigger and a test-only trigger proves it | No acknowledgement trigger in the contract; tests S7-S8 |
| Q9 | (a) a compiled `(domain, platform, translation_key)` allowlist in the signed contract; each key confirmed from integration source at Core 2026.11.0 and the pinned integration version; a target without a `translation_key` is dropped | Allowlist (`select`, `matter`, `startup_on_off`), (`switch`, `matter`, `child_lock`) and (`number`, `matter`, `sensitivity_level`) |
| Target confirmation | The architect's read-only registry read (register HAMCP-297, 2026-11-08) gave platforms `matter` and `tapo_control` but no `translation_key`; the keys were derived from source. tapo_control 7.2.13 sets none on the digital-sensitivity number, so the Tapo numbers are dropped. The Aqara sensitivity select is dropped because `(select, matter, sensitivity_level)` is shared with the smoke/CO alarm sensitivity select (Josh follow-up) | First build is Matter only: power-on behavior, child lock and the sensitivity number |
| Breadth (follow-up and Q10) | Accepted with disclosure: `child_lock` also covers the Eve thermostat-valve child lock, `sensitivity_level` (number) covers any Matter boolean-state sensitivity device, and `startup_on_off` covers any Matter `OnOff.StartUpOnOff` endpoint, not only the plugs | No device-type filter in the contract |

Nothing on #389 is pending Josh.

**Pending review, blocking:** the Codex delta review of each design at its
cited head (header table), the full review of #389 (header item 2), and
then the Codex delta review of this plan.

**Pending Josh, not blocking the freeze:** none. The #394 J3 sub-question
is decided: aligning the delegated `thread_network` ids is HAMCP-341, after
2.6 (§9A).

**Late-row rule: decided** (Josh decision (in-session 2026-11-08), late-row rule).

- **Question.** What happens if a row other than N1 is not frozen when the
  combined signing is assembled? The plan had offered (a) wait or (b) move
  the late row to 3.0, recommending (a) bounded by the 2027-02-05 expiry.
- **Decision.** Wait for all rows, with a hard cutoff of **2027-01-22**, the
  HAMCP-139 renewal checkpoint. At the cutoff, sign what is ready; that
  signing doubles as the renewal. Late rows get their own later additive
  signing. They are not moved to 3.0.
- **Supersedes** the "no separate or phased 2.6 signings" part of the §1
  direction, but only for rows late at the cutoff.
- **Consequences recorded by this plan** (source-derived, not decisions):
  - §4 Count: the combined signing appends only the frozen rows. Each late
    row is appended whole by its own `extend-capabilities` signing of the
    2026.11.0 entry.
  - §5.4: the cutoff signing renews the authority (90 days from preparation).
    With nothing frozen, the renewal is an identical `add`.
  - G1-G8 apply to every signing. G7 re-fetches the predecessor each time,
    so an earlier 2.6 signing is part of a later one's preserved prefix.
  - Until a late row is signed, its tool refuses with the §5.3 test 3
    reason. G2 still requires its implementation on `main` before its
    signing. Which Engineering release carries a late row's runtime is not
    decided here.
- **Plan reading, pending Josh only if he wants otherwise.** Item 6 still
  governs N1: if F1-F4 fail at assembly, HAMCP-295/109 moves to 3.0 and gets
  no later 2.6 signing. The late-row rule answered a question asked about
  N2-N6 only.
- Blocks: nothing.

**Signing-specific choices: none new.** The additive architecture, the scene
profile, the 2026.11.0-only target, the single batch (with the late-row
rule above) and the placement of HAMCP-134 and HAMCP-231 are settled (§1). Final signing approval is a later,
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
| Remaining questions | n/a | No new signing-policy choice; only unresolved feature choices hold freeze | §8 (#389 Q8, Q9, the target confirmation and Q10 decided later on 2026-11-08; §9A) |

**Cross-document notes for the delta reviews** (the design PRs are not edited
here). Re-checked on 2026-11-08 against the heads in the header table:

| Note (as recorded at `e1dfe1da`) | Status at the cited head | Where |
| --- | --- | --- |
| #389 names no persisted-state downgrade evidence | **Closed in design.** One proof remains required, not assumed: the preceding image's stale recovery and the unknown-owner lock (§7.3 N4) | #389 §5.13, §8 P1-P9 |
| #389 names lane evidence and an evidence group | **Closed in design** (lane, group `device_configuration_entity`, module, fixture, receipt binding). The Q9 test-seam residual is open for the #389 review (§5.2 G4) | #389 §9.2 |
| #389 §6 labels its id column "Profile id" but gives the dotted capability id | **Closed.** The column is now headed "Capability id" | #389 §6 |
| #389 uses the same names and paths as this plan | **Minor, open.** §5.13 says "§8 P1–P8" while the §8 table has P1-P9 | #389 §5.13, §8 |
| #389 build touches more protected paths | **Recorded here** (§5.2 G2): `E/approval_web.py`, `E/providers/routing.py`, `E/capabilities.py`, a workflow overlay; new provider contract `direct-ha-exact-device-configuration-v1` | #389 §5.3, §9.2, §12 |
| #390 §9 says "HAMCP-290 and the other new 2.6 profiles share the one combined 2.6 signing" | **Open.** The sentence is still at the cited head. This plan keeps the review's position: HAMCP-290 is separate 2.5 scope and is not pulled into the batch (§6) | #390 §9 (build plan, dependencies) |
| #390 names no exact 2026.11.0 lane evidence or evidence group | **Open in design, addressed here.** F2 now requires the lane evidence this plan requires and defers the group to this plan; §5.2 G5 defines the rule. The build contract must name the group | #390 §9 F2 |
| #393 names no exact 2026.11.0 lane evidence or evidence group | **Open.** Acceptance still uses a test-signed registry and a fake Core. G4 and G5 apply | #393 §5, §7 |
| #392 names no evidence group | **Open.** Per-command lane evidence is named | #392 §9 |
| #394 names no evidence group | **Open.** It names the existing exact-Core lane process and a disposable lane for activity tests | #394 §7, §8 item 6 |
| #393 one or two profiles | **Closed** by item 12: two | #393 §0.1, §3.1, §10 |
| #392 optional reads change the command list | **Closed** by item 11: none in the first build | #392 §8, §10 |
| #394 A3/A4 change the command list | **Closed** by item 16: A1 + A2 only | #394 §5.1, §7, §12 |

## 9A. Josh decisions 2026-11-08 — disposition

Josh answered items 1-25 in-session on 2026-11-08: items 2-24 accept each
recommendation as written in the choices list, item 1 sets the HAMCP-297
first-build targets, and item 25 is new. Each is attributed as "Josh decision
(in-session 2026-11-08), item N". Nothing else in this plan is attributed to
Josh. This plan records how each answer reaches the signing plan; the design
documents carry the full text.

| Item | Design | Decision (short) | Effect on this plan | Sections changed |
| --- | --- | --- | --- | --- |
| 1 (E1) | #389 | First-build targets: power-on selects on five plugs, child-lock switches on four Eve plugs, Aqara light-sensor sensitivity (select and number), Tapo digital sensitivity numbers; Tapo detection selects excluded; registry confirmation required; live acceptance on the TV or kitchen plug only | N4's admitted target-kind list is part of the signed contract (narrowed later the same day by Q9 and its follow-up; rows below) | §4 N4; §8 |
| 2 (Q5) | #389 | Single entity per change | N4 contract: one exact `entity_id` | §4 N4 |
| 3 (Q6) | #389 | Disclosure only for power-on behavior | No acknowledgement field in N4 for power-on; Q8 covers the other targets | §8 |
| 4 (Q3) | #389 | `control_power` keeps config-category switches, serialized by the shared lock | `core.typed_power_operation` unchanged; shared `entity:` lock | §3; §4 (no-profile list) |
| 5 (Q7) | #389 | Profile name `core.device_configuration_entity_operation` accepted; tool name superseded by item 25 | N4 capability id accepted (final ids still unknown, §2.7) | §4 N4 |
| 6 (J3) | #390 | 2.6.0 with a hard cutoff to 3.0 if the contract is not frozen at assembly | N1 included only if F1-F4 hold; otherwise 5 references, no second signing, no delay | §1; §4 N1 and Count; §5.2 G1 |
| 7 (J1) | #390 | One plan-level acknowledgement when any reference is not rewritten | Plan behavior; no contract field | §4 N1 |
| 8 (J2) | #390 | Elevated for actuators only | Policy behavior; no contract field | §4 N1 |
| 9 | #392 | Conservative privacy and detail | N5 projection | §4 N5 |
| 10 | #392 | Source-bounded omissions as explicit `not_provided`; no session defaults | N5 output, not commands | §4 N5 |
| 11 | #392 | No optional reads in the first build | N5 command list loses `homeassistant/expose_entity/list`; no satellite or Supervisor read | §4 N5 and no-profile list; §9 notes |
| 12 | #393 | Two additive profiles | N2 and N3 both included; the five-profile alternative is withdrawn | §1; §4 N2, N3 and Count; §9 notes |
| 13 (Q-A) | #393 | Option E: `modified_at`-only change included with disclosure and elevated acknowledgement | Governance-side; `core.configuration_delete_operation` should stay byte-identical, to be proven | §3; §4 (no-profile list); §7.3 |
| 14 (Q6) | #393 | Legacy contract-1 `CREATE_AUTOMATION` plans keep `unavailable_for_create` | None | §4 (no-profile list) |
| 15 (Q7) | #393 | Stored but never registered scene is non-success, not rollback-eligible | N2 readback contract | §4 N2 |
| 16 (J1) | #394 | A1 + A2 only | N6 command list; A3/A4 in `never_sent` | §1; §4 N6; §9 notes |
| 17 (J2) | #394 | Accept A2 discovery with disclosure; no refresh, discover or ping | N6 contract | §4 N6 |
| 18 (J3) | #394 | Opaque per-response ids | N6 projection | §4 N6 |
| 19 (J4) | #394 | Nothing beyond Core in 2.6 | No non-Core trust artifact in 2.6 | §1; §4 N6 and no-profile list |
| 20 (J5) | #394 | HAAC-E009 richer reasons is a separate item | None | §4 (no-profile list) |
| 21-24 | #391 | Live 296 measurement authorized at acceptance (read-only); 229/294 defaults confirmed; 143 not expanded; follow-ups deferred past 2.6 | None: #391 adds no signing requirement | §4 (no-profile list) |
| 25 | #389 | Switch-like ordinary typed control with exact readback and undo; no plan, no approval panel; profile stays additive | N4 reshaped: tool `control_device_configuration` (proposed), no F3 identity, new direct-HA provider contract, new sidecars, refusal `device_configuration_core_authority_unavailable`; full review of #389 required before freeze | Header; §1; §4 N4; §5.2 G1, G2, G4; §5.3 test 3; §7.3 N4; §9 notes |
| Q8 (later 2026-11-08) | #389 | No safety confirmation in the first build; test-only trigger proves the mechanism | Empty production trigger in N4 | §1 header; §4 N4; §8 |
| Q9 (later 2026-11-08) | #389 | Compiled `(domain, platform, translation_key)` allowlist, keys confirmed from source; a target without a key is dropped | N4 carries (`select`, `matter`, `startup_on_off`), (`switch`, `matter`, `child_lock`) and (`number`, `matter`, `sensitivity_level`); Tapo numbers dropped | §4 N4; §8 |
| Follow-up and Q10 (later 2026-11-08) | #389 | Drop the Aqara sensitivity select (its triple is shared with smoke/CO sensitivity); accept and disclose the breadth of the three kept triples | Narrows item 1; no device-type filter | §4 N4; §8 |
| J3 sub-question (later 2026-11-08) | #394 | Delegated `thread_network` id alignment is HAMCP-341, after 2.6 | None | §4 (no-profile list); §8 |
| Late-row rule (later 2026-11-08) | #395 | Wait for all rows, with a hard cutoff of 2027-01-22 (HAMCP-139 renewal checkpoint); at the cutoff sign what is ready, which doubles as the renewal; late rows get their own later additive signing, not 3.0 | The combined signing appends the rows frozen by the cutoff; each late N2-N6 row is appended later by its own extension; the cutoff signing renews the authority. Supersedes "no separate or phased 2.6 signings" for late rows only. Item 6 still governs N1 (plan reading) | §1; §4 Count; §5.4; §8 |

**Earlier direction.** The §1 bullets keep their attribution, "Josh direction
(register 2026-11-07; confirmed in-session 2026-11-08)".

**Conflicts.** None unresolved. No decision conflicts with the additive-only
rule or the 2026.11.0-only target. The late-row rule deliberately supersedes
the "no separate or phased 2.6 signings" part of the §1 direction for rows
late at the 2027-01-22 cutoff. Per ADR-022, the earlier direction is kept
with a superseded note (§1), not silently rewritten. Item 25 adds a new
direct-HA provider contract inside the one additive N4 profile; that is a
provider-boundary change for review (§5.2 G2), not a signing-policy
conflict.

**Effect on the Codex corrections (§9).** Findings 1 and 3 are unchanged.
Finding 2's correction now reflects the decided membership: the table is
settled at six rows with one recognized cutoff, and §8 lists only the
remaining HAMCP-297 items.

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

The revision at `e1dfe1da` was written before the Josh decisions of
2026-11-08 (§9A). These parts of it are no longer live:

- **The #393 one-profile alternative** ("5 under the #393 one-profile
  alternative"; N3 merging into N2). Withdrawn by item 12.
- **"One fewer if #390 J3 keeps HAMCP-295/109 in 3.0."** Replaced by the item
  6 cutoff, defined by #390's F1-F4 at assembly (§4 Count).
- **The §8 pending list** (#389 Q5, Q6, Q7, E1; #390 J1-J3; #392 choices 1-3;
  #393 two profiles, Q-A, Q6, Q7; #394 J1-J3) and the non-blocking #389 Q3,
  #394 J4 and J5. All decided (§9A).
- **The design heads** `786f4a4d` (#390), `3dd4fdf4` (#393), `d020118a`
  (#389), `e052cee8` (#392) and `8917c19a` (#394). The rows in §4 now cite
  the heads in the header table.
- **N4 as a plan** (`create_device_configuration_plan`, verification records
  and undo plans, refusal `pending_signed_registry_admission`). Replaced by
  item 25's ordinary control.
- **N5's optional `homeassistant/expose_entity/list` read** and **N6's
  conditional `otbr/info` and `matter/node_diagnostics` reads.** Removed by
  items 11 and 16.

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
- No Josh decision beyond items 1-25 and the earlier direction is recorded
  or implied, other than Josh's in-session 2026-11-08 answers to #389 Q8, Q9,
  the target follow-up and Q10, to the #394 J3 sub-question, and the
  late-row rule (§9A). The N1 and nothing-frozen readings in §5.4 and §8
  are this plan's, not Josh's. HAMCP-139's row and schedule were not edited. No
  registry read was performed here; the HAMCP-297 `translation_key` values
  come from integration source as cited in #389 §4.1 and §10.
