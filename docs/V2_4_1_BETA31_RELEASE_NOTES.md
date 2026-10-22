# Engineering 2.4.1-beta.31 release notes

Engineering 2.4.1-beta.31 adds one read-only tool,
`get_configuration_definitions` (HAMCP-132 slice 2, items B and D). For
YAML-defined template, SQL and helper entities it reports:
- how each entity is defined in `configuration.yaml`, as an allowlisted
  projection;
- how the definition was matched to the entity, with the strength of that
  match stated;
- why an entity is unavailable, from Core's read-only signals.

The tool cannot write, reload, create, call a service or open a flow.

Links:
- Design: [`HAMCP_132_DEFINITION_VISIBILITY_DESIGN.md`](HAMCP_132_DEFINITION_VISIBILITY_DESIGN.md)
  (draft PR #291), section 3 B and D, section 4 slice 2, and the owner
  decisions in section 8.
- Component review (Part 0):
  [`HAMCP_132_SLICE2_COMPONENT_REVIEW.md`](HAMCP_132_SLICE2_COMPONENT_REVIEW.md).
- Josh's decision of 2026-10-21 (section 11 of the review): the
  `ha_mcp_tools` custom component is not used, now or later.
- Acceptance: [`V2_4_1_BETA31_ACCEPTANCE.md`](V2_4_1_BETA31_ACCEPTANCE.md).

beta.31 follows these releases and includes everything in them:
- 2.4.1-beta.30 (HAMCP-131, incident-window logs and continuable history);
- 2.4.1-beta.29 (HAMCP-213 PR 1, safety-critical acknowledgement);
- 2.4.1-beta.28 (HAMCP-215, Core authority recovery);
- 2.4.1-beta.27 (HAMCP-132 slice 1).

There is no dependency change: both hashed locks are beta.29's.

The catalog grows from 92 to **93** tools, or 94 with the optional YAML reader
admitted: 65 static plus 28 delegated. No existing tool, input schema,
description, signed registry entry, HOLD, support window, workflow, add-on
permission, stored format or stable-v1 file changes.

## `get_configuration_definitions`

Input: `entity_ids` (0 to 50 strict entity IDs, no duplicates) and
`integrations` (from a closed set of twelve, no duplicates). At least one must
be non-empty. The twelve integrations are `template`, `sql`, `filter`,
`derivative`, `statistics`, `group`, `integration`, `min_max`,
`history_stats`, `threshold`, `trend` and `utility_meter`. Arguments are
validated before the SDK can coerce them, a string is never split, and a
rejection never echoes the input.

### Sources

Each source is listed in `sources` with its own status and closed reason.

| Source | Read | What it gives |
| --- | --- | --- |
| `configuration_yaml:<path>` | admitted `ha_config_get_yaml` through the new `configuration_definitions` gateway purpose | definitions under `template`, `sql`, `group`, `utility_meter`, `sensor` and `binary_sensor`; only the paths the request needs |
| `configuration_yaml:homeassistant.packages` | the same reader; `homeassistant` only to classify a missing packages key | whether packages could hold more definitions |
| `states` | REST `GET /api/states` | whether a state exists and is `unavailable` or `unknown`; no value is copied |
| `config/entity_registry/list` | Core WebSocket | platform, unique ID, config entry and `disabled_by` |
| `entity/source` | Core WebSocket | the platform that created the entity |
| `config` | REST `GET /api/config` | the loaded `components` only |
| `config_entries/get:<domain>` | Core WebSocket, one closed domain each | UI entries' identity and state. Core never returns an entry's `data` or `options` here |
| `manifest/get:ha_mcp_tools` | Core WebSocket (Core's command, not the component's) | whether the component is installed, and its version |

### Definitions

Each definition has an ID such as `sql[0]`, `sensor[1]` or
`group.downstairs_lights`, and comes in one of four forms:
- an integration key (`template:`, `sql:`, `group:`, `utility_meter:`);
- a `sensor:` or `binary_sensor:` platform entry;
- a template blueprint block, reported by path only;
- a legacy platform entry.

Fields are a positive per-integration allowlist taken from Core 2026.10.1's
schemas (`schemas.py`). Unrecognized keys are counted, never copied.

What is always withheld:
- **`db_url`**, in every position: at the top level, nested, and inside
  lists. A SQL definition's own `db_url` is a fixed policy redaction,
  reported as `{"redacted_by_policy": "database_url"}` in its `fields`
  (below); everywhere else it is withheld.
- A `!secret` or `!env_var` reference, with its name.
- Any nested user-defined key that looks like a credential or connection key.
- A credential-bearing URI inside an allowlisted text field, such as a SQL
  query, which becomes `{"withheld": "credential_uri"}`. The shared sanitizer
  redacts userinfo only for `http(s)` URLs.
- Credential text anywhere in the final report, not only in definitions:
  config entry titles, entry and capability metadata, source reasons,
  omissions and coverage. See "Completeness, bounds and sanitization".

Each definition lists what was withheld in `withheld`.

**Invisible text is withheld whole.** A YAML-projected string or key that
contains an invisible, format or control character cannot be screened
reliably: a zero-width character inside `password=` hides the credential
word from every pattern. Such a value is replaced whole with
`[withheld:unverifiable_text]` (a key: its entry is dropped) and is never
screened and shown. The characters are the Unicode categories Cc (tab, line
feed and carriage return excepted), Cf, Cs, Co, Cn, Zl and Zp, and every
Default_Ignorable_Code_Point: zero-width characters, bidi controls,
variation selectors, tag characters and the Hangul fillers. They are looked
for in the original text and after each round of a bounded percent-decode
(`%E2%80%8B` is a zero-width space), before any credential screening, in the
projection and again over every key and string at the exit point. Text
without them keeps the existing path: bounded percent-decode, NFKC, the
confusable fold, then credential screening. Ordinary templates, units such
as `°C` and accented names are shown unchanged. **By design, an emoji
written with a variation selector or a zero-width joiner is withheld too.**

**Anything withheld makes the report partial.** One scan of the final
envelope at the single exit point finds every withheld form: a
`[withheld:credential_text]` or `[withheld:unverifiable_text]` string, a
`{"withheld": <kind>}` value, a non-empty `withheld` list on a definition,
and each dropped key. Each affected item is named in `omissions`
(`withheld:definitions[3]`, `withheld:ui_entries[0]`), at most 20, then
`withheld_items_not_listed:<n>`, and the report is partial.

**A fixed policy redaction is not missing evidence** (owner decision, Josh,
2026-10-22). A SQL definition's own `db_url`, under the `sql:` key or a
legacy `platform: sql` sensor, is never read out: its `fields.db_url` is
always `{"redacted_by_policy": "database_url"}`. That is a policy, not a
gap in the evidence, so it does not make the report partial and is not an
omission. The exemption is an explicit, narrow allowlist inside the same
final-envelope scan (`credentials.POLICY_REDACTIONS`, one entry: SQL
`fields.db_url`), not a skipped scan. Everything else is still withheld and
partial:
- a `db_url` in any other integration (a template block, a group, a utility
  meter, a legacy template) or nested inside another field;
- a credential in any other field of a SQL definition (its query, column,
  unit, value template or availability);
- invisible or format characters, unverifiable titles and confusables;
- a `redacted_by_policy`-shaped value that came from configuration rather
  than from the projection.

So a SQL definition whose only redaction is its `db_url` is complete; the
same definition with any other withheld item is partial. The sanitizer's
own `redacted_by_policy` omission (HAMCP-191), which records a redaction by
the shared sanitizer, is unrelated and still makes the report partial.

**Nothing is derived from a withheld input** (owner decision, Josh,
2026-10-22). When an input to a derived field is withheld, or holds text the
exit point would withhold (a credential, a known secret, invisible or
unverifiable text, or text over the length bound), that field is not derived
and the report is partial:
- an expected entity's `derived_entity_id` is `null` with the limitation
  `derived_entity_id_withheld` when its name, default name inputs
  (`default_entity_id`, a source entity, a `type`, a namespace) or
  configuration key is withheld;
- a generated `unique_id` (and its readings) is `null` with
  `unique_id_withheld` when any of its inputs is withheld (a SQL, template
  block or entity, utility meter, tariff or trend `unique_id`);
- a withheld dependency value is not read for entity IDs, and the
  diagnosis says `undetermined` with `dependencies_withheld`;
- a withheld configuration key never appears in a definition ID, which is
  then positional (`group[0]`);
- a withheld legacy platform name is never a legacy candidate.

Each case lists a path in the definition's `withheld`
(`sql[0].expected_entities[0].derived_entity_id`), so the final-envelope
scan names the definition and the report is partial. A Core-observed
`entity_id` reported for an exact registry match is Core's data, not a
value this tool derives, and is screened at the exit point as before.

What is disclosed but never followed:
- `!include` at any level, and packages. The report says each was not
  followed, and is partial.
- Labelled keys such as `template 2:`, which the YAML reader cannot address.

A legacy `platform: sql`, `template` or `utility_meter` entry under `sensor:`
creates no entity in this Core. It is flagged
`unsupported_legacy_platform` and is never an expected entity. Other
platforms are counted by name only.

### Matching

Each requested entity's `definition` gives a method:
- **`exact_config_entry`**: the registry row names a config entry of one of
  the twelve integrations. The definition is UI-defined and
  `definition_not_readable_without_flow` (owner decision (i)). The entry's
  identity and state are listed under `ui_entries`. Its free-form `reason`
  text is withheld; `reason_present` and a pattern-checked translation key
  are given instead.
- **`exact_unique_id`**: the registry's (domain, platform, unique ID) equals
  the unique ID Core derives from one YAML definition. A unique ID that Home
  Assistant's YAML 1.1 loader could read as another type (an unquoted `on`,
  `off`, `yes`, `no` or `1:30`; slice 1's parser-dependence rule, applied
  before matching) is never exact: the expected entity carries
  `unique_id_parser_dependent` and each `unique_id_readings` Core could
  register (`on` and `True`), and one candidate is `heuristic` with `medium`
  confidence and the reason `unique_id_parser_dependent`. A name or key with
  such a reading derives no entity ID (`name_parser_dependent`).
