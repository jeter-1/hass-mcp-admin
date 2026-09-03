# HA MCP Engineering Server Beta

This directory contains the Engineering v2 add-on. The currently advertised
version is `2.2.0-beta.36`; staged version `2.2.0-beta.37` adds one exact,
governed `input_boolean` on/off action with external approval, durable
single-dispatch execution, authoritative readback, no fallback, and a
separately governed reverse plan. Beta 36 corrected bounded Supervisor
self-identity reads for approval notifications; its Android cold-start body
navigation defect remains separate future work. Beta 25 promoted only
`ha_search` after its successful bounded live canary. Beta 23 added an evidence-bound
compatibility-family compiler and exact reviewed `ha-mcp` 8.1.1
authority without weakening Beta 22 approval review or Beta 20 F3 behavior.
The exact 8.1.0 profile admits 24 delegated reads and keeps `ha_search` and
`ha_get_operation_status` held. The exact 8.1.1 profile admits 25 delegated
reads and keeps only `ha_get_operation_status` held. Both retain the expanded
`ha_manage_hacs` surface as a persistent write, and normalize only the exact
changed HACS read success envelope. Every 8.1.1 artifact, policy, provider
disposition, and family decision is exact and digest-bound. Release families
are review-time comparison policy only: unlisted 8.1.x patches, 8.2.x,
prereleases, and unrelated servers remain unavailable.

Beta 22 persists a versioned complete before/after semantic projection, binds
its digest through plan/policy authority into the unchanged F3 prepared hash,
and renders that authoritative record without Home Assistant re-query. Long
values remain inspectable, protected values use field-aware redaction, and
historical plans without a trustworthy projection remain audit-readable but
cannot be approved or dispatched.

Beta 23 preserves Beta 22's complete projections, Beta 21's exact 8.1.0
upstream admission, and Beta 20's closed
12-capability F3 registry, deterministic
child authority, central recovery, selective holds, private reconciliation,
and governed rollback. It also preserves Beta 15 lifecycle response handling,
Beta 14 model-aware catalog validation, Beta 13's secure dependency pins,
bounded restart reconciliation, and exact 7.14.2 and 8.0.0 compatibility. It
does not change an F3 route, provider action, held upstream tool, dashboard
execution boundary, or fallback behavior.
Its technical “Beta” display name, slug, image repository, and runtime
identity remain unchanged to avoid a migration. Stable v1.1.2
`hass_mcp_admin` source remains in the repository as operationally retired
history; it is not part of Engineering dependency assurance or supported
rollback.

## Contract-level reviewed upstream reads

After configuring the existing secret-bearing upstream MCP URL, Engineering
starts with 49 statically registered tools (25 canonical plus 24
Engineering-native). Fast bounded retries recover when `ha-mcp` is not yet
reachable after host boot. While configured upstream reconciliation is
initially pending, `/health` remains live but `/ready` and authenticated MCP
traffic return HTTP 503; schema-caching clients therefore cannot retain that
transient static-only catalog. Once the first stable or terminal reconciliation
result is known, `/ready` reports the bounded ready state. If discovery
establishes an exact compiled reviewed release entry (`ha-mcp` 7.14.2, 8.0.0,
8.1.0, or 8.1.1), each release's reviewed pure reads are admitted independently by their
exact input-schema fingerprint, exact bounded full-runtime-description
fingerprint, exact runtime safety-annotation presence/value fingerprint,
output-schema
presence/fingerprint, and fixed semantic contract. A stable missing or
incompatible contract uses a slower reprobe cadence.

The runtime annotation fingerprint preserves optional-field presence. It is
separate from the stricter Engineering-owned annotations that clients receive
after admission.
Each read also requires its exact pinned generic object output-schema
fingerprint; the fixed Engineering adapter, not that generic schema, owns
sanitization, bounds, fallback refusal, and partial-data behavior.

Exact 8.0.0, 8.1.0, and 8.1.1 additionally use a version-scoped operational runtime model for
the documented policy-state block. The object must retain its exact fields,
JSON types, deployment allowlist, and bounded rule count before only its
environment-dependent values are normalized. All other descriptor fields stay
exact. Raw operational-catalog and strict full-contract hashes remain separate
diagnostic evidence, and malformed policy metadata remains quarantined.

