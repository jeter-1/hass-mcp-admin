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
- Plan creation is strict for every plan family (section 7, Codex H1).
  The save-time gate is the tolerant D11 re-scan and is not the creation
  gate, so each creation path runs the strict detector itself before any
  supersession, persistence or response:
  - configuration-contract plans (`_create_configuration_plan`, which also
    serves entity metadata and the F3 rollback paths), as before;
  - legacy automation plans (`create_plan`): title, description, the
    persisted (truncated) text, the proposal and the Home Assistant current
    automation;
  - dashboard plans: title, description and patch values, before any read;
  - operational plans (backup, helper state, reload, add-on restart, Home
    Assistant restart): the complete new record, through
    `_require_strict_new_plan`.
  Caller context keeps only entries the strict pass leaves unchanged.
  So a new plan can no longer carry a forged marker or a wrapped secret
  into storage. Beta.20 at `6212b7c` claimed this but did not meet it for
  the legacy automation and operational families.
- The tolerance applies only when a stored plan is re-read, which is the
  scope Josh's D11 deferred. It is still justified: every record it
  tolerates was either written before beta.20 or passed the strict creation
  gate, and a secret between markers is still refused.
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
| `request_context.py` | D4: `RequestTelemetry.sanitizer_registry` (a new record per request). Correction batch 3 (H4, section 9): the inbound request ID is screened once at request start |
| `routing.py` | D4: the request audit write passes the request's record. Correction batch 3 (H4): both gateway entry points pass the known secrets to the request-start screen |
| `models/responses.py` | X1: `dump_json_with_truncation` returns a structural truncation flag |
| `health.py` | D3: the `sanitization` counters in `get_server_health`; correction batch 3 adds `request_id_replacements` (H4) |
| `governance/resources.py` | D11 `stored_plan` flag; D3 refusal counter at the persistence gate |
| `governance/config_validation.py`, `governance/operational_lifecycle.py` | D4: the two bounding passes of one check share a record |
| `f3_dashboard/semantic_diff.py` | D4: the `<redacted>` preview is one registered constant |
| `f3_dashboard/approval_projection.py` | D3: the refusal counter at the X7 projection gate |
| `dependency/extraction.py`, `dependency/index.py`, `dependency/models.py` | D4: one record per dependency snapshot build (`bind_sanitizer_registry`); impact and integrity read it through `inherits=` |
| `dependency/script_calls.py`, `dependency/script_sources.py` | D3: the refusal counter at the script X7 gates |
| `providers/upstream_dashboard.py` | Finding 11: dashboard get/list report `partial` and `metadata.redaction` when redaction applied, including warnings, the hint and the upstream identity (Codex M1) |
| `providers/core_logs.py` | Josh's direction after the Codex review: per-line Core log sanitization with bounded line counts (section 7). New to the pull request in the correction batch. |
| `governance/storage.py` | Architect Direction A (section 8): the one plan-persistence chokepoint (`ChangePlanRepository.save` screens every new string), the exempt-field schema and the refusal type. New to the pull request in correction batch 2. |
| `tools/governance.py` | Codex H1g (section 8): identifiers echoed in response metadata pass the strict check and are withheld when flagged. New to the pull request in correction batch 2. Correction batch 3 (H1g-residual, section 9): the tool-boundary identifier screen. |

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
- **Core log reads (owner direction, section 7; Codex H3, section 8).**
  `CoreLogReader.evidence` sanitizes the window in two passes
  (`sanitize_log_lines`). Pass 1 runs every rule over the whole window, so a
  credential spanning an LF or CRLF line break is still found; each
  replacement keeps the line breaks it consumed. Pass 2 decides marker
  look-alikes per original line: a marker-like line collapses only that
  line to the collision marker (keeping its CRLF terminator half), and Home
  Assistant's `**REDACTED**` lines stay byte-identical. The response carries bounded
  `redacted_line_count` and `collided_line_count` values and the
  `redaction` summary, and is always `partial`.
