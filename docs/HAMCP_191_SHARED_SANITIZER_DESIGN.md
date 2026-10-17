# HAMCP-191: Identity-Only Redaction Markers for Every Tool (Design Sweep)

Status: design report only. No runtime, test, release, workflow or stable-v1
change is part of this document or its pull request.

| Item | Value |
|---|---|
| Register | HAMCP-191 (Active) |
| Base | `origin/main` `22370ef4687ed500af2f76bb15d66797cf219812` (Engineering `2.4.1-beta.15`) |
| Reference design | PR #267 (HAMCP-162, `2.4.1-beta.16`, in review, not merged), head `2b6e8cbad18cccdf7b2ad27cd086c734d1335f23`, read only |
| Related, not touched | HAMCP-192 branch (separate session) |
| Line references | `main:` = base above; `#267:` = PR #267 head. All paths are under `hass_mcp_engineering_beta/ha_mcp_engineering/` unless they start with `tests/` or `docs/`. |

Content rule for this document: it contains no literal secret-shaped text.
Attack inputs are described abstractly: `<anchor>`, `<lookalike>` and `<secret>`
stand for runtime-built values. The only marker text it quotes is the existing
reviewed constant names, never a marker that wraps a payload.

## 0. Summary

The shared sanitizer `sanitize_untrusted_data` (`main:sanitization.py:388`) is
called by every tool family except the HAMCP-162 YAML reader, and it runs in
its default mode. That mode decides by text shape that a value is already
redacted. Any substring matching the marker grammar (`REDACTION_MARKER`,
`main:sanitization.py:11`) is treated as an immutable delimiter.

- The text inside the marker is never scanned.
- The text on either side is scanned as a separate segment (`main:198-205`,
  `main:302-318`, `main:321-339`).

A scratch probe used runtime-built values and printed only booleans; it is not
committed. It confirmed two leak classes on main:

- **P1, wrapped secret.** A configured known secret whose characters fit the
  marker category grammar (lowercase letters, digits, underscore) is passed
  through verbatim when it is wrapped in marker syntax, both as a value and as
  a mapping key. Long lowercase hex API keys and webhook identifiers fit the
  grammar.
- **P2, anchor split.** `<anchor><lookalike><secret>` leaks in all nine
  anchored cases probed: the `Authorization` header, `Bearer`, `Cookie`, URL
  credential parameter, URL userinfo, webhook path, webhook prose, login-flow
  path, and a serialized password assignment. The other serialized-assignment
  rules share the same anchor-then-value structure but were not probed. The
  split puts the anchor and the secret in different segments. In most cases
  `redacted_field_count` stays 0, so every "unchanged means clean" and
  "nothing redacted means clean" gate also trusts the value.

PR #267's strict mode (`identity_markers_only=True`) closes both classes in
the same probe. It leaves the seven ordinary-data samples byte-identical.

The design below makes that strict pass the only text mode, behind one entry
point. It adds three things the reader did not need:

1. Every rule runs before a collision decision, so counts stay truthful.
2. An identity ledger lets Engineering re-sanitize its own composed output
   without trusting marker text.
3. A per-family rules × positions matrix with a call-site coverage test.

The main compatibility cost is real and is an owner decision:

- PR #267's look-alike detector also matches ordinary prose such as
  parenthesized or angle-bracketed "truncated" and "redacted". It also matches
  Engineering's own truncation suffix.
- Strict mode withholds the whole string on a match.
- So some log lines and some dashboard or configuration text will become a
  collision marker. Detector gates will then refuse plans that contain such
  text.

The work must start after PR #267 merges, because it extends #267's code. It
then ships as the beta after HAMCP-162 and HAMCP-192.

## 1. Inventory of text- and shape-based trust sites (main)

Classification:

- **ID**: identity-based (OK).
- **CONST**: exact reviewed constant (OK).
- **SHAPE**: shape- or text-based; must convert.
- **GATE**: a decision based on the sanitizer result. Safe once the sanitizer
  is strict; the row says what changes.

