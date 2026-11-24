# HAMCP-481 native search — design

Status: **draft for review; not approved.** Docs only. Revision 2, the
correction batch for the Cursor review and the architect review of revision 1
(head `4cd3a430`). The build is planned for the release after 2.9.1, not for
2.9.1 (2.4.2-beta.13). Nothing here authorizes a runtime, schema, routing,
protected-path, version or release change, deployment, or live Home Assistant
or Engineering access. Decisions still open for Josh are marked **[JOSH]**.

- Register: HAMCP-481. Related rows:
  - HAMCP-472, the `ha_search` audit finding this design absorbs;
  - HAMCP-441, the `result_fields` enrichment keys, which are returned today;
  - HAMCP-447, the dashboard identity, `by_domain`, area and state echoes,
    `entry_id` and `options_withheld`, withheld today;
  - HAMCP-454, the `include_config` bodies and flow-helper options, withheld
    until a redaction design exists;
  - HAMCP-233, where `ha_search` opens options flows;
  - HAMCP-403, the `search_unified` quarantine;
  - HAMCP-485, a later `entity` value for `search_types` (Backlog).
- Scope source:
  - the 2.9.1 re-scope (architect design
    `2026-11-23-release-2.9.1-rescope-audit.md`, item B);
  - the architect blocks `release-291-next-B-D-F-G` and
    `release-291-josh-decisions-0424` (2026-11-24).
- Josh's decisions in this revision are **as relayed by the architect**. I did
  not see Josh's words. §9 gives each decision with its relayed wording.
- Reviews addressed:
  - Cursor `cursor-491-native-search-design.md`: C1, C2, C3, P1, D1, R1, B1
    and L1, all accepted;
  - the architect review `2026-11-24-491-hamcp481-native-search-design.md`:
    N1 to N4, plus the author self-review S1 to S5.
- Base: `release/2.9.1` at `774328e0` (2.4.2-beta.13 declared).
  - Engineering citations are relative to
    `hass_mcp_engineering_beta/ha_mcp_engineering/` at that base.
  - ha-mcp citations are 9.2.0 (`51e3aac7`), relative to `src/ha_mcp/`.

## 0. Summary

Engineering answers `ha_search` today by delegating to ha-mcp 9.2.0. It
projects the reply through the Row 25 key set
(`providers/upstream_reads_9_2_keys.py:313-327`). Projection alone cannot fix
three properties of that path:

1. **Always partial.** Every reply is partial with the visibility-scope
   warning (design F3, `providers/upstream_reads_9_1_entities.py:293-303`).
   ha-mcp hides entities by its visibility policy without a marker, so
   Engineering cannot prove that nothing was hidden.
2. **Replaced warnings.** Row 25 reviews no upstream warning text. Every
   upstream warning becomes `upstream_warning_replaced` and adds a "not
   reviewed" qualifier. That includes the 9.2.0 warning that explains an
   empty entities bucket (§1).
3. **Withheld content.** Several things are withheld or reachable today:
   - `by_domain`, the area and state echoes, `area_names`, `state_filter_note`,
     `message`, `partial_reason`, dashboard `url_path` and `title`, and
     flow-helper `entry_id` are withheld (HAMCP-447 and Row 25);
   - configuration bodies are withheld (HAMCP-454);
   - the upstream path can open options flows (HAMCP-233).

   The HAMCP-441 enrichment and membership keys are **returned** today.

Native search, as relayed from Josh (option (b)), is an Engineering-owned read.
It has these properties:

- **Parity.** It reaches parity with ha-mcp 9.2.0 for entities, automations,
  scripts, scenes and helpers, including configuration-body and
  entity-reference matching.
- **Identity-only results.** Configuration bodies are read for matching but
  never returned.
- **Dashboards stay delegated.** Dashboard search stays on the delegated
  ha-mcp route, through one fixed internal sub-call (§2.4).
- **Default is parity.** Every current argument behavior and reply key is
  kept unless Josh retires it by name. This revision retires nothing.
- **Partial only with a reason.** `partial` is true only when Engineering
  withheld, cut or could not search something, and it names the reason.

## 1. Cause of HAMCP-472

**Deterministic cause, pinned source.** The HAMCP-472 audit call passed
`search_types`. With ha-mcp 9.2.0 at `51e3aac7`:

- `explicit_config_only = parsed_search_types is not None`
  (`tools/tools_search.py:363`). Any explicit `search_types` value selects
  configuration-only search.
- `_compute_eligibility` (`tools/search/response.py:325-347`) sets
  `registry_eligible` to false whenever `explicit_config_only` is true
  (`response.py:337`). The entities list stays `[]`.
- The reply then carries the warning "entity search skipped: explicit
  search_types selects configuration-only search; omit search_types to
  search entities." (`response.py:630-631`).
- Row 25 reviews no warning text (`upstream_reads_9_2_keys.py:313-327`).
  Engineering therefore replaces that warning, and `_search_completeness`
  marks the reply partial with `VISIBLE_SCOPE` and `UPSTREAM_WARNINGS`
  (`upstream_reads_9_1_entities.py:293-303`). The one sentence that explains
  the empty bucket is the text the client does not get.
- With `search_types` set and **no** query, 9.2.0 refuses the call instead:
  "ha_search requires a non-empty query, or one of domain_filter /
  area_filter / state_filter to enumerate." (`tools_search.py:373-380`;
  neither branch is eligible). So the empty-entities shape needs a query, a
  domain filter and `search_types` together.

