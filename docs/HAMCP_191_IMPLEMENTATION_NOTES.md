# HAMCP-191 implementation notes

These notes record how the HAMCP-191 shared sanitizer was implemented
against its design (`docs/HAMCP_191_SHARED_SANITIZER_DESIGN.md`, PR #271 at
`c4becb390512ea569ccc576a799b9e35383212fa`, not merged into this branch) and
Josh's decisions D1–D11 on register row HAMCP-191. They finalize the list of
protected files and describe each place where the implementation differs from
or adds to the design. Release target: `2.4.1-beta.20`, built on
`2.4.1-beta.19` (base `3c4c27b702a0be33ad5f55868377eb4197d4ef38`).

## 1. Owner decisions as implemented

| Id | Decision | Where |
|---|---|---|
| D1 | Strict is the only text mode for every sanitizer caller. `identity_markers_only=False` raises `TypeError`. | `sanitization.py` `sanitize_untrusted_data`; `CoverageTests` |
| D2 | A collision replaces the whole string with `[REDACTED:redaction_marker_collision]`. | `_Sanitizer._scan` |
| D3 | Look-alike text can block governed edits. Health counts collisions and refusals. | `health.py` `sanitization`; `marker_collision_health`; `record_marker_collision_refusal` |
| D4 | One identity record per request, checked by object identity plus the producing pass's known secrets. | `ReferenceMarkerRegistry`; `RequestTelemetry.sanitizer_registry` |
| D5 | Built from HAMCP-162's strict mode and registry/matrix pattern. `screen` is not reused. | `sanitization.py`; `tests/test_hamcp191_shared_sanitizer.py` |
| D6 | Direct routes with no sanitizer are untouched (HAMCP-194). | no change |
| D7 | One PR. | this PR |
| D8 | HAMCP-162's look-alike detector is kept. Its regex was replaced by an equivalent linear scan (section 4). | `marker_like_text` |
| D9 | Client-visible counts and categories change. | sections 5 and 6 |
| D10 | As recommended. This PR does not change how often the pinned `ha-mcp` emits marker-like text; the D3 counter measures it in the field. No exact-version `ha-mcp` source survey is committed with this PR. | no change |
| D11 | Strict everywhere except the re-scan of stored plans. That re-scan keeps beta.19's marker tolerance as an explicit, tested, allow-listed exception. Follow-up: HAMCP-207. | section 2 |

**Superseding decision (ADR-022).** The design applied strict mode to the
stored-plan re-scan as well. Josh's D11 ("Defer stored plans") supersedes that
for stored plans only. The consequences are as follows:
- A persisted plan's marker-like text is still trusted by text when that plan
  is re-read (approval review, plan views, health verdicts). This is
  unchanged from beta.19.
- Write-time input is strict, so no new plan can carry a forged marker into
  storage.
- Removing the exception needs a storage-format decision, tracked by
  HAMCP-207.

## 2. D11: the stored-plan exception

- `rescan_stored_plan()` is the only entry point that uses
  `_StoredPlanSanitizer`. That class is beta.19's tolerance: canonical
  markers are kept, the text between them is still scanned by every rule,
  and canonical markers alone are not counted as collisions.
- `persistence_safety_errors(..., stored_plan=False)` is strict by default.
  Only `_require_safe_persisted_plan` passes `stored_plan=True`.
- The allow list is code-derived and committed. `StoredPlanExceptionTests`
  parses the source and requires exactly six `rescan_stored_plan` call
  sites, in `governance/resources.py` (1) and `governance/service.py` (5:
  `_public`, `_public_plan_observability_projection`, and three in
  `_review_summary`). A new caller fails the test (mutation D11a).
- Tests:
  - stored markers are kept and the text between them is scanned;
  - canonical markers alone are not counted;
  - the stored-plan gate tolerates canonical markers, while the same text is
    refused at write time and a secret between markers is refused either way
    (mutations D11b, D11c, D11d).

## 3. Protected files

Each file is under a protected path and is named here as task scope.

**From the design's table:**
- `ha_mcp_engineering/sanitization.py`
- `governance/service.py`
- `impact/provider.py`
- `integrity/provider.py`
- `audit.py`
- `logging_config.py`
- `incident/normalization.py` (X2)
- `providers/dispatch.py` (X1)
- `providers/upstream_read_gateway.py` (X3, X4, Finding 11)
- `providers/upstream_yaml_reader.py` (Finding 11)
- the release files `config.yaml`, `ha_mcp_engineering/version.py` and the
  `.release/next-version` declaration, created and then consumed by
  `promote_next_release.py --apply`.

