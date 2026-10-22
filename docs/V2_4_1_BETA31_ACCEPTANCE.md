# Engineering 2.4.1-beta.31 acceptance

This contract covers HAMCP-132 slice 2: configuration definitions (item B) and
unavailability and capability diagnosis (item D). It adds one read-only tool,
`get_configuration_definitions`. It builds on these releases:
- 2.4.1-beta.30 (HAMCP-131);
- 2.4.1-beta.29 (HAMCP-213 PR 1);
- 2.4.1-beta.28 (HAMCP-215);
- 2.4.1-beta.27 (HAMCP-132 slice 1).

See also:
- Release notes:
  [`V2_4_1_BETA31_RELEASE_NOTES.md`](V2_4_1_BETA31_RELEASE_NOTES.md).
- Design: [`HAMCP_132_DEFINITION_VISIBILITY_DESIGN.md`](HAMCP_132_DEFINITION_VISIBILITY_DESIGN.md)
  (draft PR #291).
- Component review and Josh's decision not to use `ha_mcp_tools`:
  [`HAMCP_132_SLICE2_COMPONENT_REVIEW.md`](HAMCP_132_SLICE2_COMPONENT_REVIEW.md#11-owner-decision-josh-2026-10-21).

**Release target.** `2.4.1-beta.31`. It is staged by one owner-authorized run
of `scripts/promote_next_release.py --apply` on a base that already carries
2.4.1-beta.30. The run consumes the declaration. This PR first targeted
beta.30; HAMCP-131 (#295) released it first, so this PR merged that base
with a merge commit and took beta.31. If another release takes beta.31
first, this PR merges that base again and takes the next free beta.

The contract separates four stages:
1. source and offline acceptance;
2. release CI;
3. publication;
4. installed Engineering acceptance.

It authorizes none of the later stages.

## 1. Source and offline acceptance

Bind the following:
- Josh's HAMCP-132 objective, owner decisions (i)–(vii) of 2026-10-20
  (design section 8), and the 2026-10-21 decision not to use the
  `ha_mcp_tools` component;
- `2.4.1-beta.31` materialized in all three version authorities, with exact
  acceptance and release-note resolution from `scripts/codex-context.py` and
  no staged declaration left over;
- the PR head SHA;
- the passing PR `validate` run for that exact head;
- the Evidence tier log named in the PR.

The review needs a security section covering:
- **New sources.**
  - REST `GET /api/config` (`components` only).
  - WebSocket `config_entries/get` with one closed domain.
  - WebSocket `manifest/get` for `ha_mcp_tools`.
  - The slice-1 reads `GET /api/states`, `config/entity_registry/list` and
    `entity/source`.

  All run under the existing administrative token, with no new permission.
  `config_entries/get` never returns an entry's `data` or `options`, so a
  UI-defined SQL `db_url` cannot be returned.
- **YAML reader purpose.** The new gateway purpose `configuration_definitions`
  fixes eight paths and its own audit event. The `recorder_settings` purpose
  and its three paths are unchanged. An unknown purpose gives no reader. The
  L1–L4 pipeline, Core gate, response seal and authority rechecks are
  unchanged, with no fallback.
- **No component command.** No `ha_mcp_tools/*` command, options flow,
  `.storage` file or Supervisor endpoint is reachable. There is no new
  batch-allowlist shape, and no write, reload, service call, flow, fallback
  or forwarding reachability.
- **Withholding.** `db_url` is withheld in every position, except that a
  SQL definition's own `fields.db_url` is the fixed policy redaction
  `{"redacted_by_policy": "database_url"}`. So are `!secret`
  and `!env_var` references with their names, credential-shaped nested keys,
  and credential URIs inside allowlisted text, a gap the shared sanitizer
  does not cover. The projection is a positive allowlist. Unrecognized keys
  are counted only.
- **One screened and sanitized exit point.** The complete envelope is
  screened once for credential text, in every key and string (config entry
  titles, metadata, reasons, omissions and coverage included), and then
  sanitized once, with the known secrets. Nothing is appended afterwards.
  Withheld text is replaced with `[withheld:credential_text]` and makes the
  report partial (`credential_text_withheld`), as does a redaction. Budget
  trimming re-screens and re-sanitizes the rebuilt envelope. The screening
  reads a bounded confusable fold of the canonical text.
- **Titles by allowlist.** A config entry title is checked whole, never
  truncated first, and shown only when it is printable ASCII within 255
  characters with no `@`, `://`, password-like `key=value` or `key: value`,
  known secret or credential text. Otherwise it is
  `[withheld:credential_text]` or `[withheld:unverifiable_text]` and the
  report is partial (`unverifiable_text_withheld`). **An ordinary non-ASCII
  title is withheld by design**; a plain ASCII title is shown unchanged.
- **Invisible text withheld whole.** A YAML-projected string or key with an
  invisible, format or control character (Cc except tab, line feed and
  carriage return; Cf, Cs, Co, Cn, Zl, Zp; every
  Default_Ignorable_Code_Point), in the original text or after a bounded
  percent-decode, is `[withheld:unverifiable_text]` (a key: dropped), never
  screened and shown. The exit point applies the same check to every key and
  string. Templates, units such as `°C` and accented names are shown.
- **Anything withheld is partial.** One scan of the final envelope names
  each item with any withheld form (`withheld:definitions[3]`), at most 20
  then `withheld_items_not_listed:<n>`, and makes the report partial.
- **A fixed policy redaction is not missing evidence (owner decision, Josh,
  2026-10-22).** A SQL definition's own `db_url` (the `sql:` key or a legacy
  `platform: sql` sensor) is reported as
  `{"redacted_by_policy": "database_url"}` and does not make the report
  partial. The exemption is an explicit allowlist with one entry inside the
  same scan (`credentials.POLICY_REDACTIONS`), not a skipped scan. A
  `db_url` in any other integration or nested position, a credential in any
  other SQL field, invisible text, unverifiable titles, confusables and a
  policy-shaped value from configuration are still withheld and partial.
  The sanitizer's `redacted_by_policy` omission (HAMCP-191) is a different
  thing and still makes the report partial.
- **Truthfulness.**
  - A heuristic match is never reported as exact, and its confidence names
    the signals behind it.
  - Ambiguity is never resolved, and the rules are identical from the
    entity's and the definition's side.
  - A unique ID that YAML 1.1 could read differently is never exact.
  - A failed source makes the report partial. A malformed source value
    invalidates only its item (`invalid_response`); it never aborts the
    report.
  - Diagnosis says `undetermined` rather than `entity_level` when a signal is
    missing, no single definition matched, or the dependencies were not
    checked. It never says `definition_absent` after a YAML source gap or
    an unexpanded template blueprint (`blueprint_definitions_not_projected`).
  - A malformed registry `platform`, `unique_id` or `config_entry_id` makes
    the row's `registry.identity` `invalid_response` and the registry source
    partial; the row is never matched and is `undetermined`. `null` stays a
    legitimate absence.
- **Arguments and audit.** Validation happens before the SDK, and rejections
  are non-reflecting. The audit records counts only.
- **Implementation corrections.** The five corrections listed in the release
  notes: four self-found, and one found by PR CI.
- **Independent review corrections.** H1, M1 to M4 and L1 from the review of
  `dfd1f02`, listed in the release notes. Each reproduction is a permanent
  test.
- **Delta review corrections.** H1, H2, M3, M4 and L from the delta review of
  `cde9cd1`, listed in the release notes. The reviewer's regression suite is
  kept unchanged as `tests/test_pr294_delta_review.py`.
- **Second delta review corrections.** R2-H1 and R2-M1 from the review of
  `5f6acb0`, listed in the release notes. The reviewer's suite is kept
  unchanged as `tests/test_pr294_corr2_extra.py`.
- **Owner decision on policy redaction.** The SQL `db_url` policy redaction
  leaves a report complete (above); both reviewer suites are unchanged and
  pass.

Preserve the following:
- stable v1.1.2;
- persisted formats;
- every existing descriptor and input schema. The catalog is **93 tools,
  94 with the optional YAML reader admitted**: 65 static plus 28 delegated;
- probe fingerprints, Core capability profiles, owner-signed Core registry
  entries and the signed ha-mcp registry;
- the HOLD and the support window;
- workflows, add-on permissions and dependencies.

### Offline proof

No test reaches a live system. Every fixture is synthetic, and every
credential-shaped string is assembled at test time.

`tests/test_hamcp132_configuration_definitions.py` (151 tests) covers:
- pinned shapes for each projected form: template blocks (including trigger
  and blueprint blocks), the SQL integration key (list and single),
  old-style groups, utility meters, `sensor:` and `binary_sensor:` platforms,
  legacy platforms flagged and never expected, other platforms counted, and
  unrecognized keys counted;
- the owner's six YAML definitions, anonymized: two SQL sensors with a
  database URL, a filter, two derivatives (one renamed and matched by unique
  ID) and an old-style group. Each is matched and diagnosed end to end, by
  entity and by integration;
- `db_url` and credentials withheld in every position, references and
  markers withheld, a database URL inside a SQL query withheld, the final
  sanitizer still applied to kept text, a known secret redacted with the
  report partial, config-entry reason text withheld, and provider exception
  text never public;
- a planted-secret sweep. A control run first proves the secret reaches every
  identifier-bearing output field; the protected run then finds it in none;
- match quality:
  - `exact_config_entry` with the UI entry listed;
  - `exact_unique_id`;
  - `heuristic` at each confidence, with `high` requiring every signal;
  - `ambiguous`, by name and by shared unique ID;
  - platform filtering;
  - a registered entity never matched by name;
  - `unmatched`, `not_applicable` and `not_found`;
  - unread config entries;
  - legacy candidates;
  - definition-side results;
- packages (absent, present, unreadable, not determined), the `homeassistant`
  node read, and `!include` disclosed at the path, entry and value levels. A
  truncated reader value makes the report partial;
- source failures mapped to closed reasons. Each failed source makes the
  report partial. With the reader not admitted, no YAML is dispatched. When
  every source fails, the error carries no text;
- diagnosis for each classification and reason, the capability block for
  each component state, and the authority recheck at every read stage;
- the definition and UI-entry limits, the response budget (trim order,
  listed counts, the overflow estimate) and the single sanitizer exit, using
  a spy that proves the returned report is the sanitizer's output;
- the read-only static check. It pins the package's exact command literals,
  client attributes, reader calls, dispatch, imports and YAML arguments, and
  a banned-word scan of the code without docstrings. The runtime dispatch is
  also pinned exactly;
- registration: the Core gate, capability metadata, read-only annotations,
  the schema, non-reflecting refusal in the tool and in the gateway before
  the SDK, the positive gateway output and audit, and a gateway sweep with
  the access secret planted;
- the `configuration_definitions` reader purpose, through the YAML admission
  harness: its exact path set and audit event. The recorder purpose cannot
  read its paths, and the reverse also holds;
- the independent review's reproductions, kept as permanent tests:
  - a database URI in a config entry title, through the authenticated
    gateway (response and audit);
  - PostgreSQL, MySQL, MSSQL, sqlite with a path, MongoDB and HTTP basic
    auth, plus a user and password before a host without a scheme, a percent-encoded URI, a user-only
    userinfo, a password field and a URI inside prose, each planted in a
    title and in every metadata field, directly and through the gateway;
  - the API error-detail `db_url` case;
  - two definitions sharing a unique ID, and one definition whose unique ID
    two registry rows carry, `ambiguous` from both sides;
  - unquoted `on`, `off`, `yes`, `no` and `1:30` unique IDs, registered as
    the raw text or as Core's reading, never exact, with quoted and integer
    controls exact;
  - a failed YAML source, each gap type, an unreadable UI definition and a
    template definition, each `undetermined`, with absence and
    `entity_level` controls;
  - malformed states, registry and config-entry values and a projection
    error, each confined to its item, with a valid control;
- the delta review's suite, unchanged (`tests/test_pr294_delta_review.py`),
  including its two High reproductions through the authenticated gateway: a
  credential cut by title truncation, and credentials written with the ratio
  sign, the division slash or a Cyrillic `o`; and the added tests:
  - plain ASCII titles shown through the gateway, the report complete;
  - benign non-ASCII, control-character and `DEL` titles withheld as
    `[withheld:unverifiable_text]`, partial (disclosed and accepted);
  - `@`, `://` and password-like `key=value` titles, an over-length title
    withheld whole, a credential past the length bound found on the
    original, and case-varied known secrets;
  - confusable credentials in a YAML-projected definition withheld;
  - a template blueprint block as a gap, with an absence control;
  - malformed registry identities never matched in either direction, with
    a `null` control;
- the second delta review's suite, unchanged
  (`tests/test_pr294_corr2_extra.py`), and the added tests:
  - the reviewer's 12 leak cases (six invisible characters, literal and
    percent-encoded, inside `password=`), through the projection and
    through the real reader and the authenticated gateway, each withheld
    whole and partial;
  - one representative of each invisible class (Cc, Cf, Cs, Co, Cn, Zl, Zp,
    zero-width, bidi controls, variation selectors, tag characters, Hangul
    fillers, the soft hyphen, the combining grapheme joiner), literal,
    percent-encoded and double-encoded, each withheld even when it is not
    credential text;
  - encoding beyond the decode bound withheld, an invisible nested key
    dropped, and invisible strings and keys at the exit point;
  - benign definitions shown and complete: a multi-line template with tabs
    and newlines, `°C`, `µg/m³`, an accented name, an en dash, a no-break
    space, an emoji without a selector and plain percent text;
  - the reviewer's two completeness cases, and every withholding path (each
    reference, reader marker, credential URI, credential and connection
    key, `db_url`, non-text key, unsupported value, invisible value and key,
    known secret, withheld titles, a dropped key at the exit point) partial
    with its named item; the item list bounded; omission text never itself
    an item; nothing withheld stays complete;
  - the policy redaction: a SQL definition (the `sql:` key and a legacy
    platform) with only its `db_url` redacted is complete with the
    `redacted_by_policy` marker; the same plus any other withheld item
    (invisible query, reference, nested `db_url`, another withheld
    definition, a withheld title) is partial; a credential in a non-`db_url`
    SQL field is partial; a `db_url` outside a SQL definition stays withheld
    and partial; a policy-shaped value from configuration is not exempt; an
    exit-point marker outside the allowlist (another kind, extra key, another
    field, another integration, top level) is partial; and the allowlist is
    exactly the SQL `db_url`.