- **Strict gate on every plan creation (Codex H1).** See D11 above and
  section 7. Marker-like text in an automation title or description is no
  longer plannable; it is refused before any Home Assistant read.
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
  - Any redaction anywhere in the response counts (Codex M1): dashboard
    get/list tally upstream warnings, the hint and the upstream identity
    with the data, and the held-read canary carries the summary, including
    when its result is omitted for size.
- **Structured-log counters (Codex L1).** `log_event`'s first pass is the
  strict `sanitize_untrusted_data`; its tally travels on the log record and
  `JsonFormatter` reports it with the formatter's own pass. Markers from the
  first pass are identity hits in the second and are not counted twice.

## 5. Changed outputs (compatibility)

**Corpus.** `docs/evidence/hamcp191/generate_corpus.py record` ran on the
exact base. Its fixture is `tests/fixtures/hamcp191/compat_corpus.json`, and
`compare` on this tree writes `docs/evidence/hamcp191/corpus-comparison.json`.

- **Result:** 85 samples. 68 are byte-identical (45 ordinary, 23 plain
  redactions), 17 changed by decision, 0 unexpected.
- **Correction batch 3 samples (section 9):** ordinary request IDs, entity
  IDs, `url_path`, caller ID and labels (byte-identical), and a JWT request
  ID, a known-secret request ID, a known-secret entity ID and a
  known-secret dashboard `url_path` (redacted, byte-identical). The
  request-start, tool-boundary and pre-read screens decide on these
  sanitizer outcomes, so no sanitizer output changes in batch 3.
- **Correction batch 2 samples (section 8):** a `core_log_window` entry point
  (the exported text of a complete Core log window: the base sanitized it
  as one string, the head runs `sanitize_log_lines`); ordinary LF and CRLF
  windows with Home Assistant's placeholder (byte-identical); Codex H3's LF
  and CRLF anchor-split credentials and same-line controls (byte-identical:
  the base also scanned the whole window, so H3 was a regression inside
  this pull request, not relative to the base); Codex H1g's known-secret
  identifier (redacted, byte-identical).
- **Codex review and Home Assistant samples:** Home Assistant's own
  `**REDACTED**` placeholder in three log lines, a Core log window as a
  list of lines, and Codex's template sample stay byte-identical. The
  placeholder as a serialized token value (`'token': '**REDACTED**'`) is
  redacted by the assignment rule, exactly as in beta.19. Codex's dashboard
  `(redacted)` and attribute `<redacted>` prose change by D3. The review
  kept only their outputs, so the inputs here carry the same keys and the
  same kind of prose.
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
| `lookalike_prose_parenthesized`, `lookalike_prose_truncated`, `lookalike_dashboard_markdown`, `lookalike_attribute_description` | D3 | Look-alike prose collides |
| `h1g_wrapped_identifier`, `h2_wrapped_backup_evidence` | P1 | Codex H1g and H2 shapes: the base leaked the wrapped known secret; the head does not |
| `h3_core_quoted_multiline` | H3 | A quoted value spanning lines is redacted as before; the window now keeps its line count (`core_log_window` only) |
| `cl1_core_collision_line` | CL1 | A marker-like Core log line collapses alone; the base passed it through unchanged as plain text |

**Tool-level output changes outside the corpus:**
- `get_server_health` gains the additive
  `sanitization: {strict_marker_mode, marker_collisions,
  marker_collision_refusals}` (D3).
- The generic delegated read (for example `ha_search`), the held-read canary,
  the YAML reader and dashboard get/list now report `partial`, plus
  `metadata.redaction`, plus (delegated read) `REDACTION_WARNING`, when
  redaction applied. Before, a redacted read could report `complete`
  (Finding 11).
- `get_core_log_history` sanitizes each line independently and adds
  `redacted_line_count`, `collided_line_count` and `redaction`. Lines now
  split on `\n` only; a `\r` stays inside its line (before, `splitlines`
  also split on it), and a collided CRLF line keeps it.
- New plans whose caller-supplied or Home Assistant-derived text is
  strictly flagged are refused (section 7). Caller context entries the
  strict pass would change are dropped.
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
- `tests/test_beta25_external_approval.py` (1: the review-rendering test
  first asserts that a legacy plan whose title holds a configured known
  secret is refused with `AUTOMATION_VALIDATION_FAILED` and nothing is
  stored, then renders a plan without the secret; its approve note is built
  at runtime). The exact base stored and returned that secret raw (H1).