- **`heuristic`**: no registry row, and Core's naming rules applied to a
  static name, default name or key give this entity ID. Confidence is stated:
  - `high`: one candidate, its platform confirmed by `entity/source`, every
    YAML source complete and the registry read;
  - `medium`: the platform is unconfirmed or a source is incomplete;
  - `low`: the ID matched only after removing a collision suffix (`_2`).

  The slugify port covers ASCII text without `&`. Any other text derives no
  ID.
- **`ambiguous`**: several definitions derive the same ID or share the same
  unique ID. No candidate is chosen. The rules are identical in both
  directions: each of two definitions sharing a unique ID is `ambiguous`
  from the definition's side too, and a unique ID several registry rows
  carry is `ambiguous` (`several_registry_entries_share_this_unique_id`).
- **`unmatched`** or **`not_applicable`**, each with a reason.

A registered entity is never matched by name: a YAML definition without a
unique ID cannot create a registry row. Each definition also lists its
expected entities, with the same match vocabulary from the definition's side.

### Diagnosis

Each requested entity gets a `classification` with its `reasons`:

| Classification | Typical reasons |
| --- | --- |
| `available` | none |
| `disabled` | `registry_disabled:<by>`, `config_entry_disabled:<by>` |
| `deployment_dependency` | `config_entry_state:<state>`, `integration_not_loaded:<d>`, `platform_not_loaded:<p>.<d>`, `legacy_platform_unsupported` |
| `transient` | `config_entry_state:setup_retry` or an in-progress state |
| `dependency_unavailable` | `source_entity_missing:<id>`, `source_entity_unavailable:<id>` (5 of each at most) |
| `entity_level` | the one matched YAML definition's extracted dependencies are fine, but the entity is `unavailable` or `unknown` |
| `not_found` | `no_state`, `definition_absent`, `labelled_keys_not_addressable` |
| `undetermined` | `source_failed:<source>`, `definition_not_matched`, a YAML source gap, `dependencies_not_checked`, `<value>_invalid_response` |