The existing suites that pin the tool count, sanitizer sites, Core gate and
reader purpose are updated. The Evidence tier on the PR head is the complete
final run.

Mutations: **218 of 218 killed, none survived**
(`docs/evidence/hamcp132-mutations/results.json`). The baseline ran 276
tests across the three HAMCP-132 modules and both reviewer suites
(`test_pr294_delta_review` and `test_pr294_corr2_extra`): OK, 1 skipped. Every kill is a test failure or
a test-level error; none is an import or syntax failure.

The set is slice 1's 80, 55 for slice 2, 31 for the independent-review
corrections, 18 for the delta-review corrections, 21 for the second
delta-review corrections and 13 for the owner policy decision. The first slice 2 run left two
survivors:
- `B-L2` was equivalent. `_credential_shaped` also matches `db_url`, so
  removing only the nested `WITHHELD_KEYS` test changes nothing. It was
  redefined to remove both nested guards.
- `B-M8` exposed a test gap: a registered entity whose ID a uid-less
  definition also derives by name. That test was added.

The slice-2 mutants cover:
- `db_url` leaked, as a field, nested, or under a credential key;
- the userinfo guard removed;
- references or reader redactions projected;
- the known secrets dropped;
- a heuristic reported as exact;
- ambiguity guessed;
- high confidence on incomplete sources or after a suffix;
- the platform filter removed;
- a heuristic applied to a registered entity;
- a UI entry claimed readable;
- a failed source (Core or YAML) reported complete;
- packages or includes not disclosed;
- omissions appended after sanitization;
- a redaction not making the report partial;
- each diagnosis rule weakened;
- the path set widened;
- the Core gate or either pre-SDK validation removed;
- an open integration or entries domain;
- the audit copying identifiers;
- the annotation not read-only;
- each authority recheck removed;
- each bound removed;
- a stale listed count.

