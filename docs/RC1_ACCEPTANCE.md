# HA MCP Engineering Server 2.0.0-rc.1 deployed acceptance

> **Historical only:** this procedure cannot establish the later entity-search
> correction for an already installed RC1 runtime. RC1 remains immutable. Use
> [`RC2_ACCEPTANCE.md`](RC2_ACCEPTANCE.md) after installing `2.0.0-rc.2`.

This is a post-deployment, human-operated acceptance procedure for the isolated
beta/RC add-on. It must not be run by implementation CI against a deployed Home
Assistant instance. Begin only after completing steps 1 through 10 of the exact
post-merge sequence in [`BETA_DEPLOYMENT.md`](BETA_DEPLOYMENT.md): the accepted
commit is tagged `v2.0.0-rc.1`, the prebuilt image publication workflow passes,
the three-platform manifest and expected digest are verified, an unauthenticated
pull succeeds, and only the beta/RC app is updated and reconnected. Merging the
pull request alone does not authorize this procedure or a Home Assistant update.

Use a dedicated harmless automation and non-sensitive test entities. Production
v1.1.2 must remain installed and running as `hass_mcp_admin` on port `8099`.
Do not access, restart, update, reconfigure, disable, or otherwise modify it.

Before installing the image, record the accepted tagged source commit, the
published manifest-list digest, and the exact version image
`ghcr.io/jeter-1/hass-mcp-engineering-beta:2.0.0-rc.1`. The exact commit image
must also exist as
`ghcr.io/jeter-1/hass-mcp-engineering-beta:sha-<commit>`. Do not claim that
publication or anonymous access passed unless the post-merge release workflow
and clean unauthenticated verification actually succeeded. Redact connector
secrets, Supervisor tokens, cookies, CSRF values, authenticated URLs, raw
traces, full configurations, and audit payloads from acceptance notes.

## 1. Preconditions

1. Confirm `v2.0.0-rc.1` points to the accepted `main` commit and the publication
   and validation jobs passed for that tag.
2. Confirm the generic image is
   `ghcr.io/jeter-1/hass-mcp-engineering-beta`, the version image is
   `ghcr.io/jeter-1/hass-mcp-engineering-beta:2.0.0-rc.1`, and the matching
   `sha-<commit>` tag exists.
3. Confirm the version-tag manifest contains `linux/amd64`, `linux/arm64`, and
   `linux/arm/v7`, its manifest-list digest matches the publisher's recorded
   digest, and an unauthenticated pull or inspection succeeded from a separate
   job or clean environment. Stop if the package is private or any result is
   missing or mismatched.
4. Confirm the installed target is `hass_mcp_engineering_beta`, not
   `hass_mcp_admin`.
5. Confirm MCP remains mapped on `8100`; Ingress is enabled on internal port
   `8110`; `panel_admin` is true; and `8110` is absent from host port mappings.
6. Confirm production `hass_mcp_admin` remains installed and running at v1.1.2
   on port `8099` and was not stopped, restarted, updated, reconfigured, or
   disabled.
7. Record one existing harmless automation internal ID, one existing entity ID,
   its exact automation configuration/fingerprint, and current governance
   health. The acceptance change will modify only the automation description.
8. Confirm no pre-existing active test plan, pending challenge, active apply, or
   rollback-pending plan can be confused with this acceptance run.

## 2. Foundation and provenance

After updating only the beta/RC app and reconnecting the existing Engineering
connector, call `server_info` before `tools/list`, a health check, or any other
acceptance operation. Then run the remaining calls in this order:

1. `server_info({"check_ha": true})`
2. `list_capabilities({})`
3. `get_server_health({"check_ha": true})`
4. `get_audit_log({"lines": 50, "event": ""})`

Confirm:

- server version is exactly `2.0.0-rc.1` and schema version is `1`;
- `build_sha` is non-`unknown` and is the complete accepted commit referenced by
  `v2.0.0-rc.1`, not an annotated tag object, branch name, or short SHA;
- `build_time` is non-`unknown`, bounded UTC RFC3339 ending in `Z`, and
  corresponds to the shared image publication time, not container startup;
- Home Assistant connectivity is connected and reports safe bounded metadata;
- the catalog contains exactly 38 registered tools, 25 canonical tools, and
  zero planned capabilities;
- `search_entities` reports lifecycle `transitional`, route
  `transitional_direct`, and provider `direct_ha_api`;