- `tests/test_beta11_sanitization.py`, `tests/test_automation_reliability_analysis.py`
  and `tests/test_hamcp171_health_verdict_cache.py` also build their
  secret-shaped inputs at runtime now (Codex L2); every value is unchanged.
- Correction batch 2 (section 8), three existing non-protected test files
  that planted deliberately unsafe or malformed records through
  `repository.save`. The chokepoint now refuses that as new content, so they
  plant the record as already-stored bytes through `plant_stored_record`,
  which first asserts that `save` refuses and leaves the stored bytes
  unchanged:
  - `tests/test_dev14_configuration_plans.py` (2: the corrupt contract-v2
    current and proposed configuration tests; the helper lives here);
  - `tests/test_governance_async_health.py` (1);
  - `tests/test_2_1a_beta2_operational_lifecycle.py` (1 test, 3 plants:
    historical apply events with an empty or malformed request id and a
    malformed timestamp).
  The same change applies to `tests/test_beta25_external_approval.py`
  (already in this pull request). Its beta.24 rollback-pending test plants
  a legacy record whose `expected_current_fingerprint` is `"legacy"`, which
  is not a sha256 value, so the exempt-field format check refuses it.

## 6. Mutations

`docs/evidence/hamcp191-mutations/run_mutations.py` applies each mutation to a
disposable copy outside the repository and runs the HAMCP-191 test
modules. Results are in `docs/evidence/hamcp191-mutations/results.json`:
77 mutations, 76 killed, 0 survived, 1 equivalent (M5).

The copy is a throwaway git repository whose object store borrows this
repository's objects through `objects/info/alternates`. The suites that
read a historical revision with `git show` therefore run in it, while the
real repository's HEAD and index stay out of reach.

- **Brief:** U1 (trust a marker by text), U2 (skip the identity check), U3
  (collision before the rules), U4a/U4b (raw key written back), U5a–U5d
  ("complete" despite redaction on four paths), U6 (matrix cell removed).
- **Design:** M1–M18, several with variants.
- **D11:** D11a (allow list widened), D11b (the re-scan loses its tolerance),
  D11c (write time tolerant), D11d (the stored flag ignored).
- **Counters and wiring:** C1–C6, W1, W2.
- **Codex review (section 7):** R1 and R2 (the reviewer's own probe
  mutants), H1a–H1f (each strict creation gate bypassed, both caller
  context filters reverted), M1a (dashboard warning and hint redaction not
  counted), M1b (canary summary dropped), L1 (first-pass tally dropped) and
  CL1 (Core log window sanitized as one string).
- **Correction batch 2 (section 8):**
  - A1a (restart recovery writes the record directly);
  - A1b (one service writer writes applied plans directly; caught by the
    runtime spy);
  - A2a (identifiers unscanned);
  - A2b (a schema-exempt field format-checked but not scanned);
  - A3 (an apply-time write persisted unscreened);
  - H3 (per-line-only credential detection).
- **Batch-2 review probes:** R3a (the chokepoint bypassed on update) and R3b
  (an exempt format prefix-matched instead of fully matched). The reviewer's
  exact patches were not retained, so both are reconstructed from their
  names.
- **Correction batch 3 (section 9):** B3-H4 (the request id left
  unscreened), B3-H1g (the identifier boundary skipped for
  `create_helper_state_plan`), B3-M2a (the dashboard artifact written before
  the plan gate), B3-M2b (the pre-read gate missing the caller id) and B3-L3
  (the contract-v2 condition on the D11 re-check restored).
- **A2a after L3.** Once the D11 stored-plan check also ran for contract-v1
  records, it caught the known secret in the existing repository test on its
  own, and the first batch-3 run reported A2a as a survivor. It is not
  equivalent: D11 tolerates a forged marker, and only the strict identifier
  scan refuses it.
  `test_strict_identifier_scan_refuses_a_forged_marker_target` pins this.