The correction mutants cover:
- the reviewer's own API error-detail `db_url` mutant (`REVIEW-2`);
- the exit point's credential withholding removed, a config entry title
  copied without credential screening, a credential key kept, each
  credential rule removed, refused canonicalization passed, and a
  withholding that leaves the report complete;
- reverse-match ambiguity resolved, by definitions or by registry rows. The
  first run left the registry-row mutant surviving; a test with the raw text
  and Core's reading both registered was added;
- a parser-dependent unique ID indexed or marked as exact, a dependent name
  deriving an entity ID, Core's reading not registered, and the
  parser-dependence disclosure dropped;
- absence or `entity_level` claimed despite a YAML gap, an unreadable UI
  definition or an unchecked template definition;
- each malformed-value type guard removed, a projection error aborting the
  report, a malformed value diagnosed as if valid, and a malformed item
  that leaves the report complete.

The delta-review mutants cover:
- the reviewer's own omission-credential bypass (`DELTA-REVIEW-1`), and
  the known secrets dropped from the exit-point withholding only
  (`DELTA-REVIEW-2`; the earlier `B-L7` rationale called this equivalent,
  which was wrong: it fails three tests and loses case-variant protection);
- a title truncated before screening (`B2-T1`) and the allowlist bypassed
  (`B2-T2`);
