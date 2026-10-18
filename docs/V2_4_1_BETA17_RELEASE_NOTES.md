# Engineering 2.4.1-beta.17 release notes

Engineering 2.4.1-beta.17 makes every Home Assistant REST path safe by
construction (HAMCP-200). The trigger was an `automation_id` that could
traverse from `/api/config/automation/config/<id>` into other Home Assistant
and Supervisor routes. Independent static-scan finding 7 covers path
building and finding 15 the compatibility module's executable entry point.
It builds on beta.16 (HAMCP-162, the optional `ha_config_get_yaml` read) and
beta.15 (HAMCP-189).

No tool, input schema, description, provider route, signed registry, HOLD,
support window, persisted format, workflow or stable-v1 change is included. No
new write, dispatch, service-call or fallback path is reachable. Acceptance:
[`V2_4_1_BETA17_ACCEPTANCE.md`](V2_4_1_BETA17_ACCEPTANCE.md).

**Past exposure (corrected).** Before this change, a crafted identifier could
make Engineering send token-bearing GET requests to other HA/Supervisor
routes, including any GET-enabled webhook (which can trigger automations);
no POST/PUT/DELETE/PATCH was reachable.
- The HTTP method could not change. Three groups of POST builders take a
  variable segment, and each was checked: the governed automation write
  takes its ID from a persisted, hash-approved plan whose ID had already
  passed `^[A-Za-z0-9_-]{1,128}$`; the configuration-resource write is
  called only after the same validation; the approval-notification service
  is operator configuration, validated at construction. The base package
  has no Engineering Home Assistant REST PUT, PATCH or DELETE call.
- A GET is not necessarily read-only. For example,
  `../../../webhook/<webhook_id>` as an automation ID normalized to
  `/core/api/webhook/<webhook_id>`. The Supervisor Core proxy forwards the
  GET, and Core runs the webhook's automation when that webhook trigger
  allows GET. This needed an existing webhook, its ID and a method/network
  policy that allows the request. Other selected routes could disclose
  states, attributes, history, configuration and permitted Supervisor
  information available to the add-on's `homeassistant` role token.
- Whether any deployed webhook allowed GET was not observed; no live access
  was made. See "Checking your webhook triggers" below.

The traversing GET sites were:
- `get_automation_config`, which had no validation;
- `get_history`, where the entity ID was not encoded in the query (query
  injection; it did not change the URL path by itself);
- `automation_reliability_analysis`, whose grammar allowed `..`;
- automation IDs from Home Assistant that handoff and dependency analysis
  forwarded.

**Checking your webhook triggers.** In Home Assistant, a webhook trigger's
allowed methods are set on the trigger itself: open the automation in
**Settings → Automations & scenes**, open the **Webhook** trigger and its
options (the gear or ⋮ next to the webhook ID). **Allowed HTTP methods**
and **Only accessible from the local network** are listed there. In YAML
they are the trigger's `allowed_methods:` and `local_only:` keys. The
defaults are POST and PUT with `local_only: true`, so a trigger allows GET
only when GET was added explicitly. To check, review each automation that
has a webhook trigger, or search `automations.yaml` (and any packages) for
`allowed_methods` lists that include `GET`. Engineering made no live check.

**One typed route (M2).** Every Home Assistant REST request now carries a
`Route` (`clients/paths.py`):
- `rest_path(template, *values)` is the only way to obtain one. The
  template must be one of the reviewed literals in the closed
  `ROUTE_TEMPLATES` registry (CI harness routes are a separate
  `HARNESS_ROUTE_TEMPLATES` registry that no runtime module names). Any
  other template, such as `/states/sensor/extra` or `/webhook/{}`, is
  refused as `unregistered_route`.
- Each value passes the segment guard. It refuses the dot segments `.` and
  `..`; `/`, `\`, NUL and control characters; `?` and `#`; every percent-
  and multiply-encoded form of these; and non-string, empty, oversized or
  undecodable values. Otherwise the value is encoded as one segment with
  `safe=""`.
- A `Route` cannot be constructed, subclassed, mutated, copied into a new
  object or pickled. Only routes `rest_path` issued are accepted; a
  `str.__new__` forgery is not.