- health lists `search_entities` in `approved_direct_read_tools`, while
  `standard_ha_mcp_delegation` remains `unavailable`, exact mapping count
  remains zero, and explicit direct policy remains required;
- authority version is `2`, storage is healthy, and no initialization-only plan,
  challenge, counter, or audit event appeared; and
- health and capabilities contain no new provenance fields.

## 3. Connector compatibility

### Existing Beta 26 connector

1. Keep the existing connector that was reconnected after the in-place beta/RC
   update; do not delete it.
2. Repeat the four foundation calls.
3. Confirm all 38 tools remain callable and their cached argument schemas do not
   produce a mismatch.
4. Recreate the connector only if a normal reconnect cannot clear stale client
   metadata; record that as an RC issue.

### Fresh RC1 connector

1. Create a temporary second connector pointing to the same RC MCP endpoint with
   a separately handled connector configuration.
2. Perform a fresh `tools/list` discovery.
3. Export or compare the discovered tool names, complete input schemas, and
   enums against the accepted Beta 26 discovery.
4. Confirm exact equality: 38 names, schema digest
   `eeec35d49f6d8c59fb1215694e54314b21bb6fd4a723d65e956e8e438699876a`,
   and enum digest
   `465924bf56992b93019184e30b5a322582e9d2789ca670fc3742004e8daa0cfb`.
5. Run `server_info`, `list_capabilities`, and `get_server_health` through the
   fresh connector. Both connectors must agree.

## 4. Representative read tools

Use narrow bounds and real, non-sensitive identifiers. Run each tool once and
confirm it performs no mutation, returns the established response envelope, and
reports honest timing/provider coverage:

Before the general reads, capture provider counters and run these
`search_entities` checks in order:

1. Call
   `search_entities({"query":"garage","domain":"cover","limit":10})`.
   Confirm success, provider `direct_ha_api`, classification
   `transitional_direct`, policy `bounded_entity_state_search`, no
   `provider_unavailable`, exactly one Home Assistant request, and results
   containing only `entity_id`, `state`, and `friendly_name`. Completeness is
   `complete`, or `partial` only when `truncated=true`.
2. Call
   `search_entities({"query":"","domain":"sensor","limit":5})`. Confirm no
   more than five results, deterministic `entity_id` order, `truncated=true`
   when more matches exist, partial completeness only when truncated, and no
   provider failure.
3. Use a unique nonexistent query. Confirm `count=0`, `results=[]`,
   `truncated=false`, and complete coverage.
4. Use an invalid domain or a limit outside 1 through 100. Confirm local
   `invalid_request`, zero Home Assistant requests, and no change to direct
   provider request or failure counters.
5. Re-read health and audit. Confirm successful direct-provider request
   accounting is exact, no Standard HA MCP request occurred, fallback and
   prohibited-fallback attempts remain zero, no timeout or write occurred, and
   audit records bounded intent without entity attributes or secrets.

- `get_entity({"entity_id":"<existing_entity_id>"})`
- `list_areas({})`
- `list_devices({"query":"","limit":20})`
- `list_entity_registry({"query":"","limit":20})`
- `list_automations({})`
- `get_automation_config({"automation_id":"<existing_automation_internal_id>"})`
- `list_automation_traces({"automation_id":"<existing_automation_internal_id>"})`
- `get_automation_trace({"automation_id":"<existing_automation_internal_id>","run_id":"<retained_run_id>"})`
- `get_history({"entity_id":"<existing_entity_id>","hours":1,"minimal":true})`
- `get_logbook({"hours":1,"entity_id":"<existing_entity_id>"})`
- `get_error_log({"tail_lines":20})`
- `search_services({"query":"turn_on","limit":20})`
- `list_services({"domain":"light"})`
- `list_blueprints({"domain":"automation"})`
- `get_blueprint({"path":"<existing_blueprint_path>"})` when a safe blueprint
  is installed; otherwise record `not applicable` rather than inventing a path
- `render_template({"template":"{{ states('<existing_entity_id>') }}"})`
- `check_config({})`

Re-read the acceptance automation and confirm its fingerprint/configuration is
unchanged. Provider errors must not be reported for locally rejected input or
for a tool that did not dispatch.

## 5. Engineering-native tools

