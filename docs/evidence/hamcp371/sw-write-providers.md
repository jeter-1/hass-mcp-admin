# SW (source half): write-provider diff reviews, v9.1.2 to v9.2.0

Scope, identities, method and the meaning of **[source]** and **[capture]** are
in `README.md`. A = v9.1.2, B = v9.2.0. Upstream paths are relative to
`src/ha_mcp/`. `P` = `hass_mcp_engineering_beta/ha_mcp_engineering` at
`aeb3b314`.

This is the reading half. No provider is bound by this file. Each verdict is
one of: **unchanged**, **changed, our use unaffected**, or **semantics
changed**. A verdict here covers what the source says; the confirmation half
(descriptor fingerprints in P2, payloads against a disposable Core in P6) is
still required before any 9.2.0 row is written.

## Summary

| Subject | Verdict from source | Still to confirm |
|---|---|---|
| #3050 | does not touch any tool the four providers call; its shared-module changes are scoped to `ha_config_set_helper` | P2 |
| Fan | `ha_get_state` unchanged; `ha_call_service` unchanged for our use; `ha_list_services` changed, our use unaffected | P2 descriptors, P6 payloads |
| Power | same three tools, same verdicts as fan | P2, P6 |
| Helper value | same three tools, same verdicts as fan | P2, P6 |
| Dashboard | getter and setter file identical; setter input-schema text changes through an imported documentation string; behaviour for our arguments unchanged; skill guide tool identical | P2 descriptors (new values expected for the setter), S4 skills hashes, P6 |

No provider was found with changed semantics for the calls Engineering makes.
Two descriptor changes are expected from source and need new reviewed values:
`ha_list_services` (all three service providers) and `ha_config_set_dashboard`
(input schema).

## 1. #3050 first

Commit `f78c9bc6a29c958a7288d684a4153c1c3d779835`, "feat(helpers): answer helper
flows on Core's own flow and send storage fields to Core as written (#3050)",
111 files.

1. **[source]** #3050 does not touch `tools/tools_service.py`,
   `tools/tools_services.py`, `tools/tools_search.py`,
   `tools/tools_config_dashboards.py`, `tools/auto_backup.py` or
   `utils/python_sandbox.py` (`git show --name-only f78c9bc6`).
2. **[source]** It does touch modules those tools import: `tools/helpers.py`,
   `tools/component_api.py`, `tools/config_entry_flow.py`,
   `tools/validation_middleware.py`, `server_tool_text.py` and
   `backup_manager.py`.
3. **[source]** `tools/helpers.py` A..B (blob `fb85b0fba93b` to `2b033a06744f`,
   seven hunks). The #3050 part removes `_HiddenParam`, `HIDDEN_PARAM` and
   `hidden_param_names` (A `:816-838`) and reduces `register_tool_methods`
   (hunks at A `:844-845` and `:851-855`, B `:828` and `:834`) to a plain
   `mcp.add_tool(method)`. At A the only
   users of `HIDDEN_PARAM` were `tools/tools_config_helpers.py:67,:620-684`,
   `tools/config_helpers/typed_config.py:13,:32` and
   `tools/config_helpers/validation.py:12,:25`, all on `ha_config_set_helper`.
   None of the tools the four providers call declared a hidden parameter, so
   their published schemas are not changed by this removal
   **[source, inference]**; exact descriptors **[capture]** P2.
