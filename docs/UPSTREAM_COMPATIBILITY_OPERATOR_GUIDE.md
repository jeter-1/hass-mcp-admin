# Upstream compatibility operator guide

Use this guide to interpret a changed `homeassistant-ai/ha-mcp` release without
turning a version string or catalog difference into new authority. The active
runtime decision is
[`ADR-006`](architecture/ADR-006-CONTRACT-LEVEL-UPSTREAM-COMPATIBILITY.md).

## Runtime compatibility check

1. Verify `observed_upstream_server_name`,
   `observed_upstream_server_version`, `observed_protocol_version`, and
   `observed_identity_status`. The endpoint must identify as `ha-mcp`, match an
   explicit reviewed release entry, and negotiate the supported MCP protocol.
   The compiled registry currently authorizes exact 7.14.1, 7.14.2, 8.0.0,
   and reviewed immutable-OCI 8.1.0, 8.1.1, 8.2.0, 8.4.1, and 8.4.3 entries. Release-page
   executables and MCPB are excluded because they report 8.0.0 at runtime.
   Identity, unreviewed-version, malformed-version, or protocol failure is
   global and must not be worked around with a self-advertised schema match.
2. Read `get_server_health.upstream_read_gateway`.
   `version_status=rejected_unreviewed` is bounded diagnostic evidence that the
   release lacks authority; it cannot admit a tool or permit dispatch.
   `rejected_identity` and `rejected_protocol` identify the other global
   prerequisite failures. Require the separately reported
   `compatibility_status`.
3. Compare reviewed automatic-read, exact-matched, missing, quarantined, and
   unreviewed counts. The reviewed accounting must satisfy:

   ```text
   exact matched + missing + quarantined = reviewed automatic reads
   ```

4. Confirm every quarantine entry contains only a bounded tool name, stable
   input-schema, full-runtime-description, annotation, or output-contract
   reason, and expected/observed fingerprints. Do not request raw schemas,
   descriptions, registry content, or endpoint material through health or
   logs. Annotation matching preserves optional-field presence; absence is not
   converted into an assumed Boolean default.
5. Confirm missing and quarantined tools have no route while other exact
   matches remain available. New or newly visible tools, including apparent
   reads and writes, must remain unavailable.
6. Inspect `upstream_dashboard` independently. Dashboard compatibility must not
   be inferred from generic-read status, and dashboard failure must not remove
   generic reads.
7. Require zero generic writes, arbitrary forwarding, direct-HA fallback, and
   provider fallback.

With the current 51 static tools (25 canonical and 26 Engineering-native), the
exact 7.14.x 26-read profile
produces 77 registered tools. The exact 8.0.0 and 8.1.0 24-read profiles each
produce 75
and hold exactly `ha_search` and `ha_get_operation_status`. Exact 8.1.1 and
8.2.0 each produce 76 with 25 delegated reads and hold only
`ha_get_operation_status`. Exact 8.4.1 and 8.4.3 also produce 76. The exact
8.4.3 profile independently admits its 25 reads, including the stricter
read-only `ha_search` time-budget contract, while `ha_get_operation_status`
remains held. One missing or
quarantined read reduces the corresponding total by one. Additional blocked,
held, or unreviewed tools do not increase the registered count.

Interpret `compatibility_status` as follows:

- `exact`: all reviewed reads match at the reviewed evidence version;
- `partial`: at least one matches and at least one is missing or quarantined;
- `incompatible`: a stable catalog was evaluated with zero matches; and
- `reconciling`: a bounded catalog reconciliation is in progress; and
- `unavailable`: no valid catalog identity is currently available.

## Choosing a search or analysis path

Use the narrowest existing capability that answers the question:

| Need | Preferred existing capability |
| --- | --- |
| Find entities by name, domain, area, or state | Filtered `ha_search` or `search_entities` |
| Find exact static references to an entity | `entity_dependency_analysis` |
| Read a known automation configuration | `ha_config_get_automation` or `get_automation_config`, according to the required provider surface |
| Search arbitrary text across configuration bodies | `ha_search` with explicit `search_types` |
| Bound a broad configuration scan | `config_time_budget`, accepting truthful partial coverage |
| Perform a routine household action | The standard Home Assistant MCP action capability, not an Engineering fallback path |

Broad automation configuration-body search can be materially slower when
upstream bulk access is unavailable and `ha-mcp` must fetch automations
individually. `config_time_budget` bounds the configuration-fetch phase; it is
not necessarily a strict end-to-end wall-clock deadline. A partial result is not
exhaustive.