- **H1b and H1e after the chokepoint.** Once the repository refuses new
  sensitive content, bypassing a creation gate no longer persists anything,
  and the first batch-2 run reported both mutants as survivors. Neither is
  equivalent, and each gained a test for the behavior only the gate
  provides:
  - H1b: the legacy gate refuses a secret-bearing current automation with
    `AUTOMATION_VALIDATION_FAILED`, naming the current automation. The
    chokepoint alone gives a generic `CONFIGURATION_VALIDATION_FAILED`.
  - H1e: operational creation supersedes the prior plan before its first
    save. Without the gate, a refused plan would first supersede a pending
    plan for the same target, discarding its approval.
    `test_a_refused_plan_never_supersedes_the_prior_plan` pins this, with an
    ordinary replacement as the control.

  Both tests also assert that the repository refused nothing
  (`content_refusals == 0`).
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

## 7. Codex review corrections (review of `6212b7c`)

The review in `~/handoffs/hass-mcp-admin-HAMCP-191-review.md` reported one
High, one Medium and two Low findings. Josh added the per-line Core log
direction. Every reproduction is a permanent test.

**H1 (High): legacy planning persisted a wrapped known secret.**
- Cause: the save gate (`_require_safe_persisted_plan`) is the tolerant D11
  re-scan and returns early for contract-1 plans, and `create_plan` had no
  detector gate of its own.
- Fix: strict gates at creation, before any supersession, persistence or
  response, in every family (D11 above). The sweep of creation paths found
  the operational families (backup, helper state, reload, add-on and Home
  Assistant restart) also unchecked for Home Assistant-derived text such as
  a version, a location name, a helper's `last_changed` or a dependency
  profile; `_require_strict_new_plan` now checks their complete record.
- Tests (`tests/test_hamcp191_families.py`): `PlanCreationGateTests`
  (Codex's title and `provider_current` reproductions; description,
  proposal, split anchor and forged marker; caller context; dashboard title
  and description refused before any read; an ordinary legacy plan
  unchanged) and `OperationalCreationGateTests` (backup, helper state,
  reload and Home Assistant restart: refused when injected, created when
  ordinary). Every refusal leaves the repository empty and does not echo
  the secret.
- Paths reviewed and already strict or literal-only: configuration-contract
  plans (also entity metadata and F3 rollback), the dashboard review
  projection, F3 runtime re-saves and storage-recovery saves. Apply-time
  verification evidence written into an existing plan was not traced and is
  outside this correction.

**M1 (Medium): redaction outside the data did not mark a response partial.**
Dashboard get/list now tally warnings, the hint and the upstream identity;
the held-read canary carries `metadata.redaction`. Tests: warning or hint
redaction makes get and list partial; ordinary warnings stay complete; the
canary reproduction, an omitted canary result and an ordinary canary.

**L1 (Low): structured-log counters.** See section 4. Test:
`test_structured_log_counters_are_truthful` (ordinary, collision, plain
secret).

**L2 (Low): secret-shaped literals.** The 12 literals in three existing test
files are built at runtime from `tests/hamcp162_synthetic.py`. The committed
file scan now covers every file this pull request changes
(`HAMCP191_CHANGED_FILES`) and reports no hits.

**Owner direction: Core log lines.** See section 4. Tests: a window with an
ordinary line, Home Assistant's `**REDACTED**` placeholder (kept verbatim),
two marker-like lines (collided alone) and an anchor-split secret line
(redacted, its prefix kept); an ordinary window is unchanged and has no
summary.

**Home Assistant's `**REDACTED**`.** The placeholder is not marker-shaped, so
log lines and diagnostics containing it pass unchanged and do not collide.
Serialized as a token value (`'token': '**REDACTED**'`), the assignment
rule redacts the value, as beta.19 did.

**Behavior narrowing to note.**
- Legacy and operational plans whose text the strict pass flags (D3
  look-alike prose, a sensitive key in the current automation, marker-like
  Home Assistant text) are refused instead of created.
- A configured known secret in a legacy plan's title or description is
  refused. The exact base stored and returned it raw; PR CI on the first
  correction head caught the old `test_beta25_external_approval` pin.
- Strictly flagged caller-context entries are dropped.
- Core log lines split on `\n` only.

## 8. Codex delta corrections (review of `89d6ed4`)