- each allowlist leg removed: the length bound, printable ASCII, `@`, `://`
  and password-like `key=value` (`B2-T3` to `B2-T7`);
- the casefolded known-secret comparison removed (`B2-K1`);
- the confusable fold removed (`B2-C1`);
- an unverifiable title that leaves the report complete (`B2-U1`);
- an unprojected blueprint not treated as a definition gap (`B2-G1`);
- a malformed registry identity accepted, matched, reverse-matched without
  disclosure or diagnosed as valid, and a `null` identity called malformed
  (`B2-I1` to `B2-I5`).

The second delta-review mutants cover:
- invisible text screened instead of withheld whole (`B3-H1`);
- the bounded percent-decode leg removed (`B3-H2`);
- each invisible class removed: the Default_Ignorable ranges, format
  characters, control characters, the other categories and the ASCII
  control check (`B3-H3` to `B3-H7`);
- tab and newline treated as invisible, which withholds benign multi-line
  templates (`B3-H8`);
- each withholding leg removed: projected values, projected keys, exit-point
  strings and exit-point keys (`B3-H9` to `B3-H12`);
- a dropped invisible key that leaves the report complete (`B3-H13`);
- the final-envelope withheld scan removed (`B3-M1`);
- the scan ignoring the withheld object form, the marker strings or the
  dropped keys (`B3-M2` to `B3-M4`);
