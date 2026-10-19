# Engineering 2.4.1-beta.20 acceptance

This contract covers HAMCP-191: one shared sanitizer with identity-only
redaction markers for every tool, D3 health counters, and truthful
completeness when redaction applies. Stored plans keep beta.19's marker
tolerance on re-read (D11); every plan family is strict at creation, and
every plan write passes one storage chokepoint. It
builds on 2.4.1-beta.19 (HAMCP-192).

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

**`tests/test_hamcp191_shared_sanitizer.py` (52 tests):**
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
- `IdentityRecordTests` (13):
  - Engineering's output survives by object identity, and a byte-identical
    upstream copy collides.
  - A slice, a different type or another request's record is scanned.
  - A secret the producing pass lacked is never skipped, including one a
    child pass adds to an inherited record (review R1).
  - Marker text past the string limit still collides (review R2).
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
- `CommittedFileScanTests` (1): every file this PR changes, by glob or by
  the explicit `HAMCP191_CHANGED_FILES` list, holds no secret-shaped text.

**`tests/test_hamcp191_families.py` (39 tests):** one injector per ranked
family: logs, delegated, logbook, analysis, dashboard, governance,
identifier and audit.
- Each drives a real call site offline with P1, P2 and an ordinary value.
- Delegated and dashboard reads report `partial` with `metadata.redaction`
  when redaction applied, and stay `complete` and unchanged for ordinary data.
  A redaction in a dashboard warning or hint alone makes the response
  `partial`; the held-read canary carries the summary, also when its result
  is omitted.
- Core logs are sanitized in two passes: credentials over the whole window,
  then marker collisions per line. A marker-like line collapses only that
  line, with bounded `redacted_line_count` and `collided_line_count`; an
  ordinary window is unchanged.
- `PlanCreationGateTests` (7) and `OperationalCreationGateTests` (4):
  - The review's legacy-planning reproductions are refused with nothing
    persisted or returned. They cover the title, provider current state,
    description, proposal and split anchor. A secret-bearing current
    automation is refused with `AUTOMATION_VALIDATION_FAILED`.
  - Caller-context entries that the strict pass would change are dropped.
  - Dashboard, configuration, backup, helper state, reload and restart
    plans refuse flagged text before persisting.
  - A refused operational plan never supersedes the prior plan for its
    target.
  - An ordinary legacy plan is unchanged.
- Structured-log redaction counters include `log_event`'s first pass.
- An approval note's Engineering marker is shown intact in the plan view,
  and a forged note collides.
- Persistence and resource validation refuse forged markers and count the
  refusal. Only the stored-plan gate keeps the tolerance.
- An ordinary governed plan is approved and applied (useful authorized
  success).

**`tests/test_hamcp191_plan_chokepoint.py` (21 tests):** the review's
batch-2 reproductions, each a permanent test.
- `IdentifierCreationTests` (6), H1g:
  - A valid and an invalid automation id holding a known or wrapped secret
    are refused before any Home Assistant read. Nothing is persisted, and
    the id is neither returned nor echoed in `metadata.resource_id`.
  - An ordinary id still creates a plan and is echoed.
  - A plan id carrying a known secret is withheld from the echo, also with
    no governance service.
  - The repository refuses a secret-bearing target before any file exists.
  - The strict identifier scan alone refuses a forged-marker target that
    the D11 stored-plan check tolerates (batch 3, after L3).
  - Through the tool, a secret-bearing automation id is now refused by the
    batch-3 identifier boundary with `INVALID_REQUEST`; a direct service
    call still gets `AUTOMATION_VALIDATION_FAILED`.
- `ApplyTimeEvidenceTests` (1), H2: a wrapped known secret read back as
  backup verification evidence is stored redacted, with no second dispatch.
- `ChokepointUnitTests` (5):
  - an update redacts new evidence in place;
  - an update refuses on identity paths and leaves the record unchanged;
  - exempt values are scanned and refused, never rewritten;
  - old stored marker content is kept, and only new content is screened;
  - a refused recovery write is counted, and startup continues.
- `ExemptSchemaTests` (4):
  - every one of the 72 exempt paths resolves to a typed leaf or a pinned
    mapping key;
  - none of the 14 formats can carry free text;
  - each enum accepts exactly its values;
  - a JWT fits the request-id format, which is why exempt values are still
    scanned.
- `WriterInventoryTests` (2): every `save` call is in the committed table,
  and storage writes go only through `save` and quarantine.
- `RuntimeWriterSpyTests` (1): every plan write in the 14 profiled tests in
  `SPY_TESTS`, spied through `os.replace` and `open`, was screened. All 27
  required (caller, operation, status) writes and both plan writers were
  observed.
- `CoreLogSpanningTests` (2), H3: credentials spanning LF and CRLF line
  breaks are redacted, and a collision stays line-local across CRLF.

**`tests/test_hamcp191_batch3.py` (23 tests):** the batch-3 review
reproductions (H4, H1g-residual, M2, L3), each a permanent test.
- `RequestIdScreenTests` (4), H4: ordinary request ids pass unchanged.
  Token-shaped ids, and ids equal to a configured secret, are replaced by an
  Engineering-generated id and counted in `request_id_replacements`; the
  value is never recorded. Malformed ids are regenerated without counting.