The delta review in `~/handoffs/hass-mcp-admin-HAMCP-191-review.md` reported
three High findings: H1g, H2 and H3. Each was fixed structurally, following
the architect's Direction A (one plan-persistence chokepoint) and Direction
B (two Core log passes). Each reproduction is now a permanent test in
`tests/test_hamcp191_plan_chokepoint.py`.

**Direction A: one plan-persistence chokepoint (`governance/storage.py`).**
- **One function.** Every write of a governance plan record goes through
  `ChangePlanRepository.save`, which calls `_screen_new_content`, which calls
  `sanitization.screen_plan_content`. That covers:
  - creation, updates and approvals;
  - apply-time verification and backup evidence;
  - lifecycle and dispatch receipts, failure information and events;
  - rollback plan creation;
  - storage-recovery writes (`_recovery_save`).
- **What is new.** A string equal to the stored record's string at the same
  JSON path is old content. It is written back unchanged, and only D11
  re-reads old content. Every other string, including keys and identifiers,
  runs the strict scan, which applies:
  - the known secrets;
  - every credential rule;
  - the marker look-alike collision.
  A producer's own sanitized output objects are kept through the request's
  identity record (`plan_output_registry`).
- **Refuse or redact, per field:**

  | Write | Field | Effect of a finding |
  |---|---|---|
  | Creation (no stored record) | any | refused: nothing is written |
  | Update | `target`, `title`, `description`, `requested_by` | refused, stored record unchanged |
  | Update | `proposed_config`, `current_config`, both normalized forms, `snapshot.config` (plan and each operation) | refused |
  | Update | operation identity: `operation_id`, `depends_on`, `resource_type`, `action`, `target_id`, `helper_type` | refused |
  | Update | `operational.family`, `operation`, `requested_name`, `provider` | refused |
  | Update | any exempt dispatch or recovery field (below) | refused, never rewritten |
  | Update | everything else: verification evidence, execution receipts, failure information, event and dispatch text, notes | redacted in place; the stored record and the caller's in-memory plan are the redacted form |

  Refusing identity and hash-bound configuration keeps a redacted copy from
  passing for the same approved plan. After the strict pass, a contract-v2+
  record still passes the D11 stored-plan check over its whole content.
- **Service mapping.**
  - A creation refusal is `CONFIGURATION_VALIDATION_FAILED`
    (`plan_contains_prohibited_sensitive_data`). Legacy creation is refused
    earlier, by its strict input gate, now with the identifier.
  - An update refusal is `CHANGE_PLAN_STORAGE_ERROR`
    (`unsafe_persisted_configuration_plan`).
  - A refused recovery write is counted (`recovery_refusals`), and startup
    continues.
  - Counters: `content_redactions`, `content_refusals`, `recovery_refusals`.
  - No error carries content.
- **Exempt-field schema.** These dispatch and recovery fields are kept
  byte-exact:
  - 72 schema paths (`EXEMPT_FIELDS`), each mapped to one of 14 value
    formats (`EXEMPT_FORMATS`);
  - the formats: plan id (32 hex), sha256, ISO timestamp, request id
    (`request_context.REQUEST_ID_PATTERN`), approval challenge id, and nine
    enums.

  An exempt value is never rewritten:
  - A value outside its format refuses the write.
  - A value inside its format is still strict-scanned, and any finding
    refuses. A JWT fits the request-id alphabet, which is why the scan is
    kept.
  - Non-string dispatch facts (counts, flags) are not strings and are not
    rewritten.

  Proof (`ExemptSchemaTests`):
  - Every exempt path resolves through the model dataclasses to a `str` or
    enum leaf, or to a pinned mapping key.
  - No format accepts whitespace, brackets, quotes, `=`, `@`, `%`, `/`,
    `+`, a forged marker, a bearer credential, an assignment or the empty
    string.
  - Each enum format accepts exactly its enum.