- the item list unbounded, omission text scanned as items, `complete`
  ignoring the items and the items not listed (`B3-M5` to `B3-M8`).

The owner policy-decision mutants cover:
- the policy allowlist broadened to every withheld marker (`B4-P1`);
- each allowlist check removed: the redaction kind, the integration, the
  field, the path depth, the `definitions` list, the `fields` segment and
  the single-key shape (`B4-P2` to `B4-P8`);
- a boolean list index accepted (`B4-P9`);
- a policy-shaped value from configuration not treated as withheld
  (`B4-P10`);
- the marker emitted for every integration, the SQL `db_url` listed as
  withheld again, and the `db_url` value copied into the marker
  (`B4-P11` to `B4-P13`).

## 2. Release CI and publication

Require exact-head CI before release acceptance. Pending or unavailable jobs
are not passing evidence.

Publication needs Josh's Ready authorization and the protected merge
handoff. Verify the following with the shipped verifiers:
- the merged source;
- v2.4.1-beta.31;
- the immutable version and commit images;
- provenance and SBOM.

Never invent future image identities. Retain the beta.30 artifacts and the
known-good earlier artifacts for recovery.

No stored format changes. Rollback is reinstalling the beta.30 artifact.

## 3. Installed Engineering

All installed checks below are read-only. None of them writes, reloads,
creates, opens a flow or calls a service.