**Additions beyond the design (reported):**

| File | Why |
|---|---|
| `request_context.py` | D4: `RequestTelemetry.sanitizer_registry` (a new record per request) |
| `routing.py` | D4: the request audit write passes the request's record |
| `models/responses.py` | X1: `dump_json_with_truncation` returns a structural truncation flag |
| `health.py` | D3: the `sanitization` counters in `get_server_health` |
| `governance/resources.py` | D11 `stored_plan` flag; D3 refusal counter at the persistence gate |
| `governance/config_validation.py`, `governance/operational_lifecycle.py` | D4: the two bounding passes of one check share a record |
| `f3_dashboard/semantic_diff.py` | D4: the `<redacted>` preview is one registered constant |
| `f3_dashboard/approval_projection.py` | D3: the refusal counter at the X7 projection gate |
| `dependency/extraction.py`, `dependency/index.py`, `dependency/models.py` | D4: one record per dependency snapshot build (`bind_sanitizer_registry`); impact and integrity read it through `inherits=` |
| `dependency/script_calls.py`, `dependency/script_sources.py` | D3: the refusal counter at the script X7 gates |
| `providers/upstream_dashboard.py` | Finding 11: dashboard get/list report `partial` and `metadata.redaction` when redaction applied |

`scripts/validate_addon_metadata.py` (not protected) carries the version
through `promote_next_release.py --apply`.

**Listed in the design but unchanged:**
- `reliability/provider.py` and `trace_normalization.py`. Their passes are
  first passes over upstream data, so they need no identity record.
- For reliability, a collided system-log or trace entry becomes the collision
  marker and so no longer correlates. This drops it from the correlation
  instead of matching on look-alike text.

**Not touched:** stable v1 (`hass_mcp_admin/`), workflows, signed
registries, the support window, `.coderabbit.yaml`, `repository.yaml` and
the Dockerfile.

## 4. Implementation notes and differences from the design

- **All rules run before the collision decision.** `_scan` first applies the
  known-secret pass, then every segment rule and serialized pattern, and only
  then checks `marker_like_text` on the original string. The secret rules
  therefore always run, and their counts are recorded even when the string
  collides (mutation U3/M3).
- **Raw-key failure branch.** If a key's own scan raises, the key is scanned
  as text when possible. Otherwise it is withheld as the registered
  `SANITIZATION_FAILURE_MARKER`. The raw key is never written back
  (mutations U4a, U4b).
- **Look-alike detector (D8).** HAMCP-162's `_MARKER_LIKE` regex was
  quadratic on long opener runs once every caller used it, as the beta.38
  timing test showed. It was replaced by a linear scan (`_MARKER_RUN` with an
  opener check) that gives the same answers:
  - equal results on 200,000 random strings, checked during implementation;
  - a 65,000-character opener run takes about 3 ms;
  - the folding (NFKC, casefold, confusable table) and the four decode rounds
    are unchanged.
- **Core log reads.** `CoreLogReader.evidence` sanitizes the downloaded window
  as one string. Under D2 a single marker-like line therefore replaces the
  whole window with the collision marker. The read stays truthful (no
  secret, a counted collision) but loses that window's text. Josh should
  decide whether a per-line pass is wanted; it is not part of this PR.
- **No X7 gate on automation `create_plan`.**
  - The persistence detector runs for configuration-contract plans
    (`_create_configuration_plan`), dashboard plans and resource validation,
    as in beta.19.
  - The legacy automation `create_plan` has no detector gate, also as in
    beta.19. So marker-like text in an automation description is still
    plannable.
  - The plan's approval note and the views of it are strict at write time,
    and D11 at re-read.
- **Audit registry scope.** `AuditLogger.sanitize` and `write_batch` take the
  request's record. Audit entries written outside a request get no record,
  so any marker-like text in them collides. The structured log formatter
  (`logging_config.JsonFormatter`) reads the record attached to the log
  record, if any.
- **Truthful counters (D3).**
  - `marker_collisions` counts each string replaced by a collision.
  - `marker_collision_refusals` counts each governed path that refused
    because of redaction or a collision.
  - Both are process-lifetime counts with no content. Ordinary data moves
    neither.