- **Enumeration test.** It has two parts:
  - **Static.** `WriterInventoryTests` parses the package:
    - Every `.save(` call must be in the committed table. Exactly two
      callers write plan records: `ChangeGovernanceService._save` and
      `ChangePlanRepository._recovery_save`.
    - `governance/storage.py` may write files only through `save` (`open`
      mode `x`, then `os.replace`), `_quarantine` (`os.replace` into the
      quarantine directory) and, since HAMCP-213 (H6),
      `_restore_quarantined_plan` (`os.link` of a quarantined record back to
      its plan name, never overwriting, only after the stored-plan screen in
      `_verified_quarantined_bytes`).
  - **Runtime.** `RuntimeWriterSpyTests` runs the 14 tests in `SPY_TESTS`.
    While they run, it spies on `os.replace`, `builtins.open` and `io.open`.
    A violation is either of:
    - package code outside `ChangePlanRepository.save` opening a file for
      writing in a plan root;
    - a replaced plan file whose payload was not the screen's output.

    The spy also asserts its own coverage. Every one of the 27 entries in
    `SPY_REQUIRED_WRITES`, a (package caller, operation, status) triple, must
    be observed, and so must both plan writers.

    The set comes from a profile. The spy first ran seven whole governance
    suites (149 tests, about 1,240 s locally). CI cancelled the shard holding
    it at the job's 20-minute limit (run 39195210311 on `f851ec0`). A
    per-test profile of those suites recorded every package plan save, and
    exactly these 27 triples were observed. The governed-rollback suite took
    about 1,190 s and added none of its own. `SPY_TESTS` is the cheapest
    covering set (about 6 s).

  A bypassing writer fails CI (mutations A1a and A1b).

**H1g: identifiers.**
- Legacy creation's strict input gate now includes `automation_id`. It runs
  before any Home Assistant read, supersession or write, and the chokepoint
  refuses a secret-bearing target at creation in any case.
- Identifiers echoed in response metadata also pass the strict check
  (`tools/governance.py` `_identifier_metadata`). A flagged `resource_id` is
  withheld on success and on refusal, and it is also withheld when no
  governance service is available (fail closed). The service's
  plan-not-found details withhold a flagged plan id (`_identifier_details`).
- Tests (`IdentifierCreationTests`), all through the registered tool:
  - Codex's valid known-secret ID and invalid wrapped ID: both are refused,
    no record or file is written, the ID is not echoed, and there are zero
    provider writes.
  - An ordinary ID is created and echoed.
  - A known secret in a plan-id lookup is not echoed by `get`, `approve`,
    `apply` or `rollback`.
  - Metadata is withheld without a service.
  - The repository refuses a secret target before any file exists.
- Base evidence: the delta review's own probe (reviewer artifact
  `creation_id_probe.py`, under `.artifacts/reviews/pr280-89d6ed4/`) showed
  that the exact base persisted and echoed both IDs. On the previous head
  `89d6ed4`, every `IdentifierCreationTests` reproduction fails, and only
  the ordinary-ID control passes. This module cannot import on the base,
  because it needs this pull request's family harness. Corpus sample
  `h1g_wrapped_identifier` (P1) records the base sanitizer keeping the
  wrapped ID.

**H2: apply-time evidence.**
- Fresh backup read-back evidence is new content on an update, so the
  chokepoint redacts it in place before storage.
- Dispatch facts are untouched, so a resumed apply reads back again and
  never redispatches. The lifecycle evidence assignment Codex flagged uses
  the same save.
- Test (`ApplyTimeEvidenceTests`), through the real
  `BackupAdministrationGateway`, synthetic provider I/O and real external
  approval:
  - The read-back carries a known secret wrapped in canonical marker
    syntax, so verification stays required.
  - The durable JSON, public `get_plan` and errors are clean, and the
    evidence holds the collision marker.
  - The attempt count is still one.
  - A resumed apply does not redispatch.
  - A clean read-back then completes the plan.
- `ChokepointUnitTests` covers:
  - in-place redaction of new evidence;
  - refusal on identity paths with the record kept;
  - exempt values refused, never rewritten;
  - old stored marker content kept while only new content is screened;
  - a refused recovery write counted while startup continues.

**H3: Core logs in two passes (`sanitize_log_lines`).** See section 4.
- Pass 1 scans the whole window, LF or CRLF, so an anchor and its value on
  different lines, or a quoted value spanning lines, are redacted. The
  replacement keeps the consumed line breaks.