1. **Installed-image binding.** Separately authorize installation of the
   published Engineering artifact. Bind the running add-on to:
   - the published image index digest;
   - the v2.4.1-beta.31 tag;
   - the protected `main` merge commit that contains this PR.
2. **Catalog.** Using fresh protocol enumeration, confirm:
   - 93 tools, or 94 with the optional YAML reader admitted;
   - `get_configuration_definitions` present, with read-only annotations;
   - every other descriptor unchanged from beta.30;
   - Core signed selection, storage and audit health, and zero fallback.
3. **The owner's six YAML definitions (live read).** Josh names the six
   YAML-defined entities from the HAMCP-132 objective: two SQL sensors, a
   filter sensor, two derivative sensors and an old-style group. Call
   `get_configuration_definitions` with `entity_ids` set to those six. For
   each entity, record:
   - `definition.method` and `confidence`. An entity with a YAML
     `unique_id` should be `exact_unique_id`. One without should be
     `heuristic` with a stated confidence, or `ambiguous`/`unmatched` with a
     reason. A heuristic must never appear as exact;
   - the matched `definitions[]` row: its form, projected fields and
     `expected_entities`;
   - `diagnosis.classification` and `reasons`. For any entity that is
     unavailable, check that the reasons agree with what Josh knows about it;
   - for both SQL definitions, that `fields.db_url` is exactly
     `{"redacted_by_policy": "database_url"}`, that `withheld` does not list
     `db_url`, and that `omissions` has no `withheld:definitions[<n>]` for
     them. With nothing else withheld and every source complete, the report
     is complete; any `withheld:` omission must name another withheld item.

   Also record:
   - every `sources[]` status and reason, `packages`, `omissions` and
     `completeness`. With `homeassistant.packages` configured, the report
     must be partial with `definitions_in_packages_not_read`;
   - the `capability` block, with `component_commands:
     not_used_owner_decision`;
   - that the serialized response contains no database URL, credential,
     secret name or state value.
4. **By integration (live read).** Call `get_configuration_definitions` with
   `integrations: ["sql", "filter", "derivative", "group"]`. Confirm:
   - the six definitions appear, each SQL definition with `fields.db_url`
     as the `redacted_by_policy` marker and no `withheld:definitions[<n>]`
     omission for it;
   - any UI-created entries of those integrations are listed under
     `ui_entries` as `definition_not_readable_without_flow`, with no options
     or data;
   - the response is within its bound.
5. **No change.** No configuration, registry entry, config entry or state is
   modified by steps 3 and 4. The audit shows counts only.

No push, CI trigger, signing, publication, installation or live action is
authorized by source completion.