- `ExecutionTaskRequestIdTests` (3), H4: `apply_change_plan` under a JWT
  request id, and `cancel_execution_task` under three token shapes, leave
  no copy on disk or in `get_execution_task` and `list_execution_tasks`.
  An ordinary request id is recorded unchanged.
- `GatewayRequestIdTests` (3), H4: through the authenticated gateway, a
  token-shaped or configured-secret `x-request-id` header is replaced and
  an ordinary one is kept.
- `HelperStateNoChangeTests` (2) and `EntityMetadataNoChangeTests` (2),
  H1g-residual: a secret-bearing `entity_id` is refused before the service
  is called, so the `no_change` shortcut never echoes it. An ordinary id
  still returns `no_change`.
- `IdentifierBoundaryEnumerationTests` (3), H1g-residual:
  - every input of every governance tool is classified as an identifier or
    not, and the classification is exact;
  - each identifier input, holding a known secret and holding a JWT, is
    refused with `INVALID_REQUEST`, a fixed reason and no echo, before any
    service call;
  - ordinary identifiers reach the service.
- `DashboardArtifactOrderTests` (4), M2: a secret caller id or `url_path` is
  refused before any read or artifact. A plan-gate refusal after the read
  leaves no artifact. An ordinary plan writes exactly one artifact, bound by
  its payload hash.
- `ContractV1StoredPlanTests` (2), L3: an unchanged secret in a contract-v1
  record is refused on update and the record bytes are kept; an ordinary v1
  record still updates.

**`tests/test_hamcp191_compatibility.py` (5 tests):**
- It reruns `docs/evidence/hamcp191/generate_corpus.py compare` against the
  base-recorded fixture `tests/fixtures/hamcp191/compat_corpus.json`.
- Of 85 samples, 68 are byte-identical and 17 changed by decision
  (P1 ×4, P2 ×2, D1 ×4, D2, D3 ×4, H3, CL1), with 0 unexpected.
- The P1 and P2 samples leaked on the base and do not leak on the head.
- The five batch-3 samples (ordinary identifiers; JWT and known-secret
  request ids; known-secret entity id and dashboard `url_path`) are
  recorded on the base.
- Log lines holding Home Assistant's own `**REDACTED**` placeholder, and LF
  and CRLF credentials split across Core log lines, are byte-identical.

**Existing tests whose pins changed** are listed in the implementation
notes, section 5.

### Mutation proof

`docs/evidence/hamcp191-mutations/run_mutations.py` applied 77 mutations,
each to a disposable copy, and ran the HAMCP-191 modules above. The
committed `results.json` records 76 killed, 0 survived, and one equivalent
(M5, as explained in the implementation notes, section 6). The mutations
cover:
- a marker trusted by text;
- the identity check skipped;
- the collision decided before the rules;
- the raw key written back;
- "complete" despite redaction on four paths;
- a matrix cell removed;
- design M1–M18;
- D11 (allow list widened; stored re-scan made strict; write time made
  tolerant; the stored flag ignored);
- counters and wiring;
- the Codex review corrections: R1 and R2; each H1 creation gate bypassed
  (legacy input, legacy current state, legacy and backup caller context,
  dashboard input, operational plans); M1 warning and canary summaries;
  the L1 first-pass tally; and Core logs sanitized as one window;
- correction batch 2:
  - a recovery writer and one service writer bypassing the chokepoint (A1a
    and A1b);
  - identifiers, and a schema-exempt field, left unscanned (A2a and A2b);
  - apply-time evidence persisted unscreened (A3);
  - Core-log credential detection made per line only (H3);
- the batch-2 review's R3a (chokepoint bypassed on update) and R3b (exempt
  format prefix-matched), reconstructed from their names;
- correction batch 3:
  - the request id left unscreened (B3-H4);
  - the identifier boundary skipped for one tool (B3-H1g);
  - the dashboard artifact written before the gate, and the pre-read gate
    missing the caller id (B3-M2a and B3-M2b);
  - the contract-v2 condition on the D11 re-check restored (B3-L3).

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
   persisted plan's canonical markers are trusted by text on re-read. Every
   plan family runs the strict detector on its new record before persisting
   or returning it, so a plan beta.20 creates holds no forged marker; the
   save-time gate itself is the tolerant re-scan and is not the creation
   gate.
6. **Core log two-pass behaviour (read-only).** Call `get_core_log_history`
   with a small window. **Pass:** `completeness: partial`, integer
   `redacted_line_count` and `collided_line_count`, and lines holding Home
   Assistant's `**REDACTED**` shown unchanged. Record both counts; nonzero
   counts are reported to Josh, not treated as failure.
7. **Collision behaviour, forged markers in every position, the identity
   record and the raw-key branch** are proven offline only (§1). Do not put
   forged markers into live Home Assistant data to test them.

No push, CI trigger, signing, publication, installation or live action is
authorized by source completion.