Each classification uses only the signals it names. If a source is missing,
or there is no single matched definition, the answer is `undetermined`, not
`entity_level`. In particular:
- `definition_absent` is claimed only when every YAML definition source was
  read whole. After a failed or partial YAML path, unread packages or an
  include not followed, the answer is `undetermined` with that gap
  (`source_failed:configuration_yaml:<path>`,
  `source_partial:configuration_yaml:<path>`,
  `definitions_in_packages_not_read`, `packages_not_determined`,
  `include_not_followed:<path>`, `definition_values_include_not_followed`).
- A UI-defined entity whose definition cannot be read is never
  `entity_level`: it is `undetermined` with
  `definition_not_readable_without_flow` and `dependencies_not_checked`.
  The same holds for a `template` or `sql` definition, whose sources are not
  extracted (`dependencies_not_checked`).

`disabled_by` is a closed value; anything else is `unrecognized`. A state,
`disabled_by` or entry state that is not text is `invalid_response` for its
item only.

The `capability` block reports the `ha_mcp_tools` component from Core's own
reads only. Its `component_commands` is `not_used_owner_decision`. It
classifies:
- a missing manifest as `not_installed`;
- a disabled, unloaded or failed entry as a deployment dependency;
- a retrying or in-progress entry as `transient`;
- a permission failure as `permission`;
- a version before 1.1.0 as `upstream_feature_gap`.

