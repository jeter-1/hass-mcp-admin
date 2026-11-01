# Beta provider security boundaries

## Source-only Alarmo inspection

The [inspector](INTEGRATION_INSPECTION.md) adds one separately gated read route.
Core metadata applicability and the code-owned Alarmo profile are independent.
The new transport constructs only closed read commands and rechecks request
authority before connection, dispatch and publication. It refuses redirects,
retries, unknown versions, ambiguous targets and stale authority without fallback.
Positive-field selection precedes hashing/caching/audit; user/code/MQTT/action
data and extended registry options never enter retained results. Rejected input
keys/values and exception bodies are not reflected. Cursor authority is bound to
the authenticated caller, never a connector session. No new permission, mount,
secret or listener is introduced. R3 activation and P0b remain later gates.

## 2.1A Beta 2 operational authority

Beta 2 does not reclassify or generically expose `ha_reload_core`,
`ha_manage_addon`, or `ha_restart`. Exact Engineering wrappers construct only
four reviewed reload targets, `action=restart` for the approved installed slug,
or `confirm=true`. All arbitrary service data, provider arguments, other
add-on actions, config-entry reload, and generic service execution are
unreachable.

Each operation requires immutable plan-hash binding, a distinct external
administrator principal, fresh provider and target evidence, transactional
dispatch intent before the action, one attempt, and independent readback.
Ambiguous disruption cannot reopen approval or authorize redispatch. Unknown
release, catalog drift, malformed result, unavailable verification, and
incomplete restart evidence fail closed. No direct Home Assistant or alternate
provider fallback is attempted.

## Dev15 contract-level upstream compatibility

[ADR-006](architecture/ADR-006-CONTRACT-LEVEL-UPSTREAM-COMPATIBILITY.md)
supersedes the exact-version and shared all-or-nothing admission portions of
the historical RC3A, RC2dev9, and RC2dev13 designs below. Their fixed identity,
protocol, tool-name, argument, response, no-write, and no-fallback boundaries
remain applicable.

The generic `upstream_read_gateway` evaluates each reviewed
`automatic_read` contract independently. The exact input-schema fingerprint,
exact domain-separated fingerprint of the complete bounded runtime
description, exact runtime safety-annotation presence/value fingerprint,
output-schema
presence/fingerprint, and other dispatch-relevant projections are required.
The 26 description and annotation fingerprints are captured from the pinned
image's real `tools/list` only after its exact stock-catalog fingerprint
matches. Runtime
descriptions are limited to 8,192 strict UTF-8 bytes, remain untrusted, and are
never published as instructions; the Engineering-owned bounded summary is the
only model-facing description. Any byte or code-point drift anywhere in the
full runtime description quarantines that tool. A changed contract is
quarantined and a missing contract is removed; neither condition disables
other exact matches. New tools and every reviewed mixed, write, action,
prohibited, or unsupported entry remain unavailable.

The annotation fingerprint records presence as well as Boolean value for each
MCP safety hint. An omitted optional hint is not normalized to `false` or
`true`; adding, removing, or changing a hint is contract drift. Runtime
annotations must still identify the tool as read-only and not destructive.
This upstream evidence is separate from the complete Engineering-owned safety
annotations published to clients.

Every reviewed read must also retain the pinned generic object output schema,
matched by an exact per-tool fingerprint. Missing, invalid, or changed output
schemas quarantine only the affected read. The generic schema cannot expand
the fixed sanitizer, response bound, fallback prohibition, or behavior adapter.

Live `ha-mcp` version data is evidence, not self-authorizing authority. The
generic gateway first requires an explicit reviewed release entry. RC1-dev2
contains separate exact entries for 7.14.1 and 7.14.2; a patch, minor, major,
prerelease, or downgrade without reviewed authority remains unavailable even
when its self-advertised contracts match. Health reports the observed MCP
identity, version, protocol, tool count, and catalog separately from reviewed
source/image evidence. MCP discovery does not observe the running container's
source commit, index digest, platform digest, or OCI revision, so those fields
are explicitly reported as unobserved. Deployment and exact-image CI must
verify artifact provenance independently. Neither reviewed evidence nor live
observation alone can admit a tool: the exact release entry and per-tool live
contract must both match. Unreviewed additions remain unavailable without
reducing otherwise exact per-tool matches.

Immediately before `tools/call`, Engineering re-lists the catalog in the same
MCP session and revalidates exact release/profile authority plus the selected
target against the current-generation contract. Missing, duplicate, changed
selected-target, or unreviewed-version evidence stops before dispatch. A
different server, malformed version, or unsupported protocol likewise remains
global fail-closed. Unrelated malformed or duplicate unreviewed descriptors
remain unavailable and are bounded anomalies rather than authority for, or a
block on, an exact authorized target call.

Calls acquire immutable route snapshots under short leases and do not hold a
global registry lock across network I/O. A route retired before pre-dispatch
validation cannot call upstream. A call already committed after successful
validation may finish, but cannot republish or revive a retired generation.

Dashboard admission is independent from generic-read admission.
`ha_config_get_dashboard` remains mixed and cannot enter the generic route.
The dashboard provider still permits only its two fixed non-screenshot shapes
and exact response/hash contract. It first requires an exact-version built-in
or verified signed attestation. Missing exact authority, mismatch, or
revocation blocks that release with no older-release or self-advertised
compatible-variant fallback. Expired exact entries remain deny-only and
registry unavailability cannot revive older evidence.

Fast bounded startup recovery is reserved for transport readiness. A stable
missing or quarantined contract uses a separately reported slow compatibility
reprobe cadence while the safely admitted subset remains available. Neither
lane retries a delegated operation semantically. Bounded health may report tool
names and stable incompatibility reasons, but never raw schemas, remote prose,
registry material, endpoint data, credentials, or exceptions.

For a reviewed upstream version transition, call-time validation fails closed
until the periodic compatibility reprobe publishes the newly selected exact
release. That can take up to approximately 15 minutes; restarting Engineering
forces immediate rediscovery. Dashboard and delegated-read providers may
temporarily observe different versions, and delegated reads may be unavailable.
No fallback or write authority is introduced during this interval.

When upstream is configured, the initial catalog is a readiness boundary:
authenticated MCP traffic returns HTTP 503 until the first stable or terminal
`reconcile_until_initialized` result. `/ready` exposes only bounded booleans and
`status=ready|initial_reconciliation_pending`; `/health` remains liveness. This
prevents a schema-caching client from treating the transient static-only catalog
as the initialized capability set.

## RC2dev13 reviewed read-gateway recovery

This section records the RC2dev13 recovery design; ADR-006 supersedes its exact
version and single-cadence rules. The generic `upstream_read_gateway` is
separate from the unavailable legacy
`standard_ha_mcp` route. One supervised, single-flight reconciliation loop
retries only the configured fixed upstream endpoint. Every attempt revalidates
exact server identity, version, protocol, reviewed policy, and input schemas;
no failed or partial attempt can activate an unreviewed tool, write, arbitrary
forward, or direct-HA fallback. The 40 statically registered Engineering tools
remain available while exact 26-read admission is incomplete.

`ha_search` completeness is taken only from its sanitized top-level exact
boolean. Missing or malformed completeness fails closed as partial. Upstream
diagnostic prose never becomes Engineering-authored guidance. The retry state
is bounded and secret-free. Stateless clients must re-list or reconnect after
recovery; no tool-list change notification is claimed.

## RC2dev9 signed upstream admission

RC2dev9 supersedes the single-version wording below with a signed exact-release
contract-family gate. The optional compatibility registry is a signed data
channel, not delegated authority. Ed25519 signatures authenticate release
evidence; the Engineering binary still owns the sole compiled family, one-tool
allowlist, fixed dashboard-read argument builders, response/hash validators and
no-fallback policy. Even valid registry data cannot activate an unknown family,
`ha_set_entity`, `ha_set_device`, service/batch execution, screenshots,
preferences, or a dashboard-write path.

The registry uses fixed repository-owned HTTPS locations, strict JSON and field
bounds, signature/key-ID verification, sequence rollback/replay rejection,
expiry/revocation, bounded refresh, and an atomic last-known-good cache. The
public key is a non-secret operator option; the private seed exists only in a
protected GitHub environment and is scoped to the signing step. Health, logs,
audit, errors and MCP responses expose no endpoint, registry body, signature,
public-key value, cache path or raw exception. See
[`UPSTREAM_TRUST_REGISTRY.md`](UPSTREAM_TRUST_REGISTRY.md).

## RC2dev10 upstream dashboard boundary

`upstream_dashboard` is separate from the unavailable generic
`standard_ha_mcp` gateway. It accepts one password-style, operator-configured
secret endpoint and exposes exactly one allowlisted upstream tool:
`ha_config_get_dashboard`.

The historical RC3A `contract_read_only` mode required `readOnlyHint=true`; its
deprecated single-release profile ID was `ha_mcp_7_13_dashboard_read_v1`.
RC2dev9 replaced that active profile with compiled family
`ha_mcp_dashboard_read_v2`. RC2dev10 continues to require server name `ha-mcp`,
protocol `2025-03-26`, exact tool name, exact reviewed annotations and one exact
reviewed release attestation. Built-in entries cover 7.13.0, 7.14.0, 7.14.1,
and 7.14.2;
a verified signed registry may supply another exact release for the already
compiled family. No version range is admitted.

