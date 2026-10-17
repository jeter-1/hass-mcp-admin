# Engineering 2.4.1-beta.16 release notes

Engineering 2.4.1-beta.16 makes every Home Assistant REST path safe by
construction (HAMCP-200). The trigger was an `automation_id` that could
traverse from `/api/config/automation/config/<id>` into Supervisor
endpoints. Independent static-scan finding 7 covers path building and
finding 15 the compatibility module's executable entry point. It builds on
beta.15 (HAMCP-189).

No tool, input schema, description, provider route, signed registry, HOLD,
support window, persisted format, workflow or stable-v1 change is included. No
new write, dispatch, service-call or fallback path is reachable. Acceptance:
[`V2_4_1_BETA16_ACCEPTANCE.md`](V2_4_1_BETA16_ACCEPTANCE.md).

**Write-path determination.** Before this change, no Home Assistant write was
reachable by traversal. Three groups of POST builders take a variable segment.
Each was checked:
- The governed automation write takes its ID from a persisted,
  hash-approved plan whose ID had already passed `^[A-Za-z0-9_-]{1,128}$`.
- The configuration-resource write is called only after the same
  validation.
- The approval-notification service is operator configuration, validated
  at construction.

The reachable traversals were GET reads:
- `get_automation_config`, which had no validation;
- `get_history`, where the entity ID was not encoded in the query;
- `automation_reliability_analysis`, whose grammar allowed `..`;
- automation IDs from Home Assistant that handoff and dependency analysis
  forwarded.

**One central path guard.** Every Home Assistant REST request now uses a
fixed source template plus values bound through one helper
(`clients/paths.py`). The helper refuses the following before encoding, so
an encoded or double-encoded form cannot decode into a separator later:
- the dot segments `.` and `..`;
- `/`, `\`, NUL and control characters;
- `?` and `#`;
- every percent-encoded and multiply-encoded form of these;
- non-string, empty, oversized or undecodable values.

Otherwise it encodes the value as one segment with `safe=""`.

Before a session, credential, telemetry authorization or request exists, the
transport does three things:
- re-checks every bound value;
- builds the URL the way the HTTP client parses it, with requoting and
  dot-segment normalization;
- refuses the request unless the normalized URL keeps the configured
  origin, the base path plus exactly the template's segments, the
  template's query, and no fragment or userinfo.

A plain string path is accepted only as a fixed literal route such as
`/states`.

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

**Static enforcement.** `tests/test_hamcp200_path_safety.py` scans the
Engineering package's AST. It fails on these whenever they form a Home
Assistant path, base URL or session-request argument:
- an f-string;
- `+` or `%` concatenation;
- `.format`, `.join` or `.replace`.

Three exempt sites are named and pinned.

The disposable-Core CI harnesses (`scripts/ha_mcp_850_container_acceptance.py`,
`scripts/real_ha_contract_tests.py` and
`scripts/typed_core_container_contract.py`) call the same guarded transport.
Their 21 concatenated or dotted `/states/...` and `/config/.../config/...`
paths now bind through `rest_path`. A second scan in the same test file fails
on any harness REST call whose path the transport would refuse. Before this
fix, the 8.5.0 runtime-acceptance harness reported `synthetic_fan_unavailable`
because its fan readiness probe was refused as an unbound path.