A loaded component stays `undetermined`, because its cause is not observable
without the component's own commands.

### Completeness, bounds and sanitization

The report is `partial`, with each cause in `omissions`, when any of these
holds:
- a source is unavailable or refused;
- an include or package is not followed;
- a definition is not projected;
- a definition or UI-entry limit is reached;
- a response-size trim drops rows;
- a source value is malformed: the item becomes `invalid_response`, the
  source is `partial` with that reason, and the rest of the report is kept;
- credential text is withheld (`credential_text_withheld`);
- anything is withheld, in any form, anywhere in the report: each affected
  item is named (`withheld:definitions[0]`), at most 20, then
  `withheld_items_not_listed:<n>`;
- invisible text is withheld (`unverifiable_text_withheld`);
- a redaction occurs.

The tool fails with `sources_unavailable` only when every source is
unavailable or refused. Without the YAML reader admitted, the YAML sources
are `unavailable` (`yaml_reader_not_admitted`), no delegated call is made, and
the Core-side answers are still reported.

The bounds are:
- 200 definitions and 50 UI entries;
- 4,096 characters per text value;
- a 50,000-character report inside the 60,000-character envelope.

Over the budget, trailing definitions are dropped first, then UI entries, by
their sanitized size. The listed count follows the trim.

The complete envelope is assembled first: report, omissions, coverage and
every identifier. At that single exit point every key and string is screened
for credential text, then the strict shared sanitizer runs on it, with the
Home Assistant token and the access secret as known secrets. Nothing is added
afterwards. Credential text is:
- a URI with userinfo;
- a database or broker connection URI by scheme (`db_url`-shaped, a sqlite
  path included), with or without a password;
- a user name and password before a host, without a scheme;
- a connection-string password field;
- a known secret;
- text the reader's canonicalization (percent-decoding, NFKC, casefold)
  refuses.

The checks read the canonical form through a bounded confusable fold: a
fixed table of the look-alikes of `/` and `:` that NFKC leaves unchanged
(the division slash and the ratio sign, for example) and of the Cyrillic and
Greek letters that look like Latin ones. The table is not general confusable
coverage.

A string is replaced with the bounded marker `[withheld:credential_text]`, a
dictionary entry with a credential-shaped key is dropped, and the report is
partial with `credential_text_withheld`.

**Config entry titles** are Core free text and are shown through an
allowlist, not a denylist. The complete original title is checked before
anything else; nothing is truncated or normalized first. It is shown
verbatim only when it is printable ASCII with no control character, at most
255 characters, has no `@` and no `://`, no password-like `key=value` or
`key: value`, no known secret in any letter case and no credential text.
Otherwise it is replaced whole: credential text with
`[withheld:credential_text]`, anything else with
`[withheld:unverifiable_text]`, and the report is partial with
`unverifiable_text_withheld`. **By design, an ordinary title with a
non-ASCII character (an accented letter, a CJK title, an emoji or an em
dash), with `@`, or longer than 255 characters is withheld.** A plain ASCII
title is shown unchanged.