**Corroborating replay** (beta.12, ha-mcp 9.2.0). This was reported by HA
Admin and relayed to this session through Josh and the architect on
2026-11-23 CST. I did not see the raw responses.

- Calls without `search_types` returned entities:
  - `domain_filter` light alone gave 30;
  - "light" plus light gave 30;
  - "living room lamp" plus light gave 1;
  - "leak" plus `binary_sensor` gave 24, also with `exact_match` false.

  All matched `search_entities`.
- The replay did not repeat the `search_types` call, so it did not reproduce
  the empty shape. The source above does.
- The one replaced warning appears when `query` and `domain_filter` are both
  set. That is the 9.2.0 intent-skip warning (`response.py:240-245`), which
  says configuration search was skipped because an entity filter was given.

**Disposition.** HAMCP-472 has no 2.9.1 code. Native search owns R-A to R-C
(§6.4). HAMCP-472 P2, retiring F3 on the delegated route before native ships,
is decided as relayed: defer to native (§9).

**Serving path.** 9.2.0 has two serving paths for `ha_search`: the
`ha_mcp_tools` custom component, when installed and capable
(`tools/tools_search.py:405-437`), and the legacy path. Parity here is defined
against the legacy path, whose source is cited throughout. Whether the
component serves this install today is **unknown**, and is a build input.
HAMCP-403 quarantines `search_unified`.

## 2. Scope, reads and routes

### 2.1 Surfaces

| Surface | Source | Result |
| --- | --- | --- |
| Entities | Core state list, joined with the entity, area, floor, label and device registries | Entity records (§5) |
| Automations | `automation.*` states, plus each automation's configuration, read for matching | Identity-only records with match flags |
| Scripts | `script.*` states, plus each script's configuration | The same, with `script_id` |
| Scenes | `scene.*` states, plus each UI scene's configuration | The same, with `scene_id` |
| Storage helpers | the six `<type>/list` commands 9.2.0 searches: `input_boolean`, `input_number`, `input_select`, `input_text`, `input_datetime` and `input_button` (`tools/smart_search/_deep.py:927-934`), mapped to the current `entity_id` through the entity registry (`_deep.py:749`) | Identity-only records with `helper_type` |
| Flow helpers | the config-entry list, filtered to helper flow domains (`tools/smart_search/_flow_helpers.py:105-120`) | Identity-only records with `helper_type` and `name`; for bodies see §2.5 |
| References | Core's reference graph, `search/related` for an entity, for an `entity_id`-shaped query when automations, scripts or scenes are requested (`tools/smart_search/_graph.py:374-390`) | `match_in_references` on the referencing records |
| Dashboards | the delegated ha-mcp `ha_search`, through one fixed sub-call (§2.4) | Row 25 projection, as today |

Counter, timer and schedule helpers have no body search in 9.2.0. They appear
only as entity records, and the same holds natively.

At parity, disabled entities with no state are not returned. 9.2.0's legacy
entity search scores the Core state list (`tools/search/entities.py:539`,
`tools/search/modes.py:555-617`), and native does the same. This closes
revision 1's O6.

### 2.2 Reads

Every read is read-only. Native search makes no write, service call, reload,
or options or config flow call.

Already admitted, with the profile that attests it:

- states list (`GET /api/states`): `core.direct_entity_state_read`
  (`ha_core_readmission/profiles.py:247-257`);
- area, floor, label and entity registry lists: `core.non_device_registry_read`
  (`profiles.py:195-211`);
- device registry list, with child-then-parent effective area:
  `core.direct_device_registry_read` (`profiles.py:213-229`);
- automation configuration: `core.automation_configuration_read`
  (`profiles.py:259-270`).

Admitted as a command, with a profile still to be named at build:

- `script/config` and `search/related` are in the fixed WebSocket allowlist
  (`clients/websocket.py:24-28`). Today, `ha_config_get_script` declares
  `core.basic_websocket_read` (`ha_core_readmission/routes.py:257`). The build
  names the profile that attests each command for a native provider. If none
  does, the read is new (below).

New reviewed reads that the build needs. Each is a protected-path change and
needs Josh's named OK in the build task:

- scene configuration `GET /api/config/scene/config/{config_id}` as a read
  contract. Today it is admitted only inside the scene configuration and
  write profiles (`profiles.py:149`, `core.scene_configuration_operation` at
  `:814-834`, and `SCENE_CREATE_DELETE_REQUESTS` at `:1183-1189`);
- `input_boolean/list`, `input_number/list`, `input_select/list`,
  `input_text/list`, `input_datetime/list` and `input_button/list`. Today only
  the first two appear, and only inside the governed delete profile
  `CREATED_HELPER_DELETE_REQUESTS` (`profiles.py:1190-1197`);
- `GET /config/config_entries/entry` and
  `GET /config/config_entries/flow_handlers?type=helper` for flow-helper
  identity (`tools/helper_flows.py:34-45`). Neither is in
  `ROUTE_TEMPLATES` (`clients/paths.py:53`). That registry does hold the
  options-flow templates (`clients/paths.py:58-59`) for governed paths; the
  native provider must not use them.

Not read natively:

- `integration/descriptions`. 9.2.0 uses it only to tell Core's own helper
  domains from custom ones, which decides which options flows it may open
  (`tools/helper_flows.py:48-72`, `_flow_helpers.py:64`). Native opens none.
- Any options-flow or config-flow route.

### 2.3 Routes and read admission (R1)

The build makes these route edits together, in protected files:

- Remove `ha_search` from `DEVICE_DEPENDENT_DELEGATED_TOOLS`
  (`ha_core_readmission/routes.py:16-24`). Otherwise
  `delegated_provider_compatibility` (`routes.py:681-714`) still requires an
  ha-mcp child-device adapter before the tool runs.
- Move `ha_search` out of `DELEGATED_CORE_REQUIREMENTS`
  (`routes.py:251`, entry at `:285`, `core.delegated_device_effective_area`)
  into the static native requirements (`routes.py:288`). The new entry is the
  four profiles in §2.2, plus the profiles the build names for script
  configuration, the graph, scene configuration and the new helper reads.
- Drop the ha-mcp `ha_search` error probe
  (`ha_mcp_readmission/ha_mcp.py:94-101`) from the native path. It stays only
  as the admission check for the dashboard sub-call.
- **Effective area.** An entity's area is its own registry `area_id` when
  present. An entity `area_id` that is present but invalid gives no area and
  no fallback. Otherwise the area is the device's effective area under
  `core.direct_device_registry_read`: child, then parent
  (`profiles.py:213-229`). This matches 9.2.0 `effective_entity_area_id`
  (`utils/device_registry_semantics.py:89-108`). The raw device `area_id`
  alone is not used.
- The provider's command allowlist is exactly the reads in §2.2 and nothing
  else.

### 2.4 Dashboards: how the tool routes them

Dashboard search stays on the delegated ha-mcp route, as relayed from Josh.
The routing:

- If `search_types` does not contain `dashboard`, native makes **zero** ha-mcp
  calls for dashboards. (J2's default adds one helper sub-call; §2.5.)
- If it does, native makes exactly one internal delegated `ha_search`
  sub-call with fixed arguments:
  - `query`: the caller's query;
  - `search_types`: `["dashboard"]`;
  - `exact_match`: the caller's value;
  - `include_config`: false;
  - `offset`: 0;
  - `limit`: the caller's `offset + limit`.

  No other argument and no other `search_types` value is forwarded.
- The sub-call's reply passes through the existing Row 25 projection.
  Dashboard `url_path` and `title` stay withheld (HAMCP-447).
- Its `dashboards` rows merge into the configuration list by the same score
  sort as the native buckets (§3.6). Ties keep the 9.2.0 category order, with
  dashboards last. Then the merged list is sliced to
  `[offset:offset+limit]`. `config_total_matches` is the native total plus the
  sub-call's `config_total_matches`.
- The sub-call's own completeness becomes one `partial_reasons` entry with
  `search_type` `dashboard`. The F3 qualifier applies to that bucket only.
- If ha-mcp is not admitted, or the sub-call fails, the reply is partial with
  `source_unavailable` for `dashboard`, and the native buckets are still
  served. Native never falls back to the delegated route for any other
  bucket.
- Without a query, `search_types` `["dashboard"]` is refused with the 9.2.0
  validation error (§3.1). That is the same as today.

### 2.5 Flow-helper bodies **[JOSH] J2**

9.2.0 searches a flow helper's options body by opening and aborting its
options flow, for Home Assistant's own helper domains
(`tools/smart_search/_flow_helpers.py:20-67`, scoring at `:122`). That is the
HAMCP-233 path. Entries from custom helper integrations are not searched and
are counted as a partial reason (`_deep.py:1541-1545`).

Josh's recorded decision (i) for definition visibility (HAMCP-132,
`docs/HAMCP_132_DEFINITION_VISIBILITY_DESIGN.md:445-450`, 2026-10-20) is "no
options-flow open and abort". It is my inference, not Josh's decision, that
the rule also applies to search.

- **Default until Josh decides (keep):** flow-helper body matching stays
  delegated. When helpers are requested, native makes one fixed sub-call like
  §2.4, with `search_types` `["helper"]`. Native keeps only the sub-call's rows
  whose `helper_type` is not one of the six storage types. HAMCP-233 stays
  open on that path, as it is today.
- **Recommended (retire flow-helper body matching):** native opens no flow.
  Flow helpers match on title and domain only. A flow helper whose body was
  not searched is counted in `partial_reasons` as `not_searched` with code
  `flow_not_opened`. This closes HAMCP-233 for search.

Either way, options bodies are never returned (HAMCP-454).

## 3. Search semantics (parity with 9.2.0)

### 3.1 Eligibility (copied exactly)

Inputs are stripped first (`tools_search.py:355-362`). Then:

- `explicit = search_types is not None` (`tools_search.py:363`);
- entity search runs when (`query` or `domain_filter` or `area_filter` or
  `state_filter`) and not `explicit` (`response.py:337`);
- configuration search runs when `query` and (`explicit` or no entity
  filter);
- with a query, an entity filter and no explicit `search_types`,
  configuration search is skipped and the intent-skip warning is emitted
  (R-C);
- when neither branch runs, the call is refused with the 9.2.0 validation
  error, `parameter` `query` (`tools_search.py:373-380`). This is S5, and it
  closes the P1 no-criteria hole.

### 3.2 Entity matching