A complete reviewed 7.14.2 set adds 26 delegated reads for 75 registered tools.
The exact 8.0.0 and 8.1.0 sets each add 24 for 73 registered tools and keep two
held reads. Exact 8.1.1 adds 25 for 74 registered tools and keeps only
`ha_get_operation_status` held. Held reads are accounted but never registered
as delegated upstream tools. One missing or quarantined read
leaves other matches available. A client that cached an earlier list must re-list or reconnect; the
Beta 54-compatible disabled mode remains stateless. Enabled automatic
readmission uses a stateful authenticated inbound MCP session to bind each
`tools/list` generation to later delegated calls, without advertising or
broadcasting `tools/list_changed`.

Unlisted, mixed, write, action, prohibited, and unsupported tools are never
registered. A changed reviewed contract is quarantined individually, and
delegated reads never fall back to direct Home Assistant access. Each reviewed
release has its own 78-tool policy and complete per-tool evidence. An unknown
version remains unavailable even if its live descriptors appear identical;
generated candidate evidence cannot authorize itself.
[`ADR-006`](../docs/architecture/ADR-006-CONTRACT-LEVEL-UPSTREAM-COMPATIBILITY.md)
defines the active boundary, while
[`ADR-005`](../docs/architecture/ADR-005-READONLY-UPSTREAM-GATEWAY.md) retains
the original Phase 1 decision history.

### Held-read live canary lifecycle

Held-read promotion is deliberately split into five distinct stages:

1. Static compatibility review records the exact release, descriptor,
   classification, and contract fingerprints in source-controlled evidence.
2. `held_for_canary` keeps the reviewed read accounted but absent from the
   dynamic delegated catalog.
3. `run_held_read_canary` may execute only such a held read. The caller must
   bind the request to the active exact compatibility-entry ID; Engineering
   revalidates the input and complete target contract in the same upstream
   session, applies no fallback, sanitizes and bounds the untrusted result, and
   emits audit and decision evidence. It is an unapproved read-only operator
   diagnostic: invocation neither grants approval nor creates approval state.
4. A human architecture review evaluates each tool's canary independently. A
   passing result is evidence only and does not authorize promotion.
5. Any promotion requires a later source-policy change, review, testing, and
   release. Canary execution never changes classification or admission state
   and always reports `promotion_performed: false`.

The canary path is read-only. It cannot call automatic reads, mixed or write
tools, physical/high-risk actions, prohibited or unsupported tools, or an
unreviewed/mismatched release or contract. It creates no plan, approval,
execution task, configuration write, service call, or Home Assistant mutation.

## Install the beta add-on

1. Keep the stable add-on installed and running on port `8099`.
2. In Home Assistant, refresh the add-on repository
   `https://github.com/jeter-1/hass-mcp-admin`.
3. Install **HA MCP Engineering Server Beta**.
4. Set a new, beta-only `access_secret` of at least 24 characters. Do not reuse
   or paste the production secret into source control, logs, or support output.
5. For RC3A dashboard reads, place the complete secret-bearing upstream MCP URL
   only in the password-style `upstream_dashboard_mcp_url` option. Leave it
   empty to keep the optional provider unconfigured.
   Built-in records retain reviewed evidence for `ha-mcp` 7.13.0, 7.14.0, and
   7.14.1. Dashboard admission requires an exact built-in or verified signed
   release attestation before applying compiled contract checks. Missing exact
   authority, mismatch, or revocation blocks without an older-attestation or
   self-advertised compatible-variant fallback. Expired exact evidence remains
   deny-only and registry unavailability cannot revive older evidence. Signed
   release data remains
   optional and dashboard-specific: leave
   `upstream_trust_registry_enabled=false` unless the protected registry has
   been initialized; when enabled, configure only the non-secret Ed25519 public
   key in `upstream_trust_registry_public_key`. Never place the private signing
   seed in add-on options.
   Capability-scoped ha-mcp readmission uses separate disabled-by-default
   `ha_mcp_release_registry_enabled` and
   `ha_mcp_release_registry_public_key` options. Enable them only after the
   fixed ADR-023 release registry and its separately reviewed public trust
   anchor exist. Signed data can select only binary-owned read profiles; it
   cannot add tools, code, writes, or fallback. Never place a private release-
   registry signing key in the add-on configuration.
