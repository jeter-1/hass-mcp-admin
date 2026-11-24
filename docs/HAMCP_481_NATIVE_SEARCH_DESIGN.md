# HAMCP-481 native search — design

Status: **draft for review; not approved.** Docs only. The build is planned
for the release after 2.9.1, not for 2.9.1 (2.4.2-beta.13). Nothing here
authorizes a runtime, schema, routing, protected-path, version or release
change, deployment, or live Home Assistant or Engineering access. Decisions
still open for Josh are marked **[JOSH]**.

- Register: HAMCP-481. Related rows: HAMCP-472 (the `ha_search` audit
  finding this design absorbs), HAMCP-441 (`result_fields` enrichment keys
  withheld), HAMCP-447 (dashboard identity, `by_domain`, area and state
  echoes withheld), HAMCP-454 (`include_config` bodies withheld until a
  redaction design), HAMCP-233 (`ha_search` opens options flows),
  HAMCP-403 (`search_unified` quarantine).
- Scope source: the 2.9.1 re-scope (architect design
  `2026-11-23-release-2.9.1-rescope-audit.md`, item B) and the architect
  block `release-291-next-B-D-F-G` (2026-11-24). Josh approved the re-scope;
  that approval is relayed by the architect.
- Base: `release/2.9.1` at `774328e0` (2.4.2-beta.13 declared). Engineering
  citations are relative to `hass_mcp_engineering_beta/ha_mcp_engineering/`
  at that base. ha-mcp citations are 9.2.0 (`51e3aac7`).

## 0. Summary

Engineering answers `ha_search` today by delegating to ha-mcp 9.2.0 and
projecting the reply through the 9.2 read chain. Three properties of that
path cannot be fixed by projection alone:

1. **Always partial.** Every reply is partial with the visibility-scope
   warning, because ha-mcp hides entities by its visibility policy without a
   marker, so Engineering cannot prove that nothing was hidden (design F3).
2. **Replaced warnings.** The `ha_search` KeySet reviews no upstream warning
   text, so any upstream warning becomes `upstream_warning_replaced` and adds
   a "not reviewed" qualifier.
3. **Withheld content.** Enrichment keys, `by_domain`, the area and state
   echoes and config bodies are withheld (HAMCP-441, HAMCP-447, HAMCP-454),
   and the upstream path can open options flows (HAMCP-233).

Native search is an Engineering-owned read over existing reviewed Core
reads. It covers entities, automations and scripts, applies the domain,
area, query and state filters itself, returns a reviewed `result_fields`
set, and reports `partial` only when Engineering actually withheld
something, with the reason.

## 1. Evidence behind the requirements