## Boundaries

- **Reads only.** The reads are the eight above. An AST test pins the new
  package's literals, client calls, reader paths, imports and dispatch.
  - No `ha_mcp_tools/*` command, options flow, `.storage` file or Supervisor
    endpoint is reachable.
  - No new batch-allowlist shape is added.
- **YAML reader.** A new gateway purpose, `configuration_definitions`, fixes
  its own path set: `template`, `sql`, `group`, `utility_meter`, `sensor`,
  `binary_sensor`, `homeassistant.packages` and `homeassistant`. Its audit
  event is `configuration_definitions_yaml_read`.
  - The `recorder_settings` purpose keeps its three paths, so neither
    purpose can read the other's paths.
  - An unknown purpose returns no reader.
  - The reader's L1–L4 pipeline, Core gate and response seal are unchanged.
  - Authority is rechecked before and after every read, with no fallback.
- **Core gate.** `core.basic_rest_read`, `core.basic_websocket_read` and
  `core.non_device_registry_read` (HAMCP-164).
- **Audit.** Counts and source outcomes, never entity IDs or integration
  names.

## Corrections found during implementation

The implementer's own testing found and fixed these before the PR. They are
listed for the independent reviewer:
1. A database URL with userinfo inside an allowlisted text field, such as a
   SQL query, passed the projection. The final sanitizer does not cover
   non-HTTP userinfo. It is now withheld as `credential_uri`.
2. An unavailable entity with an ambiguous or unmatched definition was
   classified `entity_level`. It is now `undetermined` with
   `definition_not_matched`.
3. `YAML_FAILURE_CODES` was unused and inconsistent. It is now the shared
   reader codes plus `yaml_reader_not_admitted`.
4. Three problems in the service:
   - An unrecognized registry `disabled_by` was echoed.
   - The response-budget trim re-sanitized once per dropped row. That was
     quadratic, about 20 s of event loop for 210 definitions.
   - `coverage.definitions_listed` was stale after a trim.
5. The gateway's pre-SDK validation branch tested the tool name by set
   membership before its type was known. A list or dict tool name then
   raised `TypeError` instead of the HAMCP-189 generic refusal. PR CI found
   it (`test_hamcp189_gateway_audit_reason`), and the equality test was
   restored.

## Corrections from the independent review

The independent review of PR #294 at `dfd1f02` found these. Each
reproduction is a permanent test, the credential one through the
authenticated gateway.
1. **H1.** A database URI in a config entry title reached the response: the
   title was copied without the definition projection's credential
   screening. Credential withholding now runs on every string of the final
   envelope at the single exit point (above).
2. **M1.** Two definitions sharing a unique ID were `ambiguous` from the
   entity's side but `exact_unique_id` from each definition's side. The
   reverse direction now applies the forward rules.
3. **M2.** An unquoted `on` unique ID was an exact match although Home
   Assistant's YAML 1.1 loader registers it as `True`. Slice 1's
   parser-dependence rule now applies before matching and naming.
4. **M3.** A failed YAML source still let diagnosis claim
   `definition_absent`, and an unavailable UI-defined entity was
   `entity_level` with no dependency check. Both are now `undetermined` with
   the reason.
5. **M4.** A malformed state value (`state: []`) aborted the report with a
   `TypeError`. Malformed values now invalidate only their item.
6. **L1.** The component review note now records the owner decision and the
   capability block's actual fields.

## Corrections from the delta review

The independent delta review of the corrections at `cde9cd1` found these.
Its regression suite is kept unchanged as `tests/test_pr294_delta_review.py`,
and the two High reproductions run through the authenticated gateway.
1. **H1.** A title was truncated to 255 characters before it was screened,
   so a credential whose host fell past the bound was cut into text that no
   longer read as one, and its password was shown. The complete original
   title is now checked first and a title over the bound is withheld whole.