6. To receive advisory governed-approval pushes on one Companion App device,
   set `approval_notification_service` to that device's exact existing service
   in the form `notify.mobile_app_<device>`. Leave it empty to disable pushes.
   The dedicated adapter cannot call a notify group or any other Home Assistant
   service. Its sole **Open Approval Panel** action still requires an
   authenticated administrator Ingress session; delivery never approves or
   executes a plan. The exact installed add-on slug comes only from Supervisor
   `/addons/self/info`; the bounded resolver retains identity fields and
   discards all other response content.
7. Keep the RC2dev5 dependency-index defaults unless the installation needs a
   different freshness budget: `prewarm_enabled=true`, a 45-second nonblocking
   startup delay, a 600-second soft TTL, and a 3600-second hard TTL. The legacy
   `dependency_index_prewarm` option remains accepted as compatibility data but
   does not control RC2dev5 prewarming.
8. Confirm the beta host port is `8100`, then start the beta add-on.

The repository-aware internal hostname is generated by Home Assistant from the
repository identifier and slug. Its beta-specific suffix is
`hass-mcp-engineering-beta`; production uses a different slug and hostname.

## Verify parallel deployment

Check both health endpoints from an appropriate network location:

```text
http://HOME_ASSISTANT_HOST:8099/health
http://HOME_ASSISTANT_HOST:8100/health
```

Each should return `ok`. Configure the beta MCP client with either authenticated
form; both are handled internally without redirects:

```text
https://BETA_TUNNEL/REDACTED_BETA_SECRET/mcp
https://BETA_TUNNEL/REDACTED_BETA_SECRET/mcp/
```

