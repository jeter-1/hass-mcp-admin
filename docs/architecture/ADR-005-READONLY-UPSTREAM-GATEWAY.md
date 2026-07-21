# ADR-005: Reviewed read-only upstream gateway

Status: proposed by the Phase 1 architecture pivot

## Context

The client can expose only one MCP server. The previous sidecar model left the
Engineering server useful for analysis and governance but made ordinary Home
Assistant inspection harder because only two explicit dashboard operations
could use `ha-mcp`. Requiring a second client-visible server is therefore not a
viable product boundary.

Phase 1 makes Engineering the single front door for both its existing tools and
the reviewed, uniformly read-only portion of `ha-mcp`. It deliberately does not
solve write confirmation, persistent-change governance, service execution, or
physical actions.

## Decision

Engineering retains all 40 existing registered tools and adds one generic
`upstream_read_gateway` provider. On startup the provider:

1. initializes the configured `ha-mcp` Streamable HTTP endpoint;
2. verifies server identity, exact built-in reviewed version, MCP protocol, and
   the existing compiled admission contract;
3. retrieves the complete paginated catalog visible in that deployment;
4. canonicalizes and SHA-256 fingerprints every input schema;
5. compares it with the committed policy manifest; and
6. registers only entries classified `automatic_read` whose exact schema still
   matches.

The committed machine-readable inventory is
[`upstream_tool_policy.json`](../../hass_mcp_engineering_beta/ha_mcp_engineering/upstream_tool_policy.json).
It is the complete classification table for the 78-tool stock fixture reviewed
from `ha-mcp` 7.14.1 source tag `v7.14.1`, commit
`255acec1affa6528004a122eb83e30aee9c77713`. The loader requires all entries to
be sorted, unique, bounded, and from that exact source revision. Production
catalog visibility may be a subset or superset of that stock fixture. An
advertised tool is not callable merely because it exists upstream, and exact
stock-catalog equality is informational rather than the gateway admission
boundary.

### Policy schema

The document has schema version 1, exact upstream server/version/source
identity, and a sorted `tools` array. Every entry contains:

- `upstream_name` and intended `exposed_name`;
- bounded description and review reason;
- one of the six classifications below;
- the complete canonical input-schema fingerprint;
- collision status and the fixed alias-on-collision policy;
- explicit argument restrictions, if any;
- response and timeout bounds; and
- exact source/catalog evidence references; and
- binary-owned reviewed MCP annotations (`readOnlyHint`, `destructiveHint`,
  `idempotentHint`, and `openWorldHint`).

Live upstream annotations are untrusted and cannot override the committed
values. Open-world is reviewed per tool rather than assigned uniformly: HACS,
blueprint, dashboard-resource, and overview reads are open-world; local
entity/state/history/device/service reads are closed-world.

The canonical schema fingerprint is SHA-256 over UTF-8 JSON serialized with
sorted keys, compact separators, non-ASCII preservation, and non-finite numbers
disabled.

### Reviewed 7.14.1 classification

| Classification | Count | Generic result |
|---|---:|---|
| `automatic_read` | 26 | An observed exact schema match is dynamically registered. |
| `mixed_or_requires_wrapper` | 14 | Unavailable generically; an explicit constrained wrapper is required. |
| `persistent_write` | 32 | Unavailable. |
| `physical_or_high_risk_action` | 4 | Unavailable. |
| `prohibited` | 1 | Unavailable. |
| `unsupported` | 1 | Unavailable. |
| **Total** | **78** | |

The maximum reviewed generic-read set contains 26 tools. A deployment exposes
only the exact-schema-matching subset that it actually advertises:

- `ha_config_get_automation`, `ha_config_get_calendar_events`,
  `ha_config_get_category`, `ha_config_get_label`, `ha_config_get_scene`, and
  `ha_config_get_script`;
- `ha_config_list_dashboard_resources`, `ha_config_list_groups`, and
  `ha_config_list_helpers`;
- `ha_eval_template`, `ha_get_automation_traces`, `ha_get_blueprint`,
  `ha_get_device`, `ha_get_entity`, `ha_get_entity_exposure`,
  `ha_get_hacs_info`, `ha_get_history`,
  `ha_get_operation_status`, `ha_get_overview`, `ha_get_skill_guide`,
  `ha_get_state`, `ha_get_todo`, and `ha_get_zone`;
- `ha_list_floors_areas`, `ha_list_services`, and `ha_search`.

The policy JSON records every blocked tool and its per-tool reason. Notable
boundaries include:

- mixed `ha_config_get_dashboard` remains excluded because accepted argument
  shapes include screenshot/preference behavior; only the existing
  `list_dashboards` and `get_dashboard_config` wrappers can reach it;