Raw schema and complete runtime descriptors are retained as observability
evidence, not dispatch gates. Their expected values come from the selected
release attestation. Normalized input/security/output/runtime fingerprints
remain the admission gate and are never substituted into legacy fields. The
published 7.13.0 runtime adds
`_meta.ha_mcp.llm_api_exposed` and `_meta.ha_mcp.pinned`; those values only
control upstream conversation-agent exposure and pinning. Along with display
title/description, annotation display title, and FastMCP grouping tags, they
are the complete closed exclusion list for the security projection. Every
input-schema field, safety-hint presence/value, output-schema change, unknown
annotation, unknown metadata field, and unknown top-level field remains
blocking. A descriptive-only raw descriptor mismatch is reported in health;
semantic or security drift makes the provider unavailable.

The 7.13.0 tool is mixed-operation: screenshots can persist rendering
preferences. Engineering therefore constructs only the reviewed non-screenshot
forms. Inventory sends `list_only=true` and `include_screenshot=false`. Exact
configuration reads send a validated canonical path, `list_only=false`, a
Boolean `force_reload`, and `include_screenshot=false`. Any other key or value
fails before network dispatch. The transport repeats the shape check directly
before `tools/call`.

Set/delete dashboard, backup, service, reload, automation write,
physical-action, screenshots, view selection, rendering preferences, and
arbitrary tool names remain unreachable. The provider does not describe the
whole upstream tool as read-only.

Raw contract fixtures, schemas, and endpoint material never appear in health.
Full URL, host, port, secret path, query, credentials, and reconstructed
fragments are excluded from logs, audit, responses, errors, tracebacks, and
startup output. Upstream dashboard titles, card text, configuration, and
warnings are untrusted data and cannot authorize or construct another tool
call.

HTTP 401/403 failures report `authentication_failed`; HTTP 404 reports the
neutral `endpoint_rejected` category because the client cannot distinguish an
incorrect secret path from an absent endpoint. Connection refusal and DNS or
route failures report `connection_failed`; genuine deadline expiry reports
`timeout`. These categories never include raw endpoint or exception text.

The provider adds no Supervisor permission and performs no discovery. Existing
direct-HA policies, governance, and external approval remain unchanged.
Historical v1.1.2 source is operationally retired and outside this provider
contract. See
[`ADR-003`](architecture/ADR-003-REVIEWED-ARGUMENT-CONSTRAINED-DASHBOARD-READS.md)
and [`RC3A_RELEASE_NOTES.md`](RC3A_RELEASE_NOTES.md).

## RC2 frozen security boundary

RC2 changes no approval authority, listener, write policy, redaction rule, or
Home Assistant behavior. Its only provider/direct-policy correction is the
bounded read-only `search_entities` route documented below. Build
provenance is limited to a validated complete Git commit and UTC RFC3339 build
time supplied by the image pipeline. Invalid/unbounded values fail closed to
`unknown`; no runtime repository lookup or credential is used.

## Beta 25 principal separation

An authenticated MCP caller can create a plan and request external review but
cannot approve. The MCP secret authorizes neither the internal Ingress listener
nor a human decision. Approval routes exist only on unmapped internal port
`8110`, after Home Assistant Ingress/admin enforcement and an application check
of the documented Ingress peer/path. Decisions are POST-only and one-time-CSRF
protected. All review text is bounded, sanitized and HTML escaped.

Authority version 2 binds plan/version/hash/kind/target/operation/risk. Apply and
rollback require separate external approvals and retain stale-state protection;
rejection is terminal; legacy caller approvals are not migrated. No log,
recommendation, request note, challenge identifier or forwarded header is an
approval credential. See [`EXTERNAL_APPROVAL.md`](EXTERNAL_APPROVAL.md).

Since HAMCP-184 (2.4.1-beta.12) `principal_separation_enforced` is `true` only
for a decision by an Ingress-identified administrator. A decision under the
anonymous fallback principal (no `X-Remote-User-Id`) reports `false` with reason
`anonymous_fallback_principal`. Since HAMCP-205 such a stored approval is
ineligible: apply and rollback refuse it with `external_approval_required` and
reason `anonymous_approval_requires_reapproval`, and governance records no new
anonymous decision.

## Beta 24 ingress and execution hardening

The authenticated beta gateway uses the canonical direct socket peer as its
client identity. `cf-connecting-ip` is ignored unless
`trust_cf_connecting_ip=true`, the direct peer belongs to one of at most 64
validated `trusted_proxy_cidrs`, and the forwarded value is one valid IPv4 or
IPv6 address. Malformed or untrusted forwarding falls back to the direct peer;
no raw address is emitted in health or audit output. Nabu Casa and tunnel users
should leave forwarding trust disabled until the actual proxy network is known.

Direct Home Assistant access is fail-closed: membership in an exception set is
insufficient without a matching explicit read policy and capability. There is no
direct-write policy for `upsert_automation`; it remains schema-visible but is
refused before dispatch. `call_service`, `delete_automation`, and
`reload_domain` also fail closed on this server. A Boolean confirmation is not
approval of an immutable plan, and evidence or recommendations never authorize a
write.

RC2dev8 enforces the four fixed legacy policy outcomes at the authenticated
Streamable HTTP routing boundary, before FastMCP/Pydantic argument coercion.
Only the exact names `call_service`, `reload_domain`, `upsert_automation`, and
`delete_automation` are eligible. The caller's arguments are neither forwarded
nor used to select the result; malformed arguments therefore cannot bypass or
preempt policy. Normal tools retain full schema validation. Refusal responses
contain no caller payload, and refusal audit records retain only bounded target
identifiers or the fixed reason.

Rate-limit bucket pressure evicts a single least-recently-used identity rather
than clearing all throttling state. See [`RATE_LIMITING.md`](RATE_LIMITING.md).
Audit reads and refusal records are bounded as documented in
[`AUDIT_LOG.md`](AUDIT_LOG.md).

## Beta 23 pre-provider rejection

Authentication and rate-limit checks run before provider dispatch. Rejected
requests cannot increment provider request, success, or failure counters and do
not acquire HA/provider evidence. Semantic validation and signed-cursor checks
follow the same rule. Audit retains only bounded intent/error data and never turns
the selected route, untrusted evidence, logs, or errors into proof of dispatch or
authorization.

RC2dev6 distinguishes authentication-failure limiter exhaustion from ordinary
authentication rejection in the audit trail without changing enforcement.
Ordinary failures remain 404/`authentication_failure`/`auth_failure`; the first
attempt beyond the authentication-failure burst remains
429/`rate_limit_exceeded` but is recorded as `auth_failure_throttled`.
Authenticated general limiting remains `rate_limited`. None of these rejected
requests reaches MCP dispatch, provider selection, Home Assistant, the dashboard
provider, or fallback, and no credential-bearing request material enters audit
or structured logs.

RC2dev7 protects the integrity of that evidence by parsing audit JSONL and
matching only the exact top-level `event`. A self-audited `get_audit_log` call
may record its requested filter inside bounded parameters, but that nested value
cannot be returned as a security event. Malformed and oversized historical
records are skipped without emitting raw content. Authentication enforcement,
limiter policy, caller identity, response codes, and pre-dispatch barriers do
not change.

RC2dev8 additionally treats MCP application results as the source of audit
truth. A JSON-RPC result with `isError=true`, a bounded validation failure, or a
structured Engineering response with `success=false` is audited as failure
with the stable error code even when HTTP transport returned 200. Validation
audit retains bounded argument field names, never rejected values or raw
Pydantic exception text.

## Beta 22 generated documentation

`bounded_handoff_generation_read` permits only bounded internal evidence reads.
It cannot call a service, create/approve/apply/rollback a plan, write
configuration, reload, restart, or perform a physical action. Recommendations
carry authorization requirements but never grant them. Logs, traces, titles and
evidence are untrusted data. Raw cursors, Markdown, configuration, traces, logs,
history, diffs, tokens, secrets and authenticated URLs are excluded from audit.
Shared evidence normalization never copies raw source payloads, warnings, or
authenticated paths. Automation scope resolution uses only the approved bounded
state/configuration reads and exposes canonical entity IDs, not credentials.
Historical governance records cannot grant or revive authorization.

## Capability truth

Provider labels describe the transport actually used. A direct Home Assistant REST or
WebSocket call is always labeled `direct_ha_api`; it is never relabeled as
`standard_ha_mcp`. Approximate upstream tool matching is prohibited.

RC2 permits five narrowly scoped administrative reads. The fifth is the
release-blocking entity-search correction; the original four remain unchanged:

| Tool | Direct policy | Allowed operation |
| --- | --- | --- |
| `search_entities` | `bounded_entity_state_search` | One `GET /states` inventory with validated filters and bounded slim output |
| `get_entity` | `exact_entity_state_read` | `GET /states/{entity_id}` |
| `list_areas` | `complete_area_registry_read` | `config/area_registry/list` WebSocket command |
| `search_services` | `bounded_service_catalog_search` | Bounded `GET /services` search |
| `list_services` | `bounded_service_schema_read` | Bounded `GET /services` schema enumeration |

`search_entities` validates its exact domain and 1-through-100 limit before
dispatch, matches only entity ID and friendly name, and returns only entity ID,
state, and friendly name. Additional matches set `truncated=true` and partial
completeness. Standard HA MCP remains unavailable, and this explicit direct
policy is not a fallback.

These policies do not authorize calls to services, automation writes, deletion,
reloads, restarts, physical actions, or destructive operations. Governed configuration
changes retain their existing plan, approval, verification, rollback, correlation, and
audit requirements.

The pre-existing transitional `get_error_log` exception remains explicit in Beta 12:

| Tool | Direct policy | Allowed operation |
| --- | --- | --- |
| `get_error_log` | `structured_system_log_read` | Admin-only `system_log/list` WebSocket read |