- **`exact_match` true** (the default): the casefolded query must be a
  substring of `entity_id` or the friendly name
  (`tools/search/entities.py:447-498`). Scores:
  - 100 on an equal match, else 80 (`entities.py:481`);
  - hidden entities lose 20 (`HIDDEN_SCORE_PENALTY`,
    `utils/fuzzy_search.py:35`);
  - `match_type` is `exact_match`.
- **`exact_match` false:** fuzzy matching, kept as relayed ("keep fuzzy
  matching"). The build ports ha-mcp's scorer, `utils/fuzzy_search.py`, as a
  reviewed Engineering module:
  - BM25 with k1 1.2 and b 0.5 (`:74-79`);
  - the typo fallback, a token ratio at threshold 75 (`:391-457`);
  - match types `exact_id`, `exact_name`, `exact_domain`, `partial_id`,
    `partial_name` and `fuzzy_match` (`:540-555`), `alias_match` (`:375`)
    and `typo_fallback` (`:457`);
  - the hidden penalty;
  - the threshold: the `fuzzy_threshold` setting, default 60
    (`config_settings.py:147`, used at `tools/smart_search/__init__.py:41-54`).
    The area-plus-query mode uses 80 (`tools/search/modes.py:85`). Whether the
    installed ha-mcp overrides the setting is unknown and is a build input.

  `score` and `match_type` are kept.
- **Attribution.** ha-mcp is MIT-licensed, "Copyright (c) 2024 Julien"
  (`LICENSE:3`). The ported module keeps that notice and names the source
  commit `51e3aac7`.
- **Ordering:** `(-score, entity_id)`, as the 9.2.0 legacy paths sort
  (`entities.py:606`, `fuzzy_search.py:233`, `modes.py:221`, `:423`, `:522`).
  The golden fixture in §8 pins it.
- Token-AND is dropped. The architect withdrew revision 1's O5 recommendation.

### 3.3 Area filter

The 9.2.0 ladder is kept (`tools/smart_search/_entities.py:686-800`):

1. An exact area match on id, name or alias.
2. Otherwise an exact floor match, which expands to the floor's areas.
3. Otherwise a fuzzy floor match. More than one is ambiguous, and gives a
   warning and an empty result.
4. A single fuzzy floor wins only if it scores above the best fuzzy area.
5. Otherwise a fuzzy area match: whole-string ratio over id, name and
   aliases, at 80 or above.

The effective-area rule is §2.3. 9.2.0 degrades to exact-only with a warning
when the floor registry is unavailable. Natively, that case fails the read
when step 1 did not match, because the floor steps are then needed (§6.2).

### 3.4 State filter

- Stripped and case-insensitive. Standalone, it enumerates.
- In two modes, 9.2.0 applies `state_filter` to the returned page only and
  adds `state_filter_note`:
  - a query with `exact_match` false (`tools/search/modes.py:663-667`);
  - a query with `area_filter` (`modes.py:146-153`).

  Native keeps that behavior at parity. The note's fact becomes a reviewed
  warning text, because the `state_filter_note` key stays withheld
  (HAMCP-447).

### 3.5 Configuration and reference matching

- **Configuration score:** 9.2.0's `_score_deep_match` and its exact and
  fuzzy dictionary walks (`tools/smart_search/_scoring.py:15-160`). The
  threshold is 100 with `exact_match` true, else the fuzzy threshold
  (`_scoring.py:39`). Native ports the same functions under the attribution
  in §3.2.
- **Match flags:** `match_in_name`, `match_in_config` and
  `match_in_references`, as 9.2.0 builds them (`_deep.py:593-594`,
  `:731-732`, `:878-883`; `_scenes.py:442-443`; `_flow_helpers.py:205-208`).
- **References:** for an `entity_id`-shaped query with automations, scripts
  or scenes requested, graph hit records have score `_GRAPH_HIT_SCORE` and
  `match_in_references` true (`_graph.py:393-421`). A hit merges into an
  existing record by setting the flag (`_graph.py:431-481`). A referencing
  automation is returned even when its configuration could not be read, as
  the public `query` description promises.
- **`config_time_budget`:** kept. With null, the 9.2.0 defaults apply: 30 s
  for automations, 20 s for scripts, 20 s for scenes, and a 5 s per-item
  request timeout (`config_settings.py:167-216`). Items skipped by the budget
  are counted (§6.1).
- Bodies are read in memory for matching only. They are never returned,
  logged or put in the audit record.

### 3.6 Ordering and paging

- Each call captures its lists once, then sorts and slices them. `offset` is
  a slice of that call's ordered list. Two calls are two snapshots: a state
  change between pages can repeat or skip a row, as it can today. The union
  test in §8 is same-snapshot only.
- **Entities** are paged with `limit` and `offset`, and report
  `entity_has_more` and `entity_next_offset`.
- **Configuration** records from all requested types, plus dashboard rows,
  are merged and sorted by score, descending and stable. Ties keep the 9.2.0
  category order (`_deep.py:1262-1267`). The list is paged as one surface and
  reports `config_has_more` and `config_next_offset`.
- `limit` applies per surface (entities, configs), as in the public schema.

## 4. Arguments (C1, C2)

The argument names, types and defaults stay those of
`PUBLIC_SCHEMAS["ha_search"]` (`providers/upstream_reads_9_2.py:275`): 15
arguments with `additionalProperties: false`. Behavior is defined row by row
below.

Status values:

- **Kept**: today's behavior is unchanged.
- **Redefined**: client-visible behavior changes. The row needs a decision.
- **Retired**: the capability is removed. That needs Josh's mark by name.

No row is retired. If a later revision retires a row without an owner mark,
the build stops.

| Argument | Today (9.2.0 through Engineering) | Native | Status |
| --- | --- | --- | --- |
| `query` | Searches entities, configuration bodies and entity references | The same (§3.2, §3.5). No `maxLength` (J1, no caps) | Kept |
| `search_types` | Configuration types `automation`, `script`, `scene`, `helper`, `dashboard`. The default is the first four. Any explicit value skips entities. The public description omits that | The 9.2.0 meaning is kept. The description gains the wire sentence: "Explicitly providing this selects configuration-only search and skips entities. Omit it for entity discovery." With a query, the R-A warning explains the empty bucket. `dashboard` routes per §2.4 | Redefined (description only), decided as relayed: reading (i), confirmed as relayed (J3) |
| `domain_filter` | Entity search only; does not affect configuration search | The same | Kept |
| `area_filter` | The ladder in §3.3, with floor expansion | The same, plus the effective-area rule (§2.3) | Kept |
| `state_filter` | Case-insensitive; standalone enumerates; page-only in the two modes of §3.4 | The same (§3.4) | Kept |
| `exact_match` | true: substring. false: fuzzy | The same, with the scorer ported (§3.2) | Kept |
| `limit` | Default 10, minimum 1, no maximum, per surface | The same, with no cap. A page the response budget ends early is `paged` with the next offset (§6.3) | Kept, decided as relayed (J1, no caps) |
| `offset` | Minimum 0 | The same | Kept |
| `group_by_domain` | Adds the `by_domain` map of page records. Withheld today | The same. `by_domain` stays withheld until HAMCP-447 releases it, and is counted in `fields_withheld` | Kept |
| `per_domain_limit` | Caps `by_domain` lists only | The same, so no visible effect while `by_domain` is withheld | Kept |
| `include_hidden` | Default true, with a score penalty; false excludes hidden entities | The same | Kept |
| `include_config` | Default false. With true, bodies are withheld by Row 25 | Bodies are still withheld (HAMCP-454). The reply is partial `filtered` with code `config_bodies_withheld` | Kept (named, not silent) |
| `result_fields` | 13 allowed keys: `entity_id`, `friendly_name`, `domain`, `state`, `score`, `match_type`, `name`, `area`, `floor`, `labels`, `aliases`, `is_group`, `member_entity_ids`. Unknown or empty is rejected (`tools/search/entities.py:42-62`, `:139-160`) | The same set, default and refusal (S3) | Kept |
| `fields` | Projects top-level keys; pagination and diagnostic keys always retained | The same, mapped exactly (S4). A withheld key that is requested is counted in `fields_withheld`, as today | Kept |
| `config_time_budget` | 0.001-300 or null; per-type configuration budget | The same (§3.5) | Kept |

The schema fingerprint (`SEARCH_PUBLIC_SCHEMA_FINGERPRINT`,
`upstream_reads_9_2.py:98`, checked at `:186`) changes only by the
`search_types` description sentence. That is the declared delta and needs a
reviewed fingerprint update in the build. Every other description and behavior
is unchanged. A contract test asserts both.

## 5. Reply keys

### 5.1 Top-level keys

The 9.2.0 top-level key list is the one in the `fields` description (31
keys). Each is kept exactly as Row 25 treats it today.

| Key | Today | Native | Status |
| --- | --- | --- | --- |
| `success`, `query`, `search_types`, `search_type`, `domain_filter` | Returned | The same | Kept |
| `entities`, `automations`, `scripts`, `scenes`, `helpers`, `dashboards` | Returned | The same. `dashboards` only from §2.4 | Kept |
| `entity_total_matches`, `config_total_matches`, `count`, `offset`, `limit`, `has_more`, `next_offset`, `entity_has_more`, `entity_next_offset`, `config_has_more`, `config_next_offset` | Returned | The same, computed natively (§3.6) | Kept |
| `partial` | Returned; true on every reply (F3) | True only with `partial_reasons` (§6.1) | Redefined, decided as relayed (HAMCP-472 P2, R-B) |
| `warnings` | Returned; every text replaced | Only reviewed fixed texts from a closed set (§6.4); nothing replaced | Redefined (the HAMCP-472 fix) |
| `errors` | Returned | Reviewed error entries only | Kept |
| `by_domain`, `area_filter`, the state filter echo, `area_names`, `state_filter_note`, `message`, `partial_reason` | Withheld (HAMCP-447, Row 25) | Still withheld. The facts they carry appear in `applied`, `partial_reasons` or a reviewed warning | Kept (withheld) |

Engineering envelope keys are kept: `fields_withheld`,
`fields_dropped_by_policy`, `upstream_warnings_replaced` (always 0 on a
native reply) and `engineering_notes`
(`providers/upstream_reads_9_2_keys.py:46-47`, `:456-458`).

Added keys (additive):

- `applied`, which records what the call actually did:
  - the effective `search_types`;
  - `domain_filter`;
  - whether `area_filter` and `state_filter` were set, as booleans only,
    because the echoes stay withheld (HAMCP-447);
  - `exact_match`, `include_hidden`, `limit`, `offset`;
  - whether entity search and configuration search ran.
- `partial_reasons`: a list of `{reason, search_type, count, code}`.

### 5.2 Record keys

Kept as Row 25 admits them today:

- entity records: `entity_id`, `friendly_name`, `domain`, `state`, `score`,
  `match_type`, plus the `result_fields` opt-ins (`name`, `area`, `floor`,
  `labels`, `aliases`, `is_group`, `member_entity_ids`);
- configuration records: `entity_id`, `friendly_name`, `score`,
  `match_in_name`, `match_in_config`, `match_in_references`, `script_id`,
  `scene_id`, `helper_type`, `name`.

Withheld, as today: `config` (HAMCP-454); `url_path`, `title`, `entry_id` and
`options_withheld` (HAMCP-447).

On the legacy path, the `name` opt-in carries no composed value, only its note
(`_COMPOSED_NAME_UNAVAILABLE`, `tools/search/entities.py:81`, used at `:117`).
The component path composes it. Natively, `name` is composed by the pinned
Core naming rule (ADR-021). If the build cannot pin that rule, `name` is
served as the legacy path serves it, with the note.

No new record field is added in this revision. Revision 1 proposed `device_id`,
`device_name`, `area_id`, `hidden`, `entity_category`, `device_class`,
`unit_of_measurement`, `last_changed`, `last_updated`, and automation `id`,
`mode` and `last_triggered`. All of those are dropped from this revision.

## 6. Partial contract, failure and bounds

### 6.1 Partial (R-B, P1)

`partial == bool(partial_reasons)` on every reply. The closed reason set:

- `paged`: more matches exist beyond this page (count, next offset). That
  includes a page the response budget ended early (§6.3).
- `truncated`: the response budget cut fields or strings inside returned
  records (count).
- `filtered`: Engineering withheld content by policy. That is a sanitizer
  redaction (count), or bodies with `include_config` (code
  `config_bodies_withheld`).
- `not_searched`: some configuration items were not searched. Each entry has
  a `search_type`, a count and one code:
  - `time_budget`;
  - `read_failed`;
  - `not_readable` (a YAML-defined item the per-item read cannot fetch);
  - `flow_not_opened` (J2 recommended path);
  - `graph_unavailable` (an `entity_id`-shaped query whose reference graph
    read failed).

  These are the 9.2.0 per-type partial causes (`_deep.py:1234-1356`,
  `:1356-1420`, `:1541-1545`), named instead of summarized.
- `hidden`: Engineering withheld matching records by its own visibility
  policy. Native has no such policy, so this reason appears only if one is
  added later. The caller's `include_hidden` false is scope, not `hidden`.
- `source_unavailable`: a source that no requested filter depends on could
  not be read, and the affected fields are null (which source, count). That
  includes the dashboard sub-call (§2.4), the J2 helper sub-call, and
  malformed state rows (code `malformed_state_row`, count). A malformed row
  is never skipped silently.

A complete reply has `partial` false and no reasons. It has no `VISIBLE_SCOPE`
text and no `REPLACED_PARTIAL` text. A caller's own filters are never a
partial reason.

### 6.2 Failure behavior

The rule: never wrong-empty, never silently partial. The read fails with an
explicit reviewed error, with no fallback to the delegated route and no empty
success, when:

- the call has no query and no entity filter (§3.1);
- the state read fails;
- the entity registry read fails while entity search runs, because hidden
  status, aliases and names depend on it;
- `area_filter` is set and the area registry or the device registry fails;
- floor resolution is needed (§3.3, steps 2-4) and the floor registry fails;
- the sanitizer returns `failed_closed` for any output string;
- an inventory bound is exceeded (§6.3).

A source failure that no requested filter depends on sets
`source_unavailable`. Examples are the label registry with only the `labels`
enrichment requested, or the device registry with only the `area` enrichment
requested. Native never treats a null as "not hidden" or "not in this area".

### 6.3 Bounds (B1)

- **Inventory:** a states or registry list longer than
  `MAX_CORE_PROBE_ITEMS` (`ha_core_readmission/source.py:22`, 20,000) fails
  closed with the observed count and an empty body. That is the same bound as
  the read-admission probe and the lifecycle inventory. This install's
  current state count is a build acceptance input. Nothing in this design
  claims it.
- **No caps (J1, decided as relayed):** the `limit` and `query` schema stay
  identical to 9.2.0. `limit` has no maximum and `query` has no `maxLength`.
  No call is refused for its `limit` or its query length.
- **Response budget:** a large `limit` returns the records that fit. When the
  budget ends the page before `limit` records, the reply is `paged`, with the
  next offset at the first record not returned. When the budget cuts fields or
  strings inside a returned record, the reply is `truncated` with a count.
  Neither cut is silent, and neither is an error.

### 6.4 HAMCP-472 requirements

- **R-A. An empty result states what was applied.** `applied` is in every
  reply.
  - When an explicit `search_types` skipped entity search, the reply carries
    the reviewed form of the 9.2.0 text (`response.py:630-631`): "Entity
    search skipped: an explicit search_types selects configuration-only
    search; omit search_types to search entities."
  - HAMCP-485 (Backlog) would add an `entity` value later.
- **R-B.** As in §6.1.
- **R-C. Intent skip.** When a query is combined with an entity filter and no
  explicit `search_types`, configuration search is skipped, as in 9.2.0
  (`response.py:240-245`). The reply carries a reviewed text: "Configuration
  search skipped: domain, area and state filters select entity search;
  remove them or pass search_types to search configurations." It is a
  warning, not a partial reason. Every reply on which configuration search
  did not run carries it or the R-A text.

The closed warning set also has reviewed texts for:

- an ambiguous fuzzy floor;
- the `state_filter` page-only note (§3.4);
- the composed-`name` note (§5.2).

## 7. Disclosure (D1)

Drawn from the Row 25 admitted set (`upstream_reads_9_2_keys.py:313-327`).

| Already returned today | Withheld today, still withheld | Withheld today, proposed for release |
| --- | --- | --- |
| Entity identity, friendly name, domain, state, score and match type; the HAMCP-441 keys `name`, `area`, `floor`, `labels`, `aliases`, `is_group` and `member_entity_ids`; configuration identity (`entity_id`, `friendly_name`, `script_id`, `scene_id`, `helper_type`, `name`); the match flags; all buckets and paging keys; the `query` and `domain_filter` echoes | Configuration bodies (`config`, HAMCP-454); flow-helper options bodies (HAMCP-454); dashboard `url_path` and `title`, `entry_id`, `options_withheld`, `by_domain`, the area and state echoes (HAMCP-447); `area_names`, `state_filter_note`, `message`, `partial_reason` | None in this design. Releasing the HAMCP-447 keys is that row's decision |

Notes for the build's security section:

- `match_in_config` and a body score show whether a query string occurs in a
  configuration body. The bits are the same as today's delegated reply, but
  native computes them over bodies Engineering itself reads. The security
  section records that this is a query oracle over bodies, already exposed
  today.
- The new reads in §2.2 add sources, not record fields.
- Every output string passes `sanitize_untrusted_data`
  (`sanitization.py:1069`) with identity-only markers (`:1075`).
  - A redaction count above zero sets `filtered`.
  - A `failed_closed` result fails the read. It is never returned as a
    redacted success or as the original string.
- The build changes provider routing, the public schema description and read
  admission. It needs:
  - a security section;
  - the negative reachability tests in §8;
  - Codex review, per the re-scope's rule for security-touching items;
  - Josh's named OK for each protected file.

## 8. Acceptance (reads only)

### Offline, in the build pull request

1. **The audit shape (C3).** Each case runs offline and again on the
   installed system:
   - `domain_filter` light plus `search_types` `["automation"]`, with no
     query, gives the 9.2.0 validation error;
   - with a query, the reply is configuration-only, with entities `[]`,
     `applied.search_types` `["automation"]`, `applied` showing that entity
     search did not run, the R-A warning, no replaced warning, and `partial`
     false when nothing else applies.
2. **The contract test (C1).**
   - It loads `PUBLIC_SCHEMAS["ha_search"]` and the Row 25 key set, and fails
     if §4 or §5 disagree with either.
   - The schema fingerprint differs from today's only by the declared
     `search_types` description sentence.
3. **Domain-only and `exact_match` true substring:** equal to the
   `search_entities` fixture for the same domain and text. `search_entities`
   is the oracle only for these two.
4. **The `exact_match` false oracle:** a golden fixture generated by running
   the pinned 9.2.0 scorer (`utils/fuzzy_search.py` and
   `tools/smart_search/_scoring.py` at `51e3aac7`) offline over a fixed
   corpus. It has provenance naming the commit and the command. Native scores,
   match types and order must equal it, including the typo, alias and
   area-plus-query (threshold 80) cases.
5. **Kept capabilities (C2):**
   - an exact `entity_id` referenced by an automation returns that
     automation with `match_in_references` true, with bodies absent and
     `partial` false;
   - the same with that automation's configuration unreadable returns the
     automation, plus `not_searched` `read_failed` for its body;
   - `result_fields` with `is_group` and `member_entity_ids` returns those
     keys;
   - a floor name, an area alias and a close-spelling floor each return that
     area's entities;
   - an ambiguous fuzzy floor gives the warning and no entities;
   - `state_filter` `"ON"` equals `"on"`;
   - scenes and each of the six storage helper types match by name and by
     body;
   - flow helpers follow J2;
   - `include_hidden` false excludes hidden entities, and true ranks them
     with the penalty.
6. **Effective area (R1):**
   - a child device whose own area is null and whose parent is in the area is
     included;
   - an entity with its own area overrides its device;
   - an invalid entity area gives no fallback.
7. **Partial invariant.** `partial == bool(partial_reasons)`. Each reason
   comes only from its own cause. A complete reply has no reasons and neither
   the `VISIBLE_SCOPE` text nor the `REPLACED_PARTIAL` text.
8. **Paging:**
   - deterministic order and the totals;
   - pages that union to the total on one frozen snapshot;
   - a fixture that changes one matching state between pages shows the
     snapshot behavior, and the test claims no union;
   - `limit` 100,000 is accepted, never refused (J1): with a response budget
     fixture smaller than the match set, the reply returns the records that
     fit, with `paged` and the next offset at the first record not returned,
     and the next page continues from it;
   - a record whose fields the budget cuts gives `truncated` with a count.
9. **Dashboards (§2.4):**
   - a dispatcher spy shows zero ha-mcp calls when `search_types` lacks
     `dashboard` (and, under J2's recommendation, `helper`), and exactly one
     call with the fixed arguments when it has it;
   - no other `search_types` value or argument is forwarded;
   - a failed sub-call gives `source_unavailable` for `dashboard` while the
     other buckets are served;
   - the merge order and `config_total_matches` are checked.
10. **Fail closed (P1, B1):**
    - the no-criteria call is refused;
    - a states failure gives an error;
    - a device registry failure with `area_filter` set gives an error;
    - a floor registry failure when a floor step is needed gives an error;
    - a label registry failure with only `labels` requested gives
      `source_unavailable`;
    - a malformed state row is counted as `source_unavailable`
      `malformed_state_row`;
    - a sanitizer `failed_closed` gives an error with no record body, and a
      redaction gives `filtered`;
    - a 20,001-state fixture fails with the bound error and the observed
      count;
    - a 20,000-state fixture with `domain_filter` succeeds;
    - a 10,000-character query is accepted.
11. **`include_config` true:** `filtered` with `config_bodies_withheld`, and
    no body.
12. **Routes (R1):**
    - `ha_search` is absent from `DEVICE_DEPENDENT_DELEGATED_TOOLS`;
    - its requirement tuple equals the declared native profiles;
    - the provider's command allowlist equals §2.2;
    - a dispatcher spy shows zero ha-mcp calls for an entity-only search,
      including when the device registry read fails.
13. **Negative reachability:**
    - no options or config flow call;
    - no service call;
    - no write;
    - no `integration/descriptions` read;
    - no fallback to the delegated route for a native bucket;
    - Engineering provider attribution in the audit record.

### Installed, read-only (separately authorized, after deploy)

Each step is one read, approved by Josh. Counts are compared with
`search_entities` or with the delegated reply captured at the same time, not
with fixed numbers.

1. The audit shape from offline item 1, both cases.
2. `domain_filter` light with no query equals `search_entities` for lights.
3. Query "light" plus light, and "leak" plus `binary_sensor`, with
   `exact_match` true, equal `search_entities` with the same text and domain.
4. The same queries with `exact_match` false return a superset that includes
   every `search_entities` match. They carry `score` and `match_type`.
5. An exact `entity_id` used by a known automation returns that automation
   with `match_in_references` true.
6. `area_filter` by floor name, by area alias and by area id.
7. `state_filter` "ON" plus light.
8. `limit` 5 on a larger set: the next offset, and a second page that
   continues the first.
9. `include_config` true gives `filtered`, `config_bodies_withheld`, and no
   body.
10. `search_types` `["dashboard"]` with a query returns the delegated
    dashboard rows, with `url_path` and `title` withheld.
11. A query with no matches gives empty buckets, `applied`, and `partial`
    false.
12. The audit log records native provider attribution, plus one delegated
    sub-call only for step 10 (and for helper searches under J2's default).
13. `tools/list` matches the catalog that the build release declares.

## 9. Decisions

### Decided, as relayed by the architect (block `release-291-josh-decisions-0424`, 2026-11-24)

I did not see Josh's words. These are the relayed texts.

- **Option (b)**, "Native search b": parity with 9.2.0 for entities,
  automations, scripts, scenes and helpers, including configuration-body and
  entity-reference matching. Results are identity-only, and bodies stay
  withheld (HAMCP-454). This also settles revision 1's items:
  - O1: native serves `ha_search`;
  - O2: no value is out of scope;
  - O4: the 9.2.0 defaults.
- **Dashboards:** "Dashboard search is NOT dropped: it stays on the delegated
  ha-mcp route until native covers it." The routing is in §2.4.
- **`exact_match` false:** "2.keep fuzzy matching" (§3.2). Revision 1's O5,
  token-AND, is withdrawn.
- **`search_types`:** "1. Ii, but I now and I filed later". The architect's
  reading: option (i) now, meaning keep the 9.2.0 meaning, fix the description
  and add the R-A warning; option (ii), an `entity` value, filed for later as
  HAMCP-485.
- **J3, the `search_types` reading confirmed** (relayed in an architect
  session message, 2026-11-24): "Confirmed", answering "is my reading — (i)
  now, (ii) filed later as HAMCP-485 — correct?".
- **HAMCP-472 P2:** "5. Yes": defer retiring F3 to native search (N2).
- **J1, no caps** (relayed in an architect session message, 2026-11-24): "No
  caps", answering "no new caps on result count or query length; page
  instead?". The `limit` and `query` schema stay identical to 9.2.0, with no
  maximum and no `maxLength`. A large `limit` returns a page with `paged` and
  the next offset; it is never refused (§6.3).
- **Change later:** "Agreed we can change later if it shows warranted".

### Design choices from the reviews (not owner decisions)

- O3: `fields` maps exactly (S4).
- S3: the 9.2.0 `result_fields` set.
- S5: the no-criteria refusal.
- B1: the inventory cap at `MAX_CORE_PROBE_ITEMS`. With J1, this supersedes
  N3 and N4.
- O6: parity. No disabled entities without a state (§2.1).

### Open for Josh

- **[JOSH] J2:** flow-helper bodies. The default keeps them delegated through
  a fixed helper sub-call (§2.5). The recommendation is to retire flow-helper
  body matching, meaning native opens no flow and counts `flow_not_opened`.
  That applies the recorded HAMCP-132 (i) decision to search, which is my
  inference.

No row in §4 or §5 is retired.
