# HAMCP-481 native search — design

Status: **implementation record, revision 5; not yet marked implemented.**
Docs only. The build is HAMCP-501 on `release/2.10.0`: W1 #506, W2 #507,
W3 #513, W4 #516 and W6 #517 are merged; W7 (this revision) and W6b #519 are
open. W5 is dropped (§3.7). The "Implemented in 2.10.0 (Engineering
2.4.2-beta.14)" mark is added only after #519 merges, as the architect
directed. Revision 5 notes in §0 to §9 are marked *Rev 5* and point to the
implementation record in §10. Where a revision 4 text and §10 differ, §10
describes what was built.

Revision 4 was the architect's delta-check change to revision 3 (head
`93175f3c`): the visibility filter gives one fixed note instead of a
per-query `hidden` count (§3.7, §7). Revision 5 supersedes that filter
(§3.7, §10.2).
Revision 3 was the correction batch for Cursor's round-2 review of revision 2
(head `3ace9227`). Revision 2 was the correction batch for the Cursor review
and the architect review of revision 1 (head `4cd3a430`), with J2 applied as
decided. The build was planned for the release after 2.9.1, not for 2.9.1
(2.4.2-beta.13). It is built in 2.10.0 (§10). Nothing here authorizes a
runtime, schema, routing,
protected-path, version or release change, deployment, or live Home Assistant
or Engineering access. No decision is open for Josh in this revision. The
build gate added in revision 3 (the source of the ha-mcp visibility state,
§3.7) is dropped: Josh's 2026-11-25 decision, as relayed by the architect,
supersedes it (§9, §10.2).

- Register: HAMCP-481. Related rows:
  - HAMCP-472, the `ha_search` audit finding this design absorbs;
  - HAMCP-441, the `result_fields` enrichment keys, which are returned today;
  - HAMCP-447, the dashboard identity, `by_domain`, area and state echoes,
    `entry_id` and `options_withheld`, withheld today;
  - HAMCP-454, the `include_config` bodies and flow-helper options, withheld
    until a redaction design exists;
  - HAMCP-233, where `ha_search` opens options flows;
  - HAMCP-403, the `search_unified` quarantine;
  - HAMCP-485, a later `entity` value for `search_types` (Backlog);
  - HAMCP-501, the build (W1 to W7 and W6b, §10.1);
  - HAMCP-499, the ha-mcp entity visibility filter, later (§3.7);
  - HAMCP-509, the 2.10.0 release documents (security, tool audit,
    release notes, acceptance and changelog), outside this document.
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
    N1 to N4, plus the author self-review S1 to S5;
  - Cursor round 2, `cursor-hass-mcp-admin-491-r2.md`: r2-S1, r2-S2, r2-S3,
    r2-M1 and r2-M2, all corrected as Cursor wrote them, as the architect
    directed. The `r2-` prefix keeps them apart from the self-review S1 to S5;
  - the architect's delta check of revision 3 (relayed in an architect session
    message, 2026-11-24): the author's additions 1 to 3 accepted, and
    addition 4, the `hidden` count, changed to the fixed visibility note (§9).