Direct requests to `/mcp` and `/mcp/` must return `404`. RC2dev12 is immutable
failed history and must not be treated as accepted. RC2dev13 corrected its
reboot and completeness defects, RC2dev14 established practical configuration
plans, and RC2dev16 corrected delegated structured-error normalization without
changing upstream admission or adding search behavior. Version `2.0.1`
promoted the accepted RC1-dev2 behavior without a functional change. Development
version `2.2.0-beta.22` retains governed controlled reload, exact add-on restart,
Home Assistant restart, readback-only reconciliation, and one durable execution
task for each newly executed plan. It adds authority-version-3 policy and
approval bundles without another tool, provider, resource, or fallback. Beta 9
recognizes the exact contract-v2 Beta 6 superseded/invalidated prohibited
record as terminal without migration, reports bounded per-record projection
failures without hiding valid list results, and reconciles policy-class health.
Beta 10 separately recognizes only the exact source-generated contract-v1
expired automation profile and its two complete event sequences without
globally allowlisting expiry.
Beta 11 prevents stale restart records from polling indefinitely and adds
exact 8.0.0 admission without broad 8.x trust. It keeps both new upstream
reads held pending canary evidence.
Beta 12 validates and normalizes only exact 8.0.0's dynamic policy-state
metadata, retains raw strict evidence, and improves bounded mismatch health.
Beta 13 changes only the Engineering dependency pins and retains the Beta 12
provider boundary. Beta 14 applies exact release/model-aware validation to the
complete catalog used by backup and lifecycle, reuses the bounded policy
projection in Dashboard v3, preserves typed Dashboard errors through nested
exception groups, and adds immutable add-on-runtime acceptance.
Beta 15 binds exact 8.0.0 lifecycle add-on detail to its reviewed structured
response envelope and makes immutable runtime acceptance reproduce the live
response cardinality without changing actions or dispatch.
Beta 16 added the disconnected F3-A executor and durable locking foundation.
Beta 17 makes `ha_mcp_engineering.f3.contracts` its sole shipped adapter
contract, preserves the inert dashboard planning and exact-verification work,
and formally defers dashboard execution because no reviewed interface closes
the external-writer lost-update race. Existing adapters, providers, startup,
routing, health, public tools, Dashboard reads, and write reachability remain
unchanged. Beta 18 adds only the disconnected F3-C1 configuration conformance
package against those canonical contracts and the merged single-operation
executor. Route activation and durable multi-operation ownership remain
pending F3-D. Beta 19 layers disconnected F3-C2 operational conformance on the
exact merged Beta 18 base. It consumes canonical F3 objects, places final
locked preflight before caller-owned approval consumption, and keeps durable
intent adjacent to the one provider mutation with no intervening evidence
write. One canonical prepared-authority payload is rehashed at every adapter
boundary and compared with the durable child's expected hash during final
preflight. Executor timing is rejected before claim unless it exactly matches
the operation's 86,400/900/1,800/1,800-second evidence duration; child evidence
must preserve that exact intent-relative deadline through reconstruction. F3
child records remain authoritative; deadline expiry is inclusive and enters
manual review without automatically releasing selective holds. Beta 20 adds
the sole central coordinator, private reconciliation, selective-hold
administration, governed configuration rollback, and route activation. Issue
#92 remains separate: dashboard execution stays unregistered because
external-writer atomicity is not proven. Current public tools, task and plan
schemas, F2 policy, and fallback remain unchanged.
Version 2.2.0-beta.21 independently reviews exact `ha-mcp` 8.1.0. It uses the
runtime 78-tool `tools/list` as catalog authority, delegates 24 reads, holds
exactly two, and keeps every other classification blocked. It adds one exact
8.1.0 HACS-read response projector because upstream moved `success` to the top
level, while classifying the expanded `ha_manage_hacs` tool as persistent write
and exposing none of its actions. Captured lifecycle add-on envelopes are
unchanged from 8.0.0 and reuse the existing strict model. Exact-image evidence
also covers the settings sidecar, shutdown cancellation, and provider
disconnect/readmission. The complete source and artifact boundary is recorded
in
[`../docs/V2_2_0_BETA21_HA_MCP_8_1_0_ACCEPTANCE.md`](../docs/V2_2_0_BETA21_HA_MCP_8_1_0_ACCEPTANCE.md).
Beta 22's approval-review invariant, projection schema, historical behavior,
and validation evidence are recorded in
[`../docs/V2_2_0_BETA22_ACCEPTANCE.md`](../docs/V2_2_0_BETA22_ACCEPTANCE.md).
Beta 7 response truthfulness and current prohibited projection remain
unchanged. Its
update/recovery preflight,
knowledge-provenance, and signed compatibility-registry packages are not loaded
during startup and do not change compatibility admission.
Recovery verification does not manufacture an original provider response, and
durable duplicate-task events contribute exactly once to operation health. It
retains
exact reviewed 7.14.2 compatibility, reviewed 7.14.1 rollback compatibility,
and the hardened MCP SDK boundary. Its changes, rollback, and acceptance
requirements are recorded in
[`../docs/V2_2_0_BETA21_RELEASE_NOTES.md`](../docs/V2_2_0_BETA21_RELEASE_NOTES.md),
[`../docs/V2_2_0_BETA21_ACCEPTANCE.md`](../docs/V2_2_0_BETA21_ACCEPTANCE.md), and
[`../docs/OPERATIONAL_ADMINISTRATION.md`](../docs/OPERATIONAL_ADMINISTRATION.md).
Determine
exact advertised state from version metadata and `scripts/codex-context.py`.

Before connecting an MCP client, require `/ready` HTTP 200 with `ready=true`,
`initial_reconciliation_complete=true`, and `status=ready`. A configured
startup still reporting `initial_reconciliation_pending` is not catalog-ready,
even though `/health` is live.

For any separately authorized deployment, call `server_info(check_ha=false)`
and verify the expected version, complete release commit SHA, and UTC build
time. Then call `list_capabilities` and verify the preserved 25-tool canonical
catalog plus 24 beta-native tools. Without an admitted upstream, MCP
`tools/list` exposes those 49 tools. With exact reviewed release/profile
authority and matching per-tool contracts, it also exposes the exact dynamic
count reported by `upstream_read_gateway`.
Require `version_status`, `compatibility_status`, missing/quarantine counts,
and the separate dashboard provider state to agree with the observed catalog;
do not infer compatibility from the upstream version alone.
Beta 17 added the read-only
`configuration_integrity_analysis` capability; Beta 18 hardens its shared entity
reference classifier without changing the tool catalog or schemas. Its contract,
false-positive safeguards, and conservative orphan behavior are documented in
[`../docs/CONFIGURATION_INTEGRITY_ANALYSIS.md`](../docs/CONFIGURATION_INTEGRITY_ANALYSIS.md).