The Engineering dependency index performs structured entity-reference analysis;
it is not an arbitrary free-text search index. The optional companion component
is not required by the Engineering server and must not be recommended merely as
a routine latency fix. Issue 57 introduces no new Engineering search
implementation and does not alter `ha_search`.

## Call-time contract check

Admission is not the final dispatch check. In the same MCP session that would
perform `tools/call`, Engineering first obtains `tools/list`, confirms the
bounded server identity, exact reviewed release/profile, and protocol, requires
the selected upstream target exactly once, and compares that target's complete
reviewed contract with the currently registered route. Missing, duplicate,
changed-target, or unreviewed-version evidence stops before `tools/call`.

A different but syntactically valid upstream version is observed evidence only.
Without a current valid signed release entry selecting one compiled profile,
Engineering stops before dispatch, enters `blocked_incompatible_upstream`, and
waits for the slow periodic reconciliation. A signed entry still cannot
authorize a live descriptor by self-attestation: only independently matching
binary-known capabilities return. A malformed version, different server name,
or unsupported protocol is a global pre-dispatch failure.

The call-time check is target-local. Unrelated new, changed, malformed, or
duplicate unreviewed descriptors cannot authorize a route and do not block an
exact selected target. Periodic or event-triggered catalog reconciliation
records them only as bounded, redacted unreviewed anomalies. It never exposes
them or infers policy from their contents.

### Exact Home Assistant response adapters

Response adapters require exact upstream and Home Assistant version authority;
they never inherit through a version range. The current reviewed adapter is
`ha-get-device-composite-ha-2026.8-v1`. It applies only to upstream 8.1.0 or
8.1.1 on exact Core 2026.8.0 when `ha_get_device(device_id=<old composite id>)`
returns the proven incoherent empty entity join for a multi-entry synthesized
device. It reads Core's composite-split map and entity registry, restores only
the entity membership fields, and reports its identifier in response metadata.
Malformed split or registry evidence fails closed. A complete upstream result,
a non-composite device, another Core version, or another upstream release is
returned without adaptation. This is a reviewed response repair, not provider
fallback; `fallback=none` and `fallback_occurred=false` remain authoritative.

## Recovery versus incompatibility

Use `/health` for process liveness and `/ready` for initial catalog readiness.
The readiness response is deliberately bounded to `ready`,
`initial_reconciliation_required`, `initial_reconciliation_complete`, and
`status=ready|initial_reconciliation_pending`. With upstream configured,
HTTP 503 from `/ready` and authenticated MCP paths means the initial
reconciliation has not yet returned a stable or terminal result. Do not point a
schema-caching client at the MCP path until `/ready` returns HTTP 200 with
`ready=true`; otherwise it could retain a transient static-only catalog.

Use the fast bounded retry state only for endpoint startup and transient
transport availability. A discovered missing or incompatible contract uses the
separate slow compatibility-reprobe state. Do not restart-loop Engineering or
reduce the compatibility delay merely to make a stable upstream difference
look transient.

Connection and timeout failures use the capped fast cadence. A transient
404/session-not-ready classification receives a bounded 600-second startup
grace, matching the full-host reboot gate, and then falls back to the slow
cadence if it remains unresolved.

The fast lane reports `retry_count`, `next_retry_delay_seconds`, and
`reconciliation_status`. The slow lane reports
`compatibility_reprobe_interval_seconds`,
`last_compatibility_reprobe_at`, `next_compatibility_reprobe_at`, and
`compatibility_reprobe_status`. Do not interpret one lane's counters or
timestamps as activity by the other.

An admitted exact subset remains usable while slow reprobe waits. Clients that
cache `tools/list` must re-list or reconnect after the subset changes; the
server does not claim `tools/list_changed` delivery.

Equivalent same-session calls that overlap a probe do not starve it: a bounded
admission-relevant token can prove that the reviewed outcomes are unchanged.
A different token keeps the probe stale. Health exposes
`stale_reprobe_retry_armed`; only the first consecutive stale mismatch receives
an immediate retry, and continued churn returns to the slow cadence.

Dev15 acquires an immutable current-generation route snapshot under a short
lease, then releases registry coordination before same-session network I/O.
Generic delegated calls are not globally serialized. Each still adds a bounded
paginated `tools/list` before `tools/call`. A route retired before
pre-dispatch validation cannot dispatch; a call already committed after
successful validation may finish, but cannot republish or revive a retired
generation. Characterize `ha_search` latency and concurrent delegated-read
throughput, and prove a slow read blocks neither another read nor
reconciliation.