- The REST transport accepts nothing else. A plain string, even a fixed
  one such as `"/states"`, and any value derived from a `Route` by a string
  operation, is refused as `unbound_request_path` before any session,
  credential, telemetry authorization or request exists.

Before attaching the token, the transport also:
- re-encodes the route from its bound template and values and refuses any
  difference (`route_mismatch`);
- builds the URL the way the HTTP client parses it, with requoting and
  dot-segment normalization;
- refuses the request unless the normalized URL keeps the configured
  origin, the base path plus exactly the template's segments, the
  template's query, and no fragment or userinfo.

Websockets have no path builder: every connection uses the one configured
`websocket_url`, and identifiers travel in JSON messages.

**Redirects are never followed.** The REST transport, the Core re-admission
reader and the Supervisor self-information reader send requests with
`allow_redirects=False`. In the REST transport a 3xx response is an
`ha_api_error`; the two readers already treat any non-200 status as a
failure. Before this change, the shared REST transport followed redirects.

**Tool-boundary grammars.** `identifiers.py` defines the grammars:
- automation configuration ID: `[A-Za-z0-9_:-]{1,256}`;
- script ID;
- domain;
- service;
- entity ID: the existing bounded grammar.

`get_automation_config`, `get_entity`, `get_history` and
`automation_reliability_analysis` check these before any provider or
session is involved. An invalid value is refused as `invalid_request` with
the message "The identifier is invalid." and details `reason:
invalid_identifier` plus the field name. The refusal is audited as a
failure and counts as an `invalid_request` error. No Home Assistant request
is sent.

Provider paths that take IDs read from Home Assistant behave as follows:
- Handoff generation skips an unsafe automation or entity ID and reports
  partial coverage.
- Dependency analysis, impact analysis, incident correlation and the
  automation-baseline capture refuse through the same guard.
- Governed writes report the refusal as not dispatched, with reason
  `invalid_identifier` and `provider_response_received: false`.

**Narrowed automation-ID grammar (disclosed).** Automation configuration IDs
no longer accept `.`:
- `automation_reliability_analysis` previously accepted dotted IDs other
  than `automation.<object_id>`.
- `capture_automation_baseline` previously percent-encoded any ID.

The Home Assistant frontend creates numeric IDs, and YAML-authored IDs are
usually identifiers. A configuration whose `id:` contains a dot can no
longer be read by these tools. Its `automation.<object_id>` entity is
unaffected.

**Compatibility entry point removed (finding 15).** `tools/compatibility.py`
no longer contains:
- its own ASGI gateway;
- token bucket;
- audit writer;
- `main()` and the `__main__` guard.

That standalone server path bypassed the canonical application. The add-on
still starts `python -m ha_mcp_engineering`. The compatibility tool
functions themselves are unchanged apart from the guard.

**Secondary source scan.** The runtime type check is the control; the scan
in `tests/test_hamcp200_path_safety.py` keeps the source honest. Over the
Engineering package's AST it fails on:
- an f-string, `+` or `%` concatenation, or `.format`, `.format_map`,
  `.join` or `.replace` forming a Home Assistant path, base URL or
  session-request argument;
- a bound method of a route literal (`"/states/{}".format`);
- a route-shaped prefix or template literal outside `rest_path`;
- a `rest_path` template that is not a literal;
- a client `.request` read without a call (an alias, `functools.partial` or
  callback) and `getattr(x, "request")`;
- a websocket connection to anything but the configured URL.

Three exempt sites are named and pinned. The runtime package uses exactly
the `ROUTE_TEMPLATES` registry.

The disposable-Core CI harnesses (`scripts/ha_mcp_850_container_acceptance.py`,
`scripts/real_ha_contract_tests.py`,
`scripts/typed_core_container_contract.py` and
`scripts/alarmo_inspection_contract_acceptance.py`) call the same guarded
transport, so every path there, including fixed ones such as `/config`, now
binds through `rest_path`. The harness scan fails on a plain or built string
path for any receiver, a keyword path, a `.request` alias, a computed
template, or a `state_path`/`rest_path` binder whose body does not return
`rest_path` with a literal template. Before this fix, the 8.5.0
runtime-acceptance harness reported `synthetic_fan_unavailable` because its
fan readiness probe was refused as an unbound path.