- Pass 2 decides marker look-alikes per original line. A look-alike formed
  only across two lines collapses both; one no pair explains collapses the
  window (fail closed).
- Home Assistant `**REDACTED**` lines stay byte-identical, and
  `redacted_line_count` and `collided_line_count` stay truthful.
- Tests (`CoreLogSpanningTests`, behind the real `AuthenticatedMcpGateway`):
  - LF anchor, CRLF anchor and quoted multi-line credentials are redacted,
    with no secret anywhere in the serialized output.
  - Same-line LF and CRLF controls.
  - A CRLF window whose marker-like line collides alone: exact output, the
    ordinary and placeholder lines intact, one collided line.
- The exact base sanitized the window as one string and redacted these
  cases; the regression was this pull request's earlier per-line split.

**Narrowing to note.**
- New plan content that the strict pass flags is refused or redacted
  according to the table above.
- A deliberately unsafe or malformed record can no longer be planted through
  `save`. Four existing test files now plant such records as stored bytes
  (section 5).

**Limitations (outside Direction A).** These were reviewed and left
unchanged in batch 2:
- `f3_dashboard` `DashboardArtifactStore` artifacts are strict-gated before
  creation and are not plan records. (The Antigravity review found the
  gate incomplete; corrected in section 9, M2.)
- `ExecutionTaskRepository` task records hold dispatch state and their own
  event text. They do not pass this chokepoint. (The Antigravity review
  found that a request ID reached them unscreened; corrected in section 9,
  H4.)
- Entity IDs echoed in error details by `governance/entity_metadata.py` and
  `impact/provider.py`, and execution-task IDs echoed by the
  `get_execution_task` and `cancel_execution_task` tools (schema-bound to 32
  hex characters, so only a hex-shaped known secret could appear), are not passed
  through the identifier check. The tool-level entity, automation,
  dashboard and plan identifiers are.
- `tests/test_dev14_configuration_plans.py` is not in the committed-file
  literal scan. This pull request adds only its `plant_stored_record`
  helper, and the file's pre-existing main-branch fixtures carry
  secret-shaped refusal inputs by design.

## 9. Antigravity delta corrections (correction batch 3, review of `7e0c86f`)

The Antigravity delta review (`~/handoffs/hass-mcp-admin-HAMCP-191-antigravity-review.md`)
reported H4 and H1g-residual (High), M2 (Medium) and L3 (Low). Each
reproduction is now a permanent test in `tests/test_hamcp191_batch3.py`, and
each fix has a mutation (section 6).

**H4: the inbound request ID is screened once, at request start.**
- `REQUEST_ID_PATTERN` admits JWTs, Matter payloads, `name:value`
  assignments and configured secrets. Before this batch an unscreened ID
  reached `ExecutionTaskRepository` (`execution_request_id`,
  `lifecycle_events[*].request_id`) and the `get_execution_task` and
  `list_execution_tasks` responses.
- `request_context.begin_request(candidate, known_secrets=...)` now runs
  the strict sanitizer (`request_id_is_safe`) over a well-formed inbound ID.
  A failing ID is replaced by an Engineering-generated ID (`uuid4().hex`)
  before anything else reads the request context. Nothing keeps the
  original value. The replacement is recorded only as:
  - `RequestTelemetry.inbound_request_id_replaced` (a flag);
  - `request_id_replacements` in the `get_server_health` `sanitization`
    block (a process counter).
- The gateway passes `access_secret` and `ha_token` (the governance
  `sensitive_values`) on both entry points: an ordinary request and the
  inbound-denial path. The `x-request-id` response header carries the
  generated ID.
- A malformed ID was already regenerated by `normalize_request_id`. It is
  not counted as a replacement. An ordinary ID passes unchanged.
- Every other `begin_request()` caller generates its own ID, and
  `reverify_configuration_task`'s caller-supplied `request_id` is screened
  at the tool boundary (H1g-residual).
