# Engineering 2.4.1-beta.20 acceptance

This contract covers HAMCP-191: one shared sanitizer with identity-only
redaction markers for every tool, D3 health counters, and truthful
completeness when redaction applies. Stored plans keep beta.19's marker
tolerance (D11). It builds on 2.4.1-beta.19 (HAMCP-192).

See also:
- Release notes:
  [`V2_4_1_BETA20_RELEASE_NOTES.md`](V2_4_1_BETA20_RELEASE_NOTES.md).
- Implementation notes:
  [`HAMCP_191_IMPLEMENTATION_NOTES.md`](HAMCP_191_IMPLEMENTATION_NOTES.md).

**Release target.**
- This PR was implemented on `2.4.1-beta.19` (base
  `3c4c27b702a0be33ad5f55868377eb4197d4ef38`).
- It was staged as `2.4.1-beta.20` by one owner-authorized run of
  `scripts/promote_next_release.py --apply`, which consumed the declaration.

The contract separates four stages:
1. source and offline acceptance;
2. release CI;
3. publication;
4. installed Engineering acceptance.

It authorizes none of the later stages.

## 1. Source and offline acceptance

Bind the following:
- `2.4.1-beta.20` materialized in all three version authorities, with exact
  acceptance and release-note resolution from `scripts/codex-context.py` and
  no staged declaration left over;
- the PR head SHA;
- the passing PR `validate` run for that exact head;
- the Evidence tier log named in the PR.

### Offline proof

Every synthetic secret is built at runtime. No committed HAMCP-191 file
holds secret-shaped text (`CommittedFileScanTests`).

**`tests/test_hamcp191_shared_sanitizer.py` (50 tests):**
- `RuleDerivationTests` (4) and `UnitMatrixTests` (6): the
  rule × wrapper × position matrix.
  - Rules are derived from the code, and each rule has a builder that fires
    it.
  - Every cell redacts its needle, and the needle alone is kept.
  - P1 and P2 are closed.
  - Emitted markers deduplicate by type.
  - Forged markers collide in every position (value, key and nested) and
    spelling:
    - exact and `[TRUNCATED:` forms;
    - percent-encoded one to three times;
    - full-width, Cyrillic, Greek/Cherokee, zero-width, combining and
      look-alike-bracket forms;
    - prose truncation.

    Four percent-decode rounds are followed. A fifth round fails closed.
- `CoverageTests` (6): every sanitizer call site in the source is in the
  committed table and maps to a ranked family with an injector. The proof
  fails for an unlisted or removed site. `identity_markers_only=False`
  raises, and the default pass is strict.
- `IdentityRecordTests` (11):
  - Engineering's output survives by object identity, and a byte-identical
    upstream copy collides.
  - A slice, a different type or another request's record is scanned.
  - A secret the producing pass lacked is never skipped.
  - Inherited records are read-only.
  - The truncation suffix, the dependency snapshot, the audit sink and
    gateway-withheld evidence each join the record.
- `FailureBranchTests` (3): the raw-key branch scans or withholds the key
  and never writes it back.
- `StoredPlanExceptionTests` (7), D11:
  - Stored markers are kept and the text between them is scanned.
  - Canonical markers alone are not counted.
  - The strict pass shares none of the tolerance.
  - Memos do not cross passes.
  - Only the six allow-listed call sites use `rescan_stored_plan`, and the
    proof fails for a new caller.
- `MarkerCollisionHealthTests` (3): collisions and refusals are counted,
  ordinary data counts nothing, and the health payload carries the counters.
- `RedactionEvidenceTests` (3): the redaction summary is absent without
  redaction, and bounded and truthful with it.
- `StructuralTruncationTests` (2), X1.
- `IncidentHeaderTests` (1), X2.
- `LinearDetectorTests` (3): the linear look-alike detector equals the
  reference grammar, and long opener runs are linear.

**`tests/test_hamcp191_families.py` (21 tests):** one injector per ranked
family: logs, delegated, logbook, analysis, dashboard, governance,
identifier and audit.
- Each drives a real call site offline with P1, P2 and an ordinary value.
- Delegated and dashboard reads report `partial` with `metadata.redaction`
  when redaction applied, and stay `complete` and unchanged for ordinary data.
- An approval note's Engineering marker is shown intact in the plan view,
  and a forged note collides.
- Persistence and resource validation refuse forged markers and count the
  refusal. Only the stored-plan gate keeps the tolerance.
- An ordinary governed plan is approved and applied (useful authorized
  success).