This is not a general log or Supervisor permission. The server does not request broad
Supervisor log access, scrape frontend output, or read the host journal. Returned
System Log fields are untrusted data: the complete recursive upstream result is
sanitized before selection, bounding, normalization, formatting, or serialization.
Content is never executed or interpreted as instructions and never constructs an
endpoint, tool call, service call, or action.

## Beta 12 analytical read policy

`automation_reliability_analysis` uses the engineering-native
`single_automation_reliability_read` policy. Its facilitator provider may compose one
automation configuration/state, one blueprint, bounded traces, deduplicated referenced
entity state, filtered entity-registry metadata, and sanitized correlated System Log
entries. Every underlying Home Assistant source is labeled `direct_ha_api`; no result
claims Standard HA MCP coverage or fallback.

The policy grants no write, service-call, trigger, change-plan, approval, reload,
restart, deletion, physical-action, or destructive permission. The handler cannot call
transport clients directly. Audit records contain input bounds and the automation ID,
not configuration, trace, log, finding, or evidence payloads.

## Standard Home Assistant MCP

Home Assistant documents a stateless Streamable HTTP MCP endpoint at `/api/mcp`. From
an add-on it is available through the fixed Supervisor Core API proxy at
`http://supervisor/core/api/mcp`, authenticated by the add-on's Supervisor bearer token.
The selected Assist API does not expose exact entity-ID lookup, complete area-registry
enumeration, or service-catalog discovery. `GetLiveContext` is therefore not used as a
substitute. Beta 12 retains the gateway abstraction but does not configure or call the
upstream endpoint.

Any future live delegation requires an exact or explicitly reviewed loss-tolerant
contract, fixed destination validation, bounded timeouts, redacted authentication, and
schema-verified upstream discovery.

## Secret handling

Access secrets, Supervisor tokens, authorization headers, authenticated MCP paths,
session identifiers, and raw upstream error bodies are excluded from tool results,
health output, provider metadata, logs, audit records, fixtures, and documentation.
Complete authenticated paths must be redacted before diagnostics are shared.

## Central fail-closed sanitizer

All System Log-derived mappings, lists, tuples, string leaves, exception text,
tracebacks, Python representations, JSON-like text, URLs, multiline messages, and
unknown future fields use the same recursive sanitizer used by beta logging and audit
contexts. Key-aware rules run before free-text scanning. Redaction occurs before any
truncation, ensuring a truncated response cannot reveal a prefix or suffix.

Stable markers identify only the category:

- `[REDACTED:token]` for bearer/access/refresh/long-lived/API/client secrets;
- `[REDACTED:auth_cookie]` and `[REDACTED:password]`;
- `[REDACTED:webhook_secret]` and `[REDACTED:auth_flow]`;
- `[REDACTED:matter_setup_code]` and `[REDACTED:matter_setup_payload]`;
- `[REDACTED:url_credentials]`; and
- `[REDACTED:sanitization_failure]` when a field cannot be processed safely.

Markers disclose no original length, fragment, hash, prefix, suffix, character set, or
reversible encoding. Existing markers are preserved unchanged. Sanitization is
deterministic and idempotent. If one field raises during sanitation, that field is
replaced and a safe warning is reported; raw content is never used as a fallback.

When overlapping key-aware and free-text detection identifies the same Matter setup
payload, adjacent identical markers collapse to one stable marker. This does not skip
either detection pass and remains idempotent.

Useful diagnostics such as entity IDs, integration/logger names, filenames and line
numbers, timestamps, occurrence counts, ordinary error codes, device names, private IP
addresses, and non-authentication HA context IDs remain available. A generic field
named `code` is not redacted; only verified credential contexts such as `setup_code`
or `authorization_code` are sensitive.

Output telemetry is limited to `redaction_applied`, a redacted-field count, unique
bounded category names, and fail-closed state. It contains no original value or
one-to-one identifier.

## Beta 13 reliability correlation boundary

Reliability configuration, trace details, System Log records, friendly names, and
exception text are inert untrusted evidence. Complete trace detail and complete System
Log results are recursively sanitized before normalization, matching, hashing,
selection, or serialization. Correlation accepts exact bounded identifiers or an
independently matching service/error signature; it rejects friendly-name-only,
time-only, substring, and generic executor matches. Root-cause IDs are derived from
sanitized normalized semantics. Health and audit output contain aggregate counts and
timing only—never automation configurations, traces, logs, findings, evidence
summaries, normalized error text, or evidence fingerprints.

The `single_automation_reliability_read` policy remains read-only. Beta 13 adds no
service execution, trigger, write, approval, reload, restart, fallback, Supervisor
permission, or Standard HA MCP success path.

Beta 14 applies the recursive sanitizer to the complete trace-list result before
selection, timestamp parsing, run-ID deduplication, hashing, truncation, formatting, or
serialization. Trace detail follows the same fail-closed boundary. Pagination stores
only already-sanitized public findings, references, coverage, and metadata in a bounded
short-lived process snapshot; it never stores raw configuration, trace bodies, logs,
entity state values, or normalized error content for audit.

## Beta 15 change-impact boundary

`single_entity_change_impact_read` permits only exact state/registry reads and bounded
evidence collectors already approved for dependency, trace, and System Log analysis.
The handler is transport-independent, fallback is prohibited, and the capability
cannot call services, mutate registries/configuration, create plans, request approval,
reload, or restart. The dependency index is reused rather than copied.

Every Home Assistant-derived field is sanitized before selection, correlation,
hashing, truncation, formatting, or serialization. A sanitation failure cannot become
clean coverage. Audit stores only bounded validated identifiers, operation, outcome,
counts, coverage state, and timing; it excludes state values, findings, evidence,
paths, trace/log/configuration content, cursors, and error text. Health contains only
identity-free cumulative aggregates. See
[`CHANGE_IMPACT_ANALYSIS.md`](CHANGE_IMPACT_ANALYSIS.md) for the full contract.

## Beta 17 configuration-integrity boundary

`global_configuration_integrity_read` permits the shared dependency-index read,
one current-state inventory, and one entity-registry inventory. It permits no
service, entity-registry update, automation write, governance operation, reload,
restart, automatic cleanup, or fallback. All source evidence is sanitized before
classification and output; cursor material is represented in audit only as a
Boolean presence flag. Templates and source text are untrusted inert evidence,
never instructions.

Beta 18 moves entity classification to a context-plus-validation boundary in the
shared dependency extractor. Only explicit entity-bearing fields and literal
arguments to the documented Home Assistant template helpers can create exact
edges. Template comments, quoted prose, arbitrary member expressions, services,
network identifiers, and dotted diagnostics remain inert. Recognized dynamic
arguments produce target-free limited-confidence evidence. The bounded scanner
does not execute templates, return rejected tokens, or place raw template content
in health or audit records.

## Beta 19 incident-correlation boundary

`bounded_incident_correlation_read` permits only bounded reads of current state,
entity registry, history, logbook, automation configuration/traces, structured
System Log, and the shared dependency/integrity/reliability services. It caps
related entities at 20, lookback at 168 hours, traces at 50, System Log inspection
at 200 entries, retained events/evidence at 1,000 each, and concurrent HA reads at
five. No fallback or write-capable provider is permitted.

Logs, traces, templates, history, and evidence summaries are untrusted inert data:
they cannot authorize another call or operation. Sanitization occurs before
normalization, correlation, output, or audit. Audit stores only bounded intent,
counts, result, and cursor-presence Boolean; it excludes raw cursor material,
configuration, trace/history/log content, evidence text, authentication data, and
secrets. Dynamic expressions stay targetless, text-only matches cannot produce
high confidence, and contradiction lowers confidence rather than being omitted.

Beta 20 changes only coverage truth. Warnings and unsupported-source limits remain
bounded, deduplicated, non-secret, and non-instructional; they cannot authorize a
provider call or be promoted into a failure automatically. Audit stores bounded
counts for source failures and coverage limitations, not full warnings. The same
read-only policy, provider allowlist, redaction boundary, and prohibition on
service, entity, registry, automation, governance, reload, restart, remediation,
and background-monitoring actions remain in force.

## RC2dev4 hardening boundary

Compatibility schemas for legacy writes remain visible, but their handler
bodies cannot reach Home Assistant. `upsert_automation` is a governed redirect;
deletion is prohibited; service and reload operations require an unavailable
Standard HA MCP provider; all have no fallback. External approval is separate
from chat authorization and remains hash-bound, single-use, expiring, and tied
to a distinct Ingress administrator. Pre-approval principal separation is
reported as not evaluated, never as an enforcement failure.

Relayed Home Assistant logs and structured application logs pass through the
same recursive fail-closed sanitizer. Credential-bearing keys, authorization
headers, setup payloads, authentication flows, webhook paths, signed URLs,
query credentials, exception text, and multiline tracebacks are redacted before
output or logging. Dashboard/log/YAML text is inert evidence and cannot authorize
tool dispatch.

RC2dev5 treats Home Assistant webhook IDs as sensitive identifiers because they
can identify or participate in credential-bearing callback routes. Webhook URLs,
keyed IDs, and narrowly recognized prose forms are replaced with
`[REDACTED:webhook_identifier]` while surrounding diagnostic context is kept.
The sanitizer deliberately does not redact unrelated hexadecimal values such as
Git commit SHAs. Sanitizer failure remains fail closed, and the original payload
is never included in an exception log.

## MCP SDK and dependency boundary