- Base: `release/2.9.1` at `774328e0` (2.4.2-beta.13 declared).
- Revision 5 base: `release/2.10.0` at `c2b68911` (#517 merged).
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
  never returned. *Rev 5 (S1 = b+, §10.4):* Engineering reads no
  automation, script or scene body. Those rows come from fixed delegated
  sub-calls, which match bodies inside ha-mcp.
- **Dashboards stay delegated.** Dashboard search stays on the delegated
  ha-mcp route, through one fixed, paged internal sub-call (§2.4).
- **Visibility not ported (rev 5).** Superseded on 2026-11-25 by Josh's
  decision, as relayed by the architect: native search ships without the
  ha-mcp entity visibility filter, and HAMCP-499 follows later (§3.7,
  §10.2). Revision 4 kept the filter and its enforce mode.
- **Default is parity.** Every current argument behavior and reply key is
  kept unless Josh retires it by name. This revision retires nothing.
- **Partial only with a reason.** `partial` is true only when Engineering
  withheld, cut or could not search something, and it names the reason. Rev 5:
  native has no visibility filter, so visibility never sets it, and
  revision 4's fixed visibility note is not built (§3.7, §10.10).

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
| Automations | Rev 5: the fixed delegated sub-call for this type (S1 = b+, §10.4). Revision 4: `automation.*` states, plus each automation's configuration, read for matching | Identity-only records with match flags |
| Scripts | Rev 5: the fixed delegated sub-call for this type (S1 = b+, §10.4). Revision 4: `script.*` states, plus each script's configuration | The same, with `script_id` |
| Scenes | Rev 5: the fixed delegated sub-call for this type (S1 = b+, §10.4). Revision 4: `scene.*` states, plus each UI scene's configuration | The same, with `scene_id` |
| Storage helpers | the six `<type>/list` commands 9.2.0 searches: `input_boolean`, `input_number`, `input_select`, `input_text`, `input_datetime` and `input_button` (`tools/smart_search/_deep.py:927-934`), mapped to the current `entity_id` through the entity registry (`_deep.py:749`) | Identity-only records with `helper_type` |
| Flow helpers | the delegated ha-mcp `ha_search`, through one fixed, paged helper sub-call (§2.5) | Identity-only records with `helper_type` and `name`, as today |
| References | Core's reference graph, `search/related` for an entity, for an `entity_id`-shaped query when automations, scripts or scenes are requested (`tools/smart_search/_graph.py:374-390`) | `match_in_references` on the referencing records |
| Dashboards | the delegated ha-mcp `ha_search`, through one fixed, paged sub-call (§2.4) | Row 25 projection, as today |

Counter, timer and schedule helpers have no body search in 9.2.0. They appear
only as entity records, and the same holds natively.

At parity, disabled entities with no state are not returned. 9.2.0's legacy
entity search scores the Core state list (`tools/search/entities.py:539`,
`tools/search/modes.py:555-617`), and native does the same. This closes
revision 1's O6.

### 2.2 Reads

Every read is read-only. Native search makes no write, service call, reload,
or options or config flow call. The delegated helper sub-call does open and
abort options flows inside ha-mcp, as today (§2.5).

*Rev 5 (§10.4):* the reads were built under J2' option A, as relayed by the
architect: only existing signed Core profiles, with no profile, model or
signed-source change. `config/entity_registry/get_entries` was added for
entity aliases (architect, 2026-11-25). Under S1 = b+ native reads no
automation, script or scene configuration, so the configuration reads below
were not built. The closed read table is `search/core_contracts.py`.

Already admitted, with the profile that attests it:

- states list (`GET /api/states`): `core.direct_entity_state_read`
  (`ha_core_readmission/profiles.py:247-257`); *rev 5 correction:* the
  states list is in `core.basic_rest_read` (`profiles.py:159`,
  `rest_states_list` at `:162`) and `core.state_service_discovery` (`:185`,
  `:188`). `core.direct_entity_state_read` (`:248`) covers single-entity
  reads only and is unused (`search/core_contracts.py:61-64`);
- area, floor, label and entity registry lists: `core.non_device_registry_read`
  (`profiles.py:195-211`);
- device registry list, with child-then-parent effective area:
  `core.direct_device_registry_read` (`profiles.py:213-229`);
- automation configuration: `core.automation_configuration_read`
  (`profiles.py:259-270`). *Rev 5:* not built (S1 = b+, §10.4).

Admitted as a command, with a profile still to be named at build:

- `script/config` and `search/related` are in the fixed WebSocket allowlist
  (`clients/websocket.py:24-28`; *rev 5 correction:* `_BATCH_COMMAND_KEYS` is
  at `:32-38`, and `:24-28` is its comment). Today, `ha_config_get_script`
  declares `core.basic_websocket_read` (`ha_core_readmission/routes.py:257`).
  The build
  names the profile that attests each command for a native provider. If none
  does, the read is new (below).

New reviewed reads that the build needs. Each is a protected-path change and
needs Josh's named OK in the build task:

- scene configuration `GET /api/config/scene/config/{config_id}` as a read
  contract. *Rev 5:* not built (S1 = b+, §10.4). Today it is admitted only
  inside the scene configuration and
  write profiles (`profiles.py:149`, `core.scene_configuration_operation` at
  `:814-834`, and `SCENE_CREATE_DELETE_REQUESTS` at `:1183-1189`);
- `input_boolean/list`, `input_number/list`, `input_select/list`,
  `input_text/list`, `input_datetime/list` and `input_button/list`. Today only
  the first two appear, and only inside the governed delete profile
  `CREATED_HELPER_DELETE_REQUESTS` (`profiles.py:1190-1197`);
- none for flow helpers. Their identity and matching come from the helper
  sub-call (§2.5), so native does not read
  `GET /config/config_entries/entry` or
  `GET /config/config_entries/flow_handlers?type=helper`
  (`tools/helper_flows.py:34-45`). `ROUTE_TEMPLATES` (`clients/paths.py:53`)
  holds the options-flow templates (`clients/paths.py:58-59`) for governed
  paths; the native provider must not use them.

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
  ha-mcp child-device adapter before the tool runs. *Rev 5 (G2, §10.3):* not
  done. The internal route stays in it (`routes.py:16`, entry `:22`),
  fail-closed.
- Move `ha_search` out of `DELEGATED_CORE_REQUIREMENTS`
  (`routes.py:251`, entry at `:285`, `core.delegated_device_effective_area`)
  into the static native requirements (`routes.py:288`). The new entry is the
  four profiles in §2.2, plus the profiles the build names for script
  configuration, the graph, scene configuration and the new helper reads.
  *Rev 5 (G2, §10.3):* the internal route keeps its own entry
  (`routes.py:292`), and native `ha_search` has its own static entry
  (`routes.py:333-336`). G1 makes the delegated route internal-only
  (§10.3).
- Drop the ha-mcp `ha_search` error probe
  (`ha_mcp_readmission/ha_mcp.py:94-101`) from the native path. It stays only
  as the admission check for the dashboard and helper sub-calls.
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
*Rev 5:* the reader as built is in §10.7 (W4 #516, extended by W6 #517). The
page size is the §10.5 ladder, not one constant P. The routing:

- If `search_types` does not contain `dashboard`, native makes **zero** ha-mcp
  calls for dashboards. (A search that covers helpers adds one helper
  sub-call; §2.5.)
- If it does, native makes one internal delegated `ha_search` sub-call for
  dashboards, paged by §2.6, with fixed arguments:
  - `query`: the caller's query;
  - `search_types`: `["dashboard"]`;
  - `exact_match`: the caller's value;
  - `include_config`: false;
  - `offset` and `limit`: set for each page by §2.6.

  No other argument and no other `search_types` value is forwarded. The scan
  stops once it holds the caller's `offset + limit` rows, or when a page has
  `has_more` false, or by the §2.6 bounds.
- Each page passes through the existing Row 25 projection. Dashboard
  `url_path` and `title` stay withheld (HAMCP-447).
- Its `dashboards` rows merge into the configuration list by the same score
  sort as the native buckets (§3.6). Ties keep the 9.2.0 category order, with
  dashboards last. Then the merged list is sliced to
  `[offset:offset+limit]`. `config_total_matches` is the native total plus the
  first page's `config_total_matches`.
- The sub-call adds partial reasons only by the §2.6 rule. A sub-call whose
  pages are not partial, with enforce mode off, adds none.
- If a page fails, or a §2.6 bound stops the scan before it holds
  `offset + limit` rows, the reply is partial with `source_unavailable` for
  `dashboard`, code `dashboard_subcall_incomplete`. The dashboard rows already
  returned are kept. If ha-mcp is not admitted, the reply is partial with
  `source_unavailable` for `dashboard`, code `subcall_not_admitted`. In both
  cases the native buckets are still served. Native never falls back to the
  delegated route for any other bucket.
- Without a query, `search_types` `["dashboard"]` is refused with the 9.2.0
  validation error (§3.1). That is the same as today.

### 2.5 Flow helpers: the helper sub-call (J2, decided: keep)

9.2.0 searches a flow helper's options body by opening and aborting its
options flow, for Home Assistant's own helper domains
(`tools/smart_search/_flow_helpers.py:20-67`; the probe at `:169-181`,
scoring at `:122`). That is the HAMCP-233 path. Entries from custom helper
integrations are not searched and are counted as a partial reason
(`_deep.py:1541-1545`).

**J2, decided as relayed** (architect session message, 2026-11-24): keep
flow-helper options-body matching. Josh's words as relayed: "Keep it. Keep in
mind, the idea isn't to lose features. Also keep in mind governance
proportionate to risk." Search therefore does not apply the HAMCP-132 (i)
rule, "no options-flow open and abort"
(`docs/HAMCP_132_DEFINITION_VISIBILITY_DESIGN.md:445-450`). That rule still
governs definition visibility.

- **When.** Native makes one internal delegated `ha_search` sub-call for
  helpers, paged by §2.6, whenever the helper bucket is searched:
  configuration search runs (§3.1) and `search_types` is absent or contains
  `helper`. Otherwise it makes none.
- **Fixed arguments:**
  - `query`: the caller's query;
  - `search_types`: `["helper"]`;
  - `exact_match`: the caller's value;
  - `include_config`: false;
  - `offset` and `limit`: set for each page by §2.6.

  No other argument is forwarded. A 9.2.0 reply has one total for all
  helpers (`_deep.py:1269`) and no per-type count. Native therefore counts
  flow helpers by scanning pages until one has `has_more` false. A scan that
  stops early gives no full flow-helper count (below).
- **Projection.** Each page passes through the Row 25 projection. `entry_id`
  and `options_withheld` stay withheld (HAMCP-447). Options bodies are never
  returned (HAMCP-454).
- **Merge.** On each page, native drops the rows whose `helper_type` is one of
  the six storage types, because native serves those (§2.1). It keeps the
  flow-helper rows. Those rows merge into the configuration list by §3.6, and
  their count is added to `config_total_matches`. No helper is counted twice.
- **Incomplete scan.** If a page fails, or a §2.6 bound stops the scan, the
  reply is partial with `source_unavailable` for `helper`, code
  `helper_subcall_incomplete`, count null. The flow-helper rows already
  returned are kept and merged. `config_total_matches` then counts only those
  rows, and the reply does not claim a full flow-helper count. These two are
  the only causes of that code. A scan that reaches `has_more` false is
  complete.
- **Partial reasons.** The sub-call adds partial reasons only by the §2.6
  rule. A sub-call whose pages are not partial, with enforce mode off, adds
  none. `helper` is in the default `search_types` (`_deep.py:169`), so this is
  what lets a default query search reply with `partial` false.
- **Not admitted.** If ha-mcp is not admitted, the reply is partial with
  `source_unavailable` for `helper`, code `subcall_not_admitted`. Storage
  helpers and every other native bucket are still served. Native never falls
  back to the delegated route for any other bucket.
- **Flow effect, confined.** Each page opens and aborts options flows inside
  ha-mcp, for each Home Assistant helper entry whose title is not a perfect
  match (`_flow_helpers.py:169-181`; the flow is read and aborted by
  `fetch_entry_options_with_status`, `tools/tools_integrations.py:182`). That
  is the HAMCP-233 behaviour. The probes run before the gateway's size check,
  so a page the gateway then rejects has already opened them. A page is a
  whole ha-mcp helper search, so each page repeats the probes: a scan of n
  pages opens and aborts up to n times as many flows as today's single call.
  I found no cache of probe results across calls. The §2.6 page bound caps n.
  The effect is confined to the sub-call's pages. The native provider's
  command allowlist has no flow route (§2.2), and the §8
  negative-reachability tests assert that. HAMCP-233 stays open on this path.
- **Rev 5: the F1 probes (inference).** Each F1 scrub probe (§10.6) is a
  one-row helper page, so it is a whole ha-mcp helper search. It therefore
  likely repeats the options-flow probes. That is two such searches per call
  that searches automation, script or scene bodies, one before and one after
  the scans. This is an inference from the page behavior above. It was not
  stated in the W6 pull request and is not tested (§10.11).
- **Until replaced.** The sub-call stays until HAMCP-484 (native UI helper
  reads, as the architect relayed) replaces it with matching that opens no
  flow. HAMCP-484's register row today scopes dependency and integrity
  analysis (`dependency/yaml_sources.py`), so search must be added to that
  row's scope when it is scheduled.

### 2.6 Sub-call paging and reasons (r2-S1, r2-S2)

This section applies to both sub-calls, dashboard (§2.4) and helper (§2.5).
*Rev 5:* it applies to all five sub-calls: automation, script, scene,
dashboard and helper (§10.4).

**Paging (r2-S1).**

- **One page, one call.** Each page is one delegated `ha_search` call on the
  existing admitted route. The 9.2.0 route policy gives it 60,000 bytes and
  60 seconds (`upstream_tool_policy_9_2_0.json`, the `ha_search` entry). The
  gateway projects the body. Then it fails the page when the encoded size
  plus 8,000 bytes exceeds that limit, so above about 52,000 bytes
  (`providers/upstream_read_gateway.py:4418-4429`). It does not return a
  short page. No new or unbounded transport is added.
- **Page size.** The page size is a fixed constant P. The build names P and
  pins it with a test, so that a page of P projected rows stays under the
  gateway check. For scale: Cursor's local simulation of projected helper rows
  was 49,121 bytes at 250 rows and 58,896 at 300. Page k asks
  `offset` k × P and `limit` P. *Rev 5:* superseded by the page-size
  ladder, 150, 30, 6 and 1 (§10.5).
- **Stop conditions.** The scan stops at the first of:
  - a page with `has_more` false (the scan is complete);
  - for the dashboard sub-call, holding the caller's `offset + limit` rows
    (the scan has what the reply needs);
  - a failed page;
  - 20,000 rows scanned (`MAX_CORE_PROBE_ITEMS`, §6.3; rev 5: `MAX_SCAN_ROWS`,
    `search/subcalls.py:45`, plus 300 page calls, `MAX_PAGE_CALLS`, `:69`);
  - a whole-scan deadline, a fixed constant the build names (rev 5: 60 s,
    `SCAN_DEADLINE_SECONDS`, `search/subcalls.py:43`).

  None of these is caller-controlled. The last three make the scan
  incomplete.
- **Failed page.** A page fails when the gateway rejects it (size, timeout or
  any other gateway error), or when it is malformed. A page whose
  `config_total_matches` differs from the first page's also counts as failed:
  the upstream snapshot moved under the scan, so the pages may skip or repeat
  rows.
- **Incomplete scan.** An incomplete scan gives one `source_unavailable`
  entry, with `search_type` `helper` or `dashboard` and code
  `helper_subcall_incomplete` or `dashboard_subcall_incomplete`, count null.
  The rows already returned are kept. These two causes, a failed page and a
  bound that stops the scan, are the only ones for these codes. A dashboard
  scan that stops because it holds `offset + limit` rows is not incomplete.
- **Not admitted.** When ha-mcp is not admitted, native sends no page. The
  entry is `source_unavailable` with code `subcall_not_admitted`.
- **Options flows.** The helper sub-call's options-flow probes run only inside
  its pages (§2.5). The dashboard sub-call opens none.

**Partial reasons from a sub-call (r2-S2).**

The F3 overlay that `_search_completeness` adds
(`providers/upstream_reads_9_1_entities.py:293-303`) is not used for the
sub-calls. That overlay always marks a delegated search partial, so native
reads ha-mcp's own `partial` field from each projected page instead. Row 25
does not admit `partial_reason`, so native does not know which upstream
reason applied or its count.

For each sub-call that sends at least one page:

- **A page whose projected `partial` is true:** one `not_searched` entry, code
  `upstream_partial`, count null. In 9.2.0 that flag comes from a failed
  helper search, from custom helper entries that were not searched
  (`helper_unsearched`) or from a failed dashboard search
  (`_deep.py:1535-1560`). Native does not invent a count.
- **Otherwise,** no entry. A sub-call whose pages have `partial` false adds no
  partial reason.
- **Enforce mode adds no entry** (architect delta check). In enforce mode,
  9.2.0 omits any configuration record that references a hidden entity
  (`tools/smart_search/_deep.py:44-65`, applied at `:376`, before the reply is
  paged and counted at `:378`). It reports no count and does not set
  `partial`. Native adds no partial reason for it either. The fixed
  visibility note covers it (§3.7).
  *Rev 5:* no visibility note is built (§3.7). W4 disclosure 1 and the W4
  notes on enforce-mode pages are in §10.7.

A failed page gives only the `source_unavailable` entry above. It is not also
`not_searched`.

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

- **`exact_match` true** (the default; *rev 5 correction:* 9.2.0 normalizes
  with `.lower().strip()`, `utils/fuzzy_search.py:194`, not casefold): the
  casefolded query must be a
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
- *Rev 5 (G-c):* the enumeration match types are `area_match`,
  `domain_listing` and `state_listing`. An empty query takes
  `domain_listing`. Native has no fuzzy-to-substring fallback.
- Token-AND is dropped. The architect withdrew revision 1's O5 recommendation.
- **Visibility filter.** *Rev 5: superseded, not ported (§3.7).* Entities in
  the ha-mcp visibility hidden set are
  excluded before scoring, counting and paging (§3.7). That is separate from
  `include_hidden` and its score penalty.

### 3.3 Area filter

The 9.2.0 ladder is kept (`tools/smart_search/_entities.py:686-800`):

1. An exact area match on id, name or alias.
2. Otherwise an exact floor match, which expands to the floor's areas.
3. Otherwise a fuzzy floor match. More than one is ambiguous, and gives a
   warning and an empty result.
4. A single fuzzy floor wins only if it scores above the best fuzzy area.
5. Otherwise a fuzzy area match: whole-string ratio over id, name and
   aliases, at 80 or above.
   *Rev 5 (G-j):* it is 9.2.0's `partial_ratio` (`_entities.py:856-860`,
   native `search/entities.py:406-408`), not a whole-string ratio. Area plus
   a query is always fuzzy at 80. *G-i:* area mode lists a registry entity
   with no state as `unknown` (`search/entities.py:573`).

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
  in §3.2. *Rev 5 (S1 = b+, §10.4):* native does not score automation,
  script or scene bodies; their body match is the route's own. G-b, the
  chunked body-read approximation, is superseded.
- **Match flags:** `match_in_name`, `match_in_config` and
  `match_in_references`, as 9.2.0 builds them (`_deep.py:593-594`,
  `:731-732`, `:878-883`; `_scenes.py:442-443`; `_flow_helpers.py:205-208`).
- **References:** for an `entity_id`-shaped query with automations, scripts
  or scenes requested, graph hit records have score `_GRAPH_HIT_SCORE` (100,
  `_graph.py:371`) and
  `match_in_references` true (`_graph.py:393-421`). A hit merges into an
  existing record by setting the flag (`_graph.py:431-481`). A referencing
  automation is returned even when its configuration could not be read, as
  the public `query` description promises.
- **`config_time_budget`:** kept. With null, the 9.2.0 defaults apply: 30 s
  for automations, 20 s for scripts, 20 s for scenes, and a 5 s per-item
  request timeout (`config_settings.py:167-216`). Items skipped by the budget
  are counted (§6.1). *Rev 5:* validated but not forwarded; each sub-call
  scan has its own 60 s deadline, and `time_budget` is not built (§10.9).
- *Rev 5:* Engineering reads no automation, script or scene body (S1 = b+,
  §10.4). The next item still holds for the storage-helper lists.
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

### 3.7 Visibility filter (superseded 2026-11-25; not ported, HAMCP-499)

**Superseded.** Josh decided on 2026-11-25, as relayed by the architect, in
the ADR-022 way: native search ships in 2.10.0 without ha-mcp's entity
visibility filter.

- **Why.** The filter, its enforce mode and Assist exposure are all off in
  this house (Josh's screenshots, 00:15 CST 2026-11-25, as relayed).
- **Consequence.** In a house with the filter on, today's delegated
  `ha_search` hides entities, and native search would not.
- **Later.** HAMCP-499 follows ha-mcp's own approach.
- **Dropped.** W5, the visibility read, and this section's build gate.
- **No oracle.** The §7 no-oracle point holds trivially: with no filter
  there is no visibility signal.
- **Not built.** No visibility note and no fail-open warning (§10.10).
- **Sub-calls.** The delegated sub-calls still run inside ha-mcp, so
  ha-mcp applies its own filter to their pages where it is on. How native
  classifies those pages is in §10.7.

#### Superseded text (revision 4)

ha-mcp 9.2.0 has an entity visibility filter. It is set in ha-mcp's own
configuration, not by a call argument. Native keeps it, and its enforce mode,
at 9.2.0 parity, as the architect relayed (§9).

**9.2.0 behavior** (paths under `src/ha_mcp/` at `51e3aac7`):

- **Configuration.** `entity_visibility.json` in ha-mcp's data directory
  (`visibility/persistence.py:14`, loaded at `:25`). Its fields
  (`visibility/model.py:60-93`) include `enabled` and `enforce`, and the
  category, area, label, allow, deny and Assist-exposure dimensions. The data
  directory is ha-mcp's own (`utils/data_paths.py:23`).
- **Hidden set.** `load_hidden_set` (`visibility/resolver.py:840`) resolves the
  configuration into a set of entity ids. For search it fails open: when the
  configuration cannot be loaded, the set is empty and the warning "Entity
  visibility config could not be loaded; the filter is disabled." is returned
  (`:866-880`). Its strict mode, which fails closed, is used only by the
  enforce middleware.
- **Entity search.** The legacy path hard-excludes the hidden set, whatever
  `include_hidden` says (`tools/search/entities.py:460-461`). `include_hidden`
  is a separate control over registry-hidden entities (`:462-464`).
- **Group members.** The legacy path omits members in the hidden set from
  `member_entity_ids`, and with `include_hidden` false also registry-hidden
  members (the set is built at `entities.py:580-582`). The smart-search
  paths keep `is_group` and withhold the member list of a group that has a
  hidden member (`tools/smart_search/_entities.py:30-52`).
- **Enforce scrub.** With enforce mode on, configuration records whose
  serialized form references a hidden entity are omitted before paging
  (`_scrub_results_for_enforce`, `tools/smart_search/_deep.py:44-65`, applied
  at `:376`; `scrub_records`, `visibility/enforcement.py:981`). The record
  still holds its body at that point, so the body is scanned too. No count is
  reported and `partial` is not set.
- **Enforce gates.** With enforce mode on, ha-mcp's middleware also gates every
  tool call, `ha_search` included (`visibility/enforcement.py:1-40`). A call
  whose arguments name a hidden entity id exactly is refused as not found. A
  call whose arguments embed one is refused with a generic error
  (`:735-757`). A reply that would name a hidden id is refused, and the error
  names no id (`:784-815`).

**Native, at parity:**

- **Entities.** Native excludes the hidden set from the entity bucket before
  scoring, counting and paging.
- **Members.** Native applies the member rule of the 9.2.0 path that the call's
  mode follows. The build's golden fixture pins which rule applies to each
  mode.
- **Configuration.** With enforce mode on, native omits each automation,
  script, scene and storage-helper record whose serialized form, body
  included, references a hidden entity, by the 9.2.0 matcher. A record whose
  body was not read is checked on what native holds for it, as in 9.2.0.
  The sub-calls follow §2.6.
- **Gates.** With enforce mode on, native applies the same two argument gates
  before any read, and the same reply check after the projection. A refused
  call returns a reviewed error that names no hidden id. These gates were not
  in Cursor's round-2 text. They are part of enforce mode for this tool in
  9.2.0, so parity includes them.
- **No visibility partial reason** (architect delta check). A visibility
  omission never gives a count, a partial reason or a `partial` flip. That
  covers an excluded entity, a reduced or withheld member list and an
  enforce-scrubbed record, in a native bucket or in a sub-call (§2.6). The
  entity exclusion and the enforce scrub run before counting and paging, as
  in 9.2.0 (`tools/search/entities.py:460-461`; the scrub at `_deep.py:376`
  runs before the reply is paged and counted at `:378`). So the totals and
  the paging keys count only records the filter lets through. Every other
  partial reason is unchanged.
- **The fixed visibility note.** When the filter is on and its resolved hidden
  set is non-empty, every reply carries one fixed note in `engineering_notes`
  (§5.1), named `visibility_filter_active` in the build's closed note set. The
  note:
  - is the same on every reply, whatever the query, arguments, search types
    or results;
  - carries no count and no id;
  - does not by itself set `partial`;
  - is always retained, whatever `fields` asks for (§5.1).

  Its text is a reviewed fixed text, in this form: "The ha-mcp entity
  visibility filter is active: entities in its hidden set are not returned,
  and in enforce mode neither are configuration records that reference
  them." When the filter is off, or its hidden set is empty, there is no note.
  When ha-mcp's own load failed open, there is no filter and no note, only the
  reviewed fail-open warning (§6.4).
- **No id.** The reply never lists a hidden entity id, in records, `applied`,
  warnings, notes or partial reasons. A gate refusal is an error, not a
  partial reply.

**Source of the visibility state (build gate).**

- The state is local to ha-mcp. Engineering reaches ha-mcp only as an MCP
  upstream (`configuration.py:51`). I found no 9.2.0 tool that returns the
  visibility configuration, the hidden set or the enforce flag. The readers
  outside `visibility/` are `settings_ui/__init__.py`,
  `tools/bulk_selector.py`, `tools/smart_search/_overview.py`,
  `tools/search/modes.py`, `tools/search/overview.py`,
  `tools/search/entities.py` and `tools/smart_search/_entities.py`. The
  `ha_get_state` omission warning lists omitted paths, never ids
  (`visibility/enforcement.py:130`).
- So the build must name a reviewed read that gives native the same hidden
  set and the same enforce flag. That read is new and needs Josh's named OK in
  the build task. A parity fixture pins the resolved set for each
  configuration dimension.
- If no such read can be found, the build stops and reports. It does not ship
  native search without the filter.
- Native never treats an unknown filter state as off. If the source reports
  that ha-mcp's own load failed open, native mirrors it: no filter, and a
  reviewed warning from the closed set (§6.4). If the source cannot be read,
  the read fails (§6.2).

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
| `search_types` | Configuration types `automation`, `script`, `scene`, `helper`, `dashboard`. The default is the first four. Any explicit value skips entities. The public description omits that | The 9.2.0 meaning is kept. The description gains the wire sentence: "Explicitly providing this selects configuration-only search and skips entities. Omit it for entity discovery." With a query, the R-A warning explains the empty bucket (*rev 5 correction:* R-A fires for any explicit `search_types`, with or without a query, `search/engine.py:347-348`, §10.12). `dashboard` routes per §2.4 | Redefined (description only), decided as relayed: reading (i), confirmed as relayed (J3) |
| `domain_filter` | Entity search only; does not affect configuration search | The same | Kept |
| `area_filter` | The ladder in §3.3, with floor expansion | The same, plus the effective-area rule (§2.3) | Kept |
| `state_filter` | Case-insensitive; standalone enumerates; page-only in the two modes of §3.4 | The same (§3.4) | Kept |
| `exact_match` | true: substring. false: fuzzy | The same, with the scorer ported (§3.2) | Kept |
| `limit` | Default 10, minimum 1, no maximum, per surface | The same, with no cap. A page the response budget ends early is `paged` with the next offset (§6.3) | Kept, decided as relayed (J1, no caps) |
| `offset` | Minimum 0 | The same | Kept |
| `group_by_domain` | Adds the `by_domain` map of page records. Withheld today | The same. `by_domain` stays withheld until HAMCP-447 releases it, and is counted in `fields_withheld` | Kept |
| `per_domain_limit` | Caps `by_domain` lists only | The same, so no visible effect while `by_domain` is withheld | Kept |
| `include_hidden` | Default true, with a score penalty; false excludes hidden entities | The same. It is separate from the visibility filter (§3.7) | Kept |
| `include_config` | Default false. With true, bodies are withheld by Row 25 | Bodies are still withheld (HAMCP-454). The reply is partial `filtered` with code `config_bodies_withheld` | Kept (named, not silent) |
| `result_fields` | 13 allowed keys: `entity_id`, `friendly_name`, `domain`, `state`, `score`, `match_type`, `name`, `area`, `floor`, `labels`, `aliases`, `is_group`, `member_entity_ids`. Unknown or empty is rejected (`tools/search/entities.py:42-62`, `:139-160`) | The same set, default and refusal (S3) | Kept |
| `fields` | Projects top-level keys; pagination and diagnostic keys always retained | The same, mapped exactly (S4). The added keys `applied` and `partial_reasons`, and `engineering_notes`, are diagnostic keys and are always retained (r2-M2, §5.1). A withheld key that is requested is counted in `fields_withheld`, as today | Kept |
| `config_time_budget` | 0.001-300 or null; per-type configuration budget | Rev 5: validated but not forwarded; each sub-call scan has its own 60 s deadline (§10.9). Revision 4: the same (§3.5) | Kept as an argument; its effect changed (§10.9) |
| Visibility filter (ha-mcp configuration, not an argument) | Hard-excludes the hidden set from entities and members; in enforce mode, scrubs configuration records and gates arguments and replies (§3.7) | Superseded (rev 5): not ported (§3.7, HAMCP-499). Revision 4: The same, from a reviewed source of the visibility state (§3.7 build gate). An active filter gives the fixed `visibility_filter_active` note; an omission gives no count and no partial reason | Superseded (§3.7) |

*Rev 5 (as built, §10.8):* native search has its own schema copy, which
differs from `PUBLIC_SCHEMAS["ha_search"]` only by the `search_types`
description (J3), and its own pinned fingerprint,
`NATIVE_SEARCH_SCHEMA_FINGERPRINT` (`tools/search.py:56`, checked at
`:224`). `SEARCH_PUBLIC_SCHEMA_FINGERPRINT` is unchanged, because it binds
the internal route. This replaces the revision 4 paragraph below.

Revision 4: the schema fingerprint (`SEARCH_PUBLIC_SCHEMA_FINGERPRINT`,
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
*Rev 5:* the native reply builds three counts (`ENGINEERING_COUNTS`,
`search/reply.py:79`: `fields_withheld`, `fields_dropped_by_policy` and
`upstream_warnings_replaced`) and no `engineering_notes` (§10.10).

Added keys (additive):

- `applied`, which records what the call actually did:
  - the effective `search_types`;
  - `domain_filter`;
  - whether `area_filter` and `state_filter` were set, as booleans only,
    because the echoes stay withheld (HAMCP-447);
  - `exact_match`, `include_hidden`, `limit`, `offset`;
  - whether entity search and configuration search ran.
- `partial_reasons`: a list of `{reason, search_type, count, code}`. `count`
  is an integer, or null when the count is unknown (§2.6). Native never
  invents a count.

`applied` and `partial_reasons` are diagnostic keys. Like `partial` and the
paging keys, they are always retained, whatever `fields` asks for (r2-M2).
`engineering_notes` is retained in the same way, so the fixed visibility note
(§3.7) is never projected away. *Rev 5:* no note is built, and
`ALWAYS_KEPT` (`search/reply.py:88-97`) has no `engineering_notes`.

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
  - `time_budget` (*rev 5:* not built, §10.10);
  - `read_failed`;
  - `not_readable` (a YAML-defined item the per-item read cannot fetch;
    *rev 5:* not built, S1 = b+);
  - `upstream_partial` (a page of a delegated sub-call had its own
    `partial` true, for its `search_type`; count null, §2.6);
  - `graph_unavailable` (an `entity_id`-shaped query whose reference graph
    read failed).
  - *rev 5, F1 (§10.6):* `body_numeric_query`, `body_scrub_degraded` and
    `body_scrub_unverified`, each for one body type, with count null.

  These are the 9.2.0 per-type partial causes (`_deep.py:1234-1356`,
  `:1356-1420`, `:1541-1545`), named instead of summarized.
- `source_unavailable`: a source that no requested filter depends on could
  not be read, and the affected fields are null (which source, count). That
  includes the dashboard and helper sub-calls (§2.6: codes
  `dashboard_subcall_incomplete`, `helper_subcall_incomplete` and
  `subcall_not_admitted`), and malformed state rows (code
  `malformed_state_row`, count). A malformed row is never skipped silently. A
  failed sub-call page gives only `source_unavailable`, not `not_searched`.
  *Rev 5:* the closed set as built is in §10.10. It adds the automation,
  script and scene sub-call codes, the four registry codes and
  `entity_aliases`, and names the `paged`, `truncated` and `filtered`
  codes. A failed `get_entries` chunk gives a
  partial reply with `source_unavailable` `entity_aliases`, in both
  `exact_match` modes, never a failed read (G-h).

Visibility is not a partial reason (architect delta check, §3.7). *Rev 5:*
native has no visibility filter (§3.7). The closed
set has no `hidden` reason. A visibility omission gives no count and no
`partial` flip, and an active filter is disclosed only by the fixed note. The
F3 qualifier is not a reason either. The caller's `include_hidden` false is
scope, not a reason.

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
- an inventory bound is exceeded (§6.3);
- *(rev 5: dropped; no visibility source is read, §3.7)* the visibility
  state cannot be read from its reviewed source (§3.7), or an enforce-mode
  gate refuses the call (§3.7).

*Rev 5 (G-h):* an alias read (`get_entries` chunk) failure is never a failed
read; it is partial (§6.1).

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
  warning, not a partial reason. It applies only to that intent-gate case
  (r2-M1). A call with no query that uses `domain_filter`, `area_filter` or
  `state_filter` alone, or together, is enumeration: configuration search
  does not run, and the reply carries neither the R-C text nor the R-A text.

The closed warning set also has reviewed texts for:

- an ambiguous fuzzy floor;
- the `state_filter` page-only note (§3.4);
- the composed-`name` note (§5.2);
- *(rev 5: not built, §3.7)* ha-mcp's own visibility filter failed open, so
  no filter applied (§3.7). The warnings as built are in §10.10.

## 7. Disclosure (D1)

Drawn from the Row 25 admitted set (`upstream_reads_9_2_keys.py:313-327`).

| Already returned today | Withheld today, still withheld | Withheld today, proposed for release |
| --- | --- | --- |
| Entity identity, friendly name, domain, state, score and match type; the HAMCP-441 keys `name`, `area`, `floor`, `labels`, `aliases`, `is_group` and `member_entity_ids`; configuration identity (`entity_id`, `friendly_name`, `script_id`, `scene_id`, `helper_type`, `name`); the match flags; all buckets and paging keys; the `query` and `domain_filter` echoes | Configuration bodies (`config`, HAMCP-454); flow-helper options bodies (HAMCP-454); dashboard `url_path` and `title`, `entry_id`, `options_withheld`, `by_domain`, the area and state echoes (HAMCP-447); `area_names`, `state_filter_note`, `message`, `partial_reason` | None in this design. Releasing the HAMCP-447 keys is that row's decision |

*Rev 5:* Row 25 is unchanged. Dashboard `url_path` and `title` and helper
`entry_id` are still withheld and counted, so a flow-helper row carries
`helper_type` and `name` with no id. HAMCP-447 owns releasing them (§10.8).

Notes for the build's security section:

- `match_in_config` and a body score show whether a query string occurs in a
  configuration body. The bits are the same as today's delegated reply, but
  native computes them over bodies Engineering itself reads. The security
  section records that this is a query oracle over bodies, already exposed
  today.
  *Rev 5:* superseded by S1 = b+ (§10.4). Engineering reads no automation,
  script or scene body. The bits come from the delegated route, which
  matches bodies inside ha-mcp, and F1 withholds them where the scrub is not
  shown clean or the query is numeric-like (§10.6).
- The new reads in §2.2 add sources, not record fields. *Rev 5:* the reads
  as built are in §10.4.
- **Visibility filter, kept (r2-S3).** *Superseded 2026-11-25 (§3.7, §10.2):
  native has no visibility filter; HAMCP-499 follows.* The ha-mcp visibility
  filter and its
  enforce mode keep their 9.2.0 effect (§3.7). Records the filter withholds
  today stay withheld, and no hidden entity id is returned. So the table above
  stands as it is.
- **No visibility oracle** (architect delta check). *Superseded 2026-11-25
  (§3.7): it holds trivially, since with no filter there is no visibility
  signal.* This keeps 9.2.0's
  no-oracle property for visibility omissions. 9.2.0 reports no count and sets
  no `partial` flag when its filter excludes an entity or its enforce scrub
  omits a record (`tools/search/entities.py:460-461`,
  `tools/smart_search/_deep.py:44-65`). Native reports none either (§3.7).
  - The only visibility signal native adds is the fixed note. It depends on
    the filter configuration alone. It tells a caller that some entities are
    hidden, never which, how many, or whether any of them matched a query.
  - Native keeps the residual signals of 9.2.0 itself at parity and does not
    widen them. Enforce mode conceals a hidden entity's existence only on a
    best-effort basis (`visibility/enforcement.py:11-16`). The smart-search
    member rule keeps `is_group` while withholding the member ids of a group
    with a hidden member (`tools/smart_search/_entities.py:30-52`).
  - The build's security section records both.
- Every output string passes `sanitize_untrusted_data`
  (`sanitization.py:1069`) with identity-only markers (`:1075`).
  - A redaction count above zero sets `filtered`.
  - A `failed_closed` result fails the read. It is never returned as a
    redacted success or as the original string.
- *Rev 5:* the as-built security facts are in §10: G1 and G2 (§10.3), the
  F1 trigger, probe and residuals (§10.6), the reader checks (§10.7) and
  HAMCP-233 (§10.11).
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
     `search_types` description sentence. *Rev 5:* the native copy has its
     own pinned fingerprint, and the internal route's is unchanged (§10.8).
3. **Domain-only and `exact_match` true substring:** equal to the
   `search_entities` fixture for the same domain and text. `search_entities`
   is the oracle only for these two.
   *Rev 5 (G-d):* the comparison is of match sets, not order.
4. **The `exact_match` false oracle:** a golden fixture generated by running
   the pinned 9.2.0 scorer (`utils/fuzzy_search.py` and
   `tools/smart_search/_scoring.py` at `51e3aac7`) offline over a fixed
   corpus. It has provenance naming the commit and the command. Native scores,
   match types and order must equal it, including the typo, alias and
   area-plus-query (threshold 80) cases.
5. **Kept capabilities (C2):**
   - an exact `entity_id` referenced by an automation returns that
     automation with `match_in_references` true, with bodies absent. With
     the visibility filter off and every helper sub-call page `partial`
     false, the reply has `partial` false and no reason. With one page
     `partial` true, it has exactly one reason, `not_searched`
     `upstream_partial` for `helper`, count null, and no other (r2-S2);
   - the same with that automation's configuration unreadable returns the
     automation, plus `not_searched` `read_failed` for its body *(rev 5:
     superseded by S1 = b+; Engineering reads no automation body, and a
     sub-call that does not finish is `automation_subcall_incomplete`,
     §10.4)*;
   - `result_fields` with `is_group` and `member_entity_ids` returns those
     keys;
   - a floor name, an area alias and a close-spelling floor each return that
     area's entities;
   - an ambiguous fuzzy floor gives the warning and no entities;
   - `state_filter` `"ON"` equals `"on"`;
   - scenes and each of the six storage helper types match by name and by
     body *(rev 5: scenes match by body through the scene sub-call, S1 =
     b+)*;
   - flow helpers match through the paged helper sub-call (§2.5, §2.6): its
     storage-type rows are dropped on each page, its flow-helper rows merge
     by score, each helper is counted once in `config_total_matches`, and a
     scan that reaches `has_more` false is complete with no reason;
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
   - a dispatcher spy shows zero dashboard sub-calls when `search_types`
     lacks `dashboard`. When it has it, the spy shows only pages with the
     fixed arguments and successive offsets 0, P, 2P and so on, ending at the
     first §2.6 stop condition;
   - the same spy shows zero helper sub-calls when helpers are not searched.
     When they are, it shows only pages with the §2.5 fixed arguments and
     successive offsets;
   - no other `search_types` value or argument is forwarded;
   - a failed dashboard page gives `source_unavailable`
     `dashboard_subcall_incomplete` while the other buckets are served;
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
    - `ha_search` is absent from `DEVICE_DEPENDENT_DELEGATED_TOOLS` *(rev 5,
      G2: the internal route stays in it, fail-closed, and each page
      acquires the route's requirement,
      `tests/test_hamcp501_native_search_cutover.py:682`; §10.3)*;
    - its requirement tuple equals the declared native profiles *(rev 5:
      `CORE_REQUIREMENTS`, `search/core_contracts.py:64`, pinned by a
      test)*;
    - the provider's command allowlist equals §2.2;
    - a dispatcher spy shows zero ha-mcp calls for an entity-only search,
      including when the device registry read fails.
13. **Negative reachability:**
    - no options or config flow call from the native provider (the helper
      sub-call's flows are ha-mcp's, §2.5);
    - no service call;
    - no write;
    - no `integration/descriptions` read;
    - no fallback to the delegated route for a native bucket;
    - Engineering provider attribution in the audit record.
14. **Sub-call paging and reasons (r2-S1, r2-S2):**
    - a helper page fixture whose projected body exceeds 52,000 bytes: the
      gateway fails that page, the native reply stays within the gateway
      limit, reports `source_unavailable` `helper_subcall_incomplete` with
      count null, keeps the flow-helper rows of the earlier pages, and does
      not claim a full flow-helper count;
    - a scan stopped by the 20,000-row bound or by the scan deadline gives the
      same code. A failed dashboard page gives
      `dashboard_subcall_incomplete`. A page whose `config_total_matches`
      differs from the first page's counts as failed;
    - a pinned page-size test: a page of P projected rows of the fixture's
      largest row shape stays under the gateway check *(rev 5: the §10.5
      ladder replaces the single size)*;
    - a helper sub-call whose pages have `partial` false: the reply has no
      partial reason from it, with enforce mode off *(rev 5: the
      enforce-on case is dropped, HAMCP-499)*;
    - a page fixture with `helper_unsearched` set (custom helper entries not
      searched): the reply has `not_searched` `upstream_partial` for
      `helper` with count null, and no invented count;
    - *(rev 5: dropped, HAMCP-499)* enforce mode on, with a non-empty hidden
      set: a sub-call that sent a page adds no partial reason, and the reply
      carries the fixed visibility note;
    - a failed page adds `source_unavailable` only, not `not_searched`;
    - ha-mcp not admitted: no page is sent, and the reply has
      `source_unavailable` `subcall_not_admitted` for each sub-call it
      needed.
15. **Visibility filter (r2-S3, architect delta check): dropped in
    revision 5.** Native has no visibility filter (§3.7, §10.2), so these
    cases do not apply. HAMCP-499 owns them with the filter. The
    revision 4 cases, superseded:
    - one entity in the hidden set and one automation whose body references
      it. With the filter on and enforce mode on, neither appears, the reply
      carries the fixed visibility note, no partial reason or count refers to
      visibility, `partial` is false when nothing else applies, and the hidden
      id is absent from the whole reply. With the filter off, both follow the
      ordinary match rules, and there is no note;
    - the filter on and enforce mode off: the entity is excluded, the
      automation is returned, and the reply carries the note and no partial
      reason;
    - **no oracle:** with the same query and the same visible entities, a
      fixture whose hidden entity matches the query and one whose hidden
      entity does not give identical replies, totals and paging keys
      included. In enforce mode, a corpus with one more automation that
      references a hidden entity gives the same reply as the corpus without
      it;
    - the note is the same on a reply with results and on one with none, for
      each search type, and with `fields: ["entities"]`. With the filter on
      and an empty hidden set, there is no note;
    - a group with a hidden member follows the pinned member rule for its
      mode, with no count and no partial reason;
    - `include_hidden` true does not bring back an entity in the visibility
      hidden set;
    - enforce mode on: a query that names a hidden id exactly is refused as
      not found, and one that embeds a hidden id is refused with the generic
      error. Neither error names the id, and no read or sub-call is sent;
    - the visibility source unreadable: the read fails. The source reporting
      ha-mcp's own fail-open state: no filter, no note, and the reviewed
      warning.
16. **Enumeration and `fields` (r2-M1, r2-M2):**
    - `domain_filter` set, with no query and no `search_types`: entities are
      returned, there is no R-C or R-A text, and `partial` is false when
      nothing else applies. The same holds for `area_filter` alone and
      `state_filter` alone;
    - `fields: ["entities"]` on a paged reply still returns `partial`,
      `partial_reasons` and `applied`.

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
11. A query with no matches gives empty buckets and `applied`. `partial` is
    false, or it carries only `not_searched` `upstream_partial` when a helper
    page was itself partial (r2-S2). No other reason is accepted. The fixed
    visibility note is on every step's reply or on none (§3.7). *(rev 5:
    dropped; no note is built, §3.7)*
12. The audit log records native provider attribution, plus the delegated
    sub-call pages only where §2.4, §2.5 and §2.6 call for them: the
    dashboard pages for step 10, and the helper pages for each step that
    searches helpers.
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
- **J2, keep flow-helper options search** (relayed in an architect session
  message, 2026-11-24): "Keep it. Keep in mind, the idea isn't to lose
  features. Also keep in mind governance proportionate to risk." Native keeps
  options-body matching through the helper sub-call until HAMCP-484 replaces
  it (§2.5). The architect relayed this as standing direction for the
  remaining work: keep every capability by default, and size governance to
  risk.
- **Change later:** "Agreed we can change later if it shows warranted".
- **Visibility filter kept** (relayed in an architect session message,
  2026-11-24, for Cursor's r2-S3): "KEEP both, the visibility hard-exclude and
  the enforce-mode config scrub, at 9.2.0 parity." The architect gave this as
  an application of Josh's standing direction under J2, "the idea isn't to
  lose features", in which every capability is kept unless Josh retires it by
  name. So it is not a new decision for Josh. §3.7 has the behavior, §4 the
  row and §7 the disclosure, which stands as it is.
  *Superseded 2026-11-25 (§3.7, §10.2).*

### Decided for the build, as relayed by the architect (2026-11-25 and 2026-11-26)

Josh's decisions, as relayed by the architect:

- **Visibility supersession** (2026-11-25): native ships without the ha-mcp
  entity visibility filter; HAMCP-499 follows (§3.7, §10.2).
- **S1 = b+** (2026-11-26): native reads no automation, script or scene
  body; the delegated sub-calls match them (§10.4).
- **"Approve A+" and A'+** (both 2026-11-26): the F1 trigger and the scrub
  probe (§10.6).

The architect's decisions:

- G1 and G2, the internal route and its requirement (§10.3);
- the page-size ladder (§10.5);
- J2' option A, and the `get_entries` alias read (2026-11-25, §10.4);
- the reader rules D1 to D5 (§10.7);
- the W6b split, with the composed name served by #519 (§10.1);
- the CI-lane acceptance for W6, and R2-F1 as W6's last review round
  (§10.1).

### Design choices from the reviews (not owner decisions)

- O3: `fields` maps exactly (S4).
- S3: the 9.2.0 `result_fields` set.
- S5: the no-criteria refusal.
- B1: the inventory cap at `MAX_CORE_PROBE_ITEMS`. With J1, this supersedes
  N3 and N4.
- O6: parity. No disabled entities without a state (§2.1).
- r2-S1: the sub-calls are paged on the existing route (§2.6). Two
  definitions are the author's, not Cursor's: a page whose
  `config_total_matches` differs from the first page's counts as failed, and
  the scan has a whole-scan deadline that the build names. The architect's
  delta check accepted both.
- r2-S2: sub-call reasons come from the projected `partial` only (§2.6).
  Enforce mode adds no reason (architect delta check). The F3 qualifier is
  not a partial reason.
- r2-S3: the enforce-mode argument gates and the reply check are included as
  part of 9.2.0 enforce parity (§3.7). They go beyond Cursor's round-2 text,
  and the architect's delta check accepted them. Cursor's `hidden` count is
  replaced by the fixed visibility note (next item).
  *Superseded 2026-11-25 (§3.7).*
- **Architect delta check of revision 3** (relayed in an architect session
  message, 2026-11-24; an architect direction, not an owner decision). The
  author's additions 1 to 3 were accepted: a moved total is a failed page,
  the whole-scan deadline, and the enforce gates. Addition 4, the `hidden`
  count, was changed: "when the filter is on and the hidden set is
  non-empty, every reply carries the same fixed note, e.g.
  `visibility_filter_active`. It is not query-dependent, has no count, and
  doesn't by itself set partial. Visibility omissions never produce a
  per-query count or partial flip. Every other partial reason is
  unchanged."
  - Applied in §2.6, §3.7, §4, §5.1, §6.1, §7 and §8.
  - Under the same rule, the sub-call entry `upstream_visibility_enforce` is
    removed, because it set `partial` from visibility.
- r2-M1: the R-C text only in the intent-gate case (§6.4).
- r2-M2: `applied` and `partial_reasons` always retained (§5.1).

### Open for Josh

None.

No row in §4 or §5 is retired.

Rev 5: the §4 visibility row is superseded (§3.7). It was ha-mcp
configuration, not an argument, so no argument is retired.

## 10. Implementation record (2.10.0)

Revision 5 records what HAMCP-501 built on `release/2.10.0`, from the merged
source at `c2b68911`, the merged pull requests and their reviews. Source paths
are under `hass_mcp_engineering_beta/ha_mcp_engineering/` unless a path says
otherwise, and line numbers are at `c2b68911`. Where this section and an
earlier section differ, this section describes the build. The labels G-b to
G-j, R-A and the plan corrections in §10.12 are the architect's W7 review
labels.

### 10.1 Pull requests and reviews

| Step | Pull request | Merge commit | Merged (Central) |
| --- | --- | --- | --- |
| W1 | #506 | `6a72f12f` | 2026-11-25 06:21 CST |
| W2 | #507 | `b7f7e0df` | 2026-11-25 06:42 CST |
| W3 | #513 | `3f9f6177` | 2026-11-25 14:19 CST |
| W4 | #516 | `cc1c5bb2` | 2026-11-25 20:39 CST |
| W5 | none | dropped (§10.2) | not applicable |
| W6 | #517 | `c2b68911` (head `9e68614d`) | 2026-11-26 14:36 CST |
| W7 | this revision | open | not merged |
| W6b | #519 | open | not merged |

- W6 (#517) is the cutover to native `ha_search`. Its reviews:
  - Codex r1: F1 High (§10.6) and F2 Medium, both corrected in one batch.
  - Cursor delta r2 on that batch: one new finding, R2-F1 High. Script and
    scene body matching was untested. It was fixed with tests and mutants
    only (#517 Disclosure 19). The architect named it W6's last review round
    and verified the fix.
- The architect accepted the CI lane as W6's real Home Assistant evidence.
  Real Home Assistant stays CI-only.
- W6b (#519) serves the composed device and entity name (X12, §10.13), split
  from W6 by the architect. Until it merges, the native reply warns that the
  composed name is not served (§10.10).
- The "Implemented in 2.10.0 (Engineering 2.4.2-beta.14)" mark is added only
  after #519 merges.

### 10.2 Superseding decision: visibility (2026-11-25)

Josh decided on 2026-11-25, as relayed by the architect, that native search
ships without ha-mcp's entity visibility filter (§3.7). The filter, its
enforce mode and Assist exposure are all off in this house (Josh's
screenshots, 00:15 CST 2026-11-25, as relayed). In a house with the filter
on, today's delegated `ha_search` hides entities and native would not.
HAMCP-499 follows ha-mcp's approach later.

Consequences in this document:

- §3.7 keeps its revision 4 text under a "Superseded" heading.
- W5 and the §3.7 build gate are dropped.
- The §7 bullets "Visibility filter, kept (r2-S3)" and "No visibility oracle"
  carry the note. The no-oracle point holds trivially.
- §8.15 and the enforce-mode bullets of §8.14 are dropped, citing HAMCP-499.
- No visibility note and no fail-open warning are built (§10.10).

### 10.3 G1 and G2: the internal route and its requirement

The architect's G1 and G2 replace the §2.3 plan (#517 Disclosure 4).

G1, internal-only:

- `ha_core_readmission/routes.py:249-250` makes the delegated `ha_search`
  route internal-only whenever native `ha_search` is registered, which is
  every build.
- The route is admitted only for the fixed sub-call readers (§10.7). It is
  never listed, aliased (there is no `ha_mcp__ha_search`) or
  capability-recorded.
- It is still Row 25 on ha-mcp 9.2.0. There is no policy JSON change and no
  signing.
- The mechanism is `execute(..., search_page=...)` under the lease (D5,
  §10.7).
- Catalog: 114 = 88 + 26. `exact_matched` includes the internal route, and
  `dynamically_exposed` excludes it (codex-context reports 26/114).

G2, the route requirements:

- The internal route keeps `DELEGATED_CORE_REQUIREMENTS["ha_search"]` =
  `core.delegated_device_effective_area` (`routes.py:292`). It stays in
  `DEVICE_DEPENDENT_DELEGATED_TOOLS` (`routes.py:16`, entry `:22`),
  fail-closed.
- Native `STATIC_TOOL_CORE_REQUIREMENTS["ha_search"]` (`routes.py:333-336`)
  equals `CORE_REQUIREMENTS` (`search/core_contracts.py:64`):
  `core.basic_rest_read`, `core.basic_websocket_read`,
  `core.non_device_registry_read` and `core.direct_device_registry_read`. A
  test pins the equality.
- Each sub-call page acquires the route's requirement
  (`tests/test_hamcp501_native_search_cutover.py:682`).

### 10.4 S1 = b+ and the reads as built

S1 = b+ was Josh's decision on 2026-11-26, as relayed by the architect.

- Native search reads no automation, script or scene body.
- Those rows come from the fixed delegated sub-calls (`search/subcalls.py`,
  `ENTITY_TYPES` at `:56`). Their body match (`match_in_config`) is the
  route's own, inside ha-mcp, except where F1 withholds it (§10.6).
- The five sub-calls are automation, script, scene, dashboard and helper
  (`SEARCH_SUBCALL_BUCKETS`, `providers/upstream_read_gateway.py:888-899`).
  The helper sub-call carries flow helpers. Native search reads the six
  storage helper types itself and matches them by name and body, in memory.
- The injected Core read session serves the reference graph only. Nothing
  falls back to the delegated route for a native bucket (`search/configs.py`
  docstring).
- A script or scene id that the route leaves empty is filled from the entity
  id. For a scene, that goes through the registry storage key.
- An unusable entity registry fails scene and helper search.
- A helper list that cannot be read, its dropped rows and a row with an
  invalid field, including a row without a valid name, count as
  `read_failed` (`search/configs.py:291-296`).
- The AST test `test_no_configuration_body_read_is_reachable`
  (`tests/test_hamcp481_native_search_engine.py:695`) pins that no
  configuration body read is reachable.

The Core reads follow J2' option A (architect), whose closed table is
`search/core_contracts.py`:

- Only existing signed Core profiles are used. There is no profile, model or
  signed-source change.
- A read is "attested" when a profile pins its exact command and reply shape.
  Otherwise it uses `core.basic_rest_read` or `core.basic_websocket_read` for
  transport only.
- The architect added `config/entity_registry/get_entries` for entity aliases
  on 2026-11-25. On Core 2026.11.3 it has no `require_admin`, and the entity
  registry list carries no aliases.
- The six `input_*/list` commands are not admin-only.
- The states list is `rest_states_list` under `core.basic_rest_read`.
  `core.direct_entity_state_read` attests a single-entity read and is unused
  (`search/core_contracts.py:61-64`).
- Under S1 = b+, the automation, script and scene configuration reads are not
  in the table, and `core.automation_configuration_read` is not a
  requirement.

### 10.5 The page-size ladder (architect)

The ladder replaces the single page size P of §2.6 (#517 Disclosure 3).

- The sizes are `PAGE_SIZE_LADDER` = 150, 30, 6, 1 (`search/subcalls.py:65`),
  within a page budget of `PAGE_BUDGET_BYTES` = 37,000 (`:81`).
- A scan steps down only when the gateway reports `page_too_large`. The
  gateway sets it only when the reply-size bound alone refused the page: the
  encoded-size check or `dump_json_with_truncation`.
- `SearchSubcallPageTooLarge` is raised only for that, plus
  `response_too_large`. It is never inferred from `invalid_response`.
- Every other failure fails closed, with no retry.
- The gateway accepts a page whose offset is a multiple of its size and whose
  limit equals the size. It then checks the page arguments for exact equality
  (§10.7).
- A step-down re-reads only that span at the next size, then climbs back.
- A row too large even at size 1 gives `<type>_subcall_incomplete`.
- Scan limits:
  - 20,000 rows (`MAX_SCAN_ROWS` = `MAX_ITEMS`, `:45`);
  - 300 page calls (`MAX_PAGE_CALLS`, `:69`);
  - a 60 s deadline per scan (`SCAN_DEADLINE_SECONDS`, `:43`).

  Past any of these, the bucket is `<type>_subcall_incomplete`, for that
  bucket only.
- A dashboard scan completes early once it has enough rows.

The costs (#517 Disclosure 3):

- Each step-down adds one `response_too_large` to the health
  `failure_counts` and one failed provider result.
- The 2 MB transport bound gives no step-down.
- The full-page rule is tightened.

The sizing evidence (#517 Disclosure 3):

- The row sizes are 3,470 bytes for automation, 6,560 for script, 6,560 for
  scene, 90 for dashboard and 9,360 for helper.
- The budget fits 10, 5, 5, 150 and 3 such rows. So the largest fitting
  ladder size is 6, 1, 1, 150 and 1.
- Before the ladder, a scene page took 58,115 of 60,000 bytes at 150 rows,
  and 80-character names gave `scene_subcall_incomplete`.

### 10.6 F1: the A'+ trigger, the scrub probe and the three reason codes

F1 (Codex r1 on #517, High) found that the route's body match could confirm a
value that ha-mcp's scrub is meant to hide (#517 Disclosure 17). Josh's
decisions, as relayed by the architect, are "Approve A+" and A'+, both
2026-11-26. Their relation is not recorded here. The build follows A'+.

**Trigger.** `body_match_unsafe(query, exact_match)` (`search/subcalls.py:398`)
names a numeric-like query:

- with `exact_match` true, the lowercased query holds only `0-9 . + - e : t`
  and spaces (`_NUMERIC_CHARS`);
- with `exact_match` false, the same, or any query term holds a digit.

The `inf` and `nan` forms are narrowed out.

**Effect.** For each body type requested (automation, script, scene and
helper; `BODY_TYPES`, `:137`):

- a sub-call row keeps only a name rescore and its graph references, with
  `match_in_config` false (`search/configs.py:381`);
- a row with no name match is dropped;
- the bucket gets `not_searched` `body_numeric_query`, count null;
- no probe is read.

**Scrub probe.**

- When an automation, script or scene type is requested and the query is not
  numeric-like, native reads a one-row helper page before the automation,
  script and scene scans and again after them:
  `page_arguments("helper", query, exact_match, 0, PROBE_SIZE)`
  (`search/subcalls.py:380`; `PROBE_SIZE` 1 at `:140`;
  `PROBE_TIMEOUT_SECONDS` 15.0 at `:141`).
- The probe's rows are never used. Its warnings classify the scrub state as
  clean, degraded or unverified (`:130-132`), with the worst winning:
  degraded, then unverified, then clean.
- The state is degraded when the page carries one of ha-mcp's two
  scrub-degraded texts.
- It is unverified when there is no reader, the page failed, timed out or was
  invalid (`:387`), or the page carries a warning the build has not reviewed.
- A probe that is not clean applies the F1 effect to every body type in the
  call, with `body_scrub_degraded` or `body_scrub_unverified`.
- The classifier order is numeric, then degraded, then unverified
  (`search/configs.py:394-400`).
- The scrub state can change between a probe and a page. The two probes
  bracket the scans, and the race is disclosed (#517 Disclosure 17).
- No scrub-degraded text reaches the reply.

**Residuals (for 2.11).**

- Lost: a legitimate numeric body match, such as `10:00` or `75`.
- Narrowed: there is no arm for the `inf` and `nan` forms.
- Not covered:
  - non-scalar `!secret` values;
  - YAML-mode dashboards;
  - `bool` leaves.
- Dashboards and native storage helpers get no body code.

**Evidence.**

- `docs/evidence/hamcp501/f1/`: `generate_body_match_fixtures.py`,
  `body-match-golden.json` (228 rows), `provenance.json` and
  `body_match_mutation_check.py`.
- `tests/test_hamcp501_w6_f1_body_matching.py`: 40 tests.
- Mutation: 50 mutants, all killed, at `9e68614d`.
- R2-F1 added the script and scene cases and their mutants.

### 10.7 The sub-call reader

`gateway.search_subcall_reader()` returns a `SearchSubcallReader` whose
`.readers` is a read-only map (`providers/upstream_read_gateway.py:4462`).
W4 (#516) had dashboard and helper. W6 extended it to all five
`SEARCH_SUBCALL_BUCKETS` (`:888-899`).

Each page is checked:

- the page arguments must be exact, checked by `_search_page_arguments`
  (`:963`);
- D2: a redacted or truncated page fails;
- D1: withheld names are counted from the raw bucket;
- D3: `config_has_more` must equal `has_more`, and a page without it fails;
- W4-F1: with no parent request or authorizer, the reader refuses before
  dispatch;
- D5: the lease is the authorizer.

Rows are rendered (projected to Row 25) only after the D2 check passes.

The W4 defect fix (#517 Disclosure 1, `54e45720`):

- The reader validated pages against the raw upstream schema. Under the
  unified descriptor (`c1ca76bd`), every page was refused.
- It now uses `_public_read_schema`. Reverting the fix gives 17 failures and
  6 errors.
- The class sweep (`dda85df4`) found that `script_dependency`, `history` and
  `yaml_config` are not in the class.

W4 disclosure 1:

- On the component path in enforce mode, 9.2.0 scrubs after paging. A short
  page that is not the last fails closed (`invalid_response`), so the
  sub-call is incomplete.
- The legacy path scrubs before paging.
- Enforce mode is off in this house.

W4 notes:

- `ENTITY_VISIBILITY_ENFORCED`, or an `ENTITY_NOT_FOUND` that conceals, gives
  `upstream_error`, so the sub-call is incomplete.
- 9.2.0 omits `config_has_more` when its configs branch raised, so that page
  fails closed (D3).

The generic `_upstream_completeness` `ha_search` branch is unreachable
(#517 Disclosure 6).

### 10.8 Schema, fingerprint and Row 25

- Native `ha_search` has its own schema copy. Only the `search_types`
  description differs (J3), by the one appended sentence of §4.
- Its own fingerprint is `NATIVE_SEARCH_SCHEMA_FINGERPRINT` `47b15a3e…`
  (`tools/search.py:56`, checked at `:224`).
- `SEARCH_PUBLIC_SCHEMA_FINGERPRINT` (`providers/upstream_reads_9_2.py:98`,
  checked at `:186`) is unchanged, because it binds the internal route.
- §7 Row 25 is unchanged. Dashboard `url_path` and `title` and helper
  `entry_id` are still withheld and counted. So a flow-helper row carries
  `helper_type` and `name` with no id.
- HAMCP-447 owns releasing those fields.

### 10.9 Arguments and audit

- `config_time_budget` is validated but not forwarded to the pages. Each
  sub-call scan has its own 60 s deadline (§10.5).
- An invalid argument gives the native `SearchError` envelope, audited as
  failure / `validation_failure`.
- A partial reply is audited partial, as on the delegated route (#517
  Disclosure 12).
- Arbitrary delegated projection is unreachable.

### 10.10 The reply as built

The closed reason set (`search/reply.py:124-145`), in `REASON_ORDER`:

- `paged`: `response_budget`, `more_matches`;
- `truncated`: `record_strings`, `record_lists`, `query_echo`,
  `domain_filter_echo`;
- `filtered`: `sanitizer_redaction`, `config_bodies_withheld`;
- `not_searched`: `read_failed`, `upstream_partial`, `graph_unavailable`,
  `body_numeric_query`, `body_scrub_degraded`, `body_scrub_unverified`;
- `source_unavailable`:
  - `automation_subcall_incomplete`, `script_subcall_incomplete`,
    `scene_subcall_incomplete`, `dashboard_subcall_incomplete`,
    `helper_subcall_incomplete`;
  - `subcall_not_admitted`;
  - `malformed_state_row`;
  - `area_registry`, `floor_registry`, `label_registry`, `device_registry`;
  - `entity_aliases`.

Compared with §6.1:

- `time_budget` and `not_readable` are not built.
- The three body codes are added (§10.6).
- The automation, script and scene sub-call codes, the four registry codes
  and `entity_aliases` are added.
- A failed `get_entries` chunk gives a partial reply with `source_unavailable`
  `entity_aliases`, in both `exact_match` modes, never a failed read (G-h).

The other parts of the reply:

- **Warnings.** `WARNINGS` (`search/reply.py:119`) holds five closed texts:
  - entity search skipped;
  - configuration search skipped;
  - an ambiguous floor, so no area was selected;
  - `state_filter` applied to this page only;
  - `name` requested, but not composed (until W6b).

  `upstream_warnings_replaced` is 0.
- **Counts.** `ENGINEERING_COUNTS` (`search/reply.py:79`) holds three:
  `fields_withheld`, `fields_dropped_by_policy` and
  `upstream_warnings_replaced`.
- **No notes.** Native builds no `engineering_notes`, and `ALWAYS_KEPT`
  (`search/reply.py:88-97`) has none. There is no visibility note and no
  fail-open warning (§10.2).

### 10.11 HAMCP-233 and the probe inference

- The internal helper sub-call is still delegated `ha_search`. ha-mcp's
  `fetch_entry_options_with_status` still opens and aborts config-entry
  options flows, as today. That is unchanged, not new, and 2.11 follows up.
- **Inference, not tested:**
  - Each F1 probe (§10.6) is a one-row helper page, so it is a whole ha-mcp
    helper search. It therefore likely repeats those options-flow probes.
  - That is two such searches per call that searches automation, script or
    scene bodies, one before the scans and one after.
  - The W6 pull request did not state this.

### 10.12 Corrections to revision 4

- **States list (§2.2).** It is `rest_states_list` in `core.basic_rest_read`
  (`ha_core_readmission/profiles.py:159`, `:162`) and in
  `core.state_service_discovery` (`:185`, `:188`).
  `core.direct_entity_state_read` covers single-entity reads only and is
  unused (`search/core_contracts.py:61-64`).
- **Batch keys (§2.2).** `_BATCH_COMMAND_KEYS` is at
  `clients/websocket.py:32-38`. The cited `:24-28` is its comment.
- **Scorer (§3.2).** 9.2.0 normalizes with `.lower().strip()`
  (`utils/fuzzy_search.py:194`), not casefold.
- **Graph score (§3.5).** `_GRAPH_HIT_SCORE` is 100, at `_graph.py:371`.
- **G-b (§3.5).** The chunked body-read approximation is superseded by S1 = b+
  (§10.4).
- **G-c (§3.2).** The enumeration match types are `area_match`,
  `domain_listing` and `state_listing`. An empty query takes
  `domain_listing`. Native has no fuzzy-to-substring fallback.
- **G-d (§8.3).** The golden comparison is of match sets, not order
  (`tests/test_hamcp481_native_search_engine.py:783`).
- **G-h (§6.1, §6.2).** A failed `get_entries` chunk gives a partial
  `source_unavailable` `entity_aliases`, in both modes, never a failed read.
- **G-i (§3.3).** Area mode lists a registry entity with no state as
  `unknown` (`search/entities.py:573`).
- **G-j (§3.3).** The fuzzy area match is `partial_ratio`
  (`_entities.py:856-860`, native `search/entities.py:406-408`), not a
  whole-string ratio. Area plus a query is always fuzzy at 80.
- **R-A (§4, §6.4).** It fires for any explicit `search_types`, not only with
  a query (`search/engine.py:347-348`).
- **Plan corrections.** These are not in this document, so they are recorded
  here only:
  - `ha_core_readmission/websocket.py` does not exist;
  - the manifest path is
    `docs/evidence/hamcp315/manifest-compatibility.json`.

### 10.13 W6 parity with the delegated route

From the W6 pull request (#517), against 9.2.0's delegated `ha_search`.

Kept:

- The full 9.2.0 schema, plus one appended sentence: `per_domain_limit`,
  `group_by_domain`, `fields`, `result_fields`, `config_time_budget`, and
  `include_hidden` defaulting to true.
- X1 eligibility: domain, area or state intent skips configuration search
  unless `search_types` is explicit.
- X5: the area match is a union. X6: an unknown area gives success with 0.
- X10: the count is the sum, and the limit is per surface. X15: ties sort by
  `entity_id`.
- X3 and X4: the Row 25 projection for dashboard and helper rows.
- Automation, script and scene body matching (`match_in_config`) is the
  delegated route's own (S1 = b+), scrubbed upstream. The exceptions are the
  F1 trigger and an unproven scrub (see Changed).
- Audit status: a partial reply is audited partial, as on the delegated
  route.
- The `result_fields` enrichment: area, floor, labels, aliases, `is_group`
  and `member_entity_ids`.

Improved:

- X9: the warnings are closed Engineering texts, and
  `upstream_warnings_replaced` is 0.
- X1 now warns `config_skipped`.
- X11: `offset` and `limit` are always present.
- X7: completeness reflects the real `partial_reasons`. A partial or failed
  sub-call affects only its own bucket.
- The description is served in full, with no truncation.
- The combined `next_offset` is the minimum over the surfaces.
- `match_in_references` comes from Core `search/related`, for an
  `entity_id`-shaped query. The component route hard-codes it false. A graph
  failure gives `not_searched` `graph_unavailable`.
- A long-named row no longer fails the whole type. The page-size ladder
  re-reads only its span at a smaller size.

Changed, with the reason:

- X7: there is no `VISIBLE_SCOPE` caveat. HAMCP-499's visibility filter is
  not ported, so an entity is hidden only through the registry `hidden_by`.
- X12: the composed device and entity name is not served, and the reply
  warns. W6b adds it.
- An unusable floor or area registry gives `source_unavailable`.
- The invalid-argument envelope is the native `SearchError`.
- Arbitrary delegated projection is unreachable.
- `config_time_budget` is validated but not forwarded to the pages. Each
  sub-call scan has its own 60 s deadline.
- Automation, script and scene matches need ha-mcp admitted. Without it, the
  bucket is `source_unavailable` `subcall_not_admitted`.
- An upstream partial page is `not_searched` `upstream_partial` for that
  bucket only. The upstream `partial_reason` text and hint are not carried.
- A row larger than the reply bound even at page size 1, or a bucket past
  300 page calls, is `<type>_subcall_incomplete`.
- The F1 trigger is a numeric-like query: for exact, only `0-9 . + - e : t`
  and spaces; for fuzzy, that, or any query term holding a digit.
  - Automation, script, scene and helper sub-call rows keep only a name
    rescore and graph references, with `match_in_config` false.
  - Rows with no name match are dropped, and the bucket is `not_searched`
    `body_numeric_query`.
  - A legitimate numeric body match (`10:00`, `75`) is lost.
- The F1 scrub check: when a one-row helper probe, before or after the scans,
  does not show a clean scrub, every body type in the call is treated the
  same way (`body_scrub_degraded` or `body_scrub_unverified`).
- The audit status of the native invalid-argument path is failure /
  `validation_failure`.