## Controlled upstream update sequence

Do not let a household or production updater install an unreviewed `ha-mcp`
release merely because a new version is available. Use this sequence:

1. Detect the upstream release and resolve its exact immutable source and image
   identity.
2. Use `scripts/review_upstream_read_release.py capture` against that exact
   image in an isolated disposable environment. Normalize and fingerprint the
   capture twice and require byte-for-byte deterministic evidence.
3. Use the tool's `diff` and `report` operations to review every input,
   description, annotation, output, runtime, policy, delegation, and dashboard
   change. A generated `candidate` entry is deliberately marked unapproved and
   cannot become runtime authority without a separately reviewed source change.
4. For a candidate covered by a reviewed family, use
   `scripts/admit_upstream_compatibility_family.py` with two byte-identical
   captures and every exact immutable identity. The command has no latest,
   range, wildcard, or self-discovery mode. Review its drift classification and
   canonical decision. Unknown drift rejects; material drift requires explicit
   human handling or selective holds.
5. When the release selects an existing binary profile, run the protected
   `Prepare ha-mcp release-registry update` workflow from main. It repeats the
   exact image capture, rejects any mutation, and opens a signed data-only draft
   PR. Otherwise add a new compiled profile through an Engineering release.
   Dashboard authority always uses its separate exact-attestation path.
6. Upgrade `ha-mcp` only after the target has applicable exact reviewed
   authority and its disposable contract evidence passes. Retain the exact
   prior image as the rollback target.
7. Let Engineering reconcile the live catalog without restarting it.
8. Verify the observed identity, selected registry entry, source/image
   evidence, generic matched/missing/quarantined counts,
   delegated tool count, dashboard status, and zero-write/fallback invariants.
9. Roll back `ha-mcp` if the required subset is not compatible or the result
   differs from the disposable review.

An unattended update gate may proceed only when the target already has
applicable exact reviewed release/profile authority. A bounded pre-upgrade
contract check is evidence for review; it cannot authorize its own release.
The compiled registry is source-controlled and contains human-owned policy; it
does not fetch policy or automatically track an upstream latest tag.

Rolling between compiled releases through exact 8.4.3, or to a valid signed
release selecting one compiled profile, triggers fresh discovery and atomic
route replacement in the same Engineering image. Unknown unsigned releases admit no
delegated reads. An exact reviewed release may still expose a safe exact subset:
changed reads are quarantined, new tools remain unreviewed, removed tools are
reported, and write or mixed classifications remain blocked. A full-catalog
fingerprint is diagnostic evidence and never substitutes for per-tool checks.

When the upstream `ha-mcp` version changes, a delegated route that observes the
new version fails closed until compatibility reconciliation admits that exact
reviewed release. Automatic compatibility reprobes are periodic and can take up
to approximately 15 minutes. Restarting the Engineering add-on forces immediate
rediscovery. No fallback is used during the transition.

The dashboard and delegated-read providers reconcile independently and may
temporarily report different observed upstream versions. Delegated calls can
fail during that interval. This is an availability limitation, not a relaxation
of write safety or admission policy. Wait for reconciliation or restart
Engineering before treating those delegated-read failures as a connector
defect. Do not claim that a live artifact digest or source revision was verified
from MCP discovery; deployment artifact verification remains an operator
responsibility.

For exact 8.1.0, 8.1.1, 8.2.0, 8.4.1, and 8.4.3, treat MCP `tools/list` as catalog observation. Source-only,
conditional, hidden, and nonadvertised declarations are review diagnostics,
not additional runtime tools. Require 78 unique advertised names and complete
classification. `ha_manage_hacs` is one persistent-write tool because its
8.1.0 action enum includes `remove`; 8.2.0, 8.4.1, and 8.4.3 additionally include
`update_information`, which remains classified as a persistent write. None of
those actions is an Engineering route. `ha_get_hacs_info` alone uses the exact
top-level-success response model.

Lifecycle installed-version binding uses Supervisor's endpoint-bound installed
add-on inventory, not the source tag's add-on `config.yaml`. The 8.1.0 tag
contains a stale 8.0.0 add-on version while the published add-on metadata,
image label/package, and MCP initialize identity are 8.1.0. Any disagreement
must fail before restart plan persistence. Controlled reload and Home Assistant
restart do not consume that inventory; do not infer a wider impact without a
focused test.

For 8.1.1, the tagged add-on value is stale at 8.1.0 while the published add-on
package, labels, MCP initialize identity, and Supervisor inventory are exact
8.1.1.