**`tests/test_hamcp191_compatibility.py` (5 tests):**
- It reruns `docs/evidence/hamcp191/generate_corpus.py compare` against the
  base-recorded fixture `tests/fixtures/hamcp191/compat_corpus.json`.
- Of 61 samples, 50 are byte-identical and 11 changed by decision
  (P1 ×2, P2 ×2, D1 ×4, D2, D3 ×2), with 0 unexpected.
- The P1 and P2 samples leaked on the base and do not leak on the head.

**Existing tests whose pins changed** are listed in the implementation
notes, section 5.

### Mutation proof

`docs/evidence/hamcp191-mutations/run_mutations.py` applied 52 mutations,
each to a disposable copy, and ran the three modules above. The committed
`results.json` records 51 killed, 0 survived, and one equivalent (M5, as
explained in the implementation notes, section 6). The mutations cover:
- a marker trusted by text;
- the identity check skipped;
- the collision decided before the rules;
- the raw key written back;
- "complete" despite redaction on four paths;
- a matrix cell removed;
- design M1–M18;
- D11 (allow list widened; stored re-scan made strict; write time made
  tolerant; the stored flag ignored);
- counters and wiring.

## 2. Release CI and publication

Require exact-head CI before release acceptance. Pending or unavailable jobs
are not passing evidence.

Publication needs Josh's Ready authorization and the protected merge
handoff. Verify the following with the shipped verifiers:
- the merged source;
- v2.4.1-beta.20;
- the immutable version and commit images;
- provenance and SBOM.

Never invent future image identities. Retain the beta.19 artifacts and the
known-good earlier artifacts for recovery.

No stored format changes. Rollback is reinstalling the beta.19 artifact;
rolling back restores the P1 and P2 leaks.

## 3. Installed Engineering

All installed checks below are read-only unless a step names Josh's explicit
authorization.

1. **Installed-image binding.** Separately authorize installation of the
   published Engineering artifact. Bind the running add-on to:
   - the published image index digest;
   - the v2.4.1-beta.20 tag;
   - the protected `main` merge commit that contains this PR.

   A source version alone does not prove the installed image.
2. **Catalog unchanged.** Compare the public descriptors with beta.19: the
   same tool count, input schemas and descriptions. Also compare:
   - Core signed selection;
   - storage and audit health;
   - F3 readiness;
   - zero fallback.
3. **Health: the D3 counters (read-only).** Call `get_server_health`.
   - Require `sanitization.strict_marker_mode: true` and integer
     `sanitization.marker_collisions` and
     `sanitization.marker_collision_refusals`.
   - Record both counts. They count since the add-on started.
   - Nonzero counts are not a failure. Each one is a string that looked like
     a marker or a governed refusal. Report them to Josh with the tools used
     since the start.
4. **An ordinary read is unchanged (read-only).**
   1. Call `get_entity` for an ordinary entity (for example
      `light.back_guest_room_lamp`).
   2. Call `ha_search` for an ordinary query (for example `lamp`).
   3. Compare with the same reads on beta.19, if recorded, or with Home
      Assistant's own UI.

   **Pass:**
   - the values are unchanged;
   - no `[REDACTED:redaction_marker_collision]` appears;
   - the delegated read reports `completeness: complete` with no
     `metadata.redaction` and no redaction warning;
   - repeating step 3 shows `marker_collisions` unchanged by these reads.
5. **A plan view shows Engineering's markers intact.**

   **Read-only path.**
   1. Call `list_change_plans`, then `get_change_plan` on a recent plan
      whose approval note or review holds an Engineering marker (for
      example `[REDACTED:token]`).
   2. **Pass:** the marker is shown as written, not as
      `[REDACTED:redaction_marker_collision]`, and no secret appears. This
      re-read is the D11 stored-plan path, unchanged from beta.19.

   **If no such plan exists (needs Josh's explicit authorization, because
   it writes a plan record and an audit entry).**
   1. Create a description-only configuration plan.
   2. Request approval with a note holding a synthetic credential that Josh
      types at the time, never a real one. It must not be in this document.
   3. Read the plan back with `get_change_plan`. The note holds an intact
      Engineering marker in place of the credential.
   4. Do not approve or apply the plan; let it expire.

   **D11.** Stored plans keep beta.19's tolerance until HAMCP-207. A
   persisted plan's canonical markers are trusted by text on re-read, while
   everything written by beta.20 is strict.
6. **Collision behaviour, forged markers in every position, the identity
   record and the raw-key branch** are proven offline only (§1). Do not put
   forged markers into live Home Assistant data to test them.

No push, CI trigger, signing, publication, installation or live action is
authorized by source completion.