"All" means every caller of the default pass (see section 2).

### 1a. `sanitization.py` (main)

| # | main:line | Decision | Reaches | Class |
|---|---|---|---|---|
| S1 | 11 | `REDACTION_MARKER` regex: text grammar that defines "already redacted" | All | SHAPE |
| S2 | 198-205 (`_replace`) | Splits on marker-shaped text. Each pattern rule scans only between markers. | All | SHAPE |
| S3 | 207-213 (`_sanitize_segment`) | Known-secret replacement runs per segment. A secret inside marker text, or split across a marker, is never seen (P1). | All callers that pass `known_secrets` | SHAPE |
| S4 | 302-318 | Re-splits on marker text before the serialized-assignment patterns | All | SHAPE |
| S5 | 321-339 (`_sanitize_string`) | Existing marker-shaped text is "immutable delimiters"; it is not scanned and not counted (P1, P2) | All | SHAPE |
| S6 | 12-14, 338 | `REPEATED_REDACTION_MARKER` dedup by text. Can merge upstream look-alikes with real markers. Only removes text. | All | SHAPE (benign; convert for uniformity, as #267 did by type) |
| S7 | 16, 356, 369, 377, 385, 402 | `SANITIZATION_FAILURE_MARKER` constant emitted on failure | All | CONST |
| S8 | 356 | On a per-field exception, writes the **raw mapping key** back unscanned under the failure marker. #267 keeps this branch. | All | SHAPE (restores untrusted text). Convert: scan the key, or emit a fixed failure key. |
| S9 | 348-350 | Sensitive key name: the value is replaced, and the key's own text is written back raw | All | CONST in practice: the normalized key must equal a reviewed set member. #267 strict mode scans it anyway (`#267:sanitization.py:583-588`). Adopt that. |
| S10 | 149 (`_marker`) | Emitted markers are plain f-strings with no identity | All | SHAPE: later passes can recognize them only by text. Convert to emitted pieces (#267 `_Emitted`) plus the ledger in section 3. |
| S11 | 180-184 (`_truncate`) | Appends the `...<truncated>` text suffix. It can cut a marker in half. It runs after dedup. | All with `max_string` | SHAPE: the suffix matches #267's look-alike detector, so a re-scan collides. Covered by the ledger. |
| S12 | 373 | A non-primitive becomes `repr` text, then is scanned | All | OK (scanned) |
| S13 | 126 `redaction_applied` | Counts come only from rule detections; trusted marker text adds 0 | All gates | Correct once strict |

Strict mode on PR #267 changes these sites:

- It converts S2-S6 for opted-in callers: `_strict_string` (`#267:478-532`),
  `marker_like_text` (`#267:181`), and `_Emitted` pieces skipped by type.
- It converts S9 through `_key_text`.
- It adds the ID-class `EngineeringMarker`, `ReferenceMarker` and
  `ReferenceMarkerRegistry` (`#267:sanitization.py:210-284`).

Only the YAML reader opts in (`#267:providers/upstream_yaml_reader.py:506,
1296, 1459-1460`). S8 and S11 are unchanged on #267.

### 1b. Text or shape decisions outside `sanitization.py` (main)

| # | main:line | Decision | Tools | Class |
|---|---|---|---|---|
| X1 | `providers/dispatch.py:129` | Counts evidence truncation by searching rendered output for a fixed text. Upstream data can inflate the metric. | All facilitator-routed tools | SHAPE (metric only). Convert to a structural flag from `dump_json`. |
| X2 | `incident/normalization.py:233-240` (`_bounded_text`) | Second, private regex redactor. Emits marker-shaped text with no identity (category `header`); the result is later re-sanitized. | `incident_correlation` | SHAPE. Route through the shared pass instead. |
| X3 | `providers/upstream_read_gateway.py:2345, 2355` | `_safe_identity_evidence` returns a bracketed sentinel. `_safe_version_evidence` passes it on by set membership. | Upstream handshake identity and version evidence | CONST (raw input cannot reach `:2355` with a `[`, because the regex at `:204` excludes it). Under strict mode the sentinel collides if it is ever re-scanned, so register it. |
| X4 | `upstream_read_gateway.py:2609-2619` | Same pattern, with an invalid-name sentinel | Catalog diagnostics | CONST |
| X5 | `providers/models.py:146-166`, `facilitation/models.py:141-161`, `dependency/extraction.py:4470`, `f3_dashboard/semantic_diff.py:40-56` | Key-name or prefix detectors that emit an angle-bracket `redacted` / `bounded` constant. They only add redaction and never exempt. | Provider metadata, facilitation, dependency, dashboard semantic diff | CONST for the decision. The emitted constant matches the look-alike detector, so a re-scan collides. Register it, or keep it out of re-scanned paths. |
| X6 | Identifier projections that keep a value only if the sanitizer left it unchanged. Clauses: `dashboard_analysis/provider.py:11`; `dashboard_analysis/models.py:71, 111`; `integration_inspection/projection.py:68, 119`; `audit_baseline/capture_provider.py:28`; `dependency/models.py:78, 108`; `dependency/provider.py:133`; `providers/operational_lifecycle.py:1693` | "Unchanged ⇒ keep". Today a look-alike-wrapped value is unchanged, so it is kept. | `dashboard_integrity_analysis`, `get_integration_inspection`, `capture_automation_baseline`, `entity_dependency_analysis`, add-on restart plans | GATE. Becomes safe when the pass is strict. Output changes to digest or redacted state for look-alike identifiers. |
| X7 | "`redaction_applied` ⇒ refuse" detectors: `governance/resources.py:1215` (plan persistence); `f3_dashboard/approval_projection.py:322`; `upstream_read_gateway.py:3477` (`require_unmodified_script`); `dependency/script_calls.py:33, 43`; `dependency/script_sources.py:117`; `governance/service.py:450, 2943, 5237` | The detector trusts "nothing counted". Today P1/P2 inputs pass. | Change plans, dashboard approval, dependency script read, plan projections | GATE. Becomes safe when strict. Now refuses look-alike text (see section 4, D3). |
| X8 | `governance/semantic_projection.py:113-128` | If nothing is redacted, keeps the **raw** value in the approval projection | Approval review page, change-plan semantic diff | GATE. Highest-consequence gate on main: P1/P2 inputs reach the approver page raw. Safe when strict. |
| X9 | Re-sanitization of already-sanitized output: `governance/service.py:6773, 6835, 7015` (`_review_summary`, with `str(...)[:500]` slicing in between); `:5237` over `:2943`; `impact/provider.py:386, 653`; `integrity/provider.py:156, 170`; trace lists from `trace_normalization.py:71` reused by reliability and impact; `audit.py:66` | The second pass keeps the first pass's markers *by their text* | Approval review, plan projections, impact, integrity, reliability, audit sink | SHAPE: legitimate output relies on the old trust. Convert with the ledger (section 3.3). |

No other "already sanitized" flag, skip flag or safe prefix exists.
`trace_normalization.py:78, 171` only forwards a failure flag.

### 1c. Paths with no sanitizer

These are outside HAMCP-191's bug class and are reported for completeness.
They are TRANSITIONAL_DIRECT and DIRECT routes through
`providers/dispatch.py:244` (`_direct`) or `tool_framework.py:47`
(`run_structured`). They serialize Home Assistant data without any sanitizer
pass (`models/responses.py:45-66`):

- In `tools/compatibility.py`:
  - `get_entity` (`:266`)
  - `get_history` (`:343`)
  - `render_template` (`:499`)
  - `get_automation_trace` (`:520`)
  - `get_automation_config` (`:569`)
  - `get_blueprint` (`:601`)
  - `list_blueprints`
  - `search_entities`, `list_automations`, `list_devices`,
    `list_entity_registry`, `list_areas`, `list_services`, `search_services`
- `get_audit_log` (`:851`) returns stored rows without re-scanning them. The
  rows were sanitized when written.

These paths make no trust decision, so marker text changes nothing there.
Whether they should be sanitized at all is a separate product question (D6).

## 2. Reachability by tool family

These are the default-pass call sites that untrusted Home Assistant or
upstream text can reach. "Marker plausibility" is how likely it is that HA
text matches the main grammar (or, after the change, #267's broader
detector):

- **Accidental:** ordinary content.
- **Adversarial:** someone who controls a device name, an attribute, a cloud
  integration response, an MQTT payload or a log message.
- **Echo:** Engineering output that a client wrote back into Home Assistant,
  such as notes or descriptions.

| Rank | Family (main call sites) | Untrusted text reaching the pass | Secret presence | Marker plausibility | Exposure |
|---|---|---|---|---|---|
| 1 | Error and Core logs: `get_error_log` (`tools/compatibility.py:404`), `get_core_log_history` (`providers/core_logs.py:172`), system-log slices in `incident/provider.py:446`, `reliability/provider.py:480`, `impact/provider.py:874` | Free-form log lines, tracebacks, echoed URLs and headers from any integration or library | High (URLs with credential parameters, header dumps) | Adversarial: high. Accidental: low for the main grammar, **medium for #267's detector** (prose "(truncated)", "(redacted)") | **High** |
| 2 | Delegated `ha_*` reads: `upstream_read_gateway.py:3470` (all admitted tools, including `ha_eval_template`, state, history, config, search) and `:4179` (`run_held_read_canary`) | Anything the pinned `ha-mcp` returns: states, attributes, configs, rendered templates | Medium-high | Adversarial: high. Whether pinned `ha-mcp` versions emit their own marker-like text is **unknown**; it must be checked against exact-version source under ADR-021 (D10). | **High** (breadth) |
| 3 | Logbook: `clients/logbook.py:149` | Entry names and messages, often templated from event data | Medium | Adversarial: medium. Echo: medium. | Medium-high |
| 4 | Analysis tools: incident (`:385, 406, 430`), handoff (`:451, 469, 485`; `handoff/service.py:154`), reliability (`:199, 202, 375`), impact (`:386, 564, 582, 643, 653`), integrity (`:156, 170, 257, 283`), traces (`trace_normalization.py:71`), dependency (`dependency/*`, X6, X7) | States, attributes, registry entries, traces, automation configs, plus **re-sanitized findings** (X9) | Medium | Adversarial: medium | Medium |
| 5 | Dashboard reads: `providers/upstream_dashboard.py:582, 1631, 1657`; `dashboard_analysis/*` | User-authored card text and URLs (camera, iframe or web-page cards can carry credentials) | Medium | Accidental: low-medium (markdown text) | Medium |
| 6 | Governance projections: `governance/semantic_projection.py:113` (X8), `governance/resources.py:1215`, `governance/service.py:450, 2943, 4293, 5237, 6435, 6773, 6835, 7015`, `governance/config_validation.py:22, 27`, `governance/operational_lifecycle.py:280`, `f3_dashboard/approval_projection.py:322` | Client-supplied plan input (often copied from HA text), approver notes, configuration validation messages | Medium | Echo: medium | Medium (consequence is high for X8: the raw value is shown on the approval page) |
| 7 | Identifier projections (X6): integration inspection, baseline capture, dashboard analysis, add-on names | Bounded identifiers (128-256 characters) | Low | Adversarial: low-medium | Low |
| 8 | Audit and logging sinks: `audit.py:66`, `logging_config.py:54, 94` | Engineering-composed entries that contain already-sanitized tool data | Low (already sanitized once) | Echo of own markers: **certain** (this is the main re-scan compatibility case) | Low for leaks, **high for compatibility** |
| — | Entity metadata via `get_entity`, `get_history`, `render_template` and the other direct routes (section 1c) | Not sanitized at all | — | — | Out of class (D6) |

## 3. Design

### 3.1 One shared entry point, no duplicated logic

The single public entry point stays `sanitize_untrusted_data(value, *,
known_secrets, max_string, registry=None)`. Strict becomes its only text mode:

- `_sanitize_string`'s marker-split branch (S2-S6) and the
  `REPEATED_REDACTION_MARKER` dedup are deleted. #267's `_strict_string` is
  the only string path.
- `identity_markers_only` is accepted only as `True`, for HAMCP-162 call-site
  compatibility. `False` raises `TypeError`. This removes the per-caller
  opt-in that would let a new tool silently skip the strict path. A follow-up
  may remove the parameter.
- The wrappers `logging_config.redact_data` and `redact_untrusted_text`, and
  every project-local wrapper (`upstream_dashboard._safe_string`,
  `dashboard_analysis.safe_entity`, `integration_inspection.Projector.identifier`,
  `operational_lifecycle._safe_text`), keep calling this one function. None of
  them gets its own rule list.
- X2 (`incident/normalization.py`) stops emitting its own marker text. It
  either delegates to the shared pass or emits a reviewed non-marker constant.
- What is reused from HAMCP-162, without copying:
  - `marker_like_text`, `MARKER_COLLISION`, `_Emitted`, `_strict_string`,
    `key_category` / `normalize_key`, `EngineeringMarker` and
    `ReferenceMarkerRegistry`. All live in `sanitization.py` on #267.
  - The test helpers `tests/hamcp162_synthetic.py` and the matrix and
    coverage pattern in `tests/test_hamcp162_h4_pipeline.py`.
- What is **not** reused: the YAML reader's `screen` (`#267:upstream_yaml_reader.py:622`).
  - It bundles reader-only policy: position-specific credential-text rules,
    the D12 position policy, the R11 SQLite exemption, and the PEM and
    opaque-blob scanners.
  - Applying those to other tools would be a new product decision (D5). For
    example, long base64 attributes would be withheld.
  - Lifting `screen`'s position framework into a shared module is listed as
    an optional follow-up, not part of this change.

### 3.2 Look-alike neutralization, with every rule still run

In #267, `_strict_string` returns the collision marker *before* running the
rules (`#267:sanitization.py:486-488`). For one shared pass, this design
changes the order:

1. Run the known-secret replacement and every `_SEGMENT_RULES` and
   `_SERIALIZED_PATTERNS` rule over the whole string as pieces, exactly as
   #267 does. Record each category that fires.
2. Then evaluate `marker_like_text` on the original string. If it matches,
   replace the whole string with `MARKER_COLLISION` and record
   `redaction_marker_collision` *in addition to* the categories from step 1.
3. Otherwise emit the rule output (type-based dedup, then `_truncate`).

Why the whole string and not a span:

- A span-level replacement does not close P2. The value grammars of the
  anchored rules stop at the look-alike's closing bracket, or never start
  because the opener is outside their character class.
- So the secret after the look-alike is not inside any rule match, even when
  the rules see the unsplit string.
- The string is the smallest unit that is safe without a span-aware rule
  rewrite. For the reader it was the scalar; here it is the string field, key,
  log line or message, never the whole response.

Escaping the look-alike to a visible non-marker form was rejected for the
same reason: escaping does not restore the anchor-to-value match.

This changes the HAMCP-162 reader's category lists, which gain the underlying
categories, but not its output text. HAMCP-162's tests that pin categories
need a reviewed update in the HAMCP-191 PR.

### 3.3 Engineering's own output: identity, not text

Several families re-sanitize text that Engineering already sanitized (X9),
and Engineering emits constants that match the look-alike detector (S11, X3,
X5). Under a strict-only pass, those legitimate markers would collide. The
design adds an identity ledger:

- `ReferenceMarkerRegistry` (#267) gains a third table: `register_output(obj)`
  and `output(obj)`. It holds a **strong reference** to each exact `str`
  object that a strict pass produced and that contains an emitted marker or
  the truncation suffix.
- It also holds the module-level reviewed constants an Engineering helper
  returns (X3, X5).
- The check is `type(obj) is str and table.get(id(obj)) is obj`. Equal text
  from anywhere else is a different object: it is scanned, and it collides.
  This is the same exact-type + identity + strong-reference contract the
  HAMCP-162 H2/H3 reviews accepted.
- A strict pass given `registry=` returns an exact registered object
  unchanged and uncounted. Every other string is fully scanned.
- The ledger lives for one request: one tool invocation, or one audit
  record. It is passed explicitly and never stored globally.
- Composing code must stop changing sanitized strings after the pass, for
  example the `str(...)[:500]` slices in `governance/service.py:_review_summary`.
  Those bounds move into the pass's `max_string`.
  - A slice creates a new object, which then fails closed as a collision.
  - That is safe, but it loses the output, so the PR converts each slice.
- Alternatives (D4):
  - A typed wrapper object carried until final render. Stronger, but it
    touches every composing site.
  - Removing the second passes. Rejected: it weakens defense in depth.

### 3.4 Rules × output positions × tool families

The **rules** are derived from code, never listed by hand:

- the 12 entries of `_SEGMENT_RULES` (`#267:sanitization.py:121`)
- the 8 entries of `_SERIALIZED_PATTERNS`
- the 8 key-scoped categories that `key_category` returns
- known-secret replacement
- marker look-alike

The **wrappers** applied to each rule's runtime-built case:

- plain
- look-alike around the whole value
- `<anchor><lookalike><secret>` (P2)
- look-alike as a suffix
- percent-encoded look-alike
- folded look-alike: fullwidth, homoglyph, or zero-width (reusing #267's
  `h3_markers` spellings)

The **positions** in the generic sanitizer:

| Position | Where |
|---|---|
| `value` | string leaf in a mapping |
| `list_item` | string in a list or tuple |
| `key` | mapping key at any depth |
| `sensitive_key_text` | the key text of a key-scoped field (S9) |
| `repr` | a non-primitive rendered through `repr` (S12) |
| `failure_key` | the key written by the per-field failure branch (S8) |
| `top_level` | a bare string argument |

The **families** are the eight ranked rows of section 2. Each has a
`(family, call site, injector)` entry, where the injector drives a fake
provider so the case reaches the MCP response text or the persisted record.

The matrix is the families × positions they expose × rules × wrappers.
Family injectors cover only the positions that family's call sites can carry.
The unit matrix covers every position.

## 4. Compatibility

| Output | Before (main) | After | Notes |
|---|---|---|---|
| Ordinary data with no secret and no look-alike | — | **Byte-identical** value, counts and categories | Shown for 7 samples in scratch. Proven in the PR by a corpus test against outputs generated by the base-SHA code, with provenance. |
| String containing a main-grammar marker | Kept, not counted | Whole string → collision marker. Counted with categories. | Intended |
| String containing #267-detector prose ("(truncated)", "(redacted)", angle-bracket forms, encoded or folded variants) | Kept | Whole string → collision marker | **Visible capability cost**, mainly in log lines and markdown cards (D3, D8) |
| Engineering re-scanned output (X9, S11, X3, X5) | Kept by text | Kept by ledger identity | Requires the section 3.3 conversion. A legitimate output **does** rely on the old trust today, so it is not an empty set. |
| Identifier projections (X6) | Look-alike identifier kept verbatim | Digest, or `redacted` status | Intended |
| Detector gates (X7): plan persistence, dashboard approval projection, `require_unmodified_script`, dependency script diagnostics | Pass | **Refuse** when any field contains look-alike text, even ordinary prose | Fail-closed. It blocks governed edits of automations, scripts or dashboards whose own text contains look-alike prose. Owner decision D3. |
| Semantic projection (X8) | Raw value shown | `{"state":"redacted"}` leaf | Intended |
| `redacted_field_count`, `redaction_categories` | Exclude trusted markers | Include collisions and underlying categories | Client-visible metadata change (D9) |
| HAMCP-162 YAML reader | Collision without categories | Collision plus underlying categories | Category lists only. Text is unchanged. |
| Truncation metric (X1) | Text search | Structural flag | Metric semantics only |

The PR will confirm, by test and by the X9 table, that no other output relies
on the old trust. The X9 rows are the full re-scan set the inventory found.

## 5. Test strategy

All secrets, look-alikes and anchors are built at runtime with
`tests/hamcp162_synthetic.py`. This module joins fragments, embeds a
`needle(tag)` in each built value, and percent-encodes repeatedly. Tests
assert the needle is absent from the output; no literal appears in any
committed file. The #267 `h3_markers` spellings move into that module so both
suites share one source. The committed-file scan (`CommittedFileScanTests`)
runs over the new files.

| Test | Proves |
|---|---|
| T1 unit matrix | For each rule × wrapper × generic position: needle absent; expected categories present; collision recorded when a look-alike is present. Includes a control case where only the needle is kept. Asserts `cases == rules × wrappers × positions`. |
| T2 family matrix | For each family injector: the needle is absent from final MCP text, the persisted audit row and the approval-page HTML. Counts are truthful. Covers P1 and P2 in every family. |
| T3 coverage | An AST scan of the package for `sanitize_untrusted_data`, the logging wrappers and the local wrappers must equal the committed `SANITIZER_SITES` table. Each site maps to a family with an injector. The rule ids derived from `sanitization.py` tuples must equal the matrix rule set. Any `identity_markers_only=False` fails. A new rule, position, call site or tool fails this test until it is added. |
| T4 compatibility | An ordinary corpus is byte-identical to outputs generated by base-SHA code (with provenance, and no secret-shaped corpus content). Covers re-scan sites: the ledger keeps Engineering markers; equal-text forgeries collide. |
| T5 gates | The X7 and X8 gates refuse or redact on look-alike and P1/P2 inputs, and still pass ordinary inputs. Includes useful authorized success: an ordinary governed dashboard and automation plan still persists and is approved. |
| T6 failure path | S8: a forced per-field exception never writes the raw key. |
| T7 ledger | Wrong type, equal text, copied or sliced string, and a string from another request's ledger are all scanned and collide. Strong-reference retention is checked. |

Mutation plan: `docs/evidence/hamcp191-mutations/run_mutations.py`, using the
#267 runner pattern:

- exact text replacements, each matching once
- an unmutated baseline first
- a copy of the tree outside the repo
- a JSON report
- exit status 1 if any mutation survives

Each mutation must be killed:

| Id | Mutation | Killed by |
|---|---|---|
| M1 | Restore the marker-split branch in the string path | T1, T2 |
| M2 | Allow `identity_markers_only=False` again, or default it to `False` | T3 |
| M3 | Collision check before the rules (loses categories) | T1 |
| M4 | Drop one entry from the strict rule list | T1, T3 |
| M5 | Dedup by text instead of by type | T1 |
| M6 | Ledger accepts equal text (`==` instead of identity) | T7 |
| M7 | Ledger accepts any `str` | T7 |
| M8 | Ledger stored globally across requests | T7 |
| M9 | Sensitive-key text not scanned (S9) | T1 |
| M10 | Failure branch writes the raw key (S8) | T6 |
| M11 | Truncation suffix appended outside the ledger | T4 |
| M12 | Restore a post-pass `[:500]` slice | T4 (useful output lost) |
| M13 | X2 emits marker text again | T2, T4 |
| M14 | Gate ignores the collision category | T5 |
| M15 | Remove a site from `SANITIZER_SITES`, or add an unlisted call | T3 |
| M16 | Remove a family injector or a position | T3 |
| M17 | Weaken the `marker_like_text` fold or decode rounds | T1 (reuses the #267 H3 mutations) |
| M18 | X1 metric back to text search | T4 |

## 6. Pull-request plan

- **Prerequisites and sequencing:**
  - PR #267 (HAMCP-162) must merge first. This design extends its
    `sanitization.py` strict mode, registry and test helpers. Starting
    earlier would duplicate or conflict with an unmerged branch.
  - The work then lands after HAMCP-192's release. It starts from a fresh
    worktree on freshly fetched `origin/main`, using new commits and merge
    commits only.
- **Release:** the next Engineering beta after HAMCP-162 (`2.4.1-beta.16`)
  and HAMCP-192. The exact version number is **unknown** until those release
  declarations land.
- **Recommended shape:** one PR. The default flip (3.1), the ledger and the
  call-site conversions (3.3) must land together; otherwise Engineering's own
  re-scanned markers would collide in between. One PR also fits the
  one-independent-review budget.
  - **Alternative (D7):** PR A adds the ledger, the call-site conversions and
    tests with the default unchanged, proven byte-identical. PR B flips the
    default and adds the matrix.

| Area | Files | Protected |
|---|---|---|
| Sanitizer core | `sanitization.py` | **Yes** (`ha_mcp_engineering/`) |
| Re-scan conversions (X9) | `governance/service.py`, `impact/provider.py`, `integrity/provider.py`, `reliability/provider.py`, `trace_normalization.py`, `audit.py`, `logging_config.py` | **Yes** |
| Text-trust conversions | `incident/normalization.py` (X2), `providers/dispatch.py` (X1), `providers/upstream_read_gateway.py` (X3, X4 registration), constant helpers in X5 | **Yes** |
| HAMCP-162 reader categories | `providers/upstream_yaml_reader.py` (only if category assertions need code) | **Yes** |
| Release | `config.yaml`, `version.py`, `.release/`, `CHANGELOG.md`, acceptance document | `config.yaml` and `.release/` **Yes** |
| Tests | `tests/hamcp162_synthetic.py` (shared look-alike spellings), `tests/test_hamcp191_*.py`, category updates in `tests/test_hamcp162_*.py` | No |
| Evidence | `docs/evidence/hamcp191-mutations/run_mutations.py` | No |

Size estimate (one PR): about 250-350 changed runtime lines across about 12
protected files, about 1,200-1,600 test lines, and about 300 lines of mutation
runner. That is roughly +2,000 / −250 overall. Stable v1 (`hass_mcp_admin/`)
is untouched.

## 7. Owner decisions

| Id | Decision | Recommendation |
|---|---|---|
| D1 | Strict becomes the only text mode for every sanitizer caller, versus a per-family opt-in | Only mode. An opt-in recreates the skip risk the coverage test exists to prevent. |
| D2 | Collision granularity: whole string versus span | Whole string. A span does not close P2 (3.2). |
| D3 | Detector gates (X7) refuse look-alike prose, so some automations, scripts and dashboards become non-governable until their text changes | Accept for this beta and measure it with the collision count. A withheld-leaf dashboard projection would be a separate F3 contract decision. |
| D4 | Re-scan identity: str-object ledger, typed wrapper, or remove second passes | Ledger. It is the smallest change that keeps defense in depth. |
| D5 | Extend reader-only scanners (PEM, opaque blob, position credential-text) to all tools | Not in HAMCP-191. Separate decision. |
| D6 | Direct routes with no sanitizer (section 1c) | Out of HAMCP-191's scope. Decide whether it is intended. Adding a pass would change exact-config capability that governed edits rely on. |
| D7 | One PR versus the A/B split | One PR |
| D8 | Look-alike detector breadth (it flags ordinary prose) | Keep #267's detector. Narrowing it reintroduces shape trust. |
| D9 | Counts and categories visible to clients change | Accept |
| D10 | Whether pinned `ha-mcp` versions emit marker-like text | Check against exact-version source (ADR-021) before implementation. This affects how often delegated reads collide, not the design. |

## 8. Evidence and non-actions

- **Evidence:** static reading of `main` and of PR #267 via `git show`, plus
  one scratch probe. The probe imported main's and #267's `sanitization.py`
  from disk, built every secret and look-alike at runtime, and printed only
  booleans. It is not committed.
- **Not run:** repository tests, check tiers, the mutation runner, any live
  Home Assistant, MCP endpoint or upstream source fetch.
- **Non-actions:**
  - no runtime, test, release, workflow, registry or stable-v1 change
  - no change to PR #267 or the HAMCP-192 branch
  - no reviewer session
  - no Ready, merge or tag