Beta 12 added `automation_reliability_analysis`; Beta 13 stabilized its correlation and
Beta 14 unified trace normalization. Beta 15 added read-only single-entity impact
analysis. Beta 21 added `handoff_generation`; Beta 22 stabilizes its coverage,
governance lifecycle, automation scope, and counter semantics for structured and
Markdown operational handoffs. Beta 23 corrects shared provider accounting;
Beta 24 hardens governance identity, legacy-write refusal, direct policy, proxy
identity, rate-store eviction, unavailable-provider accounting, and audit bounds
without changing the catalog or schemas. Beta 25 preserves those contracts and
makes approval an external Home Assistant administrator action through the
admin-only Ingress panel. Beta 26 makes plan and challenge expiry idempotent and
immediately effective on reads without changing the catalog or schemas. RC2
freezes those contracts, gives the reviewed direct-read `search_entities`
correction a distinct installable version, and preserves deterministic build
provenance and release compatibility tests. RC3A added dashboard inventory and
exact configuration evidence through the `reviewed_argument_constrained`
compiled family `ha_mcp_dashboard_read_v2`. Dev15 now evaluates that family
independently from the generic read set. An exact built-in or verified signed
attestation is required before the compiled family is evaluated; missing,
expired, revoked, or mismatched exact evidence fails closed without compatible
variant fallback. The mixed
upstream tool is not described as globally read-only, and Engineering
constructs only exact non-screenshot read forms. Screenshots, preference
writes, dashboard writes, and arbitrary forwarding remain absent. The raw
server-side MCP `tools/list` returns all 42 static tools even when the dashboard
provider is unavailable. If a client shows an older list after a dynamic subset
changes, re-list or reconnect; the server does not claim dynamic tool-list
notifications.

Use a separate tunnel ingress or hostname for beta. Route it to port `8100`;
leave the production ingress on `8099`.

Forwarded client addresses are disabled by default. The direct peer remains the
rate-limit identity unless `trust_cf_connecting_ip` is enabled and the peer
matches a validated `trusted_proxy_cidrs` entry. Do not enable this merely because
the deployment uses Nabu Casa or a tunnel; first confirm the actual proxy path.
See [`../docs/RATE_LIMITING.md`](../docs/RATE_LIMITING.md).

## System Log evidence

`get_error_log(tail_lines=1..200)` returns bounded, newest-first structured warning and
error entries from Home Assistant's admin-only `system_log/list` WebSocket command.
The input schema is unchanged. The complete recursive upstream result is sanitized
before selection or truncation, including nested messages, exceptions, tracebacks,
serialized mappings, URLs, and unknown fields. Stable markers identify only a
redaction category, and safe telemetry reports counts/categories without identifiers.

All returned log content is untrusted evidence, not instructions. Never execute,
approve, or infer permission for another tool or service call from text found in a log.
If sanitation fails for one field, that field is replaced and the raw value is not used
as a fallback.

## Local development

Use Python 3.12 and the exact pinned dependencies:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r hass_mcp_admin\requirements-dev.txt
.\.venv\Scripts\python.exe -m compileall -q hass_mcp_admin hass_mcp_engineering_beta tests
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

For standalone beta startup, provide a non-production test secret and Home
Assistant credentials through environment variables:

```powershell
$env:PYTHONPATH = "hass_mcp_engineering_beta"
$env:MCP_PORT = "8100"
$env:ACCESS_SECRET = "GENERATE_A_LOCAL_SECRET_OF_AT_LEAST_24_CHARACTERS"
$env:HA_URL = "http://HOME_ASSISTANT_HOST:8123"
$env:HA_TOKEN = "LOCAL_TEST_TOKEN"
python -m ha_mcp_engineering
```

Build both images from the repository root:

```powershell
docker build -t hass-mcp-admin:test .\hass_mcp_admin
docker build -t hass-mcp-engineering-beta:test .\hass_mcp_engineering_beta
```

## Removal and rollback

