# HAMCP-332: 2.6.0 signing plan (proposal)

Status: **proposal only, NOT FROZEN.** This document signs nothing. It does
not edit the registry, dispatch a workflow, or change `.release/`, any
version, or any runtime file. Signing a Core registry sequence is Josh's
owner action, done through the protected `core-release-registry-signing`
environment.

## What changed on 2026-11-13 (Josh's rulings R-a to R-g; sheet A refreshed to the release head)

Basis: seven rulings of Josh's, R-a to R-g, relayed verbatim to this
author by the orchestrating session on 2026-11-13 and quoted in §9D. Every
other value in this entry was computed or read by this author at this
branch's head `3bd5fe3f1f1ff6d858e34ecd65a1f80198c46e51` (the #395 fold
merge) and is labelled [R] or [S]; none of it is Josh's. Nothing was
signed, prepared, pushed or dispatched, and no lane was run.

- **All six 2.6 profiles are merged** into `release/2.6.0`, each with its
  lane script, evidence group and a committed PASS receipt (§5A.3, §5A.4,
  §5A.12). The tuple compiles **38** profiles at `3bd5fe3f` [R]. The
  table of unmerged profiles in §5A.12 and the provisional table under
  sheet A are removed, with the merge commits verified by `git log`.
- **Membership (R-a):** all six, each conditional on its own lane passing
  on the signing commit. A profile whose lane fails is dropped from
  sequence 18 and the others proceed (§5A.5, §5A.11 item 3).
- **Candidate construction (R-b):** `previous_capabilities` are the 32
  signed sequence 17 references in their signed order, not the first 32
  compiled profiles; the six added references follow in compiled order.
  Both rules are recorded in §5A.5 and cross-referenced from sheet B. This
  answers §5A.13 Q11, which was an [A] proposal.
- **Device-configuration fingerprint (R-c):** the provisional mark on
  `74a03a64…` is removed in sheet A's architect table. It is still
  recomputed on the signing commit, like every row.
- **Recompute on the signing commit (R-d):** the architect recomputes all
  38 fingerprints on the `release/2.6.0` to `main` merge commit before
  signing (sheet A; §5A.8 step 1).
- **New gate (R-e):** every candidate reference uses exactly the closed
  field set beta.7 accepts. Added as PC10 (§5A.7) and stop condition 14
  (§5A.11), citing the model at the tag.
- **One-way signing (R-f):** stated once in §5A.10.
- **Entity rename stays out (R-g):** the §5A.5 exclusion and the §5A.12
  removed-row note stay.
- **Computed at `3bd5fe3f` [R]:** all 38 fingerprints equal the
  `4d68e4d8` values row by row; the 32 signed references match their
  compiled profiles in every signed field; the evidence-group manifest
  and the contract-read manifest hashes are in sheet A; every committed
  receipt binds source commit `5fc40f93` and the current contract-read
  manifest hash. The HAMCP-134 receipt no longer binds `07bc9034…`
  (§5A.4). R8 still applies: every included lane re-runs once on the
  signing commit.
- **One gap found [S]:** `ci/core-contract-read-files.json` has no profile
  entry for `core.device_configuration_entity_operation`, which G5 as this
  plan words it asks for (§5A.4). Recorded as open; not resolved here.

## What changed later on 2026-11-13 (sheet A items ruling SA-1 and SA-2)

Basis: the architect's ruling on the sheet A items, relayed verbatim by
Josh on 2026-11-13 and quoted in §9D as SA-1 and SA-2. Only this file
changed; no value was recomputed.

- **G5 for M3 (SA-1):** open for `core.device_configuration_entity_operation`
  only, not ruled unnecessary; that profile is not signable until G5 is
  closed. `ci/core-contract-read-files.json` is not changed in the release
  pull request. Josh decides before signing (§5A.4, §5A.5 M3 row).
- **Stop 10 (SA-2):** reworded to match R-a (§5A.11 item 10). R3's
  recorded text (§9B) says only "If G4 can't pass before the 2027-01-22
  signing cutoff, stop and report"; it does not say it overrides R-a, so
  the reword applies.

## What changed in the second revision of 2026-11-10 (Josh's rulings)

Basis: seven rulings Josh gave in session on 2026-11-10, in two blocks,
relayed verbatim to this author by the orchestrating session and quoted in
§9C as R6-R12. They decide questions the first revision left open. They
authorize no signing, dry run, candidate push or dispatch today, and none
was done.

- **`core.marketplace_read` is in the combined sequence 18** (R6). This
  replaces the 2026-11-08 separate-signing decision. Fallback: if its lane
  cannot produce a clean receipt on the signing commit, it drops to
  sequence 19 and sequence 18 is not held. See §5A.3, §5A.5, §5A.13 Q1
  and §6.
- **The HAMCP-297 signing lane is register row HAMCP-366** (R7), started
  2026-11-10. Its profile joins sequence 18 only once that lane has merged
  and passes on the signing commit. Its lane values are pending. See
  §5A.4 and §5A.13 Q4.
- **Every included lane re-runs once on the signing commit**, HAMCP-134's
  included, and receipts bind that commit (R8). Receipts that exist today
  are review evidence only. See §5A.4, §5A.6, §5A.7 PC4, §5A.8 step 2 and
  §5A.12.
- **The candidate-branch route is decided** (R10, which replaces R9): the
  documented manual flow with `extend-capabilities`, as sequences 16 and
  17 used. Agents prepare the two files and the expected-values sheet.
  Josh approves both SHA-256 values. Only then does one agent make the
  single push of `registry-candidates/2026.11.0-seq18`. Josh dispatches
  the dry run and the real run. No agent dispatches any workflow. See
  §5A.8.
- **`core-release-evidence.yml` is not used for sequence 18.** §5A.14
  records why, as history: what R9 asked for, checked against source.
- **New §5A.15:** the expected-values sheet for Josh's dry-run comparison.
- **Sequence 18 is a Core 2026.11.0 extension only** (R11). The Core
  2026.11.1 binding is register row HAMCP-367 and is outside this plan.
  See §5A.5 Exclusions.
- **The entity-rename profile is removed** (R12). HAMCP-295/109 moves to
  2.7 as its first item, and PR #425 does not merge into `release/2.6.0`.
  Row N1 and every rename row, count and condition are removed from §4,
  §5, §5A and §7.3. §5A.5 Exclusions records it. The combined extension
  now adds at most six references (§4 Count).
- **New §9C** quotes the seven rulings. §2.7, §6, §8, §9, §9A, §9B, §11
  and §12 are updated to match. Earlier recorded Josh decisions are kept
  and marked superseded where a ruling replaces them. None was deleted.

## What changed in the first revision of 2026-11-10

Basis: an owner ruling of 2026-11-10 20:53 CST, relayed to this author and
recorded verbatim in the 2.6.0 build handoff (§9B): "Draft #395's seq 18
signing plan now from merged profiles; add #421, #424, #425 and #427
profiles as each lands. No signing dispatch." It asks for a draft. It
authorizes no signing, dry run, candidate branch or dispatch, and none was
done.

- **Merged** `origin/release/2.6.0` at
  `b810ba77f66632f63133cf844c1deb4f3139090c` into this branch (merge commit
  `02627cafff4dba874a11f1c5835d4cf56d40e8f7`, no conflicts). The tree at
  `02627caf` equals `b810ba77` plus this one file. Engineering version at
  that tip: `2.4.2-beta.8`.
- **New §5A:** the sequence 18 draft. It lists the three profiles that are
  merged and not signed, each with its fingerprint computed by the
  repository's own code, its lane evidence and the commit that evidence
  binds; the draft candidate; inputs; preconditions; the dispatch steps as
  Josh would authorize them (described, not run); verification; recovery;
  and stop conditions.
- **New §5A.12:** a separate table, "Pending profiles (not merged; inputs
  provisional)", for #421, #424, #425 and #427.
- **New §5A.13:** every mismatch found and every open question, each marked
  "pending Josh" where it needs him. The main one: this plan and the
  HAMCP-290 design say `core.marketplace_read` is signed separately, while
  later records point it at sequence 18. It is flagged, not resolved.
  (Answered later the same day by R6; see the second revision above.)
- **Updated:** the header (current tips), §2.4 (profile count), §2.7 (values
  that are no longer unknown), §6 and §8 (pointers to §5A.13), §11 and §12.
- **New §9B:** the 2026-11-10 rulings this revision relies on, with where
  each is recorded. They were not answered in this author's session.
- **Not changed:** §1, §3, the §4 rows, C1-C12, G1-G8, §5.3, §5.4, §7, §9,
  §9A and §10. No recorded Josh decision was edited.

Every source claim added in this revision carries one of five labels,
defined in §5A.1: **[S]** read from source at a stated file, line and
commit; **[R]** computed by running repository code offline; **[G]** read
from GitHub or the remote on 2026-11-10; **[H]** recorded in a PR body or a
handoff and not re-verified; **[A]** assumed or proposed by this plan.

**This plan is NOT FROZEN.** Josh answered the 2.6.0 design choices
in-session on 2026-11-08 (items 1-25; §9A). Those answers settle the
extension's membership (§4), but freezing still waits on all of these:

1. the Codex delta review of each design at the head cited in §4: #389,
   #392, #393 and #394. #390's row N1 left §4 on 2026-11-10 (R12, §9C);
   that its design review no longer gates this plan is this plan's
   reading;
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

The #390 row is kept for the record. Its profile left this plan on
2026-11-10 (R12, §9C).

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
- Refreshed 2026-11-08 after #410 merged `release/2.5.0` (`62008070`) into
  `release/2.6.0`: the integration head is now
  `7038aaea275d0347d7dc15e649534b3926edb993`. `1c4c32b3..7038aaea` brings in
  only HAMCP-319's exact-image admission lanes and rollback probe (#404,
  #405). These change `ci.yml`, `ci/support-window.yaml` (a new `admission`
  block for ha-mcp 8.5.0/9.1.0; the Core current and candidate slots are
  unchanged), `scripts/exact_image_admission_lanes.py`,
  `scripts/support_window.py`, tests and docs. The signing, selection and
  evidence paths cited here have an empty diff over that range:
  `scripts/prepare_core_release_registry.py`,
  `scripts/core_registry_one_tap.py`, `scripts/core_release_evidence.py`,
  `.github/workflows/core-release-registry-publish.yml`,
  `ha_core_readmission/`, `ci/core-evidence-groups.json` and
  `ci/core-contract-read-files.json`. So every C1-C12 citation and line
  number holds at `7038aaea`. HAMCP-290 is not in `7038aaea`.