- `ha_call_service` is mixed because its surface includes service execution and
  arbitrary WebSocket commands;
- `ha_get_addon`, `ha_get_integration`, and `ha_get_system_health` require future
  safe response projection because some results can expose administrative or
  secret-bearing material;
- `ha_get_logs` is mixed because unrestricted Home Assistant, Supervisor,
  host, and add-on logs cross an unresolved confidentiality boundary that
  generic recursive redaction cannot prove safe;
- `ha_get_camera_image` is unsupported until binary media envelopes have an
  explicit reviewed response contract; and
- `ha_report_issue` is prohibited because it aggregates sensitive diagnostics.

## Invocation boundary

Every delegated invocation is validated against the exact observed schema that
matched the reviewed fingerprint. The gateway calls only the entry's exact
upstream name, opens one bounded session, performs no semantic retry, sanitizes
the untrusted result recursively, enforces a response-size limit and timeout,
and returns provider/upstream metadata through the Engineering envelope.

Upstream descriptions, annotations, and response content are untrusted.
Engineering publishes its own per-tool reviewed annotations. Returned JSON-RPC-
looking data cannot initiate another tool call or authorize work. Audit records
contain the route, classification, bounded argument field names, timing, and
outcome rather than raw query/template content.

There is no direct Home Assistant fallback, alternate upstream route, generic
name forwarding, or write reachability. Connection, timeout, protocol,
upstream, schema, sanitization, and oversized-response failures are bounded and
classified.

## Names and collisions

An upstream read retains its original name when it does not collide. Existing
Engineering tools always win collisions; the upstream read is exposed as
`ha_mcp__<upstream_name>`. Both the mapping and collision count are reported in
capability and health output. No reviewed 7.14.1 automatic-read name currently
collides with the 40 Engineering tools, but the deterministic behavior is
covered by tests.

## Fail-closed catalog behavior

- Unlisted or newly advertised tools are unavailable.
- A listed tool with a changed schema is unavailable.
- A missing reviewed read is not registered.
- A different upstream version is not admitted merely because schemas happen
  to match.
- Mixed, write, action, prohibited, and unsupported entries never reach the
  generic provider.
- Missing reads, schema drift, unreviewed additions, and deployment-specific
  visibility differences are handled independently per tool; exact stock
  count/fingerprint matching is reported but is not a global gate.
- Identity, exact reviewed version, protocol, or discovery/admission failure
  withholds all dynamic reads; existing
  Engineering-native tools continue to start.

Health and capability output distinguish reviewed policy entries, reviewed
automatic reads, observed advertised tools, exact matched automatic reads,
dynamically exposed tools, collisions, each blocked classification,
missing automatic reads, schema-mismatched automatic reads, and unreviewed
observed tools. It also reports the observed version/catalog fingerprint and
whether the catalog exactly equals the reviewed stock fixture, while making
explicit that advertised does not mean callable.

## Usability effect

Normal tasks such as finding entities, inspecting devices, reading automation
configuration, checking state/history, reviewing traces, listing services,
and inspecting helper/area inventories become one direct tool call through the
same client-visible Engineering server. Engineering analysis and governance
tools remain alongside those reads; users no longer have to choose a sidecar
server for ordinary evidence gathering.

## Deferred Phase 2

Writes remain out of scope. A later, separately reviewed milestone may classify
real write tasks into ordinary-confirmation actions, governed persistent
changes, externally approved high-risk work, and prohibited operations. Mixed
tools need explicit wrappers rather than a universal forwarding or governance
model. Deployment acceptance should first compare the gateway with direct
`ha-mcp` for ordinary read tasks and confirm that the gateway adds no extra
steps.

Raw logs also need a future explicit wrapper, not generic delegation. That
design should require administrator scope, an explicit source allowlist,
bounded line counts, structured projection where available, strict output-size
limits, redaction and sensitive-field removal, and no arbitrary unrestricted
add-on log passthrough.

## Consequences

The binary now depends on `jsonschema` for exact pre-dispatch argument
validation and performs one extra bounded upstream catalog discovery at
startup. Catalog drift fails closed per tool. This phase does not require a
signed registry, production signing ceremony, or PR #49; built-in reviewed
7.14.1 evidence remains sufficient.

CI also starts the immutable reviewed 7.14.1 image, retrieves its complete
paginated catalog over real MCP transport, proves the stock count/fingerprint
and policy schemas, starts Engineering through its real discovery path, and
exercises representative delegated reads over authenticated `tools/list` and
`tools/call`. Production remains subset-based even though that exact-stock
fixture test is intentionally exact.