- **Finding 11.** When redaction applies, the generic delegated read, the
  held-read canary, the YAML reader envelope and dashboard get/list report:
  - `completeness: partial`;
  - a bounded `metadata.redaction` summary (`RedactionTally`, at most
    `MAX_REDACTION_CATEGORIES` categories with counts);
  - for the delegated read, the `REDACTION_WARNING` warning.

## 5. Changed outputs (compatibility)

**Corpus.** `docs/evidence/hamcp191/generate_corpus.py record` ran on the
exact base. Its fixture is `tests/fixtures/hamcp191/compat_corpus.json`, and
`compare` on this tree writes `docs/evidence/hamcp191/corpus-comparison.json`.

- **Result:** 61 samples. 50 are byte-identical (37 ordinary, 13 plain
  redactions), 11 changed by decision, 0 unexpected.
- **Entry points compared:**
  - `sanitize_untrusted_data`
  - `redact_data`
  - `redact_untrusted_text`
  - the logbook projection
- **The changed samples:**

| Sample | Reason | Effect |
|---|---|---|
| `p1_wrapped_known_secret`, `p1_wrapped_known_secret_key` | P1 | The base leaked the wrapped known secret; the head does not |
| `p2_anchor_split_bearer`, `p2_anchor_split_webhook` | P2 | The base leaked the split secret; the head does not |
| `forged_exact_marker`, `forged_marker_key`, `forged_encoded_marker`, `forged_fullwidth_marker` | D1 | A forged marker collides instead of being trusted by text |
| `forged_marker_beside_text` | D2 | The whole string is replaced |
| `lookalike_prose_parenthesized`, `lookalike_prose_truncated` | D3 | Look-alike prose collides |

**Tool-level output changes outside the corpus:**
- `get_server_health` gains the additive
  `sanitization: {strict_marker_mode, marker_collisions,
  marker_collision_refusals}` (D3).
- The generic delegated read (for example `ha_search`), the held-read canary,
  the YAML reader and dashboard get/list now report `partial`, plus
  `metadata.redaction`, plus (delegated read) `REDACTION_WARNING`, when
  redaction applied. Before, a redacted read could report `complete`
  (Finding 11).
- Incident correlation shows `(authorization header withheld)` instead of a
  marker-shaped placeholder (X2).
- The X1 evidence-truncation metric now counts from the structural flag.
  Upstream text containing `... [truncated at ` no longer moves it.
- Marker-like text anywhere in untrusted data, including keys, encoded and
  folded spellings, becomes `[REDACTED:redaction_marker_collision]`. Governed
  write paths with a detector gate refuse it (D3).

**Existing tests whose pins changed:**
- `tests/test_beta11_sanitization.py` (3: strict default)
- `tests/test_hamcp162_yaml_reader.py` (4: default strict, odd key, audit
  partial, genuine markers partial)
- `tests/test_automation_reliability_analysis.py` (1)
- `tests/test_hamcp171_health_verdict_cache.py` (memo test: strict takes the
  memo, cross-pass reuse refused)
- `tests/test_readonly_upstream_gateway.py` (1: the Finding 11 warning comes
  before the partial-coverage warning)

## 6. Mutations

`docs/evidence/hamcp191-mutations/run_mutations.py` applies each mutation to a
disposable copy outside the repository and runs the three HAMCP-191 test
modules. Results are in `docs/evidence/hamcp191-mutations/results.json`.

- **Brief:** U1 (trust a marker by text), U2 (skip the identity check), U3
  (collision before the rules), U4a/U4b (raw key written back), U5a–U5d
  ("complete" despite redaction on four paths), U6 (matrix cell removed).
- **Design:** M1–M18, several with variants.
- **D11:** D11a (allow list widened), D11b (the re-scan loses its tolerance),
  D11c (write time tolerant), D11d (the stored flag ignored).
- **Counters and wiring:** C1–C6, W1, W2.
- **M5 as worded** ("dedup by text instead of by type") is an equivalent
  mutant. A string piece equal to marker text exists only when the original
  string held marker text, and `_scan` returns the collision before
  deduplication runs. It is recorded as `equivalent`. **M5b**, which merges
  different emitted markers, is the reachable dedup mutation and is killed.
- **M12 as designed** (the `_review_summary` `[:500]` slice) is on the D11
  path, unchanged from beta.19. It is replaced by the reachable post-pass
  slice: the identity-hit branch slicing instead of calling `_truncate`.
- **M18** is killed both by a source check of the dispatcher and by the
  helper's behavioral flag test (M18b).