Before this section, record the acceptance automation configuration,
governance-plan count, provider-routing counters, and relevant per-analysis
counters. For pagination checks, select inputs known to return at least two
results. A first page may perform bounded provider/HA reads. A continuation must
reuse its signed snapshot and perform no additional provider/HA work. A tampered
cursor must fail locally and also perform no provider/HA work.

For every tool below:

1. run the valid first-page request with `limit:1`;
2. record the returned cursor and the health/provider counters;
3. call the same result-shaping request with that exact cursor and no
   `refresh_index`;
4. confirm the continuation does not increase provider/HA request counters;
5. alter one non-padding cursor character and repeat the call;
6. confirm `invalid_cursor`, no result disclosure, and no new provider/HA work;
7. run the listed local validation failure and confirm `invalid_request` before
   provider/HA work; and
8. confirm no plan was created and no entity, registry, automation, service, or
   Home Assistant configuration changed. Normal bounded audit records are
   expected and are not a Home Assistant mutation.

### `entity_dependency_analysis`

Valid first page:

```json
{"entity_id":"<existing_entity_id>","include_indirect":true,"max_depth":2,"source_types":[],"detail_level":"evidence","limit":1,"refresh_index":true}
```

Continuation: repeat the request with `refresh_index:false` and
`cursor:"<returned_cursor>"`. Local failure:

```json
{"entity_id":"not-an-entity","limit":1}
```

### `automation_reliability_analysis`

Valid first page:

```json
{"automation_id":"<existing_automation_internal_id>","lookback_hours":24,"trace_limit":10,"detail_level":"evidence","limit":1}
```

Continuation: repeat the shaping arguments with
`cursor:"<returned_cursor>"`. Local failure:

```json
{"automation_id":"bad/id","lookback_hours":24,"limit":1}
```

### `change_impact_analysis`

Valid first page:

```json
{"entity_id":"<existing_entity_id>","operation":"disable_entity","replacement_entity_id":"","include_indirect":true,"max_depth":2,"source_types":[],"detail_level":"evidence","limit":1,"refresh_index":true}
```

Continuation: use the same shaping arguments, `refresh_index:false`, and the
returned cursor. Local failure:

```json
{"entity_id":"not-an-entity","operation":"disable_entity","limit":1}
```

### `configuration_integrity_analysis`

Valid first page:

```json
{"source_types":[],"finding_types":[],"include_orphan_candidates":true,"detail_level":"evidence","limit":1,"refresh_index":true}
```

Continuation: use the same shaping arguments, `refresh_index:false`, and the
returned cursor. Local failure:

```json
{"detail_level":"verbose","limit":1}
```

### `incident_correlation`

Valid first page:

```json
{"focus_entity_id":"<existing_entity_id>","automation_id":"<existing_automation_internal_id>","related_entity_ids":[],"lookback_hours":24,"correlation_window_minutes":10,"trace_limit":10,"include_dependency_context":true,"include_integrity_context":true,"include_reliability_context":true,"detail_level":"evidence","limit":1,"refresh_index":true}
```

Continuation: use the same shaping arguments, `refresh_index:false`, and the
returned cursor. Local failure:

```json
{"focus_entity_id":"","automation_id":"","limit":1}
```

### `handoff_generation`

Valid first page:

```json
{"handoff_type":"focused_review","title":"RC1 acceptance","focus_entity_ids":["<existing_entity_id>"],"automation_ids":["<existing_automation_internal_id>"],"change_plan_ids":[],"lookback_hours":24,"include_runtime_health":true,"include_governance_context":true,"include_dependency_context":true,"include_reliability_context":true,"include_integrity_context":true,"include_incident_context":true,"include_recommendations":true,"detail_level":"evidence","output_format":"both","limit":1,"refresh_index":true}
```

Continuation: use the same shaping arguments, `refresh_index:false`, and the
returned cursor. Local failure:

```json
{"handoff_type":"focused_review","focus_entity_ids":[],"automation_ids":[],"change_plan_ids":[],"limit":1}
```

## 6. Governance lifecycle

Use one harmless description-only update. Retain the exact original
configuration for final restoration.

1. Call `create_change_plan` for the description-only update. Confirm the dry
   run identifies only `description`, risk is not high, and no HA write occurs.
2. Call `approve_change_plan` with the exact returned plan hash. Call it again
   with the same hash while the challenge is active. Confirm the same active
   challenge is returned and no duplicate request/grant event is created.