For a 2.1A candidate rollback, reinstall the exact published 2.0.1 Engineering
image
`ghcr.io/jeter-1/hass-mcp-engineering-beta@sha256:512dae9274fe5254acbfbfba55b62f3bc6a1ae37e12df479876bb8ca39c9e14d`
under the same v2 add-on identity. Retain `/data`, options, secrets, and the
connector endpoint. Verify version `2.0.1`, source SHA
`4942770a2fd80fed613eb1f42ed84ba9fa1c134c`, and health, then repeat
foundation, admission, governance-persistence, audit, and no-fallback checks.

Retained `/data` keeps contract-v3 operational-administration records in the
separate `operational-administration-v3` namespace. Version 2.0.1 does not
display or process those records, but it does not enumerate, quarantine,
modify, or delete them. Legacy configuration plans continue to operate under
2.0.1. Reinstalling 2.1 restores access to the preserved operational plans and
resumes any required readback-only verification.

Operational plans cannot be approved, applied, or recovered while 2.0.1 is
running. Do not manually move them into the legacy governance namespace, and
do not recreate a pending or `verification_required` operation during the
downgrade. Re-upgrade to 2.1 to resume operational-plan recovery.

Stable v1.1.2 is historical source only. Do not re-enable, rehabilitate, or
describe it as an equivalent recovery path for Engineering v2.

The exact accepted image, source SHA, and complete procedures are in
[`../docs/V2_0_1_RELEASE_NOTES.md`](../docs/V2_0_1_RELEASE_NOTES.md) and
[`../docs/V2_0_1_ACCEPTANCE.md`](../docs/V2_0_1_ACCEPTANCE.md).

## Architecture and migration

See [`../V2_BETA_ARCHITECTURE.md`](../V2_BETA_ARCHITECTURE.md) for module
boundaries, compatibility strategy, current limitations, and the rules for
migrating tools out of the compatibility layer.

See [`OBSERVABILITY.md`](OBSERVABILITY.md) for structured response examples,
the error-code catalog, audit schema, redaction and request-correlation rules,
logging conventions, health fields, startup validation, and the incremental
tool-migration policy.

See [`../docs/RC3A_RELEASE_NOTES.md`](../docs/RC3A_RELEASE_NOTES.md) and
[`../docs/RC3A_ACCEPTANCE.md`](../docs/RC3A_ACCEPTANCE.md) for the dashboard-only
provider contract, secret handling, staged deployment, observation, and
rollback procedure.

See [`../docs/RC2DEV10_RELEASE_NOTES.md`](../docs/RC2DEV10_RELEASE_NOTES.md),
[`../docs/RC2DEV10_ACCEPTANCE.md`](../docs/RC2DEV10_ACCEPTANCE.md),
[`../docs/RC2DEV9_RELEASE_NOTES.md`](../docs/RC2DEV9_RELEASE_NOTES.md),
[`../docs/RC2DEV9_ACCEPTANCE.md`](../docs/RC2DEV9_ACCEPTANCE.md), and
[`../docs/UPSTREAM_TRUST_REGISTRY.md`](../docs/UPSTREAM_TRUST_REGISTRY.md) for
contract-family admission, signed release data, registry/cache operation, future
version review, and the explicit deferred registry-write boundary.

See [`../docs/BETA_DEPLOYMENT.md`](../docs/BETA_DEPLOYMENT.md) for the validated
Windows release workflow, Supervisor cache troubleshooting, and rollback steps.

See [`../docs/CHANGE_GOVERNANCE.md`](../docs/CHANGE_GOVERNANCE.md) for the
external-approval automation change lifecycle, risk model, persistence, audit,
and compatibility boundaries. See
[`../docs/OPERATIONAL_ADMINISTRATION.md`](../docs/OPERATIONAL_ADMINISTRATION.md)
for the 2.1A operational plan, constrained backup provider, exact-once apply,
verification, recovery, audit, health, and rollback-unavailable contracts.
See
[`../docs/HAMCP_089_EXACT_HELPER_STATE.md`](../docs/HAMCP_089_EXACT_HELPER_STATE.md)
for the exact governed `input_boolean` on/off action, direct-provider boundary,
authoritative readback, no-redispatch recovery, and separate reverse-plan rule.