For 8.4.1 and 8.4.3, the exact published-image catalogs contain 78 tools and use App
terminology. The source-checkout-only catalog fingerprint is not artifact
authority. Error contracts are evaluated per bound capability: the changed
search validation envelope can quarantine `ha_search` without disabling
unrelated reads. Exact dashboard getter/setter authority is separately reviewed
for both releases; backup and lifecycle provider surfaces remain held and must
not inherit delegated-read admission.

The generic release registry is fixed at
`upstream-trust/ha-mcp-release-registry.json`. Its workflow writes only that
signed journal, one bounded evidence record, and its generated index. Production
environment/key creation, registry publication, and the add-on public-key and
enable options are distinct later operations.

## Dashboard exact-attestation path

The dashboard provider still allows only `ha_config_get_dashboard` through the
two fixed non-screenshot Engineering operations. Evaluate its exact compiled
input, safety, output/hash, and runtime contract.

- Require an exact-version built-in or verified signed attestation first.
  Revocation, missing exact authority, or fingerprint mismatch blocks that
  release. Never substitute an older release entry or a self-advertised
  compatible variant.
- A semantic mismatch remains unavailable regardless of version or signed
  data. Descriptive-only changes may remain informational only when every
  dispatch-relevant projection is exact.

## Optional dashboard attestation setup

1. Generate an Ed25519 seed and public key in a protected administrative
   environment. Do not use a shell command that echoes the seed.
2. Create the GitHub environment `upstream-attestation-signing` with required
   reviewers and no untrusted branch access.
3. Add environment secrets:
   `UPSTREAM_TRUST_REGISTRY_SIGNING_KEY` (base64 raw 32-byte seed),
   `UPSTREAM_TRUST_REGISTRY_PUBLIC_KEY` (base64 raw public key), and a bounded
   `UPSTREAM_TRUST_REGISTRY_KEY_ID`.
4. Put only the public key into the Engineering Beta add-on option
   `upstream_trust_registry_public_key`, then explicitly enable
   `upstream_trust_registry_enabled` in a later deployment window. Do not repeat
   the key or endpoint in tickets/logs.

The dashboard-attestation trust options above do not authorize automatic
readmission. ADR-023 uses the separate disabled-by-default
`ha_mcp_release_registry_enabled` and
`ha_mcp_release_registry_public_key` options. Its registry location is fixed in
the binary, redirects are rejected, and signed data may select only an existing
binary-owned read profile and adapter. Do not reuse either private signing key.
Production release-registry signing and publication require a separately
reviewed protected workflow; no private key belongs in add-on options.

## Review a dashboard release for provenance

1. Open GitHub Actions on `main` and select
   **Prepare ha-mcp compatibility attestation**.
2. Enter only the exact stable version, such as `7.14.2`.
3. Approve the protected environment after confirming the upstream release is
   intentional.
4. The workflow resolves the exact official tag/source and GHCR image, verifies
   image/source ancestry and allowed metadata-only packaging delta, records
   amd64/arm64 platform digests and provenance, starts the exact image by digest
   against disposable Home Assistant, and extracts the actual MCP contract.
5. The workflow invokes only dashboard inventory and exact config reads with
   `include_screenshot=false`; it verifies the dashboard hash and records zero
   write dispatches.
6. Semantic normalization must match the compiled family. Descriptive changes
   may normalize away; argument, annotation, output/hash, protocol or unknown
   semantic changes fail. Do not create an attestation to bypass an
   incompatibility.
7. The workflow signs a new entry and opens a draft PR containing only registry,
   signature, bounded evidence and generated index files.

## Review the data PR

Confirm exact version, source commit/tag, official immutable image/index/platform
digests, image revision/created time, provenance/SBOM result, runtime identity,
ordinary catalog fingerprint, all four normalized fingerprints, fixed argument
shapes, dual-hash evidence and zero write dispatches. Confirm the sequence
increased once and no prior entry was silently replaced.

Reject the PR if the release adds a required argument, changes a type/default,
loosens `additionalProperties`, changes safety annotations, adds an output schema,
changes the dashboard return/hash contract, changes the protocol, or cannot prove
the exact source/image relationship. Do not whitelist a fingerprint to bypass a
compiled-family rejection.

The PR must not change runtime code, workflows, Dockerfiles, dependencies,
Engineering versions, public schemas, tool allowlists, capability metadata, or
governance. Merge remains a separate human action. This dashboard evidence
workflow does not attest the 26 generic read contracts and cannot expand them.