3. Before human approval, call `apply_change_plan` with the exact hash. Confirm
   `external_approval_required`, no HA read/write for execution, and no approval
   granted by the MCP secret.
4. Open the administrator-only Home Assistant Ingress panel. Review the exact
   plan/version/hash/target/operation/risk/diff and approve it there.
5. Call `apply_change_plan` with the exact hash. Confirm description-only write,
   exact identity/configuration readback, configuration check, verification
   `passed`, and single approval consumption.
6. Call `apply_change_plan` again. Confirm `already_applied` and no second write.
7. Call `rollback_change` to request rollback. Confirm a new rollback hash and
   separate external-pending approval; the prior apply grant is unusable.
8. Attempt rollback before approval and confirm fail-closed refusal. Approve the
   rollback in Ingress, then call `rollback_change` with its exact hash. Confirm
   exact original configuration/fingerprint restoration and single-use
   consumption.
9. Create a second description-only plan, request review, and reject it through
   Ingress. Confirm terminal `rejected`, no apply, no reopening, and historical
   visibility only.
10. Create one more harmless pending challenge. Record its ID/state, restart
    only the beta/RC add-on, reconnect, and confirm the same unexpired challenge
    remains pending without grant, replacement, revival, or consumption. Reject
    it through Ingress. Never restart Home Assistant or production.
11. Create a dry-run plan containing a known high-risk action from the existing
    policy (for example a prohibited destructive service against a test-only
    target). Confirm approval/apply is refused as high risk before any write.
    Do not weaken or bypass the policy.
12. Create a valid low-risk plan, change the target externally before apply, and
    confirm stale-state protection refuses the write and consumes no unrelated
    authority.
13. Call legacy `upsert_automation` with a syntactically valid harmless payload.
    Confirm `governance_required` before provider dispatch and no configuration
    payload in audit.
14. Confirm any persisted authority-v1 active fixture/history remains authority
    version 1; terminal history is readable, while executable legacy authority
    fails closed and is never silently upgraded.

## 7. Security checks

1. Verify only a Home Assistant administrator can open the Ingress review UI.
2. Verify direct access, a missing/invalid Ingress path header, or a normal
   non-admin session cannot list or decide reviews.
3. Verify port `8110` is not host mapped, tunnelled, or reachable as an MCP
   route; approval routes are absent from port `8100`.
4. Verify the MCP access secret cannot authorize an approval, mint an approver
   identity, or substitute for CSRF.
5. Verify replaced and expired challenges cannot be viewed as actionable or
   used for a decision, and an apply approval cannot authorize rollback.
6. Inspect bounded `get_audit_log` output for request, refusal, grant,
   consumption, apply, rollback, expiry/replacement, and rejection. Confirm no
   CSRF value, cookie, token, secret, authenticated URL, full config, raw trace,
   raw cursor, Ingress header, Supervisor token, or request note appears.
7. Confirm every locally invalid or tampered request has zero provider/HA work
   and does not inflate provider failure counters.

## 8. Final reconciliation

Run `server_info`, `list_capabilities`, `get_server_health`, and a bounded
`get_audit_log` again. Confirm all of the following:

- exact expected build SHA and UTC build time;
- 38 registered, 25 canonical, zero planned, schema version 1;
- Home Assistant connected and governance storage healthy;
- zero pending challenges;
- zero active applies;
- zero rollback-pending plans;
- zero failed applies;
- zero storage corruption;
- zero audit write failures;
- zero prohibited fallbacks;
- no active acceptance test plan;
- exact automation configuration/fingerprint restored;
- no unintended entity, registry, service, automation, add-on, or Home
  Assistant mutation; and
- production v1.1.2 remains installed, running, and untouched.

If any item fails, stop the soak, retain bounded evidence, make no new approval
decision, and follow the rollback strategy in
[`RC1_RELEASE_NOTES.md`](RC1_RELEASE_NOTES.md).

## 9. Soak recommendation

Soak the accepted RC for at least 48 hours under normal read-only Engineering
usage. At start, 24 hours, and completion, record `server_info`, bounded health,
and bounded audit summaries. Re-run one valid request and one local validation
failure for each Engineering-native tool. Do not preserve cursors across a
restart and do not leave plans/challenges pending. Promote to stable only when
the exit criteria in the RC1 release notes remain satisfied for the entire soak.