Beta 24 changes automation normalization and plan hashes. Re-create pending or
approved pre-Beta-24 plans; they are not silently migrated. The
compatibility-visible `upsert_automation` always refuses before provider work.
See [`../docs/BETA_24_RELEASE_NOTES.md`](../docs/BETA_24_RELEASE_NOTES.md).

Beta 25 keeps MCP on port `8100` and adds an administrator-only Home Assistant
Ingress approval panel on internal port `8110`. Port `8110` is not host mapped;
do not expose it through a tunnel. `approve_change_plan` requests review but
cannot grant approval. Apply and rollback require separate exact-hash Ingress
decisions. Recreate active pre-Beta-25 plans because legacy caller approvals fail
closed. See [`../docs/EXTERNAL_APPROVAL.md`](../docs/EXTERNAL_APPROVAL.md) and
[`../docs/BETA_25_RELEASE_NOTES.md`](../docs/BETA_25_RELEASE_NOTES.md).

Beta 26 keeps the same external authority and listener boundaries. Expired
plans are terminal and repeated reads do not rewrite them; expired challenges
are excluded from public actionable state, health pending counts, and the
Ingress inbox. See
[`../docs/BETA_26_RELEASE_NOTES.md`](../docs/BETA_26_RELEASE_NOTES.md).

See [`../docs/ENTITY_DEPENDENCY_ANALYSIS.md`](../docs/ENTITY_DEPENDENCY_ANALYSIS.md)
for dependency source coverage, cache/cursor behavior, cautious assessment, limitations,
and connector recreation or `?manifest=beta11` cache-busting guidance.

See [`../docs/AUTOMATION_RELIABILITY_ANALYSIS.md`](../docs/AUTOMATION_RELIABILITY_ANALYSIS.md)
for the Beta 12 evidence sources, Beta 13 correlation/root-cause contracts, and Beta 14
shared trace, analysis-time, coverage, status, pagination, timing, and limitation
contracts.

See [`../docs/CHANGE_IMPACT_ANALYSIS.md`](../docs/CHANGE_IMPACT_ANALYSIS.md) for
Beta 15 operations, deterministic rule and assessment semantics, source coverage,
pagination, timing, security, audit, and read-only live acceptance steps.

See [`../docs/architecture/ADR-002-ENGINEERING-MCP-FACILITATOR.md`](../docs/architecture/ADR-002-ENGINEERING-MCP-FACILITATOR.md)
and [`../docs/TOKEN_EFFICIENCY.md`](../docs/TOKEN_EFFICIENCY.md) for provider routing,
direct-HA exceptions, verified Standard MCP capability limitations, and bounded
analytical response requirements.

See [`../docs/SECURITY.md`](../docs/SECURITY.md) for the four Phase 3C direct-read
policies, write-boundary protections, and secret-handling requirements.

See [`../docs/INCIDENT_CORRELATION.md`](../docs/INCIDENT_CORRELATION.md) for the
Beta 20 `incident_correlation` schema, evidence and coverage matrix, normalized
events, deterministic correlation/confidence rules, bounded cursor lifecycle,
audit/health contract, and entirely read-only acceptance sequence. Beta 20
reports `2.0.0-beta.20`, 37 registered tools, and 25 canonical tools. It changes
no public schema or tool registration, so connector recreation is not normally
required.

See [`../docs/HANDOFF_GENERATION.md`](../docs/HANDOFF_GENERATION.md) for the
handoff types, evidence/statement/completion/authorization contracts, structured
and Markdown output, signed pagination, health/audit behavior, limitations, and
the entirely read-only deployed acceptance procedure. RC2 reports 38
registered/25 canonical tools and an empty planned capability list. See
[`../docs/RC2_RELEASE_NOTES.md`](../docs/RC2_RELEASE_NOTES.md) and
[`../docs/RC2_ACCEPTANCE.md`](../docs/RC2_ACCEPTANCE.md) for release-freeze,
upgrade, provenance, deployed acceptance, soak, and rollback instructions.

RC2dev4 release-hardening and its fixture-only transport bake harness are
documented in
[`../docs/RC2DEV4_RELEASE_NOTES.md`](../docs/RC2DEV4_RELEASE_NOTES.md) and
[`../docs/RC2DEV4_ACCEPTANCE.md`](../docs/RC2DEV4_ACCEPTANCE.md).