2. **H2.** Look-alike characters NFKC leaves unchanged (the ratio sign for
   `:`, the division slash for `/`, a Cyrillic `o` in `password`) hid a
   credential from the screening. Titles now pass only the allowlist above,
   which refuses every non-ASCII character. YAML-projected text keeps the
   credential screening with the bounded confusable fold added.
3. **M3.** A template `use_blueprint` block, which the reader does not
   expand, was not a definition gap, so an entity it creates could be
   called `not_found` with `definition_absent`. Such a block now makes the
   report partial (`blueprint_definitions_not_projected`) and absence
   `undetermined`.
4. **M4.** A malformed registry `platform`, `unique_id` or
   `config_entry_id` (a dict or list, for example) was silently treated as
   absent. The row's new `registry.identity` is `invalid_response`, the
   registry source is partial with `invalid_response`, the row takes part in
   no match in either direction and its diagnosis is `undetermined`
   (`registry_identity_invalid_response`). A `null` field stays a legitimate
   absence (`identity` `valid`).
5. **L.** The mutation runner's B-L7 rationale was wrong: dropping the known
   secrets from the withholding walk alone is not an equivalent mutant (it
   fails three tests and loses the case-varied protection). It is now its
   own mutant, `DELTA-REVIEW-2`.

## Corrections from the second delta review

The independent review of the corrections at `5f6acb0` found these. Its
regression suite is kept unchanged as `tests/test_pr294_corr2_extra.py`.
1. **R2-H1 (High).** A zero-width space, zero-width non-joiner, zero-width
   joiner, word joiner, byte order mark or soft hyphen inside `password=`,
   written literally or percent-encoded, hid the credential word from the
   screening of YAML-projected text, so the password was shown (12 leak
   cases). Invisible, format and control text is now withheld whole as
   `[withheld:unverifiable_text]`, never screened and shown (above).
2. **R2-M1 (Medium).** A value the projection withheld as
   `{"withheld": "credential_uri"}` left the report `complete: true`. One
   scan of the final envelope now makes any withheld form, anywhere, a
   partial report with a named omission per affected item.

Owner decision after that review (Josh, 2026-10-22): the SQL `db_url`
policy redaction above is not missing evidence and no longer makes a report
partial. Every other withholding still does.

Owner decision before the delta rereview (Josh, 2026-10-22): nothing is
derived from a withheld input (above). A credential URL in a SQL name was
withheld from the name but still slugged into its derived entity ID; the
derived ID, generated unique IDs, dependencies, definition-ID keys and
legacy candidates are now withheld with it, and the report is partial.

## Compatibility and rollback

- **Additive.** One new tool; no existing descriptor or schema changes.
  `yaml_config_reader` keeps `recorder_settings` as its default purpose.
- **Stored formats.** No stored format changes.
- **Rollback.** Reinstall the beta.30 artifact. No state needs migration.
  beta.30's and beta.29's own rollback notes for plan records still apply
  to anything older.
- **Known limitations:**
  - Configuration comes from disk, as parsed by the YAML reader (YAML 1.2).
    It can differ from what Core loaded until a restart.
  - `!include` targets, package contents and labelled keys are not read.
  - UI-defined definitions cannot be read without a flow; only their entry
    identity and state are reported.
  - A heuristic match is evidence, not proof, and is labelled with its
    confidence. Non-ASCII names and names with `&` derive no ID.
  - With the component not used, a loaded `ha_mcp_tools` component's
    problems stay `undetermined`.
  - A config entry title with a non-ASCII character, `@`, `://`, a
    password-like `key=value` or over 255 characters is withheld as
    `[withheld:unverifiable_text]` (by design; the report is partial).
  - A YAML-projected value with an invisible, format or control character
    (an emoji with a variation selector included) is withheld whole as
    `[withheld:unverifiable_text]` (by design; the report is partial).
  - Every withholding makes the report partial, except the fixed SQL
    `db_url` policy redaction (`{"redacted_by_policy": "database_url"}`),
    which is not missing evidence (owner decision, 2026-10-22).
  - A definition with a withheld name, key or unique_id input reports no
    derived entity ID or generated unique ID for it (owner decision,
    2026-10-22; the report is partial), so it can match only by an exact
    registry unique ID or not at all.
  - A template blueprint block is not expanded; it is a disclosed gap.