- **HAMCP-472 diagnosis** (source level, my handoff
  `hass-mcp-admin-agent-release-2.9.1.md`, 2026-11-23; accepted by the
  architect):
  - `upstream_reads_9_1_entities._search_completeness` returns partial on
    every call, with `VISIBLE_SCOPE` ("Coverage is limited to entities
    visible to the upstream provider; entities hidden by its visibility
    policy are omitted without notice."). HAMCP-437 keeps this for 9.2.0.
  - The `ha_search` KeySet in `providers/upstream_reads_9_2_keys.py` has
    empty `warnings` and `prefixes`, so each upstream warning is replaced and
    `REPLACED_PARTIAL` is added.
  - Engineering does not drop or reshape the entity records; the probes kept
    every entity.
- **HA Admin replay** (beta.12, ha-mcp 9.2.0; relayed to this session on
  2026-11-23 CST). Reported by HA Admin, relayed by Josh to the architect
  and by the architect to me; I did not see the raw responses.
  - The wrong-empty result did not reproduce: `domain_filter` light with no
    query gave 30; query "light" plus light gave 30; "living room lamp" plus
    light gave 1; "leak" plus `binary_sensor` gave 24, also with
    `exact_match` false. All matched `search_entities`.
  - The audit call that returned 0 also passed `search_types`; its values
    were not kept. A `search_types` value without entities is the simplest
    explanation (unproven).
  - The one replaced upstream warning appears only when `query` and
    `domain_filter` are both set. That fits ha-mcp's `_INTENT_SKIP_WARNING`
    (`src/ha_mcp/tools/search/response.py:240`).
  - The always-partial text is the visibility-policy warning.
- **Disposition of HAMCP-472** (Josh's "Yes", relayed by the architect,
  2026-11-24): Medium priority, no 2.9.1 code; native search owns
  requirements R-A to R-C below.

## 2. Scope

In scope, all reads:

- **Entities**: records from the Core state machine (the same source as
  `search_entities`, `core.direct_entity_state_read`), joined with the
  entity and area registries (`core.non_device_registry_read`) and the
  device registry (`core.direct_device_registry_read`) for name, area and
  hidden status.
- **Automations**: the `automation.*` state records. Their identity (unique
  `id`, alias as friendly name, `mode`, `last_triggered`) comes from the
  state attributes, so no configuration read is needed. The build pins
  those attributes against the exact Core source (ADR-021).
- **Scripts**: the `script.*` state records, with object id, alias, `mode`
  and `last_triggered` from the entity id and state attributes, pinned the
  same way. The HAMCP-212 loaded-script read (`script/config`) is not
  needed for identity.

Out of scope:

- Scenes, helpers and dashboards as separate result types (see §4,
  `search_types`).
- Config-body search and config bodies in results, until the HAMCP-454
  redaction design exists.
- Any write, service call, reload, options or config flow (closes the
  HAMCP-233 path for search), ha-mcp dispatch, `search_unified` (HAMCP-403
  stays as is for the delegated route), and any fallback to the delegated
  route.
- Disabled entities that have no state. **[JOSH] O6** whether a later
  version includes them from the registry.

No new Core endpoint or WebSocket command is needed; each source above is
already admitted. The tool's read-admission entry changes:
`ha_core_readmission/routes.py:285` maps `ha_search` to
`core.delegated_device_effective_area` today, and the native read needs the
three profiles above. That file is a protected path.

## 3. Filters and query semantics

All given filters combine with AND. A caller's own filters define the
requested scope; they are never a reason for `partial`.

| Argument | Semantics |
| --- | --- |
| `domain_filter` | Exact domain match, lowercase, `^[a-z0-9_]+$`; anything else is an invalid argument. An unknown domain gives an empty result (§5, R-A), not an error. |
| `area_filter` | Matches an area id exactly, or an area name casefolded exactly. An entity's area is its registry area, else its device's area. |
| `state_filter` | Exact match on the current state string. |
| `query` | Text match over each record's match texts: `entity_id`, object id, friendly name, registry name, and for automations and scripts the alias and unique id or object id. With `exact_match` true (the default), the casefolded query must be a substring of one match text. With `exact_match` false, every whitespace-separated token must appear in some match text, in any order. **[JOSH] O5**: this replaces ha-mcp's fuzzy scoring with a deterministic rule. |

**Query plus domain: closed as "text AND domain, works."** A query with
`domain_filter` returns the records of that domain whose match texts match
the query. The replay counts in §1 match `search_entities` for the same
filters, so this is the semantics the native read must keep. The
acceptance in §8 checks it against `search_entities` at test time rather
than against fixed counts.

Ordering is deterministic: exact identifier match, then prefix match, then
substring match, then `entity_id`. Paging relies on that order.

## 4. Arguments and result fields

The native read accepts the 15 arguments of the pinned 9.2.0 public schema
(`providers/upstream_reads_9_2.py`, `PUBLIC_SCHEMAS["ha_search"]`), with
their current types and defaults and `additionalProperties: false`:
`query`, `search_types`, `domain_filter`, `area_filter`, `state_filter`,
`exact_match` (true), `limit` (10), `offset` (0), `per_domain_limit`,
`group_by_domain` (false), `include_hidden` (true), `include_config`
(false), `result_fields`, `fields`, `config_time_budget`.

- **`search_types`**: any subset of entities, automations and scripts;
  omitted means all three. The exact accepted strings are pinned at build
  from the 9.2.0 contract, with a test. **[JOSH] O2**: an out-of-scope value
  (scenes, helpers, dashboards) is refused as an invalid argument that names
  the supported values (recommended), rather than answered with an empty
  bucket.
- **`include_hidden`**: true includes hidden entities; false excludes them.
  This is the caller's scope, echoed in `applied`, and not a `partial`
  reason.
- **`group_by_domain`**: adds `by_domain`, a count per domain of the matched
  set. It is an additive view; the record lists stay. **`per_domain_limit`**
  caps records per domain; records it cuts are reported as `paged`.
- **`include_config`**: bodies are not returned. With `include_config` true
  the reply is partial with reason `filtered` and the code
  `config_bodies_withheld` (HAMCP-454), so the omission is never silent.
- **`config_time_budget`**: there is no config-body search, so it has no
  effect; `applied` reports it as not applicable.
- **`fields`**: **[JOSH] O3**. Its 9.2.0 meaning is pinned from exact source
  at build. If it cannot map onto `result_fields` exactly, it is refused
  with a reviewed error, never guessed.
- **`limit`**: bounded (proposed maximum 100) and by the response budget.

**`result_fields`** is a closed, reviewed set per record type. An unknown
field is an invalid argument that lists the allowed fields; nothing is
silently withheld, so `fields_withheld` does not exist in native replies.

| Record | Reviewed fields | Default when omitted |
| --- | --- | --- |
| Entity | `entity_id`, `domain`, `name`, `state`, `area_id`, `area_name`, `device_id`, `device_name`, `hidden`, `entity_category`, `device_class`, `unit_of_measurement`, `last_changed`, `last_updated` | `entity_id`, `name`, `state`, `area_name` |
| Automation | `entity_id`, `id`, `name`, `state`, `mode`, `last_triggered`, `area_id`, `area_name` | `entity_id`, `name`, `state`, `last_triggered` |
| Script | `entity_id`, `object_id`, `name`, `state`, `mode`, `last_triggered`, `area_id`, `area_name` | `entity_id`, `name`, `state`, `last_triggered` |

**[JOSH] O4** the default sets. Each HAMCP-441 enrichment key is either in
this table with a reviewed source or refused by name; none is silently
dropped. Arbitrary state attributes are not returned. Descriptions are left
out of the first version, because they are free text that needs its own
sanitizer review.

Every output string passes the shared sanitizer (`sanitize_untrusted_data`)
with identity-only redaction markers.

## 5. Reply contract

The reply keeps today's top-level keys (`partial`, `warnings`, `errors`) and
the `entities`, `automations` and `scripts` buckets, and adds:

- `applied`: the effective `search_types`, the filters, `exact_match`,
  `include_hidden`, `limit` and `offset`.
- `total_matches` per bucket and `next_offset` (or null).
- `partial_reasons`: a list of `{reason, search_type, count, text}`.
- `notes`: fixed reviewed texts that explain the result without qualifying
  it.

### Requirements from HAMCP-472

- **R-A. An empty result states what was applied.** When `entities` is
  empty, or any requested bucket is empty, the reply includes `applied` and
  a reviewed note. If `entities` were not in the applied `search_types`, the
  note says so. `applied` is present in every reply, so the zero case cannot
  omit it.
- **R-B. Partial only when something was withheld, with a reason.**
  `partial` is true if and only if `partial_reasons` is non-empty. The
  closed reason set:
  - `paged`: more matches exist beyond this page or the `per_domain_limit`
    (count, `next_offset`);
  - `truncated`: the response budget cut records or fields (count);
  - `filtered`: Engineering withheld content by policy: a sanitizer
    redaction, or config bodies with `include_config` (count, code);
  - `hidden`: Engineering withheld matching records by its own visibility
    policy (count). The native read has no such policy at first, so this
    reason appears only if one is added later. The caller's own
    `include_hidden` false is scope, not `hidden`;
  - `source_unavailable`: a secondary source could not be read and the
    affected fields are null (which source).

  A complete reply has `partial` false and no reasons. This replaces the F3
  always-partial rule for search: no `VISIBLE_SCOPE` text and no
  `REPLACED_PARTIAL` in native replies. Retiring F3 on the delegated route
  is HAMCP-472 P2, still **[JOSH]**.
- **R-C. The intent-skip warning maps to a reviewed text.** The native read
  never searches config bodies, so no upstream intent-skip warning exists.
  The same fact is a fixed note, `entity_filters_limit_scope`: "Domain,
  area and state filters apply to entity, automation and script records;
  configuration bodies are not searched." It is a note, not a `partial`
  reason. If the delegated route is still served during a transition
  (O1), its KeySet maps the exact 9.2.0 `_INTENT_SKIP_WARNING` text to the
  same reviewed text, and any other unknown text is still replaced. That
  KeySet change is a protected path and needs a named OK.

### Failure behavior

The rule is never wrong-empty and never silently partial:

- If the state read fails, the read fails with an explicit error. There is
  no fallback to the delegated route and no empty success.
- If a filter cannot be evaluated, the read fails. For example, the area
  registry being unavailable with `area_filter` set is an error, not an
  empty result.
- If a secondary source fails and no filter depends on it, the reply is
  partial with `source_unavailable` and nulls in the affected fields.
- Inventory bounds are exceeded (proposed: 20,000 states; query 256
  characters): the read fails closed with a reviewed bound error.

## 6. Contracts kept with today's `ha_search`

- One connector: the read is served by the Engineering endpoint. The client
  does not pick a provider.
- **[JOSH] O1, recommended:** keep the public name `ha_search` and its
  argument schema, and swap the provider from the delegated ha-mcp route to
  the native read, with no fallback. The catalog count is unchanged and the
  capability status moves from delegated to native. The alternative is a
  new tool name alongside the delegated `ha_search`, which leaves two search
  tools and keeps the F3 qualifier on one of them.
- Read-only risk, provider attribution, the audit record, bounded output
  and sanitization all stay, at least as strict as today's delegated
  route.
- The top-level keys and bucket names stay. The added keys are additive.
- Behavior changes clients will see: `partial` is false on complete
  replies; out-of-scope `search_types` and unknown `result_fields` are
  refused; scene, helper and dashboard buckets are absent; no options flows
  are opened.

## 7. Disclosure and review

Compared with today, native replies disclose more: `by_domain` counts, the
`applied` echo of the area and state filters, reviewed enrichment fields
and `last_triggered`. Config bodies remain withheld, and the read routes to
a new provider. So the build needs:

- a security section (provider routing, public schema and disclosure);
- negative reachability tests (§8);
- Codex review, per the re-scope's rule for security-touching items.

The build touches protected paths under
`hass_mcp_engineering_beta/ha_mcp_engineering/`. Each file needs Josh's
named OK in the build task.

## 8. Acceptance (reads only)

### Offline, in the build pull request

- Domain-only listing equals the `search_entities` fixture for the same
  domain.
- Text AND domain, with `exact_match` true and false; each filter alone and
  combined.
- Partial invariant: `partial == bool(partial_reasons)`. Each reason comes
  from its cause alone; a complete reply has no reasons and neither the
  `VISIBLE_SCOPE` nor the `REPLACED_PARTIAL` text.
- R-A: zero entities, including a `search_types` without entities, carries
  `applied` and the note.
- Paging: deterministic order, `total_matches`, `next_offset`, pages that
  union to the total, and `per_domain_limit`.
- The response budget gives `truncated`, never a silent cut.
- A sanitizer redaction in a name gives `filtered` with a count and an
  identity-only marker.
- `include_config` true gives `filtered` with `config_bodies_withheld` and
  no body.
- An unknown `result_fields` value and an out-of-scope `search_types` value
  are refused (per O2).
- Negative reachability:
  - zero ha-mcp dispatches (a dispatcher spy) and Engineering provider
    attribution;
  - no config or options flow call, no service call and no write;
  - no fallback to the delegated route when a native source fails.
- Failures: a state read failure gives an error; the area registry failing
  with `area_filter` gives an error; without it, `source_unavailable`.
- Schema: the argument set and types equal the pinned 9.2.0 schema
  fingerprint, or the declared delta if O1 or O3 change it.
- Bounds: query length, maximum `limit` and the state inventory cap fail
  closed.

### Installed, read-only (separately authorized, after deploy)

Each step is one read, approved by Josh. Counts are compared with
`search_entities` run at the same time, not with fixed numbers.

1. `domain_filter` light, no query: the count equals `search_entities` for
   lights. `partial` is false unless the result is paged, and then the
   reason is `paged` with `total_matches`.
2. Query "light" plus light, "living room lamp" plus light, and "leak" plus
   `binary_sensor` with `exact_match` true and false: each equals
   `search_entities` with the same text and domain.
3. `search_types` automations only, with a query: automations matched by
   alias; `applied.search_types` shows only automations, and the empty
   entities bucket has the R-A note.
4. `search_types` scripts only: the same for scripts.
5. `area_filter` by name and by id: the same set.
6. `state_filter` "on" plus light.
7. `limit` 5 on a larger set: `paged`, `next_offset`, and a second page that
   continues the first.
8. Each reviewed `result_fields` value returns; an unknown value is refused.
9. `include_config` true: `filtered`, `config_bodies_withheld`, no body.
10. Query plus `domain_filter`: no replaced warning, and the R-C note.
11. A query with no matches: empty buckets, `applied`, the note, and
    `partial` false.
12. The audit log records the search with native provider attribution and
    no argument values beyond what read tools record today.
13. `tools/list` matches the catalog the build release declares.

## 9. Open decisions

- **O1 [JOSH]**: swap the provider under `ha_search` (recommended) or add a
  new tool.
- **O2 [JOSH]**: refuse out-of-scope `search_types` (recommended) or answer
  with an empty bucket and a reason.
- **O3 [JOSH]**: `fields`, mapped exactly from 9.2.0 source or refused.
- **O4 [JOSH]**: the default `result_fields` sets.
- **O5 [JOSH]**: `exact_match` false as token AND (recommended) instead of
  fuzzy scoring.
- **O6 [JOSH]**: whether disabled entities are included later.
- **HAMCP-472 P2 [JOSH]**: retire F3 always-partial on the delegated route
  before native search ships, or leave it until the provider swap
  (the architect recommends deferring to native).
