# HAMCP-294 Logbook Window and Filter: Design Note (2.6.0 wave 1)

Status: design note only (HAMCP-332 wave 1). No runtime, test, schema, version or protected-path change.
Base: `origin/release/2.6.0` = `7246f2d21693f039504542574569f03b71476489` (2.4.2-beta.7). Engineering citations are
at that base, relative to `hass_mcp_engineering_beta/ha_mcp_engineering/` unless a full path is given. Core citations
are Home Assistant Core tag `2026.11.0` (`74cf5a49c63ed8d4d87717844bf14f79c28c9cea`), `homeassistant/components/logbook/`.
`rest_api.py` is byte-identical at the signed sequence-15 release `2026.10.3` (`1f214b84`).

## 0. Disposition (2026-11-08): historical baseline, reviewed delivery, deferred options

- **Historical baseline.** Sections 1-9 were written before the build, at design base `7246f2d2`. Where they
  conflict with the delivered contract below, the delivered contract wins.
- **Reviewed delivery.** [PR #397](https://github.com/jeter-1/hass-mcp-admin/pull/397) is merged into
  `release/2.6.0`. Reviewed head `dda873547d0b60af911b68f254e93887384c28f4`, merge commit `529d66a0`.
  `git merge-base --is-ancestor dda87354 origin/release/2.6.0` passes. It delivered the window, `entity_ids` and
  `name` filtering through the existing native bounded logbook route. It uses the same provider and the existing
  `core.basic_rest_read`.
- **Compatibility-preserving descriptor (delivered, kept).** The reviewed implementation deliberately keeps
  `hours: float = 12`. The signature is `get_logbook(hours: float = 12, entity_id="", start_time=None,
  end_time=None, entity_ids=None, name=None)` (`tools/compatibility.py:424` at `1c4c32b3`). With `start_time`,
  `hours` must stay at its default 12 and is ignored; any other `hours` with `start_time` is refused. Superseded
  (historical): the draft proposal to change the `hours` default to `null` (section 3) is withdrawn. It would undo
  the reviewed compatibility-preserving public schema.
- **Implemented defaults** (`clients/logbook.py:27-44` at `1c4c32b3`, labelled there "implementation defaults
  pending Josh (design note Q1)"):
  - `MAX_FILTER_ENTITIES = 10`, `MAX_FILTER_CHARS = 512` (joined) and `MAX_NAME_CHARS = 100`;
  - the window cap is unchanged at 168 h;
  - `NAME_MATCH_BASIS` is a casefold substring of the current `entity_id` or `friendly_name`.
- **Not claimed.** No live acceptance; the section 7 live reads still need Josh's authorization.
- **Deferred or optional.** The `context_id` and device filters stay out of scope (section 9).

## 1. Register outcome and binding decisions

HAMCP-294 Outcome (register, quoted): "get_logbook accepts an explicit bounded start/end window and an entity or name
filter, with truthful truncation and completeness reporting, so investigations can read the relevant slice instead
of the default window." Effort: Small. Workstream: Evidence / reads.

Binding decisions:
- Josh, 2026-11-07 brief: HAMCP-294 is a 2.6.0 wave-1 feature; designs first.
- Josh, 2026-11-03 ranking (row Next Action): fix-lane position 4 after HAMCP-277, batched with HAMCP-292. HAMCP-292
  is unrelated in content (plan sensitive-data guard), so the batch is a lane, not a shared change.
- Superseded (historical): at the design base no row or prompt had decided the numeric bounds, the name-match
  semantics, or whether `context_id` or a device filter belongs here. Section 0 records what #397 implemented. The HA Admin E024 case text is not in the repository; the row is the scope.

## 2. Current state at base

- Provider: Engineering-native direct Core REST, not delegated ha-mcp. `get_logbook` maps to `LOGBOOK_READ`
  (`providers/routing.py:161`), is a reviewed direct-HA exception (`providers/routing.py:360`) under policy
  `bounded_logbook_read` (`providers/routing.py:412-417`), and its Core requirement is `core.basic_rest_read`
  (`ha_core_readmission/routes.py:257`). Capability status `transitional`, risk `read` (`capabilities.py:40`).
  ha-mcp 8.6.0 `ha_get_logs` is classified `mixed_or_requires_wrapper` (`upstream_tool_policy_8_6_0.json`), so no
  delegated logbook route exists.
- Tool: `get_logbook(hours: float = 12, entity_id: str = "")` (`tools/compatibility.py:417-427`).
- Request: one `GET /api/logbook/<start>?end_time=<end>[&entity=<id>]`, where `end` is captured `now` and
  `start = end - hours` (`clients/logbook.py:200-209`). The route template is fixed (`clients/paths.py:63`); a query
  value is at most 512 characters (`clients/paths.py:33`, `232-236`). The transport refuses any other logbook
  request shape (`clients/rest.py:73-75`) and caps acquisition at min(HA timeout, 30 s) (`clients/rest.py:93-96`).
- Bounds: 1 MiB body, 10,000 records, 50,000 items, depth 32, 168 h, 30 s (`clients/logbook.py:19-24`). `hours` must
  be finite in (0, 168]; `entity_id` is empty or one ID of at most 255 characters, so `a.b,c.d` is refused
  (`clients/logbook.py:188-194`; test `tests/test_logbook_bounds.py:143-153`). One read per process
  (`logbook_busy`, `clients/logbook.py:195-196`; `errors.py:187-195`).
- Output: complete results are a bare list; a budget overflow returns a source-order prefix
  `{entries, truncated: true, returned, omitted, requested_interval, ordering: "source_order", required_action}`
  (`clients/logbook.py:137-172`). The dispatcher marks `completeness: partial` when `data.truncated is True`
  (`providers/dispatch.py:255-262`). Envelope sizing: `models/responses.py:164-206`, `296-300`, `402-404`.
- Missing: an explicit start, an end other than now, more than one entity, a name filter, a continuation point,
  and any statement of what a complete result does not cover. `docs/LOGBOOK_READS.md` states "no offset/start-time
  cursor". Coverage gap RCG-077 (raise caps or paginate) is separate (`docs/read_coverage_matrix.json:5213`).

Core facts (`2026.11.0`):
- REST `LogbookView` (`rest_api.py:35-112`): `/api/logbook/{datetime}` start; `end_time` query (`79-86`); `entity`
  is a comma-separated list parsed by `cv.entity_ids` (`68-77`); `context_id` cannot combine with `entity`
  (`90-95`); no device filter, no row limit, no name parameter. Rows include `name` (`include_entity_name=True`,
  `97-106`) and `when` as a UTC ISO string (`processor.py:427-432`).
- WebSocket `logbook/get_events` (`websocket_api.py:453-520`): `start_time`, `end_time`, `entity_ids`, `device_ids`,
  `context_id`; also no row limit and no name parameter. `logbook/get_action_calls` is new in 2026.11.0 and admin-only
  (`websocket_api.py:523-532`); neither WebSocket command is used or proposed here.
- Window bounds are exclusive at both ends (`queries/common.py:122`, `175`, `200`); rows are ordered by
  `time_fired_ts` (`queries/all.py:44`, `queries/entities.py:112`). Continuous sensors and `counter`/`image`/
  `proximity` produce no rows (`processor.py:373-379`, `helpers.py:256-280`, `const.py:15`).

## 3. Proposed change

Same provider, same route, same Core profile. Name resolution adds one `GET /api/states`, a route already on the
allowlist and inside `core.basic_rest_read` (`ha_core_readmission/profiles.py:158-167`, `rest_states_list`).

**Inputs** (all optional; at most one of `entity_id`, `entity_ids`, `name`):
- `start_time`, `end_time`: ISO-8601 with an explicit offset (`Z` or `±HH:MM`), matching the existing timestamp
  segment grammar (`clients/paths.py:44-46`); naive times are refused. Normalized to UTC before the request.
- `hours` (delivered, compatibility-preserving): the default stays `12` (`hours: float = 12`). Without `start_time`
  the window is `[end - hours, end]`. With `start_time`, `hours` must equal its default 12 and is ignored; any other
  value is refused. Superseded (historical): the draft changed the default to `null` and required a null `hours`
  with `start_time`. That proposal is withdrawn (section 0).
- `entity_ids`: 1-10 unique valid IDs; the comma-joined value must fit the 512-character query bound. Sent as the
  existing `entity` query parameter. Legacy `entity_id` keeps its exact current rule (comma still refused).
- `name`: 1-100 characters, case-folded substring of `entity_id` or `friendly_name` over current states, the same
  rule as `search_entities` (`tools/compatibility.py:335-372`). 1-10 matches resolve to `entity_ids`. 0 matches
  refuses `name_matched_no_entity`; more than 10 refuses `name_matched_too_many` with `match_count` and the first
  10 matching IDs (sorted) so the caller can pass `entity_ids`. No logbook request is sent on refusal.

**Bounds:** window `0 < end - start <= 168 h` (unchanged cap); `start_time` after now is refused; `end_time` after
now is clamped to now and reported. Rows, bytes, items, depth and timeout keep today's values. The busy slot covers
the whole call, including the states read. Still exactly one logbook GET, no retry, no split query.

**Output.** Calls that use only `hours`/`entity_id` stay byte-compatible (bare list or existing partial object). Any
new argument returns one object every time:
`{entries, truncated, returned, omitted, requested_interval, effective_interval, interval_bounds: "exclusive",
ordering: "source_order", filter: {kind: none|entity_id|entity_ids|name, entity_ids, name, match_basis,
matched_count}, continuation, completeness_notes, required_action}`. `truncated: false` keeps provider completeness
`complete`; `true` keeps `partial` through the existing dispatcher rule.
- `continuation` (only when truncated): `{start_time: min(when of omitted rows) - 1 µs, end_time: effective end,
  overlap_possible: true}`. It uses the minimum over omitted rows, so it needs no ordering promise; the 1 µs margin
  covers Core's exclusive start and microsecond rounding of `when`. Null with `reason: omitted_when_unavailable` if
  any omitted row lacks a valid `when`.
- `completeness_notes` (fixed codes): `recorder_retention_not_checked`, `core_exclusions_not_observed`,
  `name_resolved_from_current_states` (name only), `continuous_entities_produce_no_rows` (when a resolved state is a
  sensor with `unit_of_measurement`, `state_class` or a numeric `device_class`, or a domain Core always skips).
- Limit errors keep `logbook_response_limit_exceeded` and its message; the object's `required_action` names
  `start_time`/`end_time` and the entity filter.

## 4. Public schema and tool-registration impact

- Tool count unchanged (`ENGINEERING_STATIC_TOOL_COUNT` 81, `tests/test_alarmo_inspection_catalog_continuity.py:109`).
  Changed public input: four new optional arguments. The `hours` default is unchanged at 12 (section 0). Changed output: additive object only
  when a new argument is used. The descriptor digest changes, so the build adds a reviewed
  `tests/hamcp294_descriptor_changes.py` strip helper, as for HAMCP-311.
- Security-sensitive surfaces: public schema, the fixed logbook route and transport guard, read-policy text,
  `docs/read_coverage_matrix.json` (logbook detail and fingerprint), `docs/LOGBOOK_READS.md`, `TOOL_AUDIT.md`.
  Unchanged: routing (`direct_ha_api`, no fallback), Core requirement, sanitization, busy slot, error codes for limits.
- HAMCP-319 (ha-mcp 9.1.0, #385 head `3c06f7b1`, design only): no interaction. The 9.1.0 `ha_get_logs` gains logbook
  `device_id`, `context_id`, `offset` and `order` (`docs/evidence/hamcp319/phase1/catalogs/full-1.json`), but
  `get_logbook` is native and #385 does not mention logbook. No in-flight 2.5.0 PR (#384, #386, #387) touches
  `clients/logbook.py`, `tools/compatibility.py` or `providers/routing.py`; 2.5.0 wins any shared file.

## 5. Signing impact

None. No signed Core profile is new or changed. The logbook request keeps its REST route and adds only values to
parameters Core already accepts (`entity` list, `end_time`). Name resolution uses `GET /api/states`, which is part of
the existing `core.basic_rest_read` contract. No WebSocket command (`logbook/get_events`, `device_ids`) is added; a
device filter would need a new contract and signing, and is out of scope (Q3).

| Profile id | New or changed (from version X) | Core commands / contract added | Why | Fingerprint impact | Can it batch with other 2.6 profiles |
| --- | --- | --- | --- | --- | --- |
| (none) | Not applicable | None | Existing REST logbook route and states read under `core.basic_rest_read` | None to a signed Core profile; the Engineering descriptor and coverage-matrix fingerprints change (section 4) | Not applicable |

Observation, not a change: Core's logbook component is not in the measured Core read inventory
(`ci/core-contract-read-files.json`), and the `core.basic_rest_read` probes cover config and states only.

## 6. Negative reachability and refusal tests

No write, service call, new route, new Core command or fallback. Extend `tests/test_logbook_bounds.py`:
- Refused with zero HTTP calls and the busy slot released: naive or malformed timestamps, `start >= end`, window over
  168 h, future `start_time`, a non-default `hours` with `start_time`, two of `entity_id`/`entity_ids`/`name`, 11 entity IDs,
  duplicates, invalid IDs, a joined list over 512 characters, empty or 101-character `name`, non-string types.
- Legacy: `entity_id="a.b,c.d"` and the existing invalid-input matrix stay refused; legacy calls stay byte-identical.
- Name: 0 and 11 matches refuse with no logbook GET; exactly 10 sends one GET with the 10 IDs; a malformed states
  body fails closed; the transport guard still refuses any non-logbook shape with `logbook_read=True`.
- Truncation: `continuation.start_time` equals min omitted `when` minus 1 µs, including unordered fixtures; missing
  `when` gives null with the reason; counts equal received rows; a sanitized secret in `name` is never echoed.
- Clamp: a future `end_time` is reported in `effective_interval` and sent as now.

## 7. Acceptance

- Success (offline loopback): a two-hour window two days back filtered to three entities returns a complete object
  with `truncated: false`; a name filter resolving to two entities returns their rows with `filter.kind: name`; an
  oversized window returns a partial prefix whose `continuation` yields the remaining rows on a second call with
  only disclosed overlap.
- Refusal: section 6. Failure preservation: all current `test_logbook_bounds.py` cases pass unchanged (limits,
  busy, timeout, redirect, encoding, small budgets). Recovery: a refused or failed call leaves no held slot.
- Live acceptance (after deployment, Josh-authorized only): one windowed read and one name read. Not part of review.

## 8. Build slice and dependencies

- One runtime PR for a 2.6.0 beta touching the protected path (`clients/logbook.py`, `tools/compatibility.py`,
  possibly `models/responses.py` sizing), so the build task must authorize it explicitly. Plus tests, the descriptor
  helper, regenerated coverage matrix, `docs/LOGBOOK_READS.md`, `TOOL_AUDIT.md` and the changelog. Security section
  required (public schema, read path).
- Delivered as #397 (section 0). Superseded (historical): "Dependencies: answers to Q1-Q3" (the build used the
  section 0 defaults). No signing dependency, no dependency on unlanded 2.5.0 code. Rollback: revert the
  PR; there is no persistence, and legacy calls are unchanged.

## 9. Open questions

None blocks the merged slice.
- Q1 Bounds: implemented as the section 0 defaults (10 entities, 512 joined characters, up to 10 name matches,
  100-character name, 168 h cap, rows/bytes/timeout unchanged). The source labels them "implementation defaults
  pending Josh". Confirming them or changing them is optional; changing a value would be a separate runtime change.
- Q2 Name semantics: implemented as a case-folded substring over current `entity_id`/`friendly_name`, refusing 0 or
  more than 10 matches. The post-filter alternative stays rejected for the reasons in the draft (row and byte caps,
  no truthful completeness).
- Q3 Scope: `context_id` and the device filter stay out of scope. The device filter would need a new Core contract
  and signing. Scoping either as a follow-up is optional and pending Josh.

## 10. Codex design review 2026-11-08 — disposition

| Finding | Severity | Correction in this note |
| --- | --- | --- |
| F2: proposal to change `hours` to null; delivered slice not identified | Low | Section 0 records the #397 delivery (reviewed head `dda87354`, merge `529d66a0`, ancestry checked) and the preserved `hours: float = 12` descriptor. The null-default proposal is withdrawn in sections 0 and 3 and in the section 4 schema line. The section 6 refusal case is now a non-default `hours` with `start_time`. Section 9 records the implemented defaults; no live acceptance is claimed. Proving check: confirm `get_logbook`'s descriptor at `1c4c32b3` (`tools/compatibility.py:424`) is the reviewed one, and reconcile with `register get HAMCP-294`. |