## Revocation and recovery

Normal attestation creation cannot replace or re-add an existing/revoked release.
Revocation requires a separately reviewed higher-sequence data change signed by
the protected key. After merge, verify runtime health reports the new sequence
and `rejected_revoked_attestation` before relying on it.

On refresh/signature/expiry failure, do not delete the cache or disable security
checks to restore access. A valid exact-version entry remains authoritative,
including its revocation state. An expired cached exact entry remains deny-only
until valid higher-sequence registry data supersedes it. Registry
unavailability or hard expiry cannot be replaced by self-advertised contract
equality or older binary evidence. Compare the bounded failure category,
repository registry/signature files, protected key ID/public key, sequence and
expiry. Never paste registry private key material into debug output.

## Deferred generic release support

Dev15 does not generalize the dashboard registry to generic reads.

- Dev16 may define signed, data-only provenance and revocation evidence for
  binary-owned generic read contract families, including cache, expiry,
  rollback/replay protection, revocation, and runtime refresh. It must not
  change classifications, routes, arguments, or fallback.
- Dev17 may automate immutable source/image resolution, disposable runtime
  extraction, catalog and annotation diffing, semantic fixture and dashboard
  testing, zero-write verification, compatibility reports, and draft evidence
  updates.

Until those milestones are separately reviewed, the 7.14.1 generic policy is
the only compiled generic release/profile authority. Other observed versions
remain unavailable even when their self-advertised contracts match. Automatic
no-rebuild admission for reviewed newer releases is deferred to Dev16.

## Capability-scoped automatic-readmission reference model

[ADR-020](architecture/ADR-020-CAPABILITY-SCOPED-AUTOMATIC-READMISSION.md)
defines a test-only decision and race specification for a later
capability-scoped readmission integration. The executable reference model is
intentionally non-authoritative: production startup, routing, providers,
health, tool registration and admission do not import or package it. It is not
deployed functionality and cannot grant admission or execution authority.
Operators must continue to use the exact runtime compatibility checks in this
guide; there is no reference-model configuration to enable.

The deterministic compatibility contract harness treats Core, ha-mcp and
configured transport as independent surfaces with separate published,
verifying, and bounded retirement lifecycles. Effective authority is projected
only to its owning surface. A global registry refresh causes reevaluation, not
automatic retirement of every surface, and updating one surface cannot retire
another surface's compatible route. Leases are surface-bound, exact,
single-use values: one atomic commit consumes a lease, duplicate commit is
rejected, and a retired generation cannot start a new call. Issued leases,
active commits, counters, and retained retirement diagnostics have explicit
bounds; exhaustion fails closed without changing unrelated authority. A
successful reconnect restores only transport observation; it does not restore
provider authority. Unknown Core versions may later regain only separately
authorized structural read profiles, never template semantics, configuration
semantics or governed writes by implication.

The reference contract is capability-scoped, not version-tolerant. Signed data
may select only binary-known profiles, adapters, and capability contracts.
Unknown semantic behavior remains held, and action or write capabilities remain
unreachable. Versioned multi-step vectors use literal inputs and expected
results through an implementation-neutral adapter so a later production
coordinator can replay them without importing the test implementation. The
shared vectors cover cross-surface authority isolation, disconnect and
restoration, one-shot commit and retirement races, lifecycle capacity,
registry limitations, every prohibited capability kind, and separate bounded
sanitized health and audit projections. Those projections expose counts,
dispositions, reason codes, generations, and fingerprints only; they do not
expose leases, commits, sessions, endpoints, raw authority, catalogs, schemas,
credentials, or exception text.

Operational work is deferred: runtime coordination, observation wiring,
signed-registry fetching/caching, ha-mcp and Core integration, proxy recovery,
dynamic client catalog refresh, release staging, deployment and runtime
acceptance all require a new release and separate review.

The current server does not advertise `tools.listChanged=true` and does not
claim `notifications/tools/list_changed` delivery. Clients must reconnect or
explicitly re-list after the existing runtime changes its dynamic catalog.
Notification support remains a separate pinned-SDK, protocol and client
compatibility decision.

## Deferred registry administration writes

The upstream 7.14.1 `ha_set_entity` and `ha_set_device` contracts are retained as
non-runtime design evidence only. They are destructive and cannot be activated by
a signed registry entry. A future governed registry-administration milestone must
separately design proposal, external approval, stale-state, apply, verification,
rollback and audit semantics before either operation can enter Engineering.