- Tests: `RequestIdScreenTests`, `ExecutionTaskRequestIdTests` (the apply
  and cancel reproductions, through the real backup lifecycle; the token
  is absent from the plan root, the execution-task files, the audit log,
  `get_execution_task` and `list_execution_tasks`), and
  `GatewayRequestIdTests` (the real `AuthenticatedMcpGateway`).

**H1g-residual: identifiers are screened at the tool boundary.**
- `tools/governance.py` `IDENTIFIER_INPUTS` lists every caller-supplied
  identifier of every governance tool. It covers entity, automation,
  add-on, dashboard, plan, task, plan-hash and request IDs; configuration
  operation identity; patch identity and paths; labels; categories (keys
  and values); areas; and the status filters.
- `screen_identifiers` runs before any tool work (`_screened` wraps each
  tool's action). It applies the strict sanitizer with the governance
  `sensitive_values`. A finding refuses with `INVALID_REQUEST` and the fixed
  details `{"reason": "identifier_contains_prohibited_sensitive_data"}`.
  Nothing is echoed, and the service is never called.
- This closes the `no_change` shortcuts that echoed `target_id` before the
  strict creation gate: `create_helper_state_plan` (already in the desired
  state) and `create_entity_metadata_plan` (already satisfied).
- **Refusal code.** A secret-bearing identifier is now refused at the
  boundary with `INVALID_REQUEST` instead of the service's own code (for
  example `AUTOMATION_VALIDATION_FAILED` for a legacy automation ID). The
  service gates are unchanged and still refuse when called directly.
  `tests/test_hamcp191_plan_chokepoint.py` asserts both.
- Free text and configuration (`title`, `description`, `name`, `aliases`,
  `approval_note`, `proposed_config`, patch `value`, `caller_context`) are
  not identifiers. No early return echoes them, and the creation gate and
  the plan chokepoint screen them as before.
- Tests:
  - `HelperStateNoChangeTests` and `EntityMetadataNoChangeTests`: both
    reproductions are refused with nothing echoed, the service is not
    called, and no plan exists. Ordinary identifiers still return
    `no_change` with their `target_id`.
  - `IdentifierBoundaryEnumerationTests`:
    - Every tool's signature parameters and nested TypedDict keys are
      classified exactly once, as identifier or non-identifier, and the
      served entity-metadata schema has no extra input.
    - For every identifier path, a known secret and a JWT (as a list item
      or mapping key and value where the input is a list or mapping) are
      refused before any service call.
    - Ordinary arguments reach the service.

**M2: no orphaned dashboard artifact.**
- The dashboard pre-read strict gate now also covers `url_path` and the
  caller ID (`requested_by`). A secret there is refused before any
  dashboard read, plan or artifact.
- The artifact is written only after the complete plan passes the strict
  creation gate. The plan binds the payload hash that
  `private_proposal_projection` yields, and the hash the store returns is
  checked against it.
- **Residual.** If the artifact write succeeds and the following plan save
  (`_record`) fails on storage I/O, the artifact remains under a plan ID
  that no plan record references. It holds only content that passed the
  pre-read gate and the strict plan gate. Every content-based refusal
  happens before the write.
- Tests (`DashboardArtifactOrderTests`): a caller ID or `url_path` secret
  is refused with zero reads and no plan or artifact. A forced plan-gate
  refusal after the read leaves no artifact. An ordinary plan writes exactly
  one artifact bound to the plan.

**L3: the D11 re-check runs for every contract version.** The
`plan.contract_version >= 2` condition is removed from
`ChangePlanRepository._screen_new_content`. `ContractV1StoredPlanTests`
plants a contract-v1 record holding a known secret in an unchanged string.
An update is refused (`creation=False`), the refusal is counted, and the
stored bytes are unchanged. An ordinary v1 record still updates.

**Main merge (`0c21062`).** `origin/main` `a1236c4` (HAMCP-206, PR #279,
test isolation) merged without conflicts. Its leak guard
(`tests/support/process_isolation.py`) runs around every test. Its probes
cover the catalog globals, the server session manager, the environment,
the working directory, `sys.path`, `sys.modules`, warning filters, logging
and the event loop. The batch-3 tests restore every request context they
open. The `request_id_replacements` counter is a monotonic health counter,
which the probes do not track, and the tests assert deltas only.