- Refreshed 2026-11-10 (21:11 CST): the integration head is now
  `b810ba77f66632f63133cf844c1deb4f3139090c`, merged into this branch as
  `02627cafff4dba874a11f1c5835d4cf56d40e8f7`. It carries HAMCP-290 (#409, by
  way of `release/2.5.0`), HAMCP-134 (#423) and HAMCP-297 (#428), so three
  compiled profiles are now unsigned (§5A.3). Over `7246f2d2..02627caf` the
  diff is empty for the signing utility, the one-tap script, the evidence
  selector and the publication workflow (the exact list and the check are in
  §5A.1), so those C1-C12 line numbers still hold.
  `E/ha_core_readmission/profiles.py` grew: the C9 id rule is now at `:120`
  (`_contract` at `:109-142`), and `CORE_RUNTIME_CAPABILITY_PROFILES` is at
  `:1076-1086`.
- Refreshed 2026-11-13: this branch's head is
  `3bd5fe3f1f1ff6d858e34ecd65a1f80198c46e51`, the merge that folds #395
  into the release head (parents `1d430d6d`, whose first parent is
  `release/2.6.0` at `7d2989ad` (#437), and `8baf4510`). It carries all six
  2.6 profiles and their lanes (§5A.3). Over `02627caf..3bd5fe3f` the diff
  is empty for `scripts/prepare_core_release_registry.py`,
  `scripts/core_registry_one_tap.py`, `scripts/core_release_evidence.py`,
  `scripts/core_lane_bindings.py`, `scripts/core_evidence_lanes.py`,
  `scripts/check_core_registry_branch.sh`,
  `.github/workflows/core-release-registry-publish.yml` and
  `E/ha_core_readmission/registry.py` [R], so their line numbers hold.
  `E/ha_core_readmission/profiles.py` grew again: `_contract` is at
  `:109-143` (the id rule still at `:120`, `"profile_version": 1` at
  `:127`), `CORE_CAPABILITY_PROFILES` at `:157`, and
  `CORE_RUNTIME_CAPABILITY_PROFILES` at `:1349-1361`. In
  `E/ha_core_readmission/models.py` the fingerprint function is now at
  `:188-189`.
- Canonical journal: `origin/registry` at
  `e486028ed0b06b8a5a737824c829705b403b34d8`, sequence 17 (re-read
  2026-11-10; unchanged).
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
  - **Superseded 2026-11-10** by R12 (§9C): the design cutoff fires.
    HAMCP-295/109 moves to 2.7 as its first item, not 3.0, and PR #425
    does not merge into `release/2.6.0`. Row N1 is removed from §4 and
    the rename profile is in no 2.6 signing (§5A.5 Exclusions).
- **Late-row rule** (Josh decision (in-session 2026-11-08), late-row rule). The combined signing waits for
  every row, with a hard cutoff of **2027-01-22**, the HAMCP-139 renewal
  checkpoint. At the cutoff, what is frozen is signed, and that signing
  doubles as the renewal of the Core authority. A late row gets its own
  later additive signing; it is not moved to 3.0. This plan reads the rule
  as answering its §8 question, which was about N2-N6; item 6 still governs
  N1 (§4 Count, §8). Since 2026-11-10 there is no N1 row: R12 moved
  HAMCP-295/109 to 2.7 (§9C).
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
- Updated 2026-11-10: at `02627caf` the tuple has **35** profiles
  (`profiles.py:1076-1086`): the 32 above plus `CORE_MARKETPLACE_PROFILES`
  (`:900`), `CORE_SECURITY_METADATA_PROFILES` (`:955`) and
  `CORE_DEVICE_CONFIGURATION_PROFILES` (`:1041`). The probe-bound tuple is
  now at `:157` and still has 17. Details in §5A.3.
- Updated 2026-11-13: at `3bd5fe3f` the tuple has **38** profiles
  (`profiles.py:1349-1361`): the 35 above plus
  `CORE_CREATED_OBJECT_PROFILES` (`:1198`, two profiles) and
  `CORE_THREAD_MATTER_TOPOLOGY_PROFILES` (`:900`). The probe-bound tuple
  is at `:157` and still has 17 [R]. Details in §5A.3.

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

**Updated 2026-11-10.** The rows above are kept as written on 2026-11-08.
What source now settles, and what it does not:

| Item | Status at `02627caf` | Where |
| --- | --- | --- |
| Ids and fingerprints of N4 (HAMCP-297) and N5 (HAMCP-134) | Known at the merged tip. Still provisional for signing until the same code is on `main` and recomputed there (C4, C5, G3) | §5A.3 |
| HAMCP-290 contract | Known: `core.marketplace_read` is compiled at the merged tip and on `main` | §5A.3 |
| Ids and fingerprints of N2, N3 and N6 | Provisional, computed at each open PR head. N1 was removed on 2026-11-10 (R12, §9C) | §5A.12 |
| Sequence number of the 2.6 extension | **18 if no other signing is published first.** The tip is 17 and the utility writes the predecessor's sequence plus one (`prepare_core_release_registry.py:245`). Any signing published first would take 18 and move this extension to 19: the Core 2026.11.1 binding (HAMCP-367, outside this plan), another automated Core add, or a renewal. `core.marketplace_read` is no longer signed separately first (R6, §9C) | §5A.2, §5A.5, §5A.11 |
| The 2026.11.0 reference list at preparation | Still the 32 of §2.3 today. Unknown at preparation for the same reason | §5A.6 |
| Final reference count | 33 to 38: the 32 signed today plus one to six added, depending on which of the six profiles are merged and pass on the signing commit in time | §5A.5 |
| Deployed Core and Engineering versions | Still unknown; not observed | n/a |

**Updated 2026-11-13 [R].** The 2026-11-10 table is kept as written. At
`3bd5fe3f`, N2, N3 and N6 are merged, so the ids and fingerprints of all
six 2.6 profiles are known at the release head (§5A.3); each is final for
signing only when the architect recomputes it on the signing commit (R-d,
§9D). The final reference count is 38 if every included lane passes on
the signing commit, and 32 plus the number added otherwise (R-a, §9D).

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

Since 2026-11-10 the #390 uses in this table are not 2.6 work: R12 (§9C)
moved HAMCP-295/109 to 2.7. The profiles stay unchanged either way.

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
| N2 | #393, HAMCP-329 / HAMCP-298, `fe83111c7f1d2b608832284412f4b3ea5effc4c9`, design §3.1, §5, §7, §8 | Capability id `core.scene_create_delete_operation`, with proposed profile id `core_scene_create_delete_operation_v1` and adapter `compiled-core-scene-create-delete-operation-v1` | `core_scene_create_delete_operation_v1` | `create_scene_configuration_plan` `action: create`; `rollback_change` (created-object rollback only) | REST `POST /api/config/scene/config/{config_id}` to an absent id; `GET` of that config; `DELETE` of it (no body; created-object rollback only); WS `config/entity_registry/list`; `GET /api/states/{entity_id}`. Create and delete targets, polled readbacks, loaded-state and concurrent-create disclosures, `never_sent` `scene.apply`, `scene.create`, `scene.delete`, `scene.reload`, `scene.turn_on`; recovery `read_only_no_redispatch`. A created scene stored but never registered within the bound is non-success and not rollback-eligible (Josh decision (in-session 2026-11-08), item 15) | **Not named.** Acceptance (§7) uses a test-signed registry and a fake Core. This plan requires exact-11.0 lane evidence and a group (G4, G5) | §5 proving tests 1-5: existing references identical; old runtime plus extended registry keeps existing admission; new runtime plus sequence-17 registry refuses only the new identities with zero reads; never admitted on 2026.10.x; probe fingerprints unchanged. §6 negative and option E tests | New: `created_identity` evidence, rollback plan form, delete intents. §8 persistence and downgrade gate with exact-historical-writer fixtures | unknown | In the extension. Decided as one of **two** profiles (Josh decision (in-session 2026-11-08), item 12); the one-profile alternative is withdrawn |
| N3 | #393 (as N2) | Capability id `core.created_helper_delete_operation`, with proposed profile id `core_created_helper_delete_operation_v1` and adapter `compiled-core-created-helper-delete-operation-v1` | `core_created_helper_delete_operation_v1` | `rollback_change` (created-object rollback only) | WS `input_boolean/list`, `input_number/list`, `input_boolean/delete {input_boolean_id}`, `input_number/delete {input_number_id}`, `config/entity_registry/list`; REST `GET /api/states/{entity_id}`. Identity correspondence; dispatch `at_most_once_ws_helper_delete_by_object_id`; absence readback (runtime, not disk durability); `never_sent` every create, update and other `{type}/delete` | **Not named.** As N2 | As N2 | As N2 | unknown | In the extension, as the second of the two profiles (Josh decision (in-session 2026-11-08), item 12) |
| N4 | #389, HAMCP-297, `84f0c5bed569d2bd2c49c64c302118fb083225f1`, design §4.1, §5.3-§5.13, §6, §8, §9.2, §12, §13 | Capability id `core.device_configuration_entity_operation` (name accepted, Josh decision (in-session 2026-11-08), item 5); adapter `compiled-core-device-configuration-entity-operation-v1`. Operation class is the typed ordinary operation, with an **explicit** `_contract` because the name does not fit the `core.typed_<family>_operation` generator (`profiles.py:425-462`) | `core_device_configuration_entity_operation_v1` (C9 still applies to the explicit `_contract`) | `control_device_configuration` (the design's proposed name; `create_device_configuration_plan` is superseded by item 25). Direct typed control with an `undo` action; **no plan, no F3 identity, no approval panel** (Josh decision (in-session 2026-11-08), item 25) | Provider boundary `direct_ha_api`, **new provider contract `direct-ha-exact-device-configuration-v1`**, new `ProviderCapability` member and `CoreIndependence` entry. REST `GET /api/states/{entity_id}`; WS `config/entity_registry/get`, `config/device_registry/list`; `call_service` `select.select_option` (`option` only), `switch.turn_on` / `switch.turn_off` (no data), `number.set_value` (`value` only); one exact `entity_id` per change (item 2). Signed contract also carries: actions including `undo`; the compiled target-kind allowlist of exactly three `(domain, platform, translation_key)` triples, (`select`, `matter`, `startup_on_off`), (`switch`, `matter`, `child_lock`) and (`number`, `matter`, `sensitivity_level`), confirmed from Matter source at Core 2026.11.0 (#389 §4.1; Josh decision (in-session 2026-11-08), Q9); changing the list changes the fingerprint; an empty production `confirmation_required` trigger (Q8); at-most-once dispatch and read-only recovery; exclusive lock `entity:<entity_id>` shared with `control_power`; the numeric exactness contract with the embedded 2026.11.0 `UNIT_CONVERTERS` set; identity-bound undo; predispatch checks; never-sent list. Signed-only authority: refuses `device_configuration_core_authority_unavailable` before HA I/O | **Yes** (§9.2): existing 2026.11.0 lane binding (`scripts/core_lane_bindings.py:62`, image index `sha256:c6c1ca99…e2f69e`, the same as the registry entry); new group `device_configuration_entity` naming exactly this capability; a new `run_disposable` module in `scripts/core_evidence_lanes.py`; a test-only fixture component; a workflow overlay step (protected). PASS receipt bound to the final fingerprint and lane image, listed in `review_artifacts_sha256`. **Residual:** under Q9 (a) the fixture kind is not admitted, so the lane proves the mechanism through a build-time test seam and proves the production list refuses the fixture kind; household kinds are proven only at deployed acceptance (§5.2 G4) | §6 proving check; §8 N-series refusals, T1-T6 (with T1a), S-series (with S7-S8, the Q8 test-only trigger proof), C1-C8, R1-R12 and P1-P9; §9.2 lane tests (Codex D4) | Shipped receipt, execution, lock and hold-release formats (unchanged writers) plus two **new** sidecars: `device-configuration-verified-observation-v1` and `device-configuration-undo-claim-v1`. Downgrade expectations and required proof in design §5.13, tests P1-P9 (§7.3) | unknown | In the extension once its contract is frozen. Q8, Q9, Q10 and the target confirmation are decided (§8, §9A); contract freeze waits only on the full #389 review |
| N5 | #392, HAMCP-134, `924d09b0e6451468e9a32a30c96206d7d7524866`, design §7, §8, §9 | Capability id `core.security_metadata_read` ("final id at build"; HAMCP-222 plan name `core.security_posture_metadata_read`). A new `CoreCapabilityClass` value | `core_security_metadata_read_v1`, if that capability id stands | `get_security_metadata` | Projected reads: `auth/current_user` (identity corroboration and `mfa_modules` only), `config/auth/list` (conservative projection, item 9), `http/config` (active-slot projection), `get_config` (URL posture fields only), `homeassistant/expose_new_entities/get`, `assist_pipeline/pipeline/list`, `conversation/agent/list`, `get_panels`. **Not admitted in the first build:** `homeassistant/expose_entity/list`, `assist_satellite/get_configuration` and every Supervisor read (Josh decision (in-session 2026-11-08), item 11). Source-bounded omissions are explicit `not_provided`, with no session defaults (item 10). Large `never_sent` | **Yes, per command:** fixtures from Core 2026.11.0 source and disposable-lane evidence against an exact 2026.11.0 Core. **No evidence group named;** required by this plan (G5) | §9: exact-version evidence (M1); HTTP active-slot cases (M2); connection-identity cases (M3); required-now negatives for the removed optional reads (refused before I/O, no Supervisor request, `supervisor/*` and `hassio/*` refused); no-echo in errors, audit and truncation; drift tests; refused before Core I/O on 2026.10.x; existing fingerprints and entries byte-identical | None beyond audit. The build must show no new persisted record type is introduced | unknown | In the extension once its contract is frozen. Nothing in #392 is pending Josh |
| N6 | #394, HAMCP-231, `c807b370be8ce880d7f4e40aae7081237c57dbe3`, design §5.1, §7, §8, §11 | Both: capability id `core.thread_matter_topology_read` and profile id `core_thread_matter_topology_read_v1` (cited to `profiles.py:119`) | `core_thread_matter_topology_read_v1` (the design states the same) | `get_thread_matter_topology` | Only `thread/list_datasets` and `matter/network_topology` with `refresh: false` fixed (A1 + A2, Josh decision (in-session 2026-11-08), item 16). `otbr/info` and `matter/node_diagnostics` are in `never_sent`. A2's possible border-router discovery start is accepted with disclosure; no `refresh: true`, `thread/discover_routers` or `matter/ping_node` (item 17). Opaque per-response ids for every identifier class (item 18). Projection allowlists, activity classes, caps, typed reasons, `principal_origin: existing_configured_authenticated_origin_only` | **Partly:** "the existing exact-Core lane and registry process for Core 2026.11.0", plus activity tests in a disposable lane with synthetic nodes (§8 item 6). **No evidence group named;** required by this plan (G5) | §8 items (refusal, A1/A2 alias canary privacy, link resolution, network name by type, activity disclosure and absence of class R reads, freshness); §9 acceptance; §11 step 3 candidate preserves every prior reference in order; §11 step 6 rollback proving check | "No persisted state is added" (design §11 step 6). The build must confirm it | unknown | In the extension once its contract is frozen. Nothing beyond Core is in 2.6 (item 19), so no Matter Server or OTBR authority can be hidden inside this profile |

**Row N1 removed (2026-11-10).** The table had a row N1 for the
entity-rename profile `core.entity_registry_rename_operation` (#390,
HAMCP-295 / HAMCP-109). R12 (§9C) moved HAMCP-295/109 to 2.7 as its first
item, and PR #425 does not merge into `release/2.6.0`. So the row is
removed and the profile is in no 2.6 signing (§5A.5 Exclusions). The
removed text is in this file's history at
`88714c8295eb09adc9538eb01e02a025823472fd`. Rows N2-N6 keep their numbers.

**Count.** The combined extension appends up to **6** references: the five
rows of this table (N2-N6) and `core.marketplace_read` (HAMCP-290). That
profile has no row here because it was designed under 2.5; R6 (§9C) puts
it in the combined sequence 18. Josh's item 12 fixes N2 and N3 as two
profiles, so there is no five-profile #393 alternative. §5A.5 lists the
membership as it stands today.

- **N2-N6 follow the late-row rule** (Josh decision (in-session 2026-11-08), late-row rule, §8). The
  combined signing waits until N2-N6 are all frozen, but never past
  2027-01-22. If one is late at the cutoff, the combined signing appends
  only the frozen rows, and each late row is appended later by its own
  additive `extend-capabilities` signing of the 2026.11.0 entry. Each
  signing has its own sequence number (the predecessor's plus one at
  preparation) and its own review evidence
  (`prepare_core_release_registry.py:189-193`).
- **`core.marketplace_read` has its own fallback** (R6): if its lane
  cannot produce a clean receipt on the signing commit, it drops to
  sequence 19 and does not hold sequence 18.
- No row is narrowed to fit the cutoff: a late row is signed whole, later
  (G1). No 10.x target is added.

"32 to 33" in the earlier #389 §7 was a base-local delta for that profile
alone, and #389 still labels its §7 counts as base-local deltas; neither is
the combined count. The final count of the extended entry is the
predecessor's count at preparation plus the references added: 38 if all
six are signed in sequence 18 on today's predecessor.

**Work that needs no new signed profile** (as the designs state):

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
- `added_capabilities`: the finally approved new references from §4 and
  `core.marketplace_read` (§4 Count), each exactly once, appended after
  that prefix;
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
| G1 | The corrected designs are frozen: each Codex delta review settled, the full review of #389 complete, and the §8 items that affect contents or inclusion answered. A narrower profile is never frozen first on the assumption that a later signing widens it silently. |
| G2 | Each included implementation is merged to `main` with its profile compiled and `auto_eligible` (C4, C5). Changes under `E/ha_core_readmission/` are protected-path changes that the build task names. The designs name further protected paths their build tasks must name: for #389, `E/approval_web.py` (`TYPED_HOLD_KINDS`), `E/providers/routing.py` (new `ProviderCapability`), `E/capabilities.py` (new provider contract `direct-ha-exact-device-configuration-v1`) and a `.github/workflows/` lane overlay step (#389 §9.2, §12). #389's new direct-HA provider contract is a provider-boundary change and needs the security section of its build review |
| G3 | Fingerprints come from the reviewed code that will be on signing `main`. A regression test compares **all then-current retained references** of every entry, not only the historical 32, with the compiled profiles, and shows them byte-identical; probe fingerprints unchanged |
| G4 | Each new contract has passing exact Core 2026.11.0 disposable-lane evidence for its actual command, projection and refusal behavior, with its receipt bound to the final fingerprint and lane image. A binding or provenance fixture, a test-signed registry with a fake Core, or green docs CI, is not lane evidence. Each implementation contract documents its concrete lane path; that is routine evidence plumbing, not a new product decision. **Open for Codex (#389 Q9 seam):** under Q9 option (a) the signed allowlist admits only household `(domain, platform, translation_key)` kinds, so the lane proves the generic mechanism through a build-time test seam that adds the fixture kind (unreachable in a production build, asserted by a test) and proves separately that the production list refuses the fixture kind; the admitted household kinds are proven only at deployed acceptance (#389 §9.2, §9.3). Whether that satisfies G4 is a judgment the #389 review must make before the contract freezes. This plan does not accept it in advance |
| G5 | Each new capability is mapped in `ci/core-evidence-groups.json`, with its contract read paths in `ci/core-contract-read-files.json`, before signing. Otherwise the next automated new-release run refuses the predecessor (C7). This plan defines the rule: every new capability is in exactly one group; no new capability joins `general`; each design's build contract names its group, its lane module in `scripts/core_evidence_lanes.py` and its read paths, following the base pattern of one group per feature (`ci/core-evidence-groups.json` at the base: `scene_configuration`, `helper_value`, and `availability_exposure` for a two-capability feature). #389 names `device_configuration_entity` (§9.2). #392, #393 and #394 name no group at their cited heads; group names are build-contract plumbing, not a product decision |
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
   #389's ordinary control (N4; #389 §5.13, §8 N1). #392 and #394 refuse
   before Core I/O (N5, N6). Their exact reason codes are fixed in each
   build contract.
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
`1c4c32b3`, unchanged at `7038aaea`.

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

## 5A. Sequence 18 draft from merged profiles (2026-11-10)

### 5A.1 Basis, scope and evidence labels

This section drafts the combined 2.6 extension from what is merged into
`release/2.6.0` today. It is a draft for review. It prepares no candidate,
pushes no candidate branch, dispatches nothing and signs nothing. §5 still
holds; this section fills it in with current values.

**Second revision, 2026-11-10.** Josh's rulings R6-R12 (§9C) decided the
membership question, the re-run rule and the candidate-branch route, and
removed the rename profile. §5A.3 to §5A.13 are updated in place. §5A.14
and §5A.15 are new. Citations to `origin/main` are at
`c041f59d77665706251fc041bc56dddc972be848` and say so. The [G] reads for
this revision were made on 2026-11-10 between 21:30 and 22:30 CST.

Labels used in §5A:

- **[S]** read from source. Unless another commit is named, the commit is
  `02627cafff4dba874a11f1c5835d4cf56d40e8f7` (this branch after the merge;
  its tree equals `release/2.6.0` at `b810ba77` apart from this file).
- **[R]** computed by running repository code offline, in a scratch virtual
  environment under the worktree's ignored `.artifacts/` (Python 3.12.14,
  with the 39 pins of `hass_mcp_engineering_beta/requirements.lock`
  installed; the fingerprints reproduced unchanged after the pins of
  `tests/requirements.lock` were added for the checks). Nothing was signed,
  dispatched or sent to a registry.
- **[G]** read from GitHub or the remote on 2026-11-10 between 21:10 and
  21:25 CST. These values can change.
- **[H]** recorded in a pull-request body or a handoff file. A claim, not
  re-verified here.
- **[A]** assumed or proposed by this plan. Not a decision.

Paths are relative to the repository root. `E/` is
`hass_mcp_engineering_beta/ha_mcp_engineering/`.

Machinery check [R]: `git diff --numstat 7246f2d2 02627caf` is empty for
`scripts/prepare_core_release_registry.py`,
`scripts/core_registry_one_tap.py`, `scripts/core_release_evidence.py`,
`scripts/core_lane_bindings.py`, `scripts/core_evidence_lanes.py`,
`scripts/check_core_registry_branch.sh`,
`.github/workflows/core-release-registry-publish.yml` and
`E/ha_core_readmission/registry.py`. Three cited files did change:
`E/ha_core_readmission/models.py` (two new capability classes; the
fingerprint function is at `:180-181`), `docs/CORE_RELEASE_REGISTRY.md`
(only at `:609`, `:611` and `:711-723`, so citations below `:600` hold) and
`docs/CORE_REGISTRY_ONE_TAP_RUNBOOK.md` (five lines added at `:270-274`).

### 5A.2 Signed state today

| Fact | Value | Label |
| --- | --- | --- |
| Canonical journal | `origin/registry` at `e486028ed0b06b8a5a737824c829705b403b34d8`; `upstream-trust/ha-core-release-registry.json` | [G] |
| Journal bytes | sha256 `3f79fce76484ff9f9a9c070defe4d1d13865ccbfd24fda0ab8724515c8cc6a5d`; 17 envelopes | [R] |
| Tip | sequence 17, generated `2026-11-07T06:16:34Z`, expires `2027-02-05T06:16:34Z` (00:16:34 CST) | [R] |
| Entries at the tip | 2026.10.0: 22 references; 2026.10.1: 26; 2026.10.3: 31; 2026.11.0: 32 | [R] |
| The entry to extend | `ha-core-2026.11.0-reviewed-v1`; Core source commit `74cf5a49c63ed8d4d87717844bf14f79c28c9cea`; image index `sha256:c6c1ca9931036bf3a2c4cc088d8db25dfea3b33935a5ecc83c40349462e2f69e` | [R] |
| Signed references against compiled profiles | All 32 match the profiles compiled at `02627caf` in every signed field; none differs | [R] |
| Next sequence | 18: the utility writes `tip.sequence + 1` (`scripts/prepare_core_release_registry.py:245`) | [S] |
| Candidate branches on the remote | `registry-candidates/2026.11.0` and `registry-candidates/2026.11.0-seq17`. No `-seq18` branch exists | [G] |

The premise "the next sequence is 18" holds today. It stops holding if any
other signing is published first (§5A.11).

### 5A.3 Profiles merged and not signed

**Refreshed 2026-11-13 to `3bd5fe3f` [R].**
`CORE_RUNTIME_CAPABILITY_PROFILES` (`E/ha_core_readmission/profiles.py:1349-1361`)
compiles 38 profiles at `3bd5fe3f`. Six are not in the signed entry. All
six are `auto_eligible`, version 1, and follow the C9 id rule
(`profiles.py:120`, `:127`). They are compiled 33rd to 38th, in the order
of the table below.

The first 32 compiled profiles are the 32 signed references, but not in
signed order [R]: signed 1-19 are compiled 1-19, signed 20-31 are compiled
21-32, and `core.typed_helper_value_operation` is compiled 20th and signed
32nd. So the candidate's carried prefix is the signed list, not the first
32 compiled profiles (R-b; §5A.5).

| # | Capability id / profile id | Adapter id; class | Fingerprint [R] | Defined at [S] | Merged into `release/2.6.0` by [S] | On `main` (`c041f59d`)? |
| --- | --- | --- | --- | --- | --- | --- |
| M1 | `core.marketplace_read` / `core_marketplace_read_v1` | `compiled-core-marketplace-read-v1`; `marketplace_inventory_read` | `sha256:9393b59801d97786e3ac8cf2b44c1aec121198568bfb017cabaa5691acc0cfeb` | `profiles.py:998-1046` (id at `:1000`) | Profile: #409 (HAMCP-290), merge `19caf5144fc8e1959b8d83db1fa5b4fe52f9ad9f` into `release/2.5.0`, then #383 (`b614be43f8c56ae06fdec0ac0bcb479b9da39715`) into `main`. Lane: #421 (HAMCP-290), merge `29339009759331118d0402d0be4aaf9e1c13fe26`, 2026-11-11 08:33 CST | Yes, with the same fingerprint [R] |
| M2 | `core.security_metadata_read` / `core_security_metadata_read_v1` | `compiled-core-security-metadata-read-v1`; `security_metadata_read` | `sha256:9aa788b50f6d9544808bd8b89fd50c00ffd32e7ebcdd940b9dddace18b2e9c55` | `profiles.py:1053-1133` (id at `:1055`) | #423 (HAMCP-134), merge `6e65ba4d83e9e9f365fc853fa31c56d0be1a35aa`, 2026-11-10 08:21 CST | No |
| M3 | `core.device_configuration_entity_operation` / `core_device_configuration_entity_operation_v1` | `compiled-core-device-configuration-entity-operation-v1`; `typed_ordinary_operation` | `sha256:74a03a640f60c9adaf8508a0a7fcbca7745f29b3d09e1dc9a611ac361475293c` | `profiles.py:1139-1173`; ids and allowlist in `E/device_configuration/contracts.py:25-28`, `:48-52`, `:80-85` (unchanged since `02627caf`) | Profile: #428 (HAMCP-297), merge `fcd6973f14dce01dd3e1fc3d59738a12cd9365c3`, 2026-11-10 15:30 CST. Lane: #434 (HAMCP-366), merge `1bb864b767531ce311c2cb2c997c48c900273bd8`, 2026-11-11 19:12 CST | No |
| M4 | `core.scene_create_delete_operation` / `core_scene_create_delete_operation_v1` | `compiled-core-scene-create-delete-operation-v1`; `scene_create_delete_operation` | `sha256:93378ae0385528f16deb977a985b778ba2dd2d39be6f5cb397ef74f2060a400d` | `profiles.py:1198-1348` (id at `:1200`) | #424 (HAMCP-329 / HAMCP-298), merge `ccaed9ef2183cff2796b835a052de8a88db12229`, 2026-11-11 11:21 CST | No |
| M5 | `core.created_helper_delete_operation` / `core_created_helper_delete_operation_v1` | `compiled-core-created-helper-delete-operation-v1`; `created_helper_delete_operation` | `sha256:3f2e681310a009a4fc853af6fa24f8eca1c671c7dd75bddbbc8b403fec9cdbf4` | `profiles.py:1198-1348` (id at `:1290`) | #424, as M4 | No |
| M6 | `core.thread_matter_topology_read` / `core_thread_matter_topology_read_v1` | `compiled-core-thread-matter-topology-read-v1`; `thread_matter_topology_read` | `sha256:d03f005cfc0dbaaa03aad3e78d9147f83321bb3dec8015aa69edb93e358ebb6c` | `profiles.py:900-989` (id at `:902`) | #427 (HAMCP-231), merge `a860671f2593ae9a39a97fff8c30effe0c79016b`, 2026-11-11 16:21 CST | No |

Every merge commit above is on the first-parent history of
`release/2.6.0` at `7d2989ad` and is an ancestor of `3bd5fe3f`, checked
with `git log --first-parent` and `git merge-base --is-ancestor` [R]. The
"On `main`" column reads the local `origin/main` ref at `c041f59d`, not
re-fetched for this refresh. #425 (HAMCP-295, entity rename) is not
merged and is excluded (R-g; §5A.5 Exclusions).

M2 is §4 row N5, M3 is N4, M4 and M5 are N2 and N3, and M6 is N6. M1 is
HAMCP-290. It has no §4 row; R6 (§9C) put it in the combined sequence 18,
and R-a (§9D) now sets the membership of all six (§5A.5).

All 38 fingerprints at `3bd5fe3f` equal, row by row, the 38 values
computed at `4d68e4d8` for HAMCP-231, and all 32 signed references match
their compiled profiles in every signed field [R]. To reproduce the six
rows, from a checkout of the commit, with the locked requirements
installed:

```sh
python -I -B -c "
import sys; sys.path.insert(0, 'hass_mcp_engineering_beta')
from ha_mcp_engineering.ha_core_readmission.profiles import CORE_RUNTIME_CAPABILITY_PROFILES as P
for p in P[32:]:
    m = p.to_mapping()
    print(m['capability_id'], m['profile_id'], m['profile_version'], m['adapter_id'], m['contract_fingerprint'], p.auto_eligible)
print(len(P))
"
```

It prints the six rows above and `38`. `P[32:]` is the six added profiles
because they are compiled last; it is not a way to build the carried
prefix (R-b). The fingerprint is
`"sha256:" + sha256(canonical_json(material))` (`E/ha_core_readmission/models.py:188-189`).

These values are final for signing only when recomputed on the signing
commit on `main` (C4, C5, G3). R-d (§9D): the architect recomputes all 38
fingerprints on the `release/2.6.0` to `main` merge commit, which is the
signing commit, before signing. Any change to a contract field before then
changes the fingerprint and voids its lane receipt.

### 5A.4 Signing readiness of each merged profile

G4 needs an exact-image lane PASS bound to the final fingerprint. G5 needs
an evidence group and a manifest profile map.

**Rule (R8, §9C).** Every included lane, HAMCP-134's included, re-runs
once on the signing commit, and the receipts listed in
`review_artifacts_sha256` bind that commit. A receipt that exists today
binds an older commit. It is review evidence only and is not reused for
signing.

State at `3bd5fe3f` (refreshed 2026-11-13). Every receipt below was
committed in `a506824170d7eace30fe611f0ada48d44cbecec7` ("HAMCP-364: commit
the seven lane receipts of 5fc40f93", 2026-11-12 05:48 CST), which #435
merged into `release/2.6.0` (`a9144591`, 2026-11-12 11:22 CST) [S]. Each
reads `result` PASS and cleanup `clean` true, and each binds source commit
`5fc40f939756ee4ed5b004f88e53c00fd7d939c8`, manifest
`48b8b87590a7c023f6ff9f44ff05abcab4fbb9aa3f597d57c2a131a149cfbbed`, journal
`3f79fce7…` (§5A.2) and the compiled fingerprint of its profile [R].
`5fc40f93` is an ancestor of `3bd5fe3f`, 54 commits behind it [R].

| Profile | Lane script [S] | Group in `ci/core-evidence-groups.json` [S] | Profile map in `ci/core-contract-read-files.json` [S] | Receipt; SHA-256 at `3bd5fe3f` [R] | Ready? |
| --- | --- | --- | --- | --- | --- |
| M1 `core.marketplace_read` | `scripts/core_marketplace_read_local.py` | `marketplace_read` (`:60-62`) | Present (`:1397`) | `docs/evidence/hamcp290/lane/marketplace_read.json`; `381358d630ff9fc83ce7d2c87e4d33507aad3f9cdfd9872f8ecc3e2883ca7142` | No. Review evidence only; the lane re-runs on the signing commit (R8) |
| M2 `core.security_metadata_read` | `scripts/core_security_metadata_local.py` | `security_metadata` (`:75-77`) | Present (`:1514`) | `docs/evidence/hamcp134/lane/security_metadata.json`; `a7f0697af9d350b69a122d540e5b5af29e5090a0779edebb3264c7a66e8d4a47` | No, as M1 |
| M3 `core.device_configuration_entity_operation` | `scripts/core_device_configuration_local.py` | `device_configuration_entity` (`:72-74`) | **None** (see the M3 gap below) | `docs/evidence/hamcp366/device_configuration_entity.json`; `6345ff58a9fc798d3ece65b7436c7c64564a79bf5866de5502e9e873c38739d0` | No, as M1; and the G5 profile-map question below is open |
| M4 `core.scene_create_delete_operation` | `scripts/core_created_object_local.py` | `scene_create_delete` (`:66-68`) | Present (`:1549`) | `docs/evidence/hamcp364/lane/scene_create_delete.json`; `7320406f98b60c34499c2d10e71675981ffc3d8b0f080cdba53de131c7e57860` | No, as M1 |
| M5 `core.created_helper_delete_operation` | `scripts/core_created_object_local.py` | `created_helper_delete` (`:69-71`) | Present (`:1570`) | `docs/evidence/hamcp364/lane/created_helper_delete.json`; `5e4d641def9c6e248ca88a22fbf3836c5c836f9b429270116dffd1b547897df6` | No, as M1 |
| M6 `core.thread_matter_topology_read` | `scripts/core_thread_matter_topology_local.py` (A1) and `scripts/core_thread_matter_topology_a2_local.py` (A2) | `thread_matter_topology` (`:63-65`) | Present (`:1474`) | A1: `docs/evidence/hamcp231/lane/thread_matter_topology.json`; `6bafb4b6eb49079435e9305f0e7f2a62a324b5579bcad69cc4a974561da2e2c2`. A2: `docs/evidence/hamcp231/lane/thread_matter_topology_a2.json`; `1b4506263090a3f56cba85841ce64cee66e1c323f51729364cadd3868f3826da` | No, as M1; both lanes re-run |

The A2 receipt also records `stub.source_commit`
`aa25c56498194c493b468db1faa842018e04acd2`: the provenance of the
HAMCP-297 Matter Server stub it uses, not the commit the lane ran [R].

**Manifest checks [R].** At `3bd5fe3f` the sha256 of
`ci/core-contract-read-files.json` is `48b8b875…`, the value every receipt
binds (`manifest_sha256` is that file's hash, for example
`scripts/core_device_configuration_local.py:411`). The file has the same
hash at `5fc40f93`. So no receipt binds a stale manifest hash against
`3bd5fe3f`. Any later change to that file before the signing commit makes
every receipt's manifest hash stale; the R8 re-runs bind the signing
commit's value.

**HAMCP-134 open item, resolved [R].** The HAMCP-134 receipt was recorded
on 2026-11-10 as sha256 `799799a6844aa6769d2098a1fd09506c95f2d94ccfa5dc0f5f4d39f1c0d509a1`,
binding `bf3c98847f2fd6b72e80c43cafbc2dec2c907b89` and manifest
`07bc9034ebb66881bd91fe710174db7dfba67e43c598a53d960f4f81587b1f4d`. At
`3bd5fe3f` the committed file is the lane's run 3
(`docs/evidence/hamcp134/lane/README.md:216-230`), committed in `a5068241`;
run 2 (`5ea622ec`) had replaced run 1 before it. It binds
`5fc40f93` and the current manifest `48b8b875…`, not `07bc9034…`. Its
source-hash record equals the sha256 of
`docs/evidence/hamcp134/lane/core-security-metadata-source-hashes.json`
(`ca821db6324e70afdc94ea6f15030274deebb989630fed4b3d2824ac9e4219b3`):
match. It is not stale against `3bd5fe3f`. It is still review evidence
only (R8).

**M2 limit.** The receipt's MFA coverage is `partial` with
`connection_identity_not_supervisor`
(`docs/evidence/hamcp134/lane/README.md:180-182`, limitation at
`:235-240`). That is a disclosed limit of the lane, proven offline in
tests, not a failure.

**M3 gap: no profile map [S].** `ci/core-contract-read-files.json` at
`3bd5fe3f` has no `profiles` entry for
`core.device_configuration_entity_operation`. Its lane does not read one:
it records only the file's SHA-256
(`scripts/core_device_configuration_local.py:411`), while the other five
lanes read their own entry (`scripts/core_marketplace_read_local.py:96`,
`scripts/core_security_metadata_local.py:96`,
`scripts/core_created_object_local.py:117`,
`scripts/core_thread_matter_topology_local.py:104`).
`scripts/core_release_evidence.py` does not read the `profiles` key. But
G5 (§5.2), as this plan words it, asks for each new capability's "contract
read paths in `ci/core-contract-read-files.json`, before signing", and the
HAMCP-297 design asks for "the read-file mapping in
`ci/core-contract-read-files.json` before signing"
(`docs/HAMCP_297_DEVICE_CONFIGURATION_ENTITIES_DESIGN.md:1440-1442`).
G5 is **open** for M3 only; it is not ruled unnecessary (SA-1, §9D). M3
is not signable until G5 is closed. The release pull request does not
change `ci/core-contract-read-files.json`, and the 2.6 release does not
depend on it. Before signing, Josh decides between a scoped follow-up that
adds the entry and re-runs the affected lanes, and dropping M3 from
sequence 18 under R-a (SA-1 item d).

### 5A.5 Draft candidate

One `extend-capabilities` candidate for Core `2026.11.0`, entry
`ha-core-2026.11.0-reviewed-v1` (§5.1).

**Construction rules (R-b, §9D; Josh's ruling, recorded here as R-b
asks).**

1. `previous_capabilities` are the 32 signed sequence 17 references in
   their signed order (§2.3), byte for byte as signed. They are **not**
   the first 32 compiled profiles: `core.typed_helper_value_operation` is
   compiled 20th and signed 32nd (§5A.3).
2. The added references follow in compiled order: marketplace,
   security_metadata, device_configuration_entity, scene_create_delete,
   created_helper_delete, thread_matter_topology. A profile dropped under
   R-a is left out and the others keep this relative order.

Sheet B (§5A.15) checks the summary against both rules. The candidate is
checked against them before Josh signs (R-f; §5A.10).

`added_capabilities` with all six, in R-b's order, as compiled at
`3bd5fe3f` [R]. Each reference has exactly the five fields a signed
reference has today, which is the closed field set R-e requires (PC10,
§5A.7) [R]:

```json
[
  {"adapter_id": "compiled-core-marketplace-read-v1", "capability_id": "core.marketplace_read", "contract_fingerprint": "sha256:9393b59801d97786e3ac8cf2b44c1aec121198568bfb017cabaa5691acc0cfeb", "profile_id": "core_marketplace_read_v1", "profile_version": 1},
  {"adapter_id": "compiled-core-security-metadata-read-v1", "capability_id": "core.security_metadata_read", "contract_fingerprint": "sha256:9aa788b50f6d9544808bd8b89fd50c00ffd32e7ebcdd940b9dddace18b2e9c55", "profile_id": "core_security_metadata_read_v1", "profile_version": 1},
  {"adapter_id": "compiled-core-device-configuration-entity-operation-v1", "capability_id": "core.device_configuration_entity_operation", "contract_fingerprint": "sha256:74a03a640f60c9adaf8508a0a7fcbca7745f29b3d09e1dc9a611ac361475293c", "profile_id": "core_device_configuration_entity_operation_v1", "profile_version": 1},
  {"adapter_id": "compiled-core-scene-create-delete-operation-v1", "capability_id": "core.scene_create_delete_operation", "contract_fingerprint": "sha256:93378ae0385528f16deb977a985b778ba2dd2d39be6f5cb397ef74f2060a400d", "profile_id": "core_scene_create_delete_operation_v1", "profile_version": 1},
  {"adapter_id": "compiled-core-created-helper-delete-operation-v1", "capability_id": "core.created_helper_delete_operation", "contract_fingerprint": "sha256:3f2e681310a009a4fc853af6fa24f8eca1c671c7dd75bddbbc8b403fec9cdbf4", "profile_id": "core_created_helper_delete_operation_v1", "profile_version": 1},
  {"adapter_id": "compiled-core-thread-matter-topology-read-v1", "capability_id": "core.thread_matter_topology_read", "contract_fingerprint": "sha256:d03f005cfc0dbaaa03aad3e78d9147f83321bb3dec8015aa69edb93e358ebb6c", "profile_id": "core_thread_matter_topology_read_v1", "profile_version": 1}
]
```

**Membership (R-a, §9D).** All six, each conditional on its lane passing
on the signing commit. A profile whose lane fails is dropped from sequence
18; the others proceed. Earlier rulings that still apply: R1 and R4
(§9B), R6, R7 and R8 (§9C), and the late-row rule (§8).

| Profile | In sequence 18? | Condition |
| --- | --- | --- |
| M1 `core.marketplace_read` | Yes (R-a; R6) | Its lane passes on the signing commit (R8). If it fails, M1 is dropped from sequence 18 and the others proceed (R-a); R6 names sequence 19 for it |
| M2 `core.security_metadata_read` | Yes (R-a; R1, R4) | Its lane re-runs and passes on the signing commit (R8). If it fails, it is dropped (R-a) |
| M3 `core.device_configuration_entity_operation` | Yes (R-a; R7) | Its HAMCP-366 lane (merged by #434) passes on the signing commit (R8). If it fails, it is dropped (R-a). G5 is open for this profile only, and it is not signable until G5 is closed (SA-1, §9D; §5A.4). If its G4 lane or its G5 gate cannot pass before 2027-01-22, it is dropped from sequence 18 and the others proceed; Josh is told before signing (§5A.11 item 10, SA-2) |
| M4 `core.scene_create_delete_operation` | Yes (R-a) | Its lane passes on the signing commit (R8). If it fails, it is dropped (R-a) |
| M5 `core.created_helper_delete_operation` | Yes (R-a) | As M4 |
| M6 `core.thread_matter_topology_read` | Yes (R-a) | Both of its lanes, A1 and A2, pass on the signing commit (R8). If either fails, it is dropped (R-a) |

A dropped profile is a later signing: for M1, sequence 19 (R6); for the
others, the late-row rule (§8). Each lane refuses unless the predecessor
has exactly 32 references, so a later signing needs that check changed
first (§5A.13 Q8, Q15).

| Case | Added in sequence 18 | References after sequence 18 |
| --- | --- | --- |
| Every lane passes on the signing commit | All six, in R-b's order | 38 |
| Some lanes fail | The six less each dropped profile, still in R-b's order | 32 plus the number added |

**Exclusions.**

- HAMCP-295/109 entity rename (`core.entity_registry_rename_operation`,
  PR #425): moved to 2.7 as its first item by Josh's ruling 2026-11-10; PR
  #425 does not merge into `release/2.6.0` (R12, §9C), and it stays out of
  2.6 (R-g, §9D). It is not merged at `3bd5fe3f` [S]. It is in no 2.6
  signing.
- Core 2026.11.1: outside this plan. Sequence 18 stays a 2026.11.0
  extension, and the 2026.11.1 binding is its own register row, HAMCP-367
  (Backlog; target pending Josh) (R11, §9C). Core 2026.11.1 was published
  on 2026-11-09 and is unsigned; scheduled run 42688632541 refused it
  ("binding PR required") [G].

Order of the added references: decided by R-b (rule 2 above). Source
requires only that the added list is exactly what follows the old prefix
(`prepare_core_release_registry.py:108-125`); it sets no order among added
references. The order is fixed once signed, because a later extension must
keep it as its prefix (C2).

This is not a signable candidate today. PC2 and PC4 are not met (§5A.7).

### 5A.6 Inputs

| Input | Value or source | Known today? |
| --- | --- | --- |
| Signing commit | The `main` commit that contains every included profile, its lane, its group and its manifest map (C4, C5) | No. Only M1's profile is on `main` |
| Predecessor journal | The bytes of `origin/registry` re-fetched at preparation (G7) | Today's are in §5A.2 |
| Public key | `docs/evidence/hamcp222/core-public-key.txt` | Yes [S] |
| Entry JSON | The signed 2026.11.0 entry with only `capabilities` extended and `evidence_sha256` replaced; source, image, architecture and probe identity unchanged (C2) | Derived at preparation |
| Evidence JSON | Canonical JSON with exactly `model` (`core-capability-extension-evidence-v1`), `version`, `previous_entry_sha256`, `previous_capabilities`, `added_capabilities`, `review_artifacts_sha256` (`docs/CORE_RELEASE_REGISTRY.md:269-277`; C10) | Derived at preparation |
| `review_artifacts_sha256` | 1-32 distinct sha256 values: the PASS receipt of each included lane, from its one re-run on the signing commit (R8), plus the reviewed candidate artifacts (§5.1). No receipt that binds an older commit is listed | No |
| Core lane image | `ghcr.io/home-assistant/home-assistant:2026.11.0@sha256:c6c1ca9931036bf3a2c4cc088d8db25dfea3b33935a5ecc83c40349462e2f69e` (C12; the same index as the signed entry) | Yes |
| Candidate branch | A new permanent branch `registry-candidates/2026.11.0-seq18`, holding `core-candidate.json` and `core-review-evidence.json` at its root and nothing else (`docs/CORE_RELEASE_REGISTRY.md:376-382`). It is created by the single push of §5A.8 step 6, only after Josh approves both SHA-256 values (R10) | Does not exist [G] |
| Workflow inputs | `core_version` `2026.11.0`, `candidate_ref`, `candidate_sha256`, `evidence_sha256`, `dry_run` (`.github/workflows/core-release-registry-publish.yml:9-31`) | The two hashes exist only after preparation |
| Engineering version | `2.4.2-beta.8` (`hass_mcp_engineering_beta/config.yaml:2`). Signing does not change it | Yes [S] |

### 5A.7 Preconditions

All must hold before a candidate is prepared. On 2026-11-13, at this
branch's head `3bd5fe3f`: PC2, PC3 and PC4 are not met; PC1 is settled as
a rule (R-a) whose outcome depends on the lanes; PC10 is a check on the
prepared candidate; and the approval PC8 needs does not exist yet.

| # | Precondition | State on 2026-11-13 (at `3bd5fe3f`) |
| --- | --- | --- |
| PC1 | Membership is settled: each profile of §5A.5 is either merged and proven, or left for a later signing (§8). R-a (§9D): all six, each conditional on its lane passing on the signing commit; a profile whose lane fails is dropped from sequence 18 and the others proceed | The rule is decided (R-a; R6, R7, R12 in §9C). All six are merged into `release/2.6.0` (§5A.3). Which profiles pass is known only from the lane runs on the signing commit |
| PC2 | **Protected `main` carries the code.** Every included profile is compiled and `auto_eligible` on `main`, with its lane script, evidence group and manifest map (C4, C5, G2, G5). The workflow refuses any ref other than `refs/heads/main` (`core-release-registry-publish.yml:62`, `:95`) | Not met. Of the six, only M1 is on `main` at the local `origin/main` ref `c041f59d` (not re-fetched for this refresh); M2 to M6 and every lane and group added for 2.6 are only on `release/2.6.0`. Merging the release branch into `main` is Josh's Ready action; it is not requested here. M3 has no manifest map entry (§5A.4) |
| PC3 | Reviews are complete for every included row (G1; §8), and the persisted-state gate passes (G6; §7.3) | The six profile PRs are merged into `release/2.6.0` (§5A.3). The release PR's review and G6 are not complete |
| PC4 | Each included profile has a lane PASS on the signing commit, loading the exact fingerprint to be signed, with cleanup `clean` true (G4). R8 (§9C): every included lane, HAMCP-134's included, re-runs once on the signing commit, and receipts bind that commit. Receipts that exist today are review evidence only | Not met for any profile. The committed receipts bind source commit `5fc40f93`, not the signing commit (§5A.4) |
| PC5 | Fingerprints are recomputed on the signing commit and equal the reviewed table. All then-current signed references still match the compiled profiles (G3). R-d (§9D): the architect recomputes all 38 on the `release/2.6.0` to `main` merge commit before signing | True at `3bd5fe3f` [R]: 38 compiled, equal to the `4d68e4d8` values, and the 32 signed references match. Must be repeated on the signing commit |
| PC6 | The predecessor is re-fetched, verified against the public key, unexpired, and still the one the lanes ran against: sequence 17 with exactly 32 references on 2026.11.0 (G7; each lane requires it: `scripts/core_security_metadata_local.py:143-145`, `scripts/core_marketplace_read_local.py:143`, `scripts/core_created_object_local.py:166`, `scripts/core_thread_matter_topology_local.py:151`, `scripts/core_device_configuration_local.py:133-135`) | The local `origin/registry` ref is still `e486028e` (journal SHA-256 `3f79fce7…`, §5A.2), and every committed receipt binds that journal [R]. The re-fetch and signature check repeat at preparation |
| PC7 | Preparation is no later than 2027-01-22 (§5.4) | 70 days away on 2026-11-13 |
| PC8 | Authorization for this exact transition follows R10 (§9C): agents prepare the two files and the expected-values sheet; one agent makes the single candidate push, and only after Josh approves both SHA-256 values; Josh dispatches the dry run and the real run. No agent dispatches a workflow. R-f (§9D, §5A.10): Josh signs only after the R-d recomputation, every included lane passing on the signing commit, and the candidate check against R-b | The route is decided (R10). Josh's approval of both SHA-256 values does not exist yet, because the files are not prepared. Nothing is pushed or dispatched |
| PC9 | Lanes are heavy jobs on the build host and follow its admission limits | Applies when lanes run |
| PC10 | **Closed reference field set (R-e, §9D).** Every reference in the sequence 18 candidate, carried and added, has exactly the five fields beta.7 accepts and no other: `capability_id`, `profile_id`, `profile_version`, `adapter_id`, `contract_fingerprint`. Source: `git show v2.4.2-beta.7:hass_mcp_engineering_beta/ha_mcp_engineering/ha_core_readmission/registry_models.py`, `class CoreProfileReference` (`:31-56`), whose `from_mapping` requires that exact set at `:41-44` through `_exact_mapping` (set equality, `signed_registry/models.py:149-156` at the same tag), raising `ENTRY_EVIDENCE_INVALID`. `CoreReleaseEntry.from_mapping` parses every reference at `:84`, so one unknown field rejects the whole entry. On a rollback to beta.7 that falls back to the cached sequence 17 or, with an empty cache, loses every Core-native capability [S] | A check on the prepared candidate; there is no candidate yet. Both files are unchanged between the tag and `3bd5fe3f` [R]. The §5A.5 draft uses exactly these five fields |

### 5A.8 Candidate branch and dispatch steps (decided route; described, not executed)

**Decided (R10, §9C).** Josh, in session, 2026-11-10: "Seq 18 candidate
branch: the documented manual flow (prepare_core_release_registry.py
extend-capabilities), as seq 16/17. Agents prepare the candidate and
evidence files and the expected-values sheet. After Josh approves both
SHA-256s, one agent makes the single push of
registry-candidates/2026.11.0-seq18 and nothing else. Josh dispatches the
publish dry run and the real run. Replaces ruling 4 of the previous block."

What that fixes:

- The route is the manual flow of `docs/CORE_RELEASE_REGISTRY.md`, "Manual
  flow for each publication" (`:372-403`), with
  `scripts/prepare_core_release_registry.py extend-capabilities`, as
  sequences 16 and 17 used (§5A.14).
- R10 replaces the earlier ruling R9, "No agent pushes
  registry-candidates/", **for this one push only**: one agent, one push,
  of `registry-candidates/2026.11.0-seq18`, and only after Josh has
  approved both SHA-256 values. No agent pushes any other
  `registry-candidates/` ref, and none pushes this one a second time.
- No agent dispatches any workflow, as a dry run or otherwise. Both
  dispatches of `core-release-registry-publish.yml` are Josh's.
- `core-release-evidence.yml` is not used for sequence 18 (§5A.14).

| Step | Who | Needs first |
| --- | --- | --- |
| 1-5: fix the commit, re-run the lanes, fetch the predecessor, prepare, check and hash | Agents | The preconditions of §5A.7 |
| Approval of both SHA-256 values | Josh | The step 5 hand-off and the §5A.15 sheet |
| 6: the single push of `registry-candidates/2026.11.0-seq18` | One agent | Josh's approval of both SHA-256 values |
| 7: publication dry run, compared with §5A.15 | Josh | Step 6 verified on the remote |
| 8: real run, and approval of the `core-release-registry-signing` environment | Josh | A dry run that matches the sheet |
| 9: verification (§5A.9) | Agents, read-only | Publication |

Nothing here was run for this plan.

1. **Fix the signing commit.** Record the `main` commit SHA: the
   `release/2.6.0` to `main` merge commit. R-d (§9D): the architect
   recomputes all 38 fingerprints on that commit before signing (§5A.3
   command). Stop if any differs from the reviewed table (sheet A).
2. **Re-run every included lane once on that commit** (R8), HAMCP-134's
   included, under the host's heavy-job limits, following each lane's own
   README (for M2, `docs/evidence/hamcp134/lane/README.md`). Each receipt
   must show PASS, cleanup `clean`, the signing commit, the manifest hash
   of that commit, the current journal hash (§5A.2; re-checked in step 3)
   and the fingerprint from step 1. A receipt that binds an older commit is
   not used. R-a (§9D): a profile whose lane fails on the signing commit
   is dropped from sequence 18 and the others proceed; sequence 18 is not
   held for it. For M1 that means sequence 19 (R6).
3. **Fetch and verify the predecessor.** Fetch `origin/registry`, hash the
   journal, verify it with the committed public key. Confirm PC6.
4. **Prepare the candidate** with the existing utility. It fetches nothing,
   signs nothing and changes nothing in the repository
   (`docs/CORE_RELEASE_REGISTRY.md:217-219`, `:259-267`):

   ```sh
   python scripts/prepare_core_release_registry.py extend-capabilities \
     --entry core-entry.json --evidence core-review-evidence.json \
     --public-key docs/evidence/hamcp222/core-public-key.txt \
     --previous current-core-journal.json --output core-candidate.json
   ```

   Preparation runs C1, C2, C4 and C10 and refuses on any violation.
5. **Check, hash, fill in the sheet, and stop.** Agents check
   `core-candidate.json` and `core-review-evidence.json` against §5.3 tests
   1, 4 and 5: one changed entry, the 32-reference prefix intact, only the
   approved references added, sequence 18, predecessor hash equal to the
   sequence 17 digest. They record both files' SHA-256 values
   (`docs/CORE_RELEASE_REGISTRY.md:224-225`, `:383`), fill in every pending
   cell of the expected-values sheet (§5A.15), keep both complete files,
   and hand the two SHA-256 values and the sheet to Josh. Nothing is pushed
   at this step.

   **Josh's approval.** Josh reviews the two files and the sheet and
   approves **both** SHA-256 values. This is the byte-review gate the
   manual flow requires before the push
   (`docs/CORE_RELEASE_REGISTRY.md:376-382`). Without it there is no push.
6. **The single push (one agent; only after Josh has approved both SHA-256
   values).** One agent pushes `registry-candidates/2026.11.0-seq18`
   exactly once: a new branch whose tree holds exactly `core-candidate.json`
   and `core-review-evidence.json` at its root, as regular files, and
   nothing else, as the sequence 16 and 17 candidate branches do (§5A.14;
   the workflow requires regular files,
   `.github/workflows/core-release-registry-publish.yml:110-112`). No other
   ref is pushed. The push is never forced, and the branch is never deleted
   or moved afterwards: it is permanent
   (`docs/CORE_RELEASE_REGISTRY.md:744-752`). Before pushing, the agent
   hashes the local files again and stops if either differs from the value
   Josh approved. After pushing, it reads the two blobs back from the
   remote, confirms that their SHA-256 values equal the approved ones, and
   reports Josh's dry-run command (step 7). It does not dispatch it.
7. **Dry run (Josh).** Josh dispatches *Core registry one-tap signing and
   publication* from `main` with `dry_run=true`
   (`docs/CORE_REGISTRY_ONE_TAP_RUNBOOK.md:140-162`). The command is
   Josh's; no agent runs it:

   ```sh
   gh workflow run core-release-registry-publish.yml --ref main --repo jeter-1/hass-mcp-admin \
     -f core_version=2026.11.0 -f candidate_ref=registry-candidates/2026.11.0-seq18 \
     -f candidate_sha256=<approved candidate SHA-256> -f evidence_sha256=<approved evidence SHA-256> \
     -f dry_run=true
   ```

   Expected: `verify`, `dry-run-sign` and `dry-run-publish` succeed; no
   approval request; the log ends "nothing was pushed"; `sign`, `reverify`
   and `publish` are skipped. Josh compares the run's summary and its
   dry-run artifact with the expected-values sheet (§5A.15). Any
   difference is a stop (§5A.11). Then
   `bash scripts/check_core_registry_branch.sh cutover` shows the same
   journal hash as before. The dry run uses an ephemeral test key. It is the
   only proof of the combined transition (§5A.13 Q8).
8. **Real run (Josh).** Josh dispatches the same inputs with
   `dry_run=false`. The `verify` job writes the approval summary:
   predecessor and new sequence, both SHA-256 values, expiry, profiles
   carried and added, and a WITHHELD block if the candidate drops a
   predecessor profile (`docs/CORE_RELEASE_REGISTRY.md:387-394`). Josh
   compares both hashes and the sequence with the approved values and the
   sheet, and approves the `core-release-registry-signing` environment only
   if they match; otherwise he rejects and nothing is signed
   (`docs/CORE_REGISTRY_ONE_TAP_RUNBOOK.md:165-174`). `sign`, `reverify` and
   `publish` then run; `publish` makes one commit on `registry` touching
   only the journal and the new evidence file, without force
   (`docs/CORE_RELEASE_REGISTRY.md:398-403`).
9. **Verify** (§5A.9).

### 5A.9 Verification after signing

| Check | How | Expected |
| --- | --- | --- |
| Publication outcome | The `publish` job summary; `bash scripts/check_core_registry_branch.sh last-publish` (`docs/CORE_REGISTRY_ONE_TAP_RUNBOOK.md:187-192`) | PUBLISHED, and "the tip changes only the journal and adds" one evidence file |
| Journal tip | Fetch `origin/registry`; parse the journal | Sequence 18; `previous_registry_sha256` is the sequence 17 digest; `expires_at` is `generated_at` plus 90 days (`prepare_core_release_registry.py:248`) |
| The extended entry | Compare the 2026.11.0 entry with sequence 17 | First 32 references identical in order; then exactly the approved references; source, image and probe identity unchanged |
| Siblings | Compare the other entries and revocations | 2026.10.0 (22), 2026.10.1 (26) and 2026.10.3 (31) identical; three revocation records unchanged |
| Offline runtime verifier | As in §2.5, against the new bytes and the committed public key | Refresh accepted at sequence 18; 2026.11.0 selects 32 plus the added count; the 2026.10.x counts are unchanged (§5.3 test 7) |
| Next automated run is not broken | Every reference of the new entry is in exactly one group (C7, G5) | `passing_subset` does not raise "unmapped or duplicate predecessor profile" (`scripts/core_release_evidence.py:195-207`) |
| Runtime sees it | Core health `release_registry` after the next six-hour refresh or an add-on restart Josh authorizes (`docs/CORE_REGISTRY_ONE_TAP_RUNBOOK.md:194-196`) | New `sequence` with `refresh_status: accepted`. This is a live observation and needs its own authorization; it is not part of this plan |

Admission in the registry is not deployed acceptance. Each feature's
deployed acceptance stays a separate, separately authorized step.

### 5A.10 Rollback and recovery

§7 governs. Applied to this signing:

| Point of failure | State | Recovery |
| --- | --- | --- |
| Preparation refuses | Nothing exists outside the working directory | Fix the cause and prepare again |
| The check, or Josh's review, finds a defect before the push | Nothing is on the remote | Prepare again and give Josh the new SHA-256 values. An approval covers only the bytes it names |
| An included profile's lane fails on the signing commit | Nothing is prepared with that profile | R-a (§9D): drop that profile from sequence 18; the others proceed. For M1, R6 sends it to sequence 19. A dropped lane's predecessor checks must change before a later run (§5A.13 Q15) |
| After the push, the remote blobs do not hash to the approved values | The permanent branch holds unapproved bytes; nothing is signed | Stop and report. The workflow refuses a file whose bytes do not hash to its inputs (`core-release-registry-publish.yml:117-120`). Do not force, delete or push again. A second candidate is pending Josh (§5A.13 Q10) |
| Dry run fails, or differs from the §5A.15 sheet | Nothing signed, nothing published | Find the cause first. The pushed candidate branch is permanent and R10 allows one push, so a corrected candidate needs a new branch name and a new authorization. Source names only `registry-candidates/<version>-seq<sequence>`; the name for a second sequence 18 candidate is not defined. Both are pending Josh (§5A.13 Q10) |
| Hashes or sequence in the approval summary differ | Nothing signed | Josh rejects the environment approval |
| `sign` or `reverify` fails | Nothing published | Investigate; do not re-dispatch until the cause is known |
| `publish` ends UNCHANGED | `registry` not changed | Fix the cause and dispatch again |
| `publish` ends UNCERTAIN | Unknown | Run the `last-publish` check and compare the tip with the attempted commit. Never dispatch again before knowing which commit `registry` holds (`docs/CORE_REGISTRY_ONE_TAP_RUNBOOK.md:203-206`) |
| A signed profile later proves wrong | The reference stays signed | No operation removes one reference (C2, C3; §7.2). Disable the tool in the runtime and keep the compiled contract on `main`, or C4 refuses every later positive signing. Never restore an old journal or delete a cache (`docs/CORE_RELEASE_REGISTRY.md:287`, `:296-297`) |
| Whole-release withdrawal | Permanent | `revoke` of 2026.11.0 removes all its capabilities for good (C6; §7.4). Not proposed. See R-f below |
| Stop all runs | n/a | Josh can disable the workflow or remove the key (`docs/CORE_REGISTRY_ONE_TAP_RUNBOOK.md:176-185`). Neither changes the published journal |

**Signing sequence 18 is one-way (R-f, Josh, §9D).** There is no
per-capability removal. The only undo is revoking the whole 2026.11.0
entry, which withdraws all its capabilities, and they cannot be re-added.
Recovery is then a new Core-version binding (HAMCP-367). Josh signs only
after: the architect recomputes all 38 fingerprints on the signing commit
(R-d; §5A.8 step 1); every included lane passes on that commit (R8, R-a;
§5A.8 step 2); and the candidate is checked against R-b (§5A.5). R-f also
asks for this statement in the release notes and on #395; this plan is
#395's document. The release notes are not part of this change.

An older Engineering image ignores the new references (C8; §7.1), provided
every reference has only the closed field set beta.7 accepts (PC10).
Persisted-state safety across a downgrade is G6 and is not shown by this
draft.

### 5A.11 Stop conditions

Stop and report to Josh, without working around it, if any of these occurs:

1. `origin/registry` is no longer at sequence 17, or its 2026.11.0 entry no
   longer has exactly the 32 references of §2.3. The sequence is then not
   18, every lane's predecessor check fails, and the lanes and this draft
   must be redone against the new predecessor (G7). The Core 2026.11.1
   binding (HAMCP-367, outside this plan) is one such case if it is
   signed first.
2. A fingerprint recomputed on the signing commit differs from the reviewed
   value, or any signed reference no longer matches its compiled profile.
3. A lane does not PASS, its cleanup is not `clean`, or its receipt binds a
   different commit, manifest, journal or fingerprint than the candidate
   uses. No substitute lane is allowed
   (`docs/CORE_REGISTRY_ONE_TAP_RUNBOOK.md:272-273`). The decided
   exception is R-a (§9D): a profile whose lane fails is dropped from
   sequence 18 and the others proceed (for M1, to sequence 19 per R6).
   The stop applies when a dropped profile's reference would still be in
   the candidate.
4. An included profile, lane, group or manifest map is not on `main`.
5. Preparation, the dry run, `verify`, `sign` or `reverify` refuses or
   fails.
6. The approval summary shows a WITHHELD block, a sequence other than the
   expected one, or a hash that differs from the approved value; or the
   dry run differs from the §5A.15 sheet in any expected value.
7. `publish` ends UNCERTAIN.
8. The candidate would change any entry other than 2026.11.0, or reorder,
   drop or alter one of the 32 signed references.
9. The 2027-01-22 cutoff arrives with a row not ready (§8 late-row rule), or
   the predecessor has expired (§5.4).
10. If HAMCP-297's G4 lane or its G5 gate cannot pass before the cutoff,
    core.device_configuration_entity_operation is dropped from sequence 18
    and the other profiles proceed (ruling R-a); Josh is told before
    signing. (Ruling SA-2, §9D. R3, §9B, does not say it overrides R-a.)
11. A step would need a secret, the signing seed, live Home Assistant, a
    workflow or protected-path change, or an authorization that Josh has
    not given.
12. An agent would have to dispatch a workflow, push any
    `registry-candidates/` ref other than the single approved push of
    §5A.8 step 6, push that ref a second time, or force or delete it.
13. The single push is about to happen without Josh's approval of both
    SHA-256 values, or the files no longer hash to the approved values.
14. Any reference in the candidate, carried or added, has a field outside
    the closed set beta.7 accepts, or lacks one of its five fields (R-e,
    §9D; PC10): `CoreProfileReference.from_mapping` at
    `git show v2.4.2-beta.7:hass_mcp_engineering_beta/ha_mcp_engineering/ha_core_readmission/registry_models.py`
    `:41-44`. beta.7 rejects the whole entry for one unknown field.

### 5A.12 Merge facts for the six 2.6 profiles (refreshed 2026-11-13)

**Refreshed 2026-11-13 to `3bd5fe3f` [R].** The table of unmerged
profiles that stood here on 2026-11-10 is removed: every PR it listed is
merged into `release/2.6.0`. Each fact below was read with `git log
--first-parent` on `release/2.6.0` at `7d2989ad` and checked as an ancestor
of `3bd5fe3f` with `git merge-base --is-ancestor`. Times are CST.

| PR, register | Merge commit into `release/2.6.0` | Merged | What it brought for sequence 18 |
| --- | --- | --- | --- |
| #423, HAMCP-134 | `6e65ba4d83e9e9f365fc853fa31c56d0be1a35aa` | 2026-11-10 08:21 | M2 profile, lane and group |
| #428, HAMCP-297 | `fcd6973f14dce01dd3e1fc3d59738a12cd9365c3` | 2026-11-10 15:30 | M3 profile |
| #421, HAMCP-290 | `29339009759331118d0402d0be4aaf9e1c13fe26` | 2026-11-11 08:33 | M1 lane and group (the M1 profile came by #409 and #383; §5A.3) |
| #424, HAMCP-329 / HAMCP-298 | `ccaed9ef2183cff2796b835a052de8a88db12229` | 2026-11-11 11:21 | M4 and M5 profiles, their lane and groups |
| #427, HAMCP-231 | `a860671f2593ae9a39a97fff8c30effe0c79016b` | 2026-11-11 16:21 | M6 profile, A1 and A2 lanes and group |
| #434, HAMCP-366 | `1bb864b767531ce311c2cb2c997c48c900273bd8` | 2026-11-11 19:12 | M3 lane and group |
| #435, HAMCP-364 | `a9144591` | 2026-11-12 11:22 | The seven committed receipts, all of source commit `5fc40f93` (commit `a5068241`; §5A.4) |

**Rule kept for every row:** every included lane re-runs once on the
signing commit (R8, §9C), and the fingerprint is recomputed there (R-d,
§9D). The committed receipts bind `5fc40f93` and are review evidence only.

**Row removed (2026-11-10).** The table had a row for #425 (HAMCP-295 /
HAMCP-109, entity rename). R12 (§9C) moved that work to 2.7, and R-g
(§9D) keeps it out of 2.6; see §5A.5 Exclusions. #425 is not merged.

Profile ids follow C9 from each capability id
(`profiles.py:120`). They equal the derived ids in §4.

Notes that affect signing [R]:

- The two copies of the HAMCP-290 lane files that #421 and #427 carried
  are now one: at `3bd5fe3f` there is a single
  `docs/evidence/hamcp290/lane/marketplace_read.json`, sha256
  `381358d6…`, rewritten by `a5068241` and bound to `5fc40f93` (sheet A).
- The per-PR contract-read manifest hashes recorded on 2026-11-10
  (`b058369d…`, `02a555cb…`, `913f8a32…`, and the then tip `07bc9034…`)
  are superseded. At `3bd5fe3f`, `ci/core-contract-read-files.json` hashes
  to `48b8b87590a7c023f6ff9f44ff05abcab4fbb9aa3f597d57c2a131a149cfbbed`,
  and every committed receipt binds that value (§5A.4; sheet A).
- Every lane proves a 32-to-33 transition for its own profile only
  (§5A.13 Q8).

### 5A.13 Mismatches and open questions

Nothing below is decided by this plan. "Pending Josh" marks a question only
he can answer. Rows that R6-R12 (§9C) answered on 2026-11-10 say so in
their status; the finding text is kept as first written.

| # | Finding | Evidence | Status |
| --- | --- | --- | --- |
| Q1 | **Is `core.marketplace_read` in the combined sequence 18 extension?** The recorded decision says no: "the profile is signed separately after 2.5 merges (Josh decision, 2026-11-08)", and this plan's §6 keeps HAMCP-290 out of the 2.6 batch. Later records say yes: #421's lane README calls the profile "proposed for signed Core sequence 18"; #421's body says "in the combined 2.6 signing (seq 18)"; the 2026-11-10 rulings say "seq 18 combined signing covering all new 2.6 profiles" and "add #421 … profiles as each lands". No record seen by this author says the 2026-11-08 decision was withdrawn. The choice sets the count (34 or 35 today) and, if HAMCP-290 is signed separately and first, moves this extension to sequence 19 | `docs/HAMCP_290_MARKETPLACE_READ_DESIGN.md:434-437`, `:454-455` [S]; §6; `docs/evidence/hamcp290/lane/README.md:3-7` at `e443b824` [S]; #421 body [H]; §9B [H] | **Answered 2026-11-10 (R6, §9C):** it is in the combined sequence 18, which replaces the 2026-11-08 separate-signing decision. Fallback: sequence 19 if its lane cannot produce a clean receipt on the signing commit. §6 carries a superseded note. The HAMCP-290 design text is not edited by this plan |
| Q2 | M1 has no merged signing prerequisite. The lane, group, manifest map and receipt are only in open #421, and the receipt binds a commit older than the head | §5A.4, §5A.12 | **Closed 2026-11-13 [R]:** #421 merged (`29339009`); the receipt is committed and binds `5fc40f93`. The lane re-runs on the signing commit (R8); if it fails there, R-a and R6 apply |
| Q3 | Does the "re-run before signing" ruling, recorded for #421, apply to every lane receipt, HAMCP-134's included? The HAMCP-134 receipt binds `bf3c9884`, and its manifest hash matches the tip only until the next §5A.12 merge | §5A.4; the build handoff lists the manifest point as open and not investigated [H] | **Answered 2026-11-10 (R8, §9C):** every included lane, HAMCP-134's included, re-runs once on the signing commit, and receipts bind that commit |
| Q4 | M3 has no lane, group, manifest map or receipt, and source does not say which pull request builds them. The recorded ruling puts HAMCP-297 in sequence 18 after its G4 lane passes on the reviewed final head | §5A.4; `docs/HAMCP_297_DEVICE_CONFIGURATION_ENTITIES_DESIGN.md:1462-1496` [S]; #428 body [H] | **Answered 2026-11-10 (R7, §9C).** 2026-11-13 [R]: #434 (HAMCP-366, `1bb864b7`) merged the lane script, evidence group and receipt (§5A.4). The contract-read manifest has no entry for M3 (§5A.4, open against G5). If the lane cannot pass before 2027-01-22, the recorded stop applies (R3); R-a (§9D) governs a failure on the signing commit |
| Q5 | M2, M3 and all 2.6 lane plumbing are on `release/2.6.0` only. `main` (`c041f59d`) compiles 33 profiles and maps 32 capabilities in 13 groups; the tip compiles 35 and maps 33 in 14 | [R] | Precondition PC2. Needs the release merge, which is Josh's. 2026-11-13 [R]: `3bd5fe3f` compiles 38, its contract-read manifest maps 37 capabilities (all but M3) plus `binding_judgments`, and its evidence-group manifest has 19 groups (sheet A) |
| Q6 | #424's body recommends holding both of its profiles out of sequence 18. That text names an older head (`6a4f0c1a`) that lacked the lanes; the current head has the lane script and groups. Its receipts are not committed and bind `b89ce817`, not the head | #424 body [H]; §5A.12 [R] | **Closed 2026-11-13:** #424 merged (`ccaed9ef`), both receipts are committed and bind `5fc40f93` [R], and R-a (§9D) puts both profiles in sequence 18, each conditional on its lane passing on the signing commit. R8 still applies |
| Q7 | #427's A1 receipt binds a manifest that is not its head's, and #427 carries a second copy of the HAMCP-290 lane files. (The #425 part of this row was removed on 2026-11-10: R12 moved that work to 2.7) | §5A.12 [R], [H] | **Closed 2026-11-13 [R]:** at `3bd5fe3f` there is one HAMCP-290 receipt, and both #427 receipts bind the current contract-read manifest `48b8b875…` (§5A.4). R8 still applies |
| Q8 | No lane proves the combined transition. Each lane proves 32 to 33 for one profile and refuses unless the predecessor has exactly 32 references. So every lane must run before sequence 18 is published, and a profile signed later (a late row) needs its lane's predecessor check changed first | `scripts/core_security_metadata_local.py:143-145` [S]; `docs/evidence/hamcp134/lane/README.md:22-25` [S] | Open. The combined transition is proven only by preparation and the one-tap dry run (G8). Whether that is enough is for the plan's reviewer |
| Q9 | G4 (§5.2) still describes the HAMCP-297 lane as using a build-time test seam, "Open for Codex (#389 Q9 seam)". The merged design says the G4 run "contains no test seam" and uses a Matter Server stub instead. G4's text predates that | `docs/HAMCP_297_DEVICE_CONFIGURATION_ENTITIES_DESIGN.md:1476-1484` [S] | Flagged. G4 is not edited in this revision, because G1-G8 are under review; the reviewer should confirm the seam question is closed |
| Q10 | Who prepares the candidate and pushes the candidate branch is not recorded, and no name is defined for a second sequence 18 candidate if the first is pushed and then found wrong | `docs/CORE_RELEASE_REGISTRY.md:377-382`, `:744-752` [S] | **Answered 2026-11-10 (R10, §9C)** for the route: agents prepare, Josh approves both SHA-256 values, one agent makes the single push, Josh dispatches (§5A.8). One contingency stays open: R10 allows one push, so if that candidate proves wrong, a second sequence 18 candidate and its branch name are **pending Josh** |
| Q11 | The order of added references is not set by source | §5A.5 | **Answered 2026-11-13 (R-b, §9D):** the 32 signed references in signed order, then the six added in compiled order (§5A.5) |
| Q12 | #421's README and #423's README both say "proposed for signed Core sequence 18". Both hold only while nothing else is signed first | §5A.2 | Covered by stop condition 1 |
| Q13 | The HAMCP-295/109 design still says its cutoff moves the work to 3.0. R12 says 2.7, as Josh's change to the design's cutoff text | `docs/HAMCP_295_109_ENTITY_RENAME_DEPENDENT_EXECUTION_DESIGN.md:1258-1264` [S]; R12 (§9C) | Flagged. This plan does not edit the design. The build handoff lists the amendment as an open follow-up [H] |
| Q14 | R11 separates the Core 2026.11.1 binding (HAMCP-367) from sequence 18. It does not say which is signed first. If 2026.11.1 is signed first, it takes sequence 18 and this extension becomes 19 against a new predecessor | R11 (§9C); register HAMCP-367 [H]; §5A.11 item 1 | **Pending Josh**, together with HAMCP-367's target and start. Outside this plan; listed because it can move this plan's sequence number |
| Q15 | The R6 fallback names sequence 19. M1's lane refuses unless the predecessor is sequence 17 and its 2026.11.0 entry has exactly 32 references, so both checks must change before the lane can run against a sequence 18 predecessor. And "19" holds only if nothing else is signed in between | `scripts/core_marketplace_read_local.py:132-133`, `:143` at `e443b824` [S] | Open. A consequence of R6 for the build, not a product question |

### 5A.14 Why `core-release-evidence.yml` is not used for sequence 18 (history: R9 against source)

This is history, not an open question. R9 (§9C), given earlier on
2026-11-10, asked for the candidate branch and the signing to come from
the existing workflows: `core-release-evidence.yml` from `main`
(`dry_run=true` first, then `false` by Josh), then
`core-release-registry-publish.yml` with reviewed SHA-256 values through
the `core-release-registry-signing` environment. It also asked this plan
to confirm that `main`'s workflows match that and that sequence 17
followed that path, and to report any difference. The check found
differences. R10 then replaced R9 with the manual flow (§5A.8). The
findings are kept here as the recorded reason.

Source citations in this table are to `origin/main` at
`c041f59d77665706251fc041bc56dddc972be848` [S]. The eight workflow, script
and evidence files cited are byte-identical at this branch's head, and
`docs/CORE_RELEASE_REGISTRY.md` differs from `main` only at `:609` and
`:611` [R].

| # | What R9 assumed | Found | Evidence |
| --- | --- | --- | --- |
| 1 | `core-release-registry-publish.yml` takes reviewed SHA-256 values and signs only through the protected environment | **Matches.** Its inputs are `core_version`, `candidate_ref`, `candidate_sha256`, `evidence_sha256` and `dry_run` (default `true`). It refuses any ref but `main` and any `candidate_ref` outside `registry-candidates/`. It refuses a candidate or evidence file whose bytes do not hash to the inputs. Only the `sign` job names the environment, and it runs only when `dry_run` is `false` | `.github/workflows/core-release-registry-publish.yml:9-31`, `:62`, `:95-99`, `:117-120`, `:178-183` |
| 2 | The same | **Not confirmed:** the environment's protection settings on GitHub (required reviewer, deployment branch). They were not queried, because that would read the environment's configuration. Source describes them | `core-release-registry-publish.yml:176-177`; `docs/CORE_RELEASE_REGISTRY.md:320-321`, `:330-336` |
| 3 | The same | **Addition.** The workflow also declares `workflow_call`, but its jobs require the `workflow_dispatch` event | `core-release-registry-publish.yml:32-48`, `:62`, `:180` |
| 4 | `core-release-evidence.yml` can produce the sequence 18 candidate branch | **Does not match.** It is the pipeline for a new Core version. Its selection returns nothing for a version the journal already holds. Its candidate is an `add` of the passing subset of the predecessor's profiles, not an `extend-capabilities`. The branch it names is `registry-candidates/<version>`, with no `-seq` suffix. It has no route to a 2026.11.0 extension | `scripts/core_release_evidence.py:118-134`, `:195-207`, `:264-272`; `scripts/core_evidence_workflow.py:248-258`, `:336-354`; `scripts/core_evidence_handoff.py:34-37`, `:65-73` |
| 5 | Dry run first, then a real run by Josh | **Different.** The `dry_run` input (default `true`) gates the handoff only for a manual dispatch. On every scheduled run the handoff job is eligible, with `DRY_RUN: 'false'` set in the job, and it dispatches the publication workflow with `dry_run` false. Signing still waits for the environment approval | `.github/workflows/core-release-evidence.yml:6-7`, `:10-17`, `:387-393`, `:402` |
| 6 | Sequence 17 followed that path | **It did not.** Sequences 16 and 17 used the manual flow. The evidence workflow has never been dispatched by hand: 98 runs, of which 90 `pull_request`, 8 `schedule` and 0 `workflow_dispatch` (read at about 21:40 CST). The publication workflow has exactly four runs, all `workflow_dispatch` by `jeter-1` on `main`: 42024277799 (dry) and 42025721763 (real) on 2026-11-05 for sequence 16; 42348993373 (dry) and 42349190978 (real) on 2026-11-07 for sequence 17 | [G] |
| 7 | The same | Each candidate branch is one root commit with no parent, holding exactly `core-candidate.json` and `core-review-evidence.json`: `registry-candidates/2026.11.0` at `c2aa0aec60368ee1e6f307d64e6a9a5e0076a5ac` (2026-11-05) and `registry-candidates/2026.11.0-seq17` at `aa227ce2ba1bdda034311217d5af425b801bde65` (2026-11-07). The git identity on both is `jeter-1`. A git identity does not show who ran the push | [G] |
| 8 | The same | The sequence 17 contract reads as the preparer making one push after written approval of both hashes, then reporting the dry-run command without dispatching it. R10 keeps that shape | `docs/evidence/hamcp315/README.md:238-245` |
| 9 | Not in R9 | Core 2026.11.1 was published on 2026-11-09 at 12:52 CST. Scheduled run 42688632541 (13:59 CST) ended `core-release-refused-2026.11.1`, "binding PR required", with the handoff skipped. So the evidence workflow's next candidate would be for 2026.11.1, not for this extension. R11 puts that binding in HAMCP-367 | [G]; R11 (§9C) |

### 5A.15 Expected-values sheet (for the dry-run comparison)

Agents fill this sheet in at §5A.8 step 5 and hand it to Josh with the two
SHA-256 values. Josh compares it with the dry run's summary and artifact
(step 7) and with the real run's approval summary (step 8). A cell that
reads "pending: X" has no value yet; X is what produces it. Nothing in
this sheet is estimated.

**A. Profiles added.** One row per profile in the candidate, in R-b's
order (§5A.5). Refreshed 2026-11-13 to the release head `3bd5fe3f`
(R-d, §9D). All six are merged into `release/2.6.0` (§5A.12). R-a: each
is in sequence 18 only if its lane passes on the signing commit; a row
whose lane fails is dropped and the others proceed. The fingerprint is
the compiled value at `3bd5fe3f` [R]. "Review receipt" is the committed
receipt at `3bd5fe3f`, with the source commit and contract-read manifest
it binds [R]; its SHA-256 is that file's hash at `3bd5fe3f` [R].

| Profile id; version | Capability id | Adapter id | Compiled fingerprint at `3bd5fe3f` [R] | Lane; evidence group [S] | Review receipt; binds [R] | Receipt SHA-256 [R] |
| --- | --- | --- | --- | --- | --- | --- |
| `core_marketplace_read_v1`; 1 | `core.marketplace_read` | `compiled-core-marketplace-read-v1` | `sha256:9393b59801d97786e3ac8cf2b44c1aec121198568bfb017cabaa5691acc0cfeb`; the same on `main` `c041f59d` | `scripts/core_marketplace_read_local.py`; `marketplace_read` (`ci/core-evidence-groups.json:60-62`) | `docs/evidence/hamcp290/lane/marketplace_read.json`; source `5fc40f93`, manifest `48b8b875…` | `381358d630ff9fc83ce7d2c87e4d33507aad3f9cdfd9872f8ecc3e2883ca7142` |
| `core_security_metadata_read_v1`; 1 | `core.security_metadata_read` | `compiled-core-security-metadata-read-v1` | `sha256:9aa788b50f6d9544808bd8b89fd50c00ffd32e7ebcdd940b9dddace18b2e9c55` | `scripts/core_security_metadata_local.py`; `security_metadata` (`:75-77`) | `docs/evidence/hamcp134/lane/security_metadata.json`; source `5fc40f93`, manifest `48b8b875…` | `a7f0697af9d350b69a122d540e5b5af29e5090a0779edebb3264c7a66e8d4a47` |
| `core_device_configuration_entity_operation_v1`; 1 | `core.device_configuration_entity_operation` | `compiled-core-device-configuration-entity-operation-v1` | `sha256:74a03a640f60c9adaf8508a0a7fcbca7745f29b3d09e1dc9a611ac361475293c` | `scripts/core_device_configuration_local.py`; `device_configuration_entity` (`:72-74`). No entry in `ci/core-contract-read-files.json` (§5A.4, open against G5) | `docs/evidence/hamcp366/device_configuration_entity.json`; source `5fc40f93`, manifest `48b8b875…` | `6345ff58a9fc798d3ece65b7436c7c64564a79bf5866de5502e9e873c38739d0` |
| `core_scene_create_delete_operation_v1`; 1 | `core.scene_create_delete_operation` | `compiled-core-scene-create-delete-operation-v1` | `sha256:93378ae0385528f16deb977a985b778ba2dd2d39be6f5cb397ef74f2060a400d` | `scripts/core_created_object_local.py`; `scene_create_delete` (`:66-68`) | `docs/evidence/hamcp364/lane/scene_create_delete.json`; source `5fc40f93`, manifest `48b8b875…` | `7320406f98b60c34499c2d10e71675981ffc3d8b0f080cdba53de131c7e57860` |
| `core_created_helper_delete_operation_v1`; 1 | `core.created_helper_delete_operation` | `compiled-core-created-helper-delete-operation-v1` | `sha256:3f2e681310a009a4fc853af6fa24f8eca1c671c7dd75bddbbc8b403fec9cdbf4` | `scripts/core_created_object_local.py`; `created_helper_delete` (`:69-71`) | `docs/evidence/hamcp364/lane/created_helper_delete.json`; source `5fc40f93`, manifest `48b8b875…` | `5e4d641def9c6e248ca88a22fbf3836c5c836f9b429270116dffd1b547897df6` |
| `core_thread_matter_topology_read_v1`; 1 | `core.thread_matter_topology_read` | `compiled-core-thread-matter-topology-read-v1` | `sha256:d03f005cfc0dbaaa03aad3e78d9147f83321bb3dec8015aa69edb93e358ebb6c` | `scripts/core_thread_matter_topology_local.py` (A1) and `scripts/core_thread_matter_topology_a2_local.py` (A2); `thread_matter_topology` (`:63-65`) | A1: `docs/evidence/hamcp231/lane/thread_matter_topology.json`; A2: `docs/evidence/hamcp231/lane/thread_matter_topology_a2.json`. Both: source `5fc40f93`, manifest `48b8b875…` | A1: `6bafb4b6eb49079435e9305f0e7f2a62a324b5579bcad69cc4a974561da2e2c2`. A2: `1b4506263090a3f56cba85841ce64cee66e1c323f51729364cadd3868f3826da` |

Every review receipt reads PASS with cleanup `clean` true, binds journal
`3f79fce7…` and the fingerprint in its row [R]. They are review evidence
only. R8 (§9C) is unchanged: every included lane, HAMCP-134's included,
re-runs once on the signing commit, and the receipts that count for
signing are the ones from those re-runs, which bind that commit (§5A.4,
§5A.8 step 2). The signing commit, its receipts and their SHA-256 values
do not exist yet and are not in this sheet.

**Manifests at `3bd5fe3f` [R].**

| File | SHA-256 | Contents |
| --- | --- | --- |
| `ci/core-evidence-groups.json` | `5474f77b2cc91d52875a5920d37ce495907e5b81cfe7a052da269f71c7985e87` | 19 groups, in this order: `general` (`:2-22`), `integration_inspection` (`:23-25`), `automation_baseline` (`:26-28`), `entity_metadata` (`:29-31`), `availability_exposure` (`:32-35`), `label_registry` (`:36-38`), `configuration_delete` (`:39-41`), `helper_options` (`:42-44`), `input_select_option` (`:45-47`), `script_traces` (`:48-50`), `dashboard_metadata` (`:51-53`), `scene_configuration` (`:54-56`), `helper_value` (`:57-59`), `marketplace_read` (`:60-62`), `thread_matter_topology` (`:63-65`), `scene_create_delete` (`:66-68`), `created_helper_delete` (`:69-71`), `device_configuration_entity` (`:72-74`), `security_metadata` (`:75-77`) |
| `ci/core-contract-read-files.json` | `48b8b87590a7c023f6ff9f44ff05abcab4fbb9aa3f597d57c2a131a149cfbbed` | The manifest every receipt binds (`manifest_sha256`). Its `profiles` map has 38 keys: 37 capabilities, every compiled one except `core.device_configuration_entity_operation`, plus `binding_judgments` |

Any change to either file before the signing commit changes its hash;
the R8 re-runs bind the signing commit's values.

**Architect-computed fingerprints (recorded 2026-11-11 at Josh's
direction).** These are the architect's values, reported by Josh. The
agents did not recompute them at the commits named here.

| Capability id | Architect-computed fingerprint | Commit | Status |
| --- | --- | --- | --- |
| `core.security_metadata_read` | `sha256:9aa788b50f6d9544808bd8b89fd50c00ffd32e7ebcdd940b9dddace18b2e9c55` | `6e65ba4d` (the HAMCP-134 merge) and `b810ba77` (the release/2.6.0 tip) | Same value at both commits. Still recomputed on the signing commit (R-d) |
| `core.device_configuration_entity_operation` | `sha256:74a03a640f60c9adaf8508a0a7fcbca7745f29b3d09e1dc9a611ac361475293c` | `b810ba77` | Provisional mark removed (R-c, Josh, 2026-11-13): ruling 4 closed with no ALLOWLIST change, and the value held through #434's merge. Still recomputed on the signing commit (R-d), like every row |

Both equal the compiled values in the M2 and M3 rows above, which the
agents computed at `3bd5fe3f` [R]. **R-d (Josh, §9D):** the architect
recomputes all 38 fingerprints on the `release/2.6.0` to `main` merge
commit, which is the signing commit, before signing (§5A.8 step 1).

There is no row for the entity-rename profile (R12, R-g; §5A.5
Exclusions).

**B. Envelope and summary values.** The left column uses the labels of
the summary that the `verify` job writes
(`scripts/core_registry_one_tap.py:198-231`), or names the file field.

| Summary line or field | Expected value | Basis |
| --- | --- | --- |
| Core version | `2026.11.0` | R11; §5.1 |
| Operation | `extend-capabilities` | R10; §5.1 |
| Predecessor sequence | `17` | §5A.2 [R] |
| Predecessor entry hash | `sha256:a39172d552c076698943e6fe0dbae0d75f328c4228189a5db274e009ec4124e7` | The sequence 17 tip's content digest, computed from the journal at `e486028e` [R] |
| Predecessor journal file SHA-256 (the candidate's `previous_journal_sha256`; `predecessor.json` in the inputs artifact) | `3f79fce76484ff9f9a9c070defe4d1d13865ccbfd24fda0ab8724515c8cc6a5d` | §5A.2 [R]; checked by `core_registry_one_tap.py:139-140` |
| Evidence file `previous_entry_sha256` | `2307c0e07e026afc157fb40af7a633242fbc664e70cb6252b953bc56abf26405` | Computed from the signed 2026.11.0 entry at sequence 17 [R] |
| New sequence | `18` | `scripts/prepare_core_release_registry.py:245` [S] |
| Compared with entry | `2026.11.0` | `core_registry_one_tap.py:114-122` [S] |
| Profiles carried (count) | `38`: the 32 signed plus the 6 added, if every included lane passes on the signing commit. R-a: a profile whose lane fails is dropped, and the count is 32 plus the number added | The summary lists every capability of the new entry (`core_registry_one_tap.py:178`); R-a (§9D) |
| Carried references | R-b rule 1: the first 32 are the 32 signed sequence 17 references in their signed order (§2.3), unchanged; not the first 32 compiled profiles (`core.typed_helper_value_operation` is compiled 20th and signed 32nd). Each has exactly the five beta.7 fields (PC10) | C2; R-b (§9D), recorded in §5A.5 |
| Withheld profiles | "Withheld profiles: none." A WITHHELD block is a stop | `core_registry_one_tap.py:219-226` |
| Profiles added (count and ids) | `6`, in R-b rule 2's order (compiled order): `core.marketplace_read`, `core.security_metadata_read`, `core.device_configuration_entity_operation`, `core.scene_create_delete_operation`, `core.created_helper_delete_operation`, `core.thread_matter_topology_read`. R-a: a profile whose lane fails on the signing commit is dropped and the others keep this order | §5A.5; R-a, R-b (§9D) |
| Other entries | 2026.10.0: 22 references; 2026.10.1: 26; 2026.10.3: 31; three revocation records. All unchanged | §5A.2 [R]; C1 |
| Generated | pending: the preparation time | Written by the utility |
| Expires | pending: Generated plus 90 days | `prepare_core_release_registry.py:247-248` |
| Candidate branch (`candidate_ref`) | `registry-candidates/2026.11.0-seq18` | R10 |
| Signing-input manifest SHA-256 | pending: each run's `verify` job computes it | `core-release-registry-publish.yml:159-164` |

If anything else is signed before sequence 18, the predecessor values in
this sheet change and the sheet is redone (§5A.11 item 1).

**C. The two SHA-256 values Josh approves.**

| Value | Expected | Where Josh sees it |
| --- | --- | --- |
| `core-candidate.json` SHA-256 | pending: preparation (§5A.8 step 4), recorded at step 5 | Workflow input `candidate_sha256`; summary line "Candidate SHA-256"; the remote blob read back at step 6 |
| `core-review-evidence.json` SHA-256 | pending: preparation (§5A.8 step 4), recorded at step 5 | Workflow input `evidence_sha256`; summary line "Evidence SHA-256"; the remote blob read back at step 6; the evidence file name in the dry-run artifact, `upstream-trust/evidence/ha-core-2026.11.0-<evidence SHA-256>.json` |

**D. What a dry run cannot be compared on.** A dry run signs a re-keyed
copy with an ephemeral test key. It replaces `public_key_sha256` and
`previous_journal_sha256` in its copy of the candidate and re-signs its
copy of the predecessor (`scripts/core_registry_one_tap.py:284-299`). So
the dry-run journal's file hash, key and signatures are not expected
values and are not compared. Its sequence, entries and capability lists
are. The dry-run summary begins "**DRY RUN.** An ephemeral test key signs
a re-keyed copy; nothing is pushed and the owner key is not used." The run
keeps its artifacts, `core-registry-inputs-<run>-<attempt>` and
`core-registry-dry-run-<run>-<attempt>`, for one day
(`core-release-registry-publish.yml:171-174`, `:436-439`).

## 6. Interaction with 2.5.0 and automated releases

- **HAMCP-319 (#385)** adds no Core signing. Its only overlap is the shared
  `main` that C4 and C5 sign from. Its admission lanes (#404, #405, now in
  `release/2.6.0` at `7038aaea`) gate ha-mcp images, not Core profiles. They
  are not G4 lane evidence for any N row.
- **HAMCP-290** is separately reviewed 2.5 scope. It is **not** pulled into
  this 2.6 batch. If it is signed first, its references are part of the
  predecessor the 2.6 extension preserves (G7). Its contract is unknown here.
  - *Note, 2026-11-10.* The bullet above is kept as written. Two things
    changed around it. The contract is now known: `core.marketplace_read` is
    compiled at the merged tip and on `main` (§5A.3). And later records point
    the profile at sequence 18 (#421; §9B), which conflicts with "not pulled
    into this 2.6 batch". The first revision of 2026-11-10 did not resolve
    that; it was §5A.13 Q1.
  - *Superseded, 2026-11-10 (second revision).* R6 (§9C) answers it:
    `core.marketplace_read` is in the combined sequence 18. That replaces
    the 2026-11-08 separate-signing decision. If its lane cannot produce a
    clean receipt on the signing commit, it drops to sequence 19. The
    bullet above and the first note are kept for the record.
- **Automated new Core releases** (for example a 2026.11.x) consume sequence
  numbers and copy only predecessor references whose groups pass (C7). Once a
  2.6 profile is signed on 2026.11.0, the next automated run refuses unless
  `ci/core-evidence-groups.json` maps it (G5). Core 2026.11.1 is that case
  today. Its binding is register row HAMCP-367 and is outside this plan
  (R11; §5A.5 Exclusions).

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

**Sequence 18 draft questions (2026-11-10): answered.** The paragraph
above covers the 2026-11-08 design choices and still stands for them. The
first revision of 2026-11-10 raised three questions for Josh. He answered
all three the same day (§9C):

| § | Question | Answer |
| --- | --- | --- |
| §5A.13 Q1 | Does `core.marketplace_read` (HAMCP-290) join the combined sequence 18 extension, or is it still signed on its own? | R6: it joins sequence 18, with a fallback to sequence 19 |
| §5A.13 Q3 | Does "the lane re-runs before signing", recorded for #421, apply to every lane receipt, HAMCP-134's included? | R8: yes, once, on the signing commit |
| §5A.13 Q10 | Who prepares the candidate and pushes the permanent candidate branch, and under which authorization? | R10: agents prepare; one agent makes the single push after Josh approves both SHA-256 values; Josh dispatches |

**Still pending Josh (2026-11-10).** None of these blocks the design
freeze.

| Item | What is pending |
| --- | --- |
| Approval of both SHA-256 values (§5A.8) | Josh's approval. It cannot be asked for yet: the two files exist only after preparation. It is required before the single push |
| A second sequence 18 candidate (§5A.13 Q10) | Contingent: pending Josh only if the one pushed candidate proves wrong |
| HAMCP-367's target, start and order relative to sequence 18 (§5A.13 Q14) | Pending Josh. Outside this plan |

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
- **Plan reading, superseded 2026-11-10.** This bullet read item 6 as
  still governing N1. R12 (§9C) settles it: the design cutoff fires and
  HAMCP-295/109 moves to 2.7 as its first item. There is no N1 row. The
  late-row rule answered a question asked about N2-N6 only.
- Blocks: nothing.

**Signing-specific choices: none new.** The additive architecture, the scene
profile, the 2026.11.0-only target, the single batch (with the late-row
rule above) and the placement of HAMCP-134 and HAMCP-231 are settled (§1). Final signing approval is a later,
concrete owner action, not requested here. The 2026-11-10 rulings on
membership, the lane re-run and the candidate route are in §9C.

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
| #390 §9 says "HAMCP-290 and the other new 2.6 profiles share the one combined 2.6 signing" | **Open.** The sentence is still at the cited head. This plan kept the review's position: HAMCP-290 is separate 2.5 scope and is not pulled into the batch (§6). **Superseded 2026-11-10:** R6 (§9C) puts `core.marketplace_read` in the combined sequence 18, and R12 moves #390's own work to 2.7 | #390 §9 (build plan, dependencies) |
| #390 names no exact 2026.11.0 lane evidence or evidence group | **Open in design, addressed here.** F2 now requires the lane evidence this plan requires and defers the group to this plan; §5.2 G5 defines the rule. The build contract must name the group. **Not a 2.6 item since 2026-11-10** (R12, §9C) | #390 §9 F2 |
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
| 6 (J3) | #390 | 2.6.0 with a hard cutoff to 3.0 if the contract is not frozen at assembly | As recorded on 2026-11-08, N1 was included only if the design's freeze criteria held at assembly. **Superseded 2026-11-10 by R12 (§9C):** the cutoff fires, HAMCP-295/109 moves to 2.7 as its first item (not 3.0), and row N1 is removed | §1; §4 Count; §5A.5 Exclusions |
| 7 (J1) | #390 | One plan-level acknowledgement when any reference is not rewritten | Plan behavior; no contract field | §4 N1 (row removed 2026-11-10, R12) |
| 8 (J2) | #390 | Elevated for actuators only | Policy behavior; no contract field | §4 N1 (row removed 2026-11-10, R12) |
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
| Late-row rule (later 2026-11-08) | #395 | Wait for all rows, with a hard cutoff of 2027-01-22 (HAMCP-139 renewal checkpoint); at the cutoff sign what is ready, which doubles as the renewal; late rows get their own later additive signing, not 3.0 | The combined signing appends the rows frozen by the cutoff; each late N2-N6 row is appended later by its own extension; the cutoff signing renews the authority. Supersedes "no separate or phased 2.6 signings" for late rows only. The reading that item 6 still governs N1 is superseded by R12 (§9C) | §1; §4 Count; §5.4; §8 |

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
remaining HAMCP-297 items. Since 2026-11-10 the table has five rows: R12
removed N1 (§9C).

## 9B. Owner rulings of 2026-11-10 recorded elsewhere

These rulings were **not** answered in this author's session. They reached
this revision in the task brief and are recorded in the places named. They
are quoted as recorded, label [H]. The "Nothing else in this plan is
attributed to Josh" sentence in §9A covers the 2026-11-08 revision; this
section adds only quotations with their sources, and no new decision.

`BH` is the 2.6.0 build handoff,
`~/handoffs/hass-mcp-admin-agent-release-2.6.0-build.md`, read on
2026-11-10 at 1,496 lines. It is outside the repository, so a reviewer
without that file can check only the quotations from the #428 and #421
bodies.

| # | Text as recorded | Where recorded | Used in |
| --- | --- | --- | --- |
| R1 | "Draft #395's seq 18 signing plan now from merged profiles; add #421, #424, #425 and #427 profiles as each lands. No signing dispatch." | `BH:1477`, under "20:53 CST 2026-11-10: Josh's rulings to move 2.6 faster" (`BH:1473`) | The basis of §5A |
| R2 | "Not authorized: Ready, merges, version values other than 2.4.2-beta.8, registry signing dispatch, publishing, deploying, Home Assistant access, secrets." | `BH:1480` | §5A.7 PC8; §12 |
| R3 | "After review: finish the independent review and corrections first. Then run the G4 lane once on the reviewed final head (it must load the exact signing fingerprint). P1/P2 are not signed separately: they go into the 2.6 combined signing (seq 18) with the other new 2.6 profiles, per design PR #395. Josh dispatches that once (dry run first, architect checks the candidate). If G4 can't pass before the 2027-01-22 signing cutoff, stop and report." | #428 body, "Activation sequence (Josh decision, 2026-11-10)"; the same in short form at `BH:513` | §5A.7 PC4; §5A.8; §5A.11 item 10; §5A.13 Q4 |
| R4 | "For the #395 update: add seq 18 combined signing covering all new 2.6 profiles, P1/P2 included." | `BH:514` | §5A.5; §5A.13 Q1 |
| R5 | "The lane re-runs before signing as already ruled; disclose the receipt head." | `BH:1434`; quoted in the #421 body | §5A.7 PC4; §5A.12; §5A.13 Q2, Q3 |

Reading notes, this plan's and not Josh's:

- "P1/P2" in R3 and R4 are HAMCP-297's own signing steps as #428 names
  them. They are not the preconditions of §5A.7, which are numbered PC1-PC9
  to avoid the clash.
- R1 and R4 name sequence 18. Source assigns the number at preparation
  (§5.1; `prepare_core_release_registry.py:245`). It is 18 only while
  nothing else is signed first (§5A.11 item 1).
- R1 and R4 do not mention the 2026-11-08 decision that
  `core.marketplace_read` is signed separately. Whether they replace it was
  §5A.13 Q1 and was not assumed. R6 (§9C) answered it later the same day.
- R1 names #425. R12 (§9C) later removed it: PR #425 does not merge into
  `release/2.6.0`.
- R2's list predates the addition of "workflow dispatch". §9C carries the
  current list.

## 9C. Josh's rulings of 2026-11-10 (in session)

Josh gave these rulings in session on 2026-11-10, in two blocks. The
session was the orchestrating session. Its orchestrator relayed each block
verbatim to this author, and the text below is quoted as relayed. Each is
attributed as "Josh, in session, 2026-11-10". Nothing else in the second
revision is attributed to Josh.

Both blocks are also recorded in the build handoff (`BH`, §9B; re-read at
1,597 lines): the first under "Josh's rulings on the #395 seq 18 plan"
(`BH:1523-1530`), the second under "2026-11-10 22:02 CST — Josh's rulings
on seq 18 route, 2026.11.1, #425, #424 (in session)" (`BH:1567-1574`) [H].
R7 is also noted on register row HAMCP-366 [H].

| # | Text (verbatim) | Status | Used in |
| --- | --- | --- | --- |
| R6 | "core.marketplace_read goes in the combined seq 18; this replaces the 2026-11-08 separate-signing decision (its 2.5 premise no longer holds). Fallback: if its lane cannot produce a clean receipt on the signing commit, drop it to seq 19 rather than hold seq 18." | In force | §4 Count; §5A.3; §5A.5; §5A.8 step 2; §5A.13 Q1, Q15; §6 |
| R7 | "New row, 2.6, start now: the HAMCP-297 signing lane (script, evidence group, receipt), built with loopback-only and closed-command guards from the start, plus tests and mutation demos. Note it on HAMCP-297." | In force. The row is HAMCP-366 [H] | §5A.4; §5A.5; §5A.13 Q4; §5A.15 |
| R8 | "Every included lane, HAMCP-134's included, re-runs once on the signing commit; receipts bind that commit." | In force | §5A.4; §5A.6; §5A.7 PC4; §5A.8 step 2; §5A.12; §5A.13 Q3; §5A.15 |
| R9 | "No agent pushes registry-candidates/. Candidate branch and signing come from the existing workflows: core-release-evidence.yml from main (dry_run=true first, then false by Josh), and core-release-registry-publish.yml with reviewed SHA-256s through the core-release-registry-signing environment. The plan includes an expected-values sheet (profile, fingerprint, receipt head and hash) for comparing against the dry-run artifact. Confirm main's workflows match this and that seq 17 followed this path; report any difference." | **Replaced by R10.** The expected-values sheet stays: R10 names it (§5A.15). The confirmation it asked for is §5A.14 | §5A.14; §5A.15 |
| R10 | "Seq 18 candidate branch: the documented manual flow (prepare_core_release_registry.py extend-capabilities), as seq 16/17. Agents prepare the candidate and evidence files and the expected-values sheet. After Josh approves both SHA-256s, one agent makes the single push of registry-candidates/2026.11.0-seq18 and nothing else. Josh dispatches the publish dry run and the real run. Replaces ruling 4 of the previous block." | In force. "Ruling 4 of the previous block" is R9 | §5A.6; §5A.7 PC8; §5A.8; §5A.10; §5A.11; §5A.13 Q10 |
| R11 | "Seq 18 stays a 2026.11.0 extension. New row: Core 2026.11.1 binding, separate from seq 18." | In force. The row is HAMCP-367 [H] | §2.7; §5A.5 Exclusions; §5A.11 item 1; §5A.13 Q14; §6 |
| R12 | "#425: the design cutoff fires. #425 does not merge into release/2.6.0; HAMCP-295/109 moves to 2.7 as its first item (not 3.0; Josh's change to the design's cutoff text). Drop its profile from the seq 18 plan. Keep the branch; stop work on it." | In force | §1; §4; §5A.5 Exclusions; §5A.12; §5A.13 Q13; §7.3; §8; §9A |

Given with the first block, and standing after the second: "Not
authorized: Ready, merges, version values other than 2.4.2-beta.8,
registry signing dispatch, workflow dispatch, publishing, deploying, Home
Assistant access, secrets."

Reading notes, this plan's and not Josh's:

- R6 to R9 were numbered 1 to 4 in the first block, and R10 to R12 were
  numbered 1 to 3 in the second. They are renumbered here to continue §9B.
- R10 permits one agent push, after Josh's approval of both SHA-256
  values. It permits no agent dispatch. "Workflow dispatch" and "registry
  signing dispatch" stay not authorized for agents; Josh dispatches.
- R10 fixes one push. It does not say what happens if that candidate
  proves wrong (§5A.13 Q10). R11 does not say whether HAMCP-367 is signed
  before or after sequence 18 (§5A.13 Q14). Both are pending Josh.
- R12 changes the destination in the HAMCP-295/109 design's cutoff text
  from 3.0 to 2.7. This plan does not edit that design (§5A.13 Q13).
- The handoff's second block has a fourth item, about #424's tests. It is
  not about this plan and is not quoted.

## 9D. Josh's rulings of 2026-11-13 (R-a to R-g)

Josh's rulings, relayed verbatim to this author by the orchestrating
session's brief of 2026-11-13 and quoted below as relayed. Each is
attributed as "Josh, 2026-11-13". Nothing else in the 2026-11-13 refresh
is attributed to Josh; the computed values are this author's, at
`3bd5fe3f`.

| # | Text (verbatim) | Status | Used in |
| --- | --- | --- | --- |
| R-a | "Membership: all six (core.marketplace_read, core.security_metadata_read, core.device_configuration_entity_operation, core.scene_create_delete_operation, core.created_helper_delete_operation, core.thread_matter_topology_read), each conditional on its lane passing on the signing commit. A profile whose lane fails is dropped from seq 18; the others proceed." | In force | §5A.5; §5A.7 PC1; §5A.8 step 2; §5A.10; §5A.11 item 3; §5A.15 A and B |
| R-b | "previous_capabilities are the 32 signed sequence-17 references in their signed order, not the first 32 compiled profiles (core.typed_helper_value_operation is compiled 20th and signed 32nd). The six added references follow in compiled order: marketplace, security_metadata, device_configuration_entity, scene_create_delete, created_helper_delete, thread_matter_topology. Record both rules on #395." | In force. Both rules are recorded in §5A.5 | §5A.3; §5A.5; §5A.10; §5A.13 Q11; §5A.15 B |
| R-c | "The provisional mark on core.device_configuration_entity_operation (74a03a64…) is removed: ruling 4 closed with no ALLOWLIST change, and the value held through #434's merge." | Applied | §5A.15 A (architect table) |
| R-d | "refresh #395's sheet A to the final release tip: 38 compiled profiles, line numbers, manifest hash, lane and group cells, and receipt hashes. The architect recomputes all 38 fingerprints on the release/2.6.0 to main merge commit (the signing commit) before signing." | Refresh applied at `3bd5fe3f`; the recomputation is in force for the signing commit | §5A.3; §5A.7 PC5; §5A.8 step 1; §5A.15 A |
| R-e | "every reference in the seq 18 candidate uses exactly the closed field set that beta.7 accepts (registry_models.py at tag v2.4.2-beta.7). beta.7 rejects a whole entry for a single unknown field, which on rollback would fall back to the cached sequence 17 or, with an empty cache, lose every Core-native capability." | In force | §5A.7 PC10; §5A.10; §5A.11 item 14; §5A.15 B |
| R-f | "Signing seq 18 is one-way. There is no per-capability removal; the only undo is revoking the whole 2026.11.0 entry, which withdraws all its capabilities and cannot be re-added; recovery is then a new Core-version binding (HAMCP-367). State this plainly in the release notes and on #395. Josh signs only after: the architect recomputes all 38 fingerprints on the signing commit; every included lane passes on that commit; and the candidate is checked against item 3." Item 3 is R-b, as relayed | In force. Stated in §5A.10. The release notes are outside this change | §5A.7 PC8; §5A.10 |
| R-g | #425 (HAMCP-295, entity rename) stays out of 2.6. Its exclusion row stays. (Relayed as a ruling, not as a quotation.) | In force | §5A.5 Exclusions; §5A.12; §5A.15 A |
| SA-1 | Architect ruling, relayed by Josh, 2026-11-13, item 1: "G5, the missing core.device_configuration_entity_operation entry in ci/core-contract-read-files.json (architect-verified at 7d2989ad: 38 profiles, this one absent): a) NOT ruled unnecessary. No change to ci/core-contract-read-files.json in the release PR; the 2.6 release does not depend on it. b) The plan records G5 as OPEN for this profile only. It is not signable until G5 is closed. c) Report to the architect, read-only, from the code and the plan: the Core source files the device-configuration contract actually reads, with the code locations; whether every one of them is already in the manifest's paths list; which receipts record manifest_sha256; whether the signing plan already requires the lanes to re-run on the signing commit. d) Josh decides before signing, on that report: a scoped follow-up adding the entry and re-running the affected lanes, or dropping the profile from seq 18 under R-a." | In force; item c reported 2026-11-13; Josh decides item d before signing | §5A.4; §5A.5 M3 row |
| SA-2 | Architect ruling, relayed by Josh, 2026-11-13, item 2: "Stop condition 10 (§5A.11): reword it to match R-a: 'If HAMCP-297's G4 lane or its G5 gate cannot pass before the cutoff, core.device_configuration_entity_operation is dropped from sequence 18 and the other profiles proceed (ruling R-a); Josh is told before signing.' Exception: if R3's recorded text explicitly says it overrides R-a for HAMCP-297, do not reword. Quote R3 and report instead. Apply it in a commit that changes only the plan file." | Applied (R3 does not override R-a) | §5A.11 item 10; §5A.5 M3 row |

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
  6 cutoff, defined by #390's F1-F4 at assembly. That cutoff reading was
  itself superseded on 2026-11-10 by R12 (§9C).
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

The 2026-11-10 revision does not change this. §5A drafts a candidate in
prose and signs nothing. The three references it lists were compiled by
pull requests #409, #423 and #428, not by this document. No candidate file,
candidate branch, dry run or signature exists for sequence 18.

The second revision of 2026-11-10 does not change this either. It records
rulings and describes a route. Sequence 18 would add up to six references
(§4 Count). The entity-rename profile is not one of them (R12, §9C).

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

For the first revision of 2026-11-10:

- No candidate or evidence file was prepared.
  `scripts/prepare_core_release_registry.py` was read, not run. No
  `registry-candidates/` branch was created or pushed.
- No workflow was dispatched, as a dry run or otherwise. Nothing was signed.
  No tag, release, image, attestation or registry commit was created.
- No lane was run and no container was started. Lane receipts were read from
  committed files, and for #424 from uncommitted files in that pull
  request's worktree, read only.
- The signed journal was read from the public `registry` branch with
  `git fetch` and parsed offline. Profile fingerprints were computed offline
  by importing the repository's profile module at each commit named in §5A.
  That is the only repository code run, apart from the checks listed in the
  pull request.
- No secret, signing seed, Supervisor token or production credential was
  read. No live Home Assistant or deployed MCP endpoint was contacted.
- Only this file was edited. No protected path, runtime file, test,
  workflow, release declaration or version value was edited. The other
  files in the branch's diff against its previous head come from merging
  `release/2.6.0`.
- The open pull requests #421, #424, #425 and #427, their branches and
  their worktrees were read and not changed. The design documents were not
  edited, the HAMCP-290 text in conflict included (§5A.13 Q1).
- No decision is attributed to Josh in this revision. §9B quotes rulings
  recorded elsewhere, with their sources. Every item marked [A] and every
  proposal in §5A is this plan's.

For the second revision of 2026-11-10:

- No candidate or evidence file was prepared, and no SHA-256 value was put
  to Josh for approval. The single push that §5A.8 step 6 describes was
  not made: no `registry-candidates/` ref was created or pushed.
- No workflow was dispatched, as a dry run or otherwise. Nothing was
  signed, published or deployed. No lane was run and no container was
  started.
- The settings and secrets of the `core-release-registry-signing`
  environment were not read. Workflow run lists, pull-request heads and
  branch tips were read from GitHub, read-only. Register rows HAMCP-366
  and HAMCP-367 were read and not edited.
- Not authorized, and not done: Ready, merges, version values other than
  2.4.2-beta.8, registry signing dispatch, workflow dispatch, publishing,
  deploying, Home Assistant access, secrets.
- Only this file was edited. No design document, protected path, workflow
  or other pull request was changed. PR #425 and its branch were not
  touched.
- The decisions attributed to Josh in this revision are exactly R6-R12,
  quoted in §9C. Every reading note, every item marked [A] and every
  section reference beside a ruling is this plan's.

For the 2026-11-13 refresh:

- Only this file was edited, in one local commit on
  `agent/HAMCP-332-release-2.6.0`. Nothing was pushed, no pull request was
  opened or edited, no GitHub write was made and no workflow was
  dispatched. Nothing was fetched; `origin/main` and `origin/registry` are
  the local refs.
- No lane was run and no container was started. Receipts were read from
  the files committed at `3bd5fe3f`; none was edited.
- The fingerprints were computed offline by importing the profile module
  from a `git archive` of `3bd5fe3f` in an ignored scratch directory. No
  candidate or evidence file was prepared and nothing was signed.
- No live Home Assistant, deployed endpoint, secret, signing seed or
  production credential was used.