Engineering version `2.0.1` pins the stable MCP 1.x SDK at 1.28.1.
Engineering 2.4.1-beta.11 moves that pin to 1.30.0 for GHSA-84m7-p3x7-pcfv
(CVE-2026-59951); the adapter still admits exactly one reviewed version.
Every required contact with the SDK's private FastMCP tool registry is isolated
in `mcp_sdk_compatibility.py`. The adapter admits only the reviewed SDK version
and expected mapping shape, returns read-only mapping snapshots containing
shared `Tool` objects, validates complete replacement candidates, restores the
previous registry if post-assignment verification fails, and fails startup
with bounded Engineering-owned wording when the contract is unavailable.
Exact SDK version resolution is cached once per process. There is no alternate
registration path or fallback.

The SDK compatibility boundary also initializes outbound upstream sessions
with reviewed protocol `2025-03-26`. A newer SDK default cannot silently
broaden admission beyond the compiled exact `ha-mcp` 7.14.1 and 7.14.2
release entries; a different returned protocol remains an
incompatible-upstream failure.

Each reviewed read-release entry is bound to a canonical committed capture by
repository-relative resource name and SHA-256 digest. Repository validation
recomputes the catalog count/fingerprint, error-shape fingerprint, and every
per-tool input, description, annotation, output, and runtime fingerprint from
that capture, then joins those facts with the separately human-owned policy.
Generated candidates never approve policy. The same validation cross-checks
each dashboard reference against the authoritative built-in attestation and a
fingerprint of the compiled non-screenshot argument constraints. CI derives its
exact-image matrix from this registry and independently verifies the resolved
index digest, platform manifests, OCI version, and OCI revision before starting
the image. A later live deployment must perform the equivalent provenance
verification outside MCP; the Engineering process cannot claim it observed
container provenance through tool discovery.

The upgrade moves the Engineering runtime beyond the affected package-version
ranges for CVE-2025-53366, CVE-2025-53365, CVE-2026-52869, and
CVE-2026-59950. Directly used `aiohttp`, `starlette`, `uvicorn`, and
`cryptography` pins were reviewed and advanced with the SDK. Pull-request
validation runs pinned `pip-audit` against the Engineering runtime
requirements, fails on dependency resolution or an applicable known
vulnerability, and is reused by the protected promotion workflow. No advisory
exceptions are configured.

The `2.2.0-beta.13` dependency-security release advances only the Engineering
runtime to `aiohttp==3.14.3` and `cryptography==50.0.0`. These are the lowest
stable releases that jointly resolve CVE-2026-69244, CVE-2026-69247,
CVE-2026-69248, and CVE-2026-69249; `cryptography==49.0.0` still remains
affected by CVE-2026-69247. The project uses `aiohttp` for bounded REST,
WebSocket, Supervisor, and registry HTTP clients and uses `cryptography` for
fail-closed Ed25519 registry verification. It does not invoke the reported
PKCS#7 decryption or X.509 verification APIs, but strict auditing is retained
without an exception because released fixes are available. Python 3.12 and
published glibc wheels remain available for amd64, arm64, and arm/v7; CI still
performs the authoritative multiarchitecture build. Stable v1.1.2 is unchanged.

The `2.4.1-beta.53` slice (HAMCP-071) adds a governed edit of one existing
UI-managed scene, **unreachable at release**. It switches on with the
sequence-16 signing. It adds no permission, add-on option, role, fallback
or delegated provider.

- **New write path.** `POST /api/config/scene/config/{config_id}`. The body
  is the full proposed configuration without `id`, and only `name`, `icon`
  and `entities` may differ from the stored configuration. The write is
  reachable only through an externally approved plan dispatched by the F3
  runtime, for the Core capability profile
  `core.scene_configuration_operation`, admitted by a signed registry.
  Signed sequence 14 does not reference that profile.
  - The tool and `create_configuration_plan` refuse
    `pending_signed_registry_admission` before any read or storage, and F3
    dispatch admission is checked again.
  - The send boundary refuses a complete, valid command before sending it,
    unless Core currently admits the profile
    (`pending_signed_registry_admission`) and the call runs inside the F3
    dispatch window (`active_f3_execution_required`).
  - A legacy apply without F3 refuses `core_authority_unavailable`.
- **Closed vocabulary.** The route allowlist admits the scene-config route
  for GET and POST only, so DELETE is unreachable. The only websocket reads
  are `config/entity_registry/list` and `get`. The profile's never-sent list
  names the scene-config DELETE, `scene.apply`, `scene.create`,
  `scene.delete`, `scene.reload` and `scene.turn_on`. A save never
  activates a scene. Core's own post-write hook reloads every scene, and
  the plan discloses that.
- **Target.** One existing scene: registry platform `homeassistant`, its
  `unique_id` equal to the config id, and a GET hit. Integration scenes,
  YAML scenes outside `scenes.yaml`, and ambiguous, disabled or
  unregistered targets are refused while planning. Because Core's POST is an upsert,
  the scene is re-read immediately before the write, and a missing scene
  is refused as stale, never created.
- **Approval and verification.**
  - A protected member (OD-4 `object_protection`) before or after the edit
    makes the plan `elevated_admin`, with physical consequence `indirect`.
    Otherwise it is `standard_admin`.
  - A multi-operation plan containing a scene operation, or an incomplete
    operation, is `PROHIBITED`.
  - Drift is refused immediately before the write, compared type-exactly.
  - The exact readback must show the stored configuration as proposed and
    the registry identity unchanged. Engineering verifies the saved scene
    and its registry identity; it does not verify the scene Home Assistant
    has loaded (`loaded_state: "not_verified"`, disclosed on every
    result).
  - An indeterminate response is decided by readback. It is never
    redispatched.
- **Untrusted text.** Planning screens scene names and stored text.
  Credential-shaped text is refused, and the plan title never repeats a
  scene name.

The `2.4.1-beta.50` slice (HAMCP-106) adds governed storage-dashboard
metadata, **unreachable at release**. It switches on with the sequence-15
signing. It adds no permission, add-on option, role, fallback or delegated
provider.

- **New write path.** `lovelace/dashboards/update` with `dashboard_id` and
  only the requested fields among `title`, `icon`, `show_in_sidebar` and
  `require_admin`. The `dashboard_id` is taken from the one listed row that
  matches the `url_path`. The write is reachable only through an externally
  approved plan dispatched by the F3 runtime, for the Core capability
  profile `core.dashboard_metadata_operation`, admitted by a signed
  registry. Signed sequence 14 does not reference that profile. The tool and
  `create_configuration_plan` refuse `pending_signed_registry_admission`
  before any read or storage, and F3 dispatch admission is checked again.
  The generic resource-write send boundary refuses a complete, valid
  command before sending it unless Core currently admits the profile
  (`pending_signed_registry_admission`) and the call runs inside the F3
  dispatch window (`active_f3_execution_required`). A legacy apply without
  F3 refuses `core_authority_unavailable`.
- **Closed vocabulary.** The gateway sends only `lovelace/dashboards/list`
  and `lovelace/dashboards/update`, and raises on anything else before I/O.
  The profile's never-sent list includes `lovelace/config/save` and
  `delete`, dashboard create and delete, the resource commands,
  `frontend/update_panel` and `frontend/set_user_data`. Dashboard content is
  read only through the existing admitted `get_dashboard_config` read, to
  fingerprint it.
- **Target.** One existing storage dashboard. `"lovelace"`, YAML
  dashboards, unknown and ambiguous targets are refused while planning.
  Core's update has no create path.
- **Approval and verification.**
  - All four fields are `standard_admin` (owner decision), physical
    consequence none, with fixed visibility disclosures. Lowering
    `require_admin` is disclosed as widening visibility only, not device
    control.
  - A multi-operation plan containing a dashboard-metadata operation, or an
    incomplete operation, is `PROHIBITED`.
  - Drift of the metadata row or the content fingerprint is refused
    immediately before the write.
  - The exact readback must show the requested fields as requested, the
    unrequested fields unchanged and the content fingerprint unchanged.
  - A response for another id is indeterminate and is decided by readback.
    It is never redispatched.
- **Untrusted titles.** Planning screens titles. Credential-shaped text is
  refused, and the plan description never repeats a title.

The `2.4.1-beta.50` audit correction (HAMCP-255) changes the shape of one
audit record and nothing else. The `home_assistant_core_authority_reconciled`
event's `analysis_summary` (model `ha-core-authority-audit-v2`) is columnar.
`dispositions` holds sorted `disposition`, `reason_code` and
`authority_source` vocabularies, and `profiles` maps each capability id to
the indexes of its three values. A fourth element carries the profile id only
when it differs from the compiled convention (the capability id with dots as
underscores, plus `_v1`). That is enough to reconstruct every profile's
admitted or withheld disposition, reason code and profile id. `adapter_id` is
dropped, and the failure event is unchanged. `AuditLogger` keeps its 8192
bound and its assertion. Before this change, the v1 per-profile list
(8972 characters on beta.48) was replaced by a `payload_truncated` stub at
every startup. The record now fits whole (1947 characters today), and
tests hold twice today's profile count to 75% of the bound (6144), both with
the real outcomes (3647) and with no two profiles sharing an outcome (5807).

The `2.4.1-beta.41` November Core batch (HAMCP-232, HAMCP-145) adds two
governed write plan families and two script-trace reads, all built
inactive. It adds three runtime-only Core capability profiles, none of them
signed, and no permission, add-on option, delegated provider or fallback.

- **Admission first.** Every entry point, the generic plan and resource
  routes included, refuses `pending_signed_registry_admission` before any
  read, validation read or persistence until a signed sequence names the
  profile, and again at the persist boundary. A retained, non-terminal task
  with no child dispatch intent is admitted before anything is persisted;
  every F3 path re-checks dispatch admission.