4. **[source]** Other `tools/helpers.py` hunks A..B (not all from #3050): new
   `tool_error_from_response` after `raise_tool_error` (B `:70-75`); a separator
   change in `extract_structured_error_reason` (B `:156-157`); and one changed
   suggestion string in `_classify_exception` for the admin-required case
   (A `:425`, B `:430-432`): "Or use the in-process server, which provisions its
   own admin token" became "Or, on the in-process server, use a token issued in
   the integration's forms from the login of a local-only administrator
   account". This is error text, not a success path. It can change the
   error-shape aggregate that 9.1.2 shared with 9.1.0 (`4ed5b1b4...`)
   **[capture]** P2.
5. **[source]** `tools/validation_middleware.py` (+19 -2): the new hint is
   gated on `_FIELDS_IN_CONFIG_TOOLS = frozenset({"ha_config_set_helper"})`
   (B `:99`). No other tool's rejection text changes.
6. **[source]** `tools/component_api.py` A..B is additive
   (`HomeAssistantCommandNotSent` import, `ENTITY_COMPOSED_NAME`,
   `COMPONENT_INSTALL_HINT`, `component_serves`, `ask_component`). The
   capability probe is the same call at A `:494` and B `:500`.
7. **[source]** `tools/config_entry_flow.py`: `is_component_domain`, the guard
   `ha_call_service` uses, has an identical body at A `:749-766` and
   B `:681-698`.
8. **[source]** `backup_manager.py` (+11 -66): the diff contains no line
   matching `dashboard` or `lovelace`.

**Verdict for #3050: no semantic change for any tool the four providers call.**
The helper value provider writes helper *values* through `ha_call_service`; it
does not use `ha_config_set_helper`, the tool #3050 reworked
(`P/providers/upstream_helper_value.py:3-5`, `:12`).

## 2. The three tools shared by fan, power and helper value

All three providers call exactly `ha_list_services`, `ha_get_state` and
`ha_call_service` (`P/providers/upstream_fan.py:24`, refusal for any other tool
at `:104-105`). Power and helper value subclass the fan provider
(`P/providers/upstream_power.py:6`, `P/providers/upstream_helper_value.py:12`).

### 2a. `ha_call_service`: unchanged

- **[source]** `tools/tools_service.py` is the same blob at both tags
  (`f029d0112b13`). Tool name `:1744`, function `:1751`.
- **[source]** Guard against the component's own service domain at `:748`
  (`is_component_domain`, body identical at both tags, section 1 item 7).
- **[source]** The component write route is skipped when the caller does not
  wait: `if not should_wait or verbose or (entity_id and "," in entity_id):
  return None` at `:1713`; the legacy REST call is `self._client.call_service`
  at `:1984`. Engineering always sends `wait: False`
  (`P/fan/contracts.py:84-90`, `P/power/contracts.py:55-58`,
  `P/helper_value/contracts.py:171-174`), so the governed dispatch stays on the
  REST route at both tags, on both profiles **[source, inference]**;
  **[capture]** P6 and P9.
- **[source]** Direct relative imports of `tools/tools_service.py`, by blob
  identity. Same at both tags: `client/websocket_client.py`, `errors.py`,
  `read_only.py`, `tools/backup_access.py`, `tools/bulk_selector.py`,
  `tools/coercion.py`, `tools/event_call.py`, `tools/response_helpers.py`,
  `tools/tool_hints.py`, `tools/util_helpers.py`, `tools/ws_waiters.py`,
  `utils/entity_membership.py`. Changed: `client/rest_client.py` (only the
  removal of the REST `render_template` method, A `:876-892`; `call_service`
  untouched), `tools/component_api.py` (additive), `tools/config_entry_flow.py`
  (guard body identical), `tools/helpers.py` (section 1 items 3 and 4), and
  the vendored packages (`s3-vendored-and-dependencies.md`). Imports of those
  imports were not walked further.
- **[capture]** P2: the descriptor (input schema, annotations, description,
  output contract) is expected to equal 9.1.2's because the defining file is
  identical, but schema generation also depends on the vendored fastmcp and on
  pydantic, which changed.

Verdict: **unchanged** in source; for our arguments nothing differs.

### 2b. `ha_get_state`: unchanged

- **[source]** `tools/tools_search.py` changed A..B (blob `da48695dc7f4` to
  `a7e11ab1c708`), but the `ha_get_state` region is byte-identical: name
  A `:707`, B `:753`; function A `:712`, B `:758`; A `:706-805` equals
  B `:752-851`.
- **[source]** The implementation is in `tools/search/state.py`, same blob at
  both tags (`03d086032255`): single-entity path `:116`, bulk path `:160`,
  component read `ha_mcp_tools/states` at `:350`.
- **[capture]** P2 for the descriptor; P6 for the `data` envelope that
  `checked_state` reads (`P/providers/upstream_fan.py:137-145`,
  `P/fan/contracts.py:97-116`, `P/providers/upstream_power.py:14-21`,
  `P/providers/upstream_helper_value.py:20-29`).

Verdict: **unchanged**.

### 2c. `ha_list_services`: changed, our use unaffected

`tools/tools_services.py` changed (+196 -146; blob `7e8b2bf02a31` to
`ba395275eac9`), mostly from `0427c50d` (#2988, services described from Core's
own descriptions).

What Engineering sends and reads:

- Arguments `{"domain": <domain>, "limit": 50, "offset": 0, "detail_level":
  "summary"}` (`P/providers/upstream_fan.py:150-152`,
  `P/providers/upstream_power.py:27-29`,
  `P/providers/upstream_helper_value.py:35-37`).
- It reads only `success is True`, `services` being a mapping, and membership
  of `<domain>.<action>` in `services` (`P/providers/upstream_fan.py:153-156`,
  `P/providers/upstream_power.py:30-33`,
  `P/providers/upstream_helper_value.py:38-41`).
- The decoded reply is refused above 60,000 bytes
  (`P/providers/upstream_fan.py:75-85`).

What is the same at both tags **[source]**:

- Tool name A `:60`, B `:62`; function A `:65`, B `:67`; `limit` bounds
  `ge=1`, `le=200` at A `:79-80`, B `:81-82`.
- The result key is built the same way: `service_key =
  f"{domain}.{service_name}"` at A `:439`, B `:473`; the reply carries
  `"success": True` (A `:461`, B `:508`) and `"services": paginated_services`
  (A `:463`, B `:510`).
- An entry is dropped only by the `query` filter (A `:494-497`, B `:553-556`).
  Engineering sends no `query`, so no entry is filtered out.
- Pagination is applied after collection with the caller's `offset` and
  `limit`, at both tags. With `limit` 50 a domain with more than 50 services
  could hide the action on a later page; this risk is the same at A and B.
- The catalog source order: component read first
  (`_fetch_services_list_via_component`, A `:250`, B `:282`), then legacy REST
  `client.get_services()` (A `:245`, B `:255`).

What changed at B **[source]**:

1. On the legacy path (no component, profile A) B reads the configured
   language first: `_configured_language` (B `:260-279`) calls
   `client.get_config()`, which is a REST `GET /config`, before
   `client.get_services()`. A failure is caught and falls back to `"en"` with a
   warning string; it does not fail the tool. So every governed write's service
   preflight makes one more REST call to Core at B on that path.
2. The reply can carry a top-level `warnings` list (`warnings=catalog_warnings`
   passed into `_process_services`, B `:183`; language-unavailable and
   incomplete-field-metadata warnings).
3. `_build_service_entry` (A `:475`, B `:522`): `name` and `description` now
   come from Core's flat translation map, then from Core's own description,
   with placeholders applied (B `:537-550`); at A the lookup was
   `translations.get("component.<domain>.services.<service>", {})`
   (A `:484-491`). Each entry can also carry a `response` key (B `:581-585`).
   Summary entries can therefore be longer at B.
4. `full` detail processing was rewritten. Engineering never asks for `full`.
5. Parameter description text changed (hunks at A `:94`, `:108`), so the input
   schema text differs.
6. Component side (profile B):
   `custom_components/ha_mcp_tools/websocket_api/services.py` (+5 -1), language
   defaults to `hass.config.language`.

Effect on our use:

- **[source, inference]** `success`, the `services` mapping and the
  `<domain>.<action>` key are produced the same way, so the three membership
  checks are unaffected.
- **[source, inference]** The extra `warnings` key and the extra `response`
  entry key are ignored by the providers, which read two keys only.
- **[capture]** P2: the `ha_list_services` descriptor fingerprints will differ
  from 9.1.2's (description text changed). A 9.2.0 row needs the new reviewed
  values for all three providers.
- **[capture]** P6: reply size against the 60,000-byte cap for each admitted
  domain with real translated descriptions, and the presence of each admitted
  `<domain>.<action>` key on both profiles.
- **[capture]** P9: the added `GET /config` on profile A.

Verdict: **changed, our use unaffected.**

## 3. Fan

- Engineering: `P/providers/upstream_fan.py` (`REQUIRED_TOOLS` `:24`,
  `validate_catalog` `:48-68`, `state` `:137-145`, `services` `:147-157`,
  `dispatch` `:159-166`); release rows `P/fan/contracts.py:16-28` (9.1.2 row
  `:26-27`), semantic contracts `:30-31`, dispatch arguments `:84-90`.
- **[source]** State read: `ha_get_state` with `fields` and `attribute_keys`
  `["percentage", "supported_features"]`: section 2b, unchanged.
- **[source]** Service preflight: `ha_list_services` for domain `fan`:
  section 2c, changed, our use unaffected.
- **[source]** Dispatch: `ha_call_service` with `domain` `fan`, `data` `{}` or
  `{"percentage": n}`, `wait: False`, `return_response: False`,
  `verbose: False`: section 2a, unchanged. The provider reads `success is True`
  only (`:164-165`).
- **[capture]** P2 for the three descriptors; P6 for the state envelope, the
  `fan.<action>` keys and the dispatch acknowledgement.

Verdict: **changed, our use unaffected** (through `ha_list_services` only).
Not bound by this file.

## 4. Power

- Engineering: `P/providers/upstream_power.py` (`state` `:14-21`, `services`
  `:23-34`); release rows `P/power/contracts.py:9-23` (9.1.2 row `:20`),
  semantic contracts `:24`, dispatch arguments `:55-58`.
- **[source]** State read: `ha_get_state` with `fields`
  `["entity_id", "state", "last_updated"]`: section 2b, unchanged.
- **[source]** Service preflight: `ha_list_services` for the request's domain
  (light or switch): section 2c, changed, our use unaffected.
- **[source]** Dispatch: `ha_call_service` with `data` `{}`, `wait: False`:
  section 2a, unchanged.
- **[capture]** P2, P6 as for fan.

Verdict: **changed, our use unaffected** (through `ha_list_services` only).
Not bound by this file.

## 5. Helper value

- Engineering: `P/providers/upstream_helper_value.py` (`state` `:20-29`,
  `services` `:31-42`; docstring `:3-5`: "ha_call_service forwards ``data``
  unchanged in every reviewed release ... wait=False excludes the component
  fallback"); sources `P/helper_value/contracts.py:25-32` (9.1.2 row `:31`),
  semantic contracts `:33-34`, release rows `:37-38`, dispatch arguments
  `:171-174`.
- **[source]** The docstring's two claims hold at B: the file that forwards
  `data` and decides the route is the same blob at both tags (section 2a), and
  the component route is skipped for `wait: False` (`tools/tools_service.py`
  `:1713`).
- **[source]** #3050 reworked helper *configuration* (`ha_config_set_helper`,
  helper flows). The helper value provider does not call that tool
  (section 1).
- **[source]** State read with per-domain `attribute_keys`: section 2b,
  unchanged. Service preflight: section 2c. Dispatch: section 2a.
- **[capture]** P2, P6 as for fan, for each admitted helper domain and action.

Verdict: **changed, our use unaffected** (through `ha_list_services` only).
Not bound by this file.

## 6. Dashboard

Engineering: dependencies `ha_config_get_dashboard`, `ha_get_skill_guide`,
`ha_config_set_dashboard` (`P/providers/upstream_dashboard.py:111-115`);
`EXACT_GETTER_RELEASES` `:119`; `STORED_CONFIG_REQUIRED_RELEASES` `:191`
(comment `:190`: "tools_config_dashboards.py is unchanged at 9.1.2");
`COLLAPSIBLE_EMPTY_LIST_RELEASES` `:195`; setter arguments `:1088-1095`
(`url_path`, `config`, `config_hash`, `MandatoryBPS: False`,
`return_screenshot: False`, `BestPracticeKey`); setter contract rows
`P/f3_dashboard/provider.py:36-64` (9.1.2 row `:39-51`, comment `:37-38`),
tool name `:22`; `P/f3_dashboard/adapter.py:44`, `:279-285`.

Getter and setter:

- **[source]** `tools/tools_config_dashboards.py` is the same blob at both tags
  (`fb0cf9c2fe65`). Getter name `:1568`, function `:1573`; setter name `:2423`,
  `@with_auto_backup(domain="dashboard", id_param="url_path")` `:2432`,
  function `:2434`. So the comment at `P/providers/upstream_dashboard.py:190`
  is also true of 9.2.0.
- **[source]** The three behaviours Engineering's release sets depend on are in
  that unchanged file: `fetch_dashboards_list` `:1005` with `or []` at `:1808`
  and `:2144` (collapsible empty list), `_finalize_get_result` `:2252` and
  `stored_config` `:2333` (stored configuration required).
- **[source]** Direct imports with the same blob at both tags:
  `client/websocket_client.py`, `errors.py`, `strict_bps.py`,
  `utils/config_hash.py`, `utils/dashboard_patch.py`, `tools/coercion.py`,
  `tools/component_dashboard_edit.py`, `tools/config_write_helpers.py`,
  `tools/dashboard_card_describe.py`, `tools/dashboard_edit_errors.py`,
  `tools/dashboard_list_checks.py`, `tools/dashboard_read_errors.py`,
  `tools/tool_hints.py`. The best-practice key modules
  (`tools/best_practice_checker.py`, `tools/best_practice_result.py`,
  `utils/skill_loader.py`) are also the same blob at both tags.
- **[source]** Changed imports: `utils/python_sandbox.py`,
  `tools/auto_backup.py`, `tools/component_api.py` (additive),
  `tools/helpers.py` (section 1), `client/rest_client.py` (`render_template`
  removal only).

Setter descriptor text:

- **[source]** The setter's `python_transform` parameter description ends with
  `get_security_documentation()` (`tools/tools_config_dashboards.py:2464`).
  That function (`utils/python_sandbox.py`, A `:508`, B `:517`) gains three
  lines at B `:541-543` ("Guards (a failed guard raises and the config is not
  changed): ...", from `aacfed5b`, #3022).
- **[source, inference]** The setter's input schema text is therefore
  different at B, so its `input_schema_fingerprint` and
  `runtime_contract_fingerprint` are expected to differ from the 9.1.2 values
  at `P/f3_dashboard/provider.py:45` and `:49`. The statement at `:37-38` that
  the descriptor is byte-identical across releases will not carry to 9.2.0.
  Exact values **[capture]** P2.
- **[source]** Engineering never sends `python_transform`: it is absent from
  the setter arguments (`P/providers/upstream_dashboard.py:1088-1095`) and is
  listed in `PROHIBITED_ARGUMENT_NAMES` (`P/f3_dashboard/provider.py:172-174`).
  The sandbox change is a new guard-statement form inside that unused
  parameter.

Backup wrapper:

- **[source]** `tools/auto_backup.py` (+42 -20). The changes are a new
  `refusal_behind_capture_failure` (B `:531-537`) and the file and YAML branch
  (`elif domain in ("file", "yaml")`, B `:446-449`). The `dashboard` domain
  still falls to `else: yield` (B `:450-451`).
- **[source]** `backup_manager.py` diff has no dashboard or lovelace line.

Skill guide:

- **[source]** `ha_get_skill_guide` is defined in `server.py:935`; `server.py`
  is the same blob at both tags (`48230a8a8aeb`), as are `strict_bps.py` and
  `utils/skill_loader.py`.
- **[source]** The skills submodule gitlink
  `src/ha_mcp/resources/skills-vendor` is
  `166defa51c606b3c77d45a9b29a459d1f993f357` at both tags.
- **[capture]** S4: the skill files actually shipped are hashed from the two
  pinned images. A gitlink is a pointer, not the image content.

Component side (profile B):

- **[source]** `custom_components/ha_mcp_tools/websocket_api/dashboards.py`
  changed (+20 -18; blob `6c3f134645` to `d8805ec3fa`); the statuses
  `not_found` and `no_config` are unchanged.
- **[capture]** P6 on profile B: getter with `stored_config: true`, the empty
  list case, and one governed set with read-back.

Verdict: **changed, our use unaffected.** The getter and setter code is
identical; the only difference Engineering can see is the setter's descriptor
text. Not bound by this file; list B's 9.2.0 setter row needs values from P2.

## 7. What this changes for the plan

1. The 9.2.0 dashboard setter row cannot reuse the 9.1.2 fingerprints. The
   cause is known from source (section 6), so a mismatch in P2 on
   `input_schema_fingerprint` and `runtime_contract_fingerprint` alone is the
   expected result, not a surprise.
2. `ha_list_services` needs new reviewed descriptor values in all three
   service-provider rows.
3. The error-shape aggregate may differ from `4ed5b1b4...` because of one
   changed suggestion string (section 1 item 4).
4. The service preflight makes one more REST call (`GET /config`) on profile A
   (section 2c item 1). Any P9 call inventory or call-count assertion for a
   governed write must allow for it.