- **Writes, once admitted.** One options-flow step submit per helper entry
  and one `input_select.select_option` call, each at most once and never
  redispatched; `mark_provider_send()` marks the send boundary. The options
  submit must be the complete intended set (baseline plus the approved
  changes), checked at create and at apply, because Core replaces the
  options with the submitted dict.
- **No flow left open.** Every options flow this server opens is held,
  registered and aborted on every exit, a cancelled caller included; a 404
  counts as gone and a lost open is disclosed as
  `options_flow_open_unconfirmed`.
- **Durable flow ledger and abort retry (correction 2, H2-R1).** The open
  intent (entry, plan, task, phase, time) is recorded in Engineering's own
  storage before the open is sent, and the received flow id before any
  abort; an open is refused, with nothing sent, when the record cannot be
  written. A failed or unknown abort stays recorded across restarts and each
  recovery sweep retries it, at most 10 times, until Core confirms the flow
  gone (aborted, or a 404); after that it is reported orphaned in health.
  The retry is the existing reviewed options-flow DELETE, by the recorded id
  of a flow this server opened, only while a signed registry admits the
  profile and inside an exact-target Core authority window for that entry.
  It adds no Home Assistant write, no new Core command and no fallback; with
  nothing recorded or no admission the sweep makes no call and no write.
- **Accepted residual (owner decision (b)).** Pinned Core 2026.10.1 lists no
  in-progress options flows (`flow/progress` and `flow/subscribe` cover
  config flows only), so a flow whose open answer was lost cannot be found
  or aborted. It is reported in health, and in the task when one exists, as
  "options flow possibly left open on entry X (no flow id received); it
  holds no configuration change and is discarded at the next Home Assistant
  restart".
- **Closed vocabularies.** Explicit never-sent lists: entry create, delete,
  reload, update, disable and subentries; `set_options` and the relative
  option moves; `trace/contexts`, every `trace/debug/*` command and the
  list-all trace form.
- **Trace sanitization.** Script traces, with the identifiers they are
  returned under, leave through the one HAMCP-225 trace exit (the shared
  `credentials.screen` first, then the strict sanitizer, truncation last).
- **Disclosure.** A disabled dependent automation or trigger is disclosed as
  not firing while disabled; enablement is part of the binding fingerprint,
  so a toggle between plan and apply refuses the apply as stale. The OD-7
  concurrency window is disclosed in the plan and the apply response.

The `2.4.1-beta.39` trace snapshot slice (HAMCP-225) adds four
Engineering-native tools and one Engineering-local store. It adds no Home
Assistant write, permission, add-on option, Core capability profile,
delegated provider or fallback.

- **Reads.** Only `trace/list` and `trace/get` with `domain: "automation"`,
  the shapes `list_automation_traces` and `get_automation_trace` already
  send. Tests assert no other command and no `domain: "script"`.
- **Sanitization.** `get_automation_trace` output and every captured trace
  pass the shared trace sanitizer before they leave Engineering or reach the
  store; capture runs one more strict pass over the complete record, and
  read-back re-sanitizes each page. Withheld content makes the result
  `partial`. Before the strict sanitizer, every string and every dictionary
  key at every depth passes the shared `credentials.screen` used by HAMCP-132
  and HAMCP-224 (no trace-specific screen); a flagged key withholds its whole
  entry, and nesting beyond 64 levels is withheld as unverifiable. PEM
  private-key blocks with a header are withheld whole.
- **Identifiers.** Home Assistant's own identifiers and names are screened
  in the default mode (owner decision, as in HAMCP-226); trace values keep
  the labelled mode. An automation or run ID the screen flags is persisted,
  audited and returned only as `withheld_` plus a per-process HMAC digest;
  refusals never echo it, and the gateway audit records it as `null`. A PEM
  block in an identity field is not withheld.
- **Store.** `trace-snapshots-v1/` under the governance path: `flock`,
  `O_NOFOLLOW`, `0600`, atomic rename, digest-checked loads, at most 20
  content-free quarantine stubs, fail-closed index, fixed diagnostics,
  bounded size, count and age. Manual
  captures are refused at capacity; a watch evicts only its own snapshots.
- **Watch poller.** The only periodic Core read: bounded to 10 watches every
  30 seconds, under its own Core authority lease, paused while authority is
  unavailable or the version is not admitted, and re-checked before each
  watch's reads are stored and again under the store lock, immediately
  before the first write. Watch changes need no approval but write a
  required, caller-attributed audit row.
- **Caller binding.** List and read cursors are HMAC-bound to the caller
  with a per-process key.

The `2.4.1-beta.33` configuration-definition slice (HAMCP-132 slice 2) adds
the read-only `get_configuration_definitions` tool. It adds no permission,
add-on option, HTTP client, Supervisor call, route template, batch-allowlist
shape, fallback or write path.

- **Reads.** The tool issues:
  - REST `GET /api/states` (only whether each state exists and is
    `unavailable` or `unknown`) and `GET /api/config` (`components` only);
  - WebSocket `config/entity_registry/list` and `entity/source`;
  - `config_entries/get` with one domain from a closed set;
  - Core's `manifest/get` for `ha_mcp_tools`.

  Core's `config_entries/get` never includes an entry's `data` or `options`.
  So a UI-defined SQL entry's `db_url` is never fetched, and UI definitions
  are reported as not readable without a flow.
- **No component commands.** The `ha_mcp_tools` custom component exposes
  admin-only WebSocket commands, including writes and a backup preparation
  that returns a password. Josh decided on 2026-10-21 that it is not used.
  No `ha_mcp_tools/*` command is reachable; an AST test pins the package's
  exact command literals and dispatch.
- **YAML reader purpose.** The admitted ha-mcp YAML reader is used through a
  second fixed gateway purpose, `configuration_definitions`:
  - eight fixed `configuration.yaml` paths and its own audit event;
  - Engineering builds the only argument;
  - each purpose's path set is closed, so neither purpose can read the
    other's paths, and an unknown purpose returns no reader;
  - L1–L4, the Core gate, the response seal and the before-and-after
    authority rechecks are unchanged.
- **Withholding.** The projection is a positive per-integration allowlist
  from Core 2026.10.1's schemas. The following are withheld:
  - `db_url`, in every position;
  - `!secret` and `!env_var` references, with their names;
  - nested user-defined keys that look like credential or connection keys;
  - any credential-bearing URI inside an allowlisted text field such as a
    SQL query. The shared sanitizer redacts userinfo only for `http(s)`.

  Unrecognized keys are counted, never copied. Config-entry `reason` text is
  withheld. State values are never copied.
- **One screened and sanitized exit point.** The complete envelope (report,
  omissions, coverage and every identifier) is first screened in every key
  and string for credential text: a URI with userinfo, a database or broker
  connection URI by scheme, a user and password before a host without a scheme, a password field, a known
  secret, or text the reader's canonicalization refuses. Such a string is
  replaced with `[withheld:credential_text]` and the report is partial
  (`credential_text_withheld`). This covers Core text the projection never
  sees, such as config entry titles (independent review H1). The checks
  read the canonical text through a bounded confusable fold (a fixed table
  of look-alikes of `/`, `:` and Latin letters, not general confusable
  coverage; delta review H2). The envelope
  is then sanitized once with the access secret and the Home Assistant
  token as known secrets. A redaction makes the report partial. Budget trimming rebuilds the envelope and sanitizes it
  again. Tests plant a known secret in every identifier-bearing field, prove
  with a control run that it reaches each one, and check through the
  authenticated gateway that the protected response never contains it.
- **Free text by allowlist (delta review H1, H2).** A config entry title,
  the only Core free text the report echoes, is checked whole before any
  truncation and shown verbatim only when it is printable ASCII without a
  control character, within 255 characters, and has no `@`, no `://`, no
  password-like `key=value` or `key: value`, no known secret in any letter
  case and no credential text. Anything else is replaced whole with
  `[withheld:credential_text]` or `[withheld:unverifiable_text]` and the
  report is partial (`unverifiable_text_withheld`). An ordinary non-ASCII
  title is withheld by design.
- **Invisible text withheld whole (second delta review R2-H1).** A
  YAML-projected string or key containing an invisible, format or control
  character (Cc except tab, line feed and carriage return; Cf, Cs, Co, Cn,
  Zl, Zp; every Default_Ignorable_Code_Point), in the original text or after
  a bounded percent-decode, is replaced whole with
  `[withheld:unverifiable_text]` (a key: dropped) before any credential
  screening, in the projection and again at the exit point. It is never
  screened and shown: a zero-width character inside `password=` previously
  hid the credential word.
- **Whitespace inside a credential is screened (correction batch 4).**
  Every YAML-projected string, and every string at the exit point, is
  screened in two forms: the canonical form (bounded percent-decode, NFKC,
  casefold, confusable fold) and its squashed form, the same text with every
  character `str.isspace` accepts removed (space, tab, line feed, carriage
  return and Unicode whitespace). If either form matches the credential or
  known-secret checks, the original value is withheld whole as credential
  text and the report is partial: `pass<tab>word=`, a line feed inside a URI's user-info and
  their percent-encoded and double-encoded forms no longer reach the caller.
  Tab, line feed and carriage return stay displayable in ordinary text, so a
  multi-line template without credential text is shown and complete. Form
  feed, vertical tab and other Cc characters stay withheld outright as
  unverifiable text. **By design, text with a colon followed later by `@`
  and no `/` between (for example `Alert: motion` then `@ {{ ... }}` on the
  next line) squashes to a URI user-info shape and is withheld** (fail
  closed; the report is partial).
- **Any withholding is partial (second delta review R2-M1).** One scan of
  the final envelope at the exit point finds every withheld form and names
  each affected item in a bounded omission list (at most 20, then a count);
  any such item makes the report partial.
- **A fixed policy redaction is not missing evidence (owner decision, Josh,
  2026-10-22).** A SQL definition's own `db_url` (the `sql:` key or a legacy
  `platform: sql` sensor) is always replaced with
  `{"redacted_by_policy": "database_url"}`, never read out. The final-envelope
  scan still runs over it; an explicit allowlist inside that scan
  (`credentials.POLICY_REDACTIONS`, exactly one entry: integration `sql`,
  field `db_url`, kind `database_url`, at `definitions[<n>].fields.db_url`)
  is the only form it does not count. A `db_url` in any other integration or
  nested position, a credential in any other SQL field, invisible text,
  unverifiable titles, confusables, and a `redacted_by_policy`-shaped value
  that came from configuration are all still withheld and partial. The
  sanitizer's own `redacted_by_policy` omission (HAMCP-191) is unrelated and
  still makes the report partial.
- **Nothing is derived from a withheld input (owner decision, Josh,
  2026-10-22).** Before deriving an entity ID, a unique ID, dependencies, a
  definition-ID key or a legacy candidate, the projection checks every input
  with its own withholding and with `credentials.withheld_at_exit` (the exit
  point's credential, known-secret and sanitizer checks). A withheld input
  yields no derived value (`derived_entity_id_withheld`,
  `unique_id_withheld`, `dependencies_withheld`, a positional definition ID)
  and a path in the definition's `withheld`, so the report is partial. A
  slug of a credential is therefore never derived, though the exit point
  would not recognize it.
- **The Core-observed entity ID is not withheld (owner decision, Josh,
  2026-10-22).** An exact registry match reports Core's own `entity_id`,
  which every state read already exposes. An entity that Home Assistant
  itself named from a credential-bearing string shows that entity ID; the
  exit point would not recognize the slug. This is a disclosed limitation:
  rename the entity in Home Assistant.

Arguments are validated before SDK coercion and rejected without echoing
them. The audit records counts and source outcomes only. Stable v1.1.2 is
unchanged.

The `2.4.1-beta.31` slice (HAMCP-069B) adds governed entity enable/disable and
voice assistant exposure, **unreachable at release**. It adds no permission,
add-on option, role, fallback or delegated provider.

- **New write paths.** `config/entity_registry/update` with exactly
  `entity_id` and `disabled_by`, and `homeassistant/expose_entity` with one
  assistant, one entity and `should_expose`. Both are reachable only through
  an externally approved plan dispatched by the F3 runtime, for a Core
  capability profile admitted by a signed registry. Neither new profile is
  referenced by the shipped sequence 12. The routing layer refuses every call
  with `CoreCapabilityUnavailable` / `pending_signed_registry_admission`
  before planning, provider contact or storage. The governance service
  repeats that check before any Home Assistant read or storage, on every
  entry point (planning service, internal plan builder, F3 inverse). It fails
  closed without a Core runtime. A tampered profile fingerprint keeps the
  profile withheld.
- **Closed vocabulary.** The gateway allow-lists the two writes and four reads
  (`config/entity_registry/get`, `config/entity_registry/list`,
  `config_entries/get_single`, `homeassistant/expose_new_entities/get`) and
  raises on anything else before I/O. `cloud/status` (which returns the
  Google secure-devices PIN), `homeassistant/expose_entity/list`,
  `homeassistant/expose_new_entities/set` and both cloud entity-update
  commands are never sent; a static check pins every occurrence of
  `cloud/status`. Alexa is refused.
- **Approval and verification.** Protected entities need the existing
  `ELEVATED_ADMIN` acknowledgement, and so does a disable whose dependency
  evidence is incomplete for any reason; a tampered policy marker is
  `PROHIBITED`. Exact registry readback decides every child; the empty
  `expose_entity` result is never proof. The only tolerated difference is a
  non-target `cloud.alexa` options key going from absent to present, which is
  reported.

The `2.4.1-beta.34` slice (HAMCP-165) adds governed label create, edit and
entity assignment, **unreachable at release**. It activates nothing new and
switches on with the sequence-13 signing. It adds no permission, add-on
option, role, fallback or delegated provider.

- **New write paths.** `config/label_registry/create` with exactly `name`,
  `color`, `icon` and `description`, and `config/label_registry/update` with
  `label_id` and only the changed fields. Both are reachable only through an
  externally approved plan dispatched by the F3 runtime, for the Core
  capability profile `core.label_registry_operation` admitted by a signed
  registry. The shipped sequence 12 does not reference it. The routing layer
  refuses every call with `CoreCapabilityUnavailable` /
  `pending_signed_registry_admission`, and the governance service checks
  admission first, before any Home Assistant read or storage. A tampered
  profile fingerprint keeps the profile withheld.
- **Closed vocabulary.** The label gateway allow-lists
  `config/label_registry/list`, `create` and `update` and raises on anything
  else before I/O. `config/label_registry/delete` is never constructed.
  Entity assignment uses the signed 069A path unchanged.
- **Approval and verification.** Label children are standard (metadata, no
  device effect); assignments keep their 069A classification. A tampered
  plan shape or proposal is `PROHIBITED`. Exact label-registry readback
  decides every child; a different generated `label_id` is never verified.
  Stale state and any label namespace change are refused immediately before
  the write.
- **HAMCP-069B profiles.** The two unsigned 069B profiles now name every read
  their gateway sends; no signed profile changes.

The `2.4.1-beta.45` slice (HAMCP-235) adds no permission, role, route,
provider, fallback, write path, Core command, input or tool. It makes
automation baseline compare and resume usable on the owner's installation.

- **Compare.** Installation identity, authority continuity and non-atomic
  capture are comparison-level `caveats` (owner decision, Josh, option a),
  not per-row `UNKNOWN` reasons. Two established, different installations
  still make every row `UNKNOWN` (`installation_identity_mismatch`).
- **Resume.** Only the installation anchor, the configuration id set and the
  id→entity mapping fence a resume; the Core version still binds it. A new
  authority generation is recorded as `authority_drift: "detected"`. A
  refusal carries only fixed categories (`details.resume_changes`), never an
  identifier, count or Home Assistant text. A base from an earlier release
  whose final fence failed has no anchor: its resume is refused as
  `resume_inventory_changed` with `resume_changes:
  ["installation_anchor_missing"]` (not retryable, owner decision D4); take a
  fresh capture.
- **Final fence.** Identity reads first; identity drift, authority loss,
  access denial and an unverifiable identity still refuse with nothing
  stored. Only an inventory read failure after a verified identity yields
  `identity_only`. The recorded first-fence identity is the same opaque
  digest as the installation id and is screened like every diagnostic.

The `2.4.1-beta.44` slice (HAMCP-242) adds no permission, role, route,
provider, fallback, write path, input, tool or upstream call. It adds three
fields to the public `home_assistant_core_authority` health block.

- **Identifiers only.** Every value in a `withheld_capabilities` entry is a
  fixed-vocabulary identifier from Engineering's own code: a compiled
  capability profile id (the production evidence collector emits no other
  id), a `CoreDisposition` value (`held`, `quarantined`, `unavailable`), the
  coordinator's existing reason code, and Engineering static tool names from
  `routes.STATIC_TOOL_CORE_REQUIREMENTS`. No Home Assistant observation text,
  entity, session or endpoint value is read into them. The `get_server_health`
  path serializes and size-bounds this block but does not run the shared
  sanitizer over it; safety rests on that provenance and the bound below.
  Adding a sanitizer to the health path is a separate item.
- **Projection, not authority.** The list is built from the assessment the
  coordinator already computes for the published generation. It changes no
  selection, admission, reason code, lease, retirement, registry handling or
  recovery, and persists nothing.
- **Fail closed on inconsistency, inside the block.** If a disposition's
  list length differs from its count, the total differs from
  `withheld_count`, an id repeats, or `compatible_count + withheld_count`
  differs from the generation's decisions, the Core-authority block is
  refused with `projection_degraded: true` and `projection_reason_code`
  `core_authority_projection_inconsistent`. It drops the disposition, every
  count, the list and `count_semantics` rather than publish a contradiction
  or substitute zeros, and keeps only partition-independent fields.
  `get_server_health` still succeeds with every other section unchanged.
  Only that one code is contained; any other coordinator error fails the
  call as before. The guards never admit and change no decision, lease or
  route.
- **Bounded.** At most 64 entries, `withheld_capabilities_truncated` past
  that, and the whole projection within `MAX_REPORT_BYTES` (32 768).

The `2.4.1-beta.43` slice (HAMCP-234 step 2) adds no permission, role,
route, provider, fallback, write path, input or tool. It **relaxes one
journal rule**, by owner decision (Josh, 2026-10-27), from the beta.42 live
calibration (`addon:core_matter_server`: 37 of 40 lines withheld, all as
`user_pass_host.squashed`).

- **What changed.** In the host journal path only, `user_pass_host` is read
  in the normal form as one contiguous token: no whitespace between user,
  `:`, password, `@` and host. It is no longer read in the squashed form,
  where a timestamp's `:` and a later `@peer` joined into the shape. It is
  an option of the same shared `credentials.screen`
  (`contiguous_user_pass_host`, default off), set only by
  `host_journal._JOURNAL_SCREEN`; it is not a separate screen.
- **Decoding never splits the token** (owner clarification, Josh,
  2026-10-28, from the review of aaf1fe8e). With the option, the token is
  also read in the text as given and after each round of its bounded
  percent-decode, each after NFKC, casefold and the confusable fold; any
  match withholds (`user_pass_host`). The reviewer's reproduction, a Python
  logging line naming a scheme-less proxy with `%20` inside the password
  (which `urllib.request.ProxyHandler` decodes and uses), was shown whole
  through the gateway at aaf1fe8e and is withheld now, also through the
  final authenticated response.
- **Raw-token boundaries** (correction D320-H1, Josh, 2026-10-28, from the
  round-2 review of c0789386). The tokens are taken from the text as given,
  split at its raw ASCII whitespace (U+0009–U+000D, U+0020) only, and each
  raw token is read as given and, if it carries an escape, decoded on its
  own (at most four rounds), the whitespace a round introduces kept inside
  the token. An encoded `:` or `@` that decodes in the same round as
  encoded password whitespace (a whole value through
  `quote(value, safe='')`) was shown at c0789386 and is withheld now, in
  the final response and the audit record.
- **Normalization before squash** (correction R3-H1, Josh, 2026-10-28,
  from the round-3 review of c371c739; fixed). Each stage of a raw token
  gets NFKC, casefold and the confusable fold first, and only then has its
  whitespace removed. At c371c739 the order was reversed, so a space NFKC
  introduces (U+00A8, U+00AF, U+00B4, U+0385) split one raw token and the
  credential reached the final authenticated response (the audit record
  stayed clean). It is withheld now; mutant `D5` restores the old order and
  is killed. Whitespace that percent-decoding or normalization introduces
  within one raw token (at most four decode rounds) is part of the token
  and withheld. Non-ASCII whitespace no longer splits a raw token: U+00A0,
  U+2007, U+202F and U+3000 inside a password are withheld as
  `user_pass_host`, U+2028 and U+0085 as `invisible_or_control`, in the
  journal and the final response as in the definitions and trace paths.
- **What did not.** Every other rule is unchanged in both forms: credential
  URLs and connection URIs, `password=`, the labelled keywords, `Bearer`,
  `-P`/`--pw`, known secrets (normal and squashed), PEM headers, invisible
  and control characters, and the value shapes. The configuration
  definitions (HAMCP-132) and trace (HAMCP-225) paths do not set the option;
  their suites pass unchanged. A mutation that leaks the option into the
  definitions path is killed by the unchanged HAMCP-132 suite. One that
  leaks it into the trace path survives the unchanged HAMCP-225 suites and
  is killed by the step 2 cross-path boundary test
  (`test_hamcp234_step2_journal_relaxation`). So is one that opts in only
  the trace identity and name fields (`alias`, `id`, `entity_id`,
  `friendly_name`), killed by the step 2 trace identity boundary tests
  (capture, store, read-back, `get_automation_trace`).
- **Residual: raw ASCII whitespace (U+0009–U+000D, U+0020), journal mode,
  scheme-less only; disclosed residual, accepted by Josh 2026-10-28.** A
  scheme-less `user:pass@host` split by raw ASCII whitespace in the raw
  text is shown in the journal unless another rule meets it. This is not
  only a non-credential: Python's proxy parser
  (`urllib.request._parse_proxy`) accepts a literal space inside a password
  (verified offline, Python 3.12.14), so a logged proxy setting written
  that way is shown. Percent-encoded whitespace inside one raw token never
  splits it, with literal or encoded `:` and `@`, within the four-round
  decode bound (above). A contiguous one, and any
  `scheme://user:pass@host` (`http`, `https`, `socks5`: `credential_url`),
  is still withheld. Non-ASCII whitespace is not part of the residual; this
  resolves the round-3 question left to Josh.
- **Severity bar (Josh, 2026-10-28, adopted project-wide).** A realistic
  accidental leak, including one through text that devices or integrations
  write into Home Assistant, is High; a leak that needs an adversarial-only
  construction is Low. Review findings and residuals of this slice are
  rated against it.
- **Known limitation (HAMCP-240, availability, not exposure).** Journal final
  screening has no total-time bound and runs synchronously on the event loop.
  A maximum-size pathological read (255 × 16,384-byte messages) took about 408
  s on c371c739 (404.877 s screening, 408.577 s call; no deadline flag; every
  field fully screened, no unscreened output). The missing bound predates
  beta.43; the D320-H1 correction made the slow shape about 1.5x slower than
  c0789386. Linear-time matching, a cumulative budget and off-loop screening
  are tracked as HAMCP-240.
- **Rule ids.** The journal's `RULE_IDS` drop `user_pass_host.squashed`
  (44); a calibration never reports it.

The `2.4.1-beta.42` slice (HAMCP-234 step 1) adds no permission, role,
route, provider, fallback, write path or tool, and **changes nothing in what
is withheld**.

- **Rule ids.** `credentials.screen` is `screen_rule(...)[0]`, and
  `credential_text` is `credential_rule(...) is not None`, so the one
  ordered chain still decides. The journal reads a rule id only for text the
  screen already withheld. Each id is a fixed constant (`RULE_IDS`, 45 with
  the reader's `field_too_long`, `not_utf8` and `unverifiable_escape`). A
  keyword id is the named group of the fixed pattern. Anything else is
  reported as `unlisted_rule`. An id never carries the text, its length or
  the matched substring.
- **Calibration.** It is accepted for host journal sources only and never
  with `min_level`, `text` or `cursor`. It uses the same sources and window
  bounds as a read, at most 2,000 entries per source, with the same screen.
  It returns counts, fixed ids, source names and window coverage times only.
  The strict sanitizer still runs at the single `_finish` exit. Any change
  it would make fails the call closed (`sources_unavailable`); this is the
  one new sanitizer call site, listed in the HAMCP-191 table.
- **Levels.** The level of a `supervisor` or `addon:<slug>` line is a
  decision read from the start of its screened, sanitized first line in an
  anchored known format; it is never output text. A withheld message is
  never parsed: its level is `priority_unverified`, a level filter never
  passes it, and it counts in `withheld_level_filtered` (partial).

The `2.4.1-beta.38` slice (HAMCP-224) lets `get_incident_logs` read the
`supervisor`, `host`, `host:<category>` and `addon:<slug>` sources from the
host journal. It adds **one read-only permission** and no role, route,
provider, fallback, write path or tool.

- **Permission.** `journald: true` makes the Supervisor bind-mount the host's
  `/var/log/journal` and `/run/log/journal` read-only (Supervisor 2026.09.1
  `docker/app.py`). `hassio_role` stays `homeassistant`; the `manager` log
  routes below are still never called.
- **Reader.** `incident_logs/host_journal.py` is the only module that imports
  `ctypes`. It binds 14 fixed symbols (`BOUND_SYMBOLS`) of the base image's
  `libsystemd.so.0` (Debian `libsystemd0` 257.13-1~deb13u1) and reads 9 fixed
  fields (`READ_FIELDS`) by name. No field-enumeration, unique-value,
  descriptor, raw-cursor-seek, inotify or write symbol is bound. It opens the
  container root `O_RDONLY|O_DIRECTORY|O_NOFOLLOW|O_CLOEXEC` and passes it
  with `SD_JOURNAL_OS_ROOT`; both journal directories must open without
  following a symlink. One worker thread opens, reads and closes the handle,
  bounded at 16 KiB per field, 4 MiB per call, 20,000 entries per source
  (the resume walk and the edge probes count) and the call deadline less one
  second. Each field read checks the deadline first and the call byte bound
  before the copy, on every path.
- **Matches.** Fixed per source (`supervisor` is `hassio_supervisor`; `host`
  is the Supervisor's 52 default identifiers plus kernel entries;
  `addon:<slug>` needs `SYSLOG_IDENTIFIER` equal to `CONTAINER_NAME`,
  `app_<slug>` or `addon_<slug>`, through `_TRANSPORT=journal`). Every entry
  is rechecked in Python; a failed recheck is counted, never returned.
- **Positions.** Continuations hold a realtime and a keyed digest of the
  libsystemd cursor, never the cursor (it embeds the raw boot ID); the boot
  marker is a keyed digest too. The machine ID is never read.
- **Secrets.** At the single `_finish` exit every returned journal field
  (message, `process`, `kernel_subsystem`, `kernel_device`) goes through the
  **same** screen as the configuration-definitions report,
  `configuration_definitions/credentials.py` `screen`; the journal has no
  screen of its own (review 1 H1/H2). It reads the complete field before any
  cut: a field over 16 KiB, or bytes that are not UTF-8, are withheld whole
  as unverifiable. Invisible, format or control characters, then the
  HAMCP-132 credential and known-secret forms after bounded percent-decoding,
  NFKC and the confusable fold, on the text and its squashed form, plus the
  labelled forms the journal opts in to (a credential label with `=` or `:`,
  `Bearer`, PEM private keys), withhold the **whole** field. In that
  labelled mode the label itself withholds (review 2): a strong keyword
  (`bearer`, `password`, `passwd`, `pwd`, `--pw`, `authorization`, `api
  key`, `secret`, `token`, `private key`) in the normal or squashed form,
  or the mosquitto `-P` option in any quoting, argv or split form (a
  bounded quote-aware tokenizer); the value shapes remain a second layer.
  Ordinary text naming a keyword, or another tool's `-P`, is withheld too.
  An ANSI SGR colour code (`ESC [`, at most 32 digits or semicolons, `m`;
  review 3) is the only display format: the field is screened both without
  it and in its original form, either withholds, and the shown text has no
  escape; any other escape (another CSI final byte, OSC, a stray or
  malformed escape) or control character is never removed and is
  unverifiable. The display cut to 2000 characters happens only after. A
  withheld field is never searched. The configuration-definitions
  final-envelope scan (`withheld_items`) then names each withheld entry, and
  a withheld entry a text filter left out (`withheld_not_searched`) or the
  level filter left out (`withheld_level_filtered`; the level is the
  journal `PRIORITY`, never the text) is counted: any of these makes the
  result partial. The shared `sanitize_log_lines` and strict
  sanitizer then run once, as below. The configuration-definitions report
  does not opt in to the labelled forms and is unchanged. A bare secret with
  no key or known value can still pass (design §7, the same residual risk as
  the Core log).
- **Unavailability.** Decided from `/proc/self/mountinfo` and the open, never
  from an empty result; an unavailable source is never `complete`.

The `2.4.1-beta.30` incident-window slice (HAMCP-131) adds two read-only tools.
Neither adds a permission, add-on option, role, fallback or write path.

- **`get_incident_logs`** issues Supervisor `GET /core/logs` and
  `GET /addons/self/logs` only. Both are registered HAMCP-200 route templates;
  `/core/logs` is newly registered. Role `homeassistant` admits `/core/.+`,
  and the Supervisor `api_bypass` admits `/addons/self/<one segment>`
  (Supervisor 2026.09.1, `api/middleware/security.py`).
  `/supervisor/logs`, `/host/logs` and `/addons/<slug>/logs` need role
  `manager`. They are reported unavailable with zero requests and are never
  attempted. The `/.+/info` pattern quirk that would expose host journal
  entries tagged `info` is deliberately not used. Caller slugs pass the
  shared `path_segment` guard and a slug pattern before any request, session
  or credential exists. Requests carry the Supervisor token only to the fixed
  Supervisor host. Per-call request, byte and deadline ceilings bound the
  download.
- **`get_history_slices`** dispatches only the admitted `ha_get_history`
  automatic-read route, through a new fixed gateway `history_reader`. It
  accepts only reviewed versions 8.5.0 and 8.6.0, rechecks route currency
  before each read, and builds the argument shape itself (strict validation;
  zero dispatch on a mismatch). The parent caller's telemetry and authority
  apply, and the audit event is `history_slice_read`. The native `get_history`
  REST tool is not used, and there is no direct Home Assistant fallback.

Journal text and recorder states are untrusted. Each service merges, filters
and limits raw values, then assembles its complete envelope: entries or
states, coverage, omissions, limitations and cursor. Its single `_finish`
exit then runs the shared sanitizers once. For logs this is
`sanitize_log_lines`, then the strict `sanitize_untrusted_data` with the
access secret and Home Assistant token as known secrets and a shared reference
registry. A redaction makes the result partial. `SANITIZER_SITES` and AST
tests pin these as the only call sites. A planted-secret sweep checks every
output field. Cursors are HMAC-signed with a per-process key and a 1-hour
TTL. The query fingerprint is keyed, so a text filter is not revealed. See
[HAMCP_131_INCIDENT_LOGS.md](HAMCP_131_INCIDENT_LOGS.md). Stable v1.1.2 is
unchanged.

The `2.4.1-beta.27` definition visibility slice (HAMCP-132) adds two read-only
tools. Neither adds a permission, add-on option, HTTP client, Supervisor call,
route template, fallback or write path.

- **`get_recorder_settings`** issues Core WebSocket `recorder/info`. For up to
  50 entities it issues `recorder/entity_options/get`, which is admin-only but
  read-only. Each runs in its own pipelined batch. It also uses the admitted
  ha-mcp YAML reader through a new gateway `yaml_config_reader` route:
  - Engineering builds the only argument, one of three fixed
    `configuration.yaml` paths;
  - the reader's L1–L4 projection, Core gate and response seal apply
    unchanged;
  - the route's authority is rechecked before and after every read, through
    the same `_bounded_route_current` check as the HAMCP-212 script reader.

  The shared sanitizer redacts URL userinfo only for `http(s)` and has no
  `db_url` rule. So the recorder projection is a positive allowlist of the
  keys recognized from Core's recorder schema:
  - `db_url` is reduced to `value_withheld` and a default-location flag;
  - a `!secret` or `!env_var` reference is withheld with its name;
  - unrecognized keys are counted, never copied.

  Tests plant database URLs under several schemes and credential needles, and
  check that none appears in the serialized output.
- **`get_unregistered_entities`** issues:
  - REST `GET /api/states`;
  - WebSocket `config/entity_registry/list` and `entity/source`;
  - batched `search/related` with `item_type: entity`.

  None of these is admin-only.

The pipelined batch allowlist gains exactly two shapes, each with an exact
key set and a strict entity-ID pattern. AST tests pin the new modules to these
reads. Arguments are validated before SDK coercion. The audit carries counts
and source outcomes, never entity IDs. Both tools are mapped in the HAMCP-164
Core gate.

Each tool assembles its complete response before sanitizing. This covers the
report, omissions, warnings, limitations and every entity ID, platform, config
entry and filter item. The tool then runs the strict shared sanitizer once on
that final envelope. The access secret and the Home Assistant token are
passed as known secrets. Nothing is appended afterwards, and any redaction
makes the report partial. An earlier head appended entity-bearing omissions
after sanitization (review finding H1 on `de1433f`). Tests now plant a known
secret in every identifier-bearing field, through the authenticated gateway,
and check that the serialized response never contains it. See the
[implementation notes](HAMCP_132_SLICE1_IMPLEMENTATION_NOTES.md). Stable
v1.1.2 is unchanged.

The `2.4.1-beta.24` backup inventory (HAMCP-130) adds the read-only
`get_backup_inventory` tool. It makes exactly two fixed Core WebSocket reads,
`backup/config/info` and `backup/info`, through Engineering's existing
Supervisor-proxy admin authority. It adds no permission, add-on option, HTTP
client, Supervisor call, delegated ha-mcp call, fallback or write path; static
and runtime tests pin the two commands. `backup/config/info` returns the backup
encryption key in plaintext. The key is never projected: the report carries
only an `encryption_configured` flag, and the key seeds that one response's
shared strict sanitizer (HAMCP-191) as a known secret, so user text that
contains it, in any letter case, is withheld. When the key cannot be
positively read (a failed config read, or a missing or malformed key field),
the tool fails closed: every name and error text is withheld, free-form
identifiers are replaced by per-response aliases, only structured facts are
shown and the report is partial with `encryption_key_unknown`. Backup names,
error text and identifiers pass through the same sanitizer, and any redaction
makes the report partial. The tool is
mapped to `core.basic_websocket_read` in the HAMCP-164 Core gate. See the
[design](HAMCP_130_BACKUP_INVENTORY_DESIGN.md). Stable v1.1.2 is unchanged.

Accepted residual (HAMCP-130 H2, Josh's decision): when the key is known, the
sanitizer matches it literally (and in any letter case). A copy of the key
that has been transformed, for example URL-encoded, base64-encoded or split
across several backup or add-on names, is not recognized and can appear in
output. Exploiting this requires a Home Assistant administrator to embed such
an encoded copy in backup names deliberately, and administrators can already
view the key in Home Assistant. The tool never outputs the key field itself,
withholds every literal occurrence of the key, and fails closed by withholding
all free text when the key is not positively known.

The `2.4.1-beta.22` dependency-security release (HAMCP-211) advances
`aiohttp` to 3.14.4 for GHSA-2g87-pp6c-29x5, CVE-2026-111063 and
CVE-2026-111064, and `yarl` to 1.25.1, the floor 3.14.4 requires. Engineering
uses only the aiohttp client; its MCP and Ingress servers run on uvicorn and
Starlette, so the server-timeout advisory does not apply. Its client
transports refuse redirects, disable automatic decompression and bound bytes,
frames and time. Inherited behaviour changes are listed in the
[release notes](V2_4_1_BETA22_RELEASE_NOTES.md) and pinned by
`tests/test_hamcp211_aiohttp_3144_contract.py`. Stable v1.1.2 still pins
`aiohttp==3.9.5` and is unchanged.

The `2.4.1-beta.26` dependency-security release (HAMCP-217) advances
`certifi` to 2026.10.19 for GHSA-6q48-6c7w-9gw2. The new bundle drops the
`emSign ECC Root CA - G3` root, whose intermediates have compromised keys.
Engineering's httpx-based MCP clients verify against `certifi.where()`: the
SDK default client and the dashboard-analysis delegate. Its aiohttp clients use
the interpreter default store. The signed registries are additionally
signature-verified. `tests/test_hamcp217_certifi_20261019_contract.py` pins
exact bundle trust and refusal of roots outside it. The base image's CA store
is unchanged.

Published 2.2.2 retains the Host/Origin configuration risk tracked in issue #62.
Engineering 2.2.3 adds an Engineering-owned policy before all MCP gateway paths.
Its strict loopback-authority defaults and exact configured
aliases/origins supplement secret-path authentication; they do not replace it.
The all-interface bind and independently authenticated approval ingress remain
separate. The MCP listener disables implicit Uvicorn proxy-header rewriting.
See [INBOUND_SECURITY.md](INBOUND_SECURITY.md) for parsing, bounds, migration,
safe diagnostics and required exact-image/deployed-path validation. The inner
SDK policy remains intentionally unused because it cannot cover earlier gateway
work. Source tests do not establish a new release, installed enforcement or
end-to-end rejection of headers discarded upstream. Do not mark the advisory or
issue fully resolved from these tests alone.

The scanner is point-in-time public-advisory evidence, not a proof that
dependencies are defect-free. The repository retains exact top-level pins but
does not yet commit a transitive lock with hashes; CI and image builds resolve
the transitive graph from those pins. This assurance applies only to the HA MCP
Engineering dependency set. Stable v1.1.2 is operationally retired historical
source, is not covered by the Engineering audit, and is not a supported
rollback target.
