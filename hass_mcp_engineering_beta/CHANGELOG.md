# Changelog

## 2.0.0-rc2-dev4 - release hardening

- Removed every reachable legacy direct-write implementation and made the
  capability catalog report governed redirects, prohibitions, and unavailable
  providers truthfully.
- Distinguished plans that merely require approval from plans with a pending
  external Ingress challenge; pre-approval principal separation is now
  explicitly not evaluated.
- Added single-flight dependency-index builds, cold-build profiling, explicit
  build-state/expiry semantics, a bounded eight-read pool, optional one-shot
  connectivity-gated prewarming, and expected-outcome metrics.
- Hardened relayed System Log and structured-log redaction, dashboard not-found
  handling, upstream health freshness, summary response size, and reliability
  root-cause deduplication.
- Added a fixture-only transport bake harness and an RC2dev4 acceptance guide.

## 2.0.0-rc2-dev3

- Reproduce the exact public `ha-mcp` 7.13.0 add-on runtime descriptor and
  record its immutable artifact and dependency evidence.
- Separate the complete input-schema and reviewed security-contract blocking
  fingerprints from the complete runtime-descriptor observability fingerprint.
- Permit only the proven presentation-only `_meta.ha_mcp` exposure/pinning
  delta while preserving exact identity, version, protocol, input schema,
  safety annotations, output contract, argument builders, and tool allowlist.
- Report expected/observed component fingerprints and bounded descriptive
  versus semantic runtime drift without exposing schemas or endpoint data.
- Publish `readOnlyHint=true` on both Engineering dashboard tools and prove the
  raw server-side MCP `tools/list` contains all 40 tools even while the provider
  is unavailable; clients may need connector refresh after schema caching.
- Stage dev3 in `.release/next-version` while Home Assistant metadata remains
  at published dev2 until the image is built, anonymously verified, and
  atomically promoted.

## 2.0.0-rc2-dev2

- Add the reviewed `ha_mcp_7_13_dashboard_read_v1` trust profile for upstream
  `ha-mcp` 7.13.0 at commit
  `f4eb53621ccb814cb7123d2811e06eda3577129c`.
- Distinguish contract-level read-only trust from the explicit
  `reviewed_argument_constrained` exception for the mixed-operation dashboard
  tool.
- Pin initialize identity, version, protocol, exact annotations, input schema,
  and the complete canonical tool contract.
- Construct only non-screenshot inventory and exact-configuration argument
  shapes; reject rendering, preferences, unknown arguments, and other tools
  before transport dispatch.
- Preserve the verified upstream 16-character optimistic-lock hash and
  independent 64-character Engineering evidence hash.
- Stage dev2 in `.release/next-version` while leaving Home Assistant metadata at
  dev1 until the image is built, anonymously verified, and atomically promoted.
- Replace manual tag-first publication with one main-only, repository-locked
  promotion transaction that publishes and verifies before pushing the release
  commit and annotated tag.
- Preserve 40 registered/25 canonical/zero planned capabilities, public input
  schemas, governance, external approval, audit, direct-HA policies,
  unavailable Standard HA MCP delegation, and production v1.1.2.

## 2.0.0-rc2-dev1

- Add the RC3A read-only `upstream_dashboard` provider using the maintained
  `mcp==1.9.0` streamable-HTTP client already present in the image.
- Add bounded `list_dashboards` and `get_dashboard_config` tools backed only by
  upstream `ha_config_get_dashboard`.
- Verify the upstream 16-character dashboard optimistic-lock hash against an
  exact local recomputation and expose a distinct 64-character Engineering
  evidence hash calculated from complete raw JSON before sanitization.
- Classify rejected endpoint/secret paths, connection failures, and genuine
  timeouts deterministically without exposing endpoint details.
- Use AwesomeVersion 25.8.0 to select the installable RC3A development version
  `2.0.0-rc2-dev1`; the rejected `2.0.0-rc.2.rc3a.1` form is incomparable.
- Validate the upstream read annotation and minimum input schema, record
  sanitized identity and schema/catalog fingerprints, and fail closed on
  missing or incompatible capabilities.
- Treat the complete upstream URL as a password-style secret and exclude it
  from responses, health, logs, audit records, errors, and tracebacks.
- Preserve all 25 canonical tool contracts, Standard HA MCP unavailability,
  direct-HA policies, governance, external approval authority version 2, and
  production v1.1.2.
- Add no dashboard write, delete, backup, service, physical-action, generic
  upstream forwarding, or live deployment path.

## 2.0.0-rc.2

- Supersede immutable RC1 because Home Assistant could continue resolving the
  original `2.0.0-rc.1` image after the reviewed source correction retained the
  same add-on version and image tag.
- Publish the already reviewed `search_entities` correction under a distinct
  installable version: explicit `transitional_direct` routing to
  `direct_ha_api`, policy `bounded_entity_state_search`, one bounded read-only
  `/states` inventory, deterministic slim results, and no fallback.
- Preserve the Beta 26 public schemas and enums, 38 registered tools, 25
  canonical tools, zero planned capabilities, schema version 1, external
  approval authority version 2, and production v1.1.2 unchanged.
- Retain deterministic multi-architecture GHCR provenance and publication for
  amd64, arm64, and arm/v7 with immutable version and source-commit tags.

## 2.0.0-rc.1

- Freeze the accepted Beta 26 public catalog, schemas, governance semantics, and
  external authority version 2 for release candidate validation.
- Add deterministic image-build provenance for the existing `build_sha` and
  `build_time` fields, with safe `unknown` fallbacks for local development.
- Add clean-install and persisted Beta 26 upgrade compatibility coverage plus
  the RC1 deployment, acceptance, soak, and rollback procedures.
- Preserve 38 registered tools, 25 canonical tools, zero planned capabilities,
  schema version 1, the Beta/RC ports and slug, and production v1.1.2 unchanged.

## 2.0.0-beta.26

- Make plan expiration a single terminal transition; repeated plan, list,
  health, Ingress, and handoff reads no longer rewrite an expired record or
  duplicate lifecycle events, audit entries, logs, or `updated_at` changes.
- Resolve external-challenge expiry through one governance lifecycle path so
  public reads, health, Ingress, approval requests, apply, and rollback agree on
  the effective current state.
- Exclude expired challenges from actionable plan views, the Ingress inbox, and
  `pending_challenge_count`; eligible plans may request one fresh challenge
  bounded by the plan expiry while the old challenge remains unusable.
- Preserve fail-closed apply/rollback enforcement, external authority version 2,
  distinct apply/rollback approvals, and all Beta 25 principal-separation rules.
- Preserve 38 registered tools, 25 canonical tools, zero planned capabilities,
  schema version 1, and every Beta 25 public input schema. Production v1.1.2 is
  unchanged.

## 2.0.0-beta.25

- Make `approve_change_plan` request external review without granting approval;
  the public input schema is unchanged.
- Add an administrator-only Home Assistant Ingress approval panel on unmapped
  internal port 8110 with escaped HTML, POST-only decisions, one-time CSRF and
  strict bounds/security headers.
- Persist 15-minute authority-version-2 challenges bound to the exact plan hash,
  kind, target, operation and risk; preserve idempotency, expiry, invalidation,
  replay resistance and restart recovery.
- Require separate external apply and rollback approvals and make rejection a
  terminal historical state.
- Fail active Beta 24 caller approvals closed; never silently migrate authority.
- Update handoff, audit and health contracts for external-pending, approved,
  consumed, rejected, expired and invalidated states.
- Add disposable, digest-pinned Home Assistant Core 2026.7.2 REST/WebSocket,
  id-less automation, configuration-validation and trace contract tests.
- Preserve 38 registered tools, 25 canonical tools, zero planned capabilities,
  schema version 1 and every existing public input schema. Production v1.1.2 is
  unchanged.

## 2.0.0-beta.24

- Treat top-level automation `id` as explicitly verified identity metadata,
  outside behavioral normalization, fingerprints, and plan hashes.
- Fail closed for legacy `upsert_automation` before provider dispatch and require
  the governed plan/approval/apply workflow.
- Require an explicit matching policy for every direct Home Assistant exception.
- Make forwarded client identity untrusted by default and add bounded trusted
  proxy CIDR configuration for validated IPv4/IPv6 forwarding.
- Replace whole-store rate-bucket resets with atomic bounded LRU eviction.
- Exclude known-unavailable pre-dispatch providers from provider request/failure
  counters and clamp audit-log reads to 1–500 records.
- Preserve 38 registered tools, 25 canonical tools, zero planned capabilities,
  and all public schemas. Production v1.1.2 remains unchanged.

## 2.0.0-beta.23

- Require an explicit provider-dispatch assertion before global provider request,
  success, partial, or failure counters change.
- Keep request/cursor validation, authentication, rate limiting, policy rejection,
  and snapshot-only continuation out of provider failure accounting.
- Preserve one attributable failure for real Engineering/direct provider errors
  and timeouts; keep successful partial coverage non-failing.
- Retain all Beta 22 handoff corrections, 38 registered/25 canonical tools, an
  empty planned list, all public schemas, and production v1.1.2 unchanged.

## 2.0.0-beta.22

- Normalize shared handoff evidence to one effective coverage record per logical
  source, removing synthetic dependency failures while preserving real failures.
- Treat expired, superseded, rolled-back, and validation-only terminal plans as
  retained history rather than active pending work or authorization requirements.
- Resolve automation internal IDs through one bounded state inventory and freeze
  successful automation entity IDs into structured, Markdown, and cursor scope.
- Define `risk_count` as the number of handoff items in the `risks` section and
  keep open/authorization counts limited to current actionable work.
- Retain 38 registered/25 canonical tools, all public schemas, an empty planned
  list, the read-only boundary, and production v1.1.2 unchanged.

## 2.0.0-beta.21

- Add read-only Engineering-native `handoff_generation`, increasing the beta
  catalog to 38 registered/25 canonical tools with no existing schema changes.
- Add system-status, focused-review, incident, and change handoffs with explicit
  fact/inference/recommendation/limitation and authorization contracts.
- Require apply plus verification evidence before work is called completed;
  preserve pending, failed, and rolled-back lifecycle truth.
- Add deterministic structured/Markdown output, five-minute signed sanitized
  pagination, bounded audit, dedicated health counters, and Beta 20 coverage
  semantics without any write capability.
- Remove `handoff_generation` from planned capabilities; no planned feature
  capability remains. Production v1.1.2 is unchanged.

## 2.0.0-beta.20

- Correct incident-correlation coverage so a successfully built, usable but
  incomplete dependency index has `failure_category=null` rather than
  `provider_upstream_error`.
- Distinguish complete, partial usable, failed, item-failed, unsupported,
  truncated/retention-limited, and not-requested evidence through shared coverage
  normalization.
- Separate hypothesis `missing_evidence` from stable bounded
  `coverage_limitations` while retaining supporting dependency references.
- Count only actual failed sources or source operations in health and provider
  failure telemetry; preserve partial assessment and cursor snapshot behavior.
- Reject non-canonical signed cursor encodings fail-closed without changing valid
  cursor behavior.
- Retain 37 registered/25 canonical tools, all existing public schemas, the
  read-only boundary, planned `handoff_generation`, and production v1.1.2.

## 2.0.0-beta.19

- Add read-only Engineering-native `incident_correlation`, increasing the beta
  tool count to 37 while retaining 25 canonical tools and all existing schemas.
- Correlate bounded automation, entity, trace, history, logbook, structured log,
  registry, dependency, integrity, and reliability evidence into deterministic
  ranked hypotheses with supporting and contradicting evidence.
- Add explicit confidence, causal-status, severity, coverage, event-normalization,
  clustering, pagination, security, audit, and health-counter contracts.
- Preserve the Beta 16–18 signed snapshot lifecycle; continuation performs no
  upstream collection, index work, classification, or recorrelation.
- Add no write capability; production v1.1.2 remains unchanged and
  `handoff_generation` remains planned.

## 2.0.0-beta.18

- Replace broad dotted-token template scanning with context-aware extraction from
  explicit entity-bearing fields and recognized Home Assistant template helpers.
- Reject numeric fragments, versions, IP addresses, URLs, hostnames, service
  names, member access, and arbitrary dotted prose as exact entity references.
- Preserve exact literal references for `states`, `is_state`, `is_state_attr`,
  `state_attr`, and `expand`, while reporting dynamic arguments separately and
  without an invented target.
- Harden the shared canonical entity-ID validator and retain deterministic
  deduplication, evidence IDs, index fingerprints, pagination, health, and audit
  contracts.
- Preserve all 36 public tools, all existing input schemas, the read-only safety
  model, and production v1.1.2.

## 2.0.0-beta.17

- Add the Engineering-native, read-only `configuration_integrity_analysis`
  tool, increasing the beta manifest from 35 to 36 tools while retaining 25
  canonical tools.
- Detect exact missing entity references, references to disabled and
  registry-only entities, conservative orphan-registry candidates, and
  unresolved dynamic references as distinct evidence-backed findings.
- Reuse the shared dependency index plus one bounded state inventory and one
  entity-registry inventory; unsupported coverage remains explicit.
- Reuse Beta 16 signed, immutable pagination snapshots with upstream-free
  continuation, fixed provenance, whole-analysis totals, and fail-closed
  cursor validation.
- Add deterministic deduplication, bounded detail levels, field-level
  validation, safe auditing, and dedicated health counters.
- Add no write capability and no automatic cleanup, reference rewrite,
  incident correlation, handoff generation, or RC stabilization.
- Preserve production v1.1.2 and all existing public tool input schemas.

## 2.0.0-beta.16

- Bind refreshed-index pagination snapshots and signed cursors to the final
  committed dependency-index generation, allowing immediate upstream-free
  continuation while retaining fail-closed expiry, tamper, query-change, and
  index-replacement checks.
- Separate impact findings from unique affected objects and root-cause groups in
  both results and health telemetry; retain corrected Beta 15 compatibility
  aliases with explicit deprecation metadata.
- Report confirmed target-related, unresolved requested-scope, and out-of-scope
  dynamic references separately, requiring manual review when requested coverage
  remains unresolved.
- Return stable field/reason validation details without provider activity,
  dependency-index access, or pagination state, and correct generated article
  wording without changing rule IDs.
- Preserve the 35-tool manifest, every public input schema, the read-only impact
  policy, governance boundaries, and production v1.1.2.

## 2.0.0-beta.15

- Add the read-only `change_impact_analysis` Engineering-native tool for one
  entity and a proposed rename, remove, or disable operation, increasing the
  callable beta manifest from 34 to 35 tools.
- Reuse the existing dependency index for bounded direct and indirect impact
  evidence; no second dependency graph or write authority was introduced.
- Add 22 deterministic, evidence-backed rule IDs, stable findings, affected-object
  grouping, operation-specific consequences, advisory remediation, and four
  conservative assessment states without an opaque risk score.
- Add exact state and entity-registry evidence, honest static-source coverage,
  bounded retained trace headers, and sanitized exact System Log correlation.
- Add signed evidence-bound pagination snapshots, detail-dependent payload caps,
  truthful cache/timing provenance, safe audit summaries, and identity-free health
  metrics.
- Preserve every Beta 14 tool name and input schema, governance and provider
  boundaries, and production v1.1.2.

## 2.0.0-beta.14

- Fixed the Beta 13 null analysis timestamp by accepting injected timezone-aware clock instants and capturing one UTC request instant.
- Unified `list_automation_traces` and reliability analysis behind one sanitized trace-list transport and normalization contract.
- Added Home Assistant `{start, finish}` trace interval support alongside offset ISO strings and permitted epoch timestamps.
- Made the lookback cutoff inclusive, timezone-aware, fixed for the request, and bound to pagination fingerprints.
- Added truthful trace coverage states and bounded counts for upstream, parsed, eligible, selected, retrieved, failed, malformed, duplicate, and truncated runs.
- Restricted `no_recent_execution_evidence` to trustworthy empty trace results; source defects now return partial or failure truthfully.
- Added bounded sanitized pagination snapshots so cursor pages do not repeat HA trace collection or inflate aggregate counters.
- Preserved Beta 13 correlation, chronology, root-cause, timing, cache-truth, redaction, routing, and write-boundary protections.
- Preserved all 34 tool names and input schemas; production v1.1.2 remains unchanged.

## 2.0.0-beta.13

- Corrected reliability observation chronology using timezone-aware UTC ordering independent of Home Assistant source order.
- Replaced broad System Log substring correlation with exact identifier and trace-signature bases, with safe confidence metadata.
- Added deterministic root-cause groups so overlapping trace and action findings are not presented as independent incidents.
- Standardized reliability timestamps as RFC 3339 UTC strings and trace intervals as `started_at`/`finished_at` objects.
- Distinguished the bounded System Log snapshot from unverifiable lookback retention without discarding independent findings.
- Separated cumulative Home Assistant request effort from upstream wall-clock span and concurrency.
- Marked reliability-result caching honestly unavailable and prevented pagination from inflating finding/root-cause counters.
- Preserved all 34 tool names and input schemas; production v1.1.2 is unchanged.

## 2.0.0-beta.12

- Add the read-only `automation_reliability_analysis` tool for one internal
  automation ID, increasing the beta manifest to 34 callable tools.
- Compose bounded configuration, state, blueprint, trace, referenced-entity,
  registry, and sanitized System Log evidence behind an engineering facilitator
  provider; no tool handler calls Home Assistant directly.
- Add 13 deterministic rules with stable findings, confidence/status, evidence
  references, fingerprint-bound pagination, three detail levels, and honest partial
  coverage. No opaque reliability score is produced.
- Bound lookback, traces, entity reads, concurrency, findings, evidence, log
  correlation, response size, and total duration; independent source failures retain
  useful confirmed findings.
- Add safe analysis health counters without exposing configuration or evidence.
- Collapse duplicate adjacent Matter setup-payload redaction markers while preserving
  detection and sanitizer idempotence.
- Preserve the original 25 names and input schemas, all eight prior beta-native tools,
  governance and Phase 3C boundaries, and production v1.1.2.

## 2.0.0-beta.11

- Sanitize the complete recursive Home Assistant System Log result before any
  entry selection, field bounding, normalization, formatting, or serialization.
- Add key-aware and free-text redaction for authentication material, auth flows,
  webhook secrets, Matter commissioning values, credential-bearing URLs,
  serialized Python/JSON representations, cookies, and known runtime secrets.
- Use stable category markers without exposing secret fragments, lengths, hashes,
  encodings, prefixes, or suffixes.
- Fail closed per field when sanitation raises, preserve existing markers
  idempotently, and keep prompt-like log text inert untrusted evidence.
- Report only bounded redaction categories, field counts, and fail-closed state.
- Preserve 33 tools, all original 25 input schemas, Phase 3C routing, governance,
  dependency behavior, and production v1.1.2.

## 2.0.0-beta.10

- Replace the conditionally registered `/api/error_log` REST read with Home
  Assistant's supported admin-only `system_log/list` WebSocket command.
- Preserve the `tail_lines` input while returning bounded, newest-first,
  structured warning/error entries with explicit truncation and untrusted-data
  metadata.
- Redact access secrets, Supervisor tokens, authorization material,
  credential-bearing URLs, webhook secrets, and session identifiers from log
  content before it reaches responses, application logs, or audit output.
- Classify pre-upstream request validation as `request_validation` source
  coverage with zero Home Assistant time.
- Count `recent_error_counts` once per terminal public tool failure instead of
  once at each REST, structured-response, and facilitator propagation layer.
- Preserve all 33 tools, Phase 3C's four direct administrative-read policies,
  dependency behavior, governance boundaries, and production v1.1.2.

## 2.0.0-beta.9

- Align capability truth for `get_entity`, `list_areas`, `search_services`, and
  `list_services`: lifecycle `transitional`, route `transitional_direct`, and provider
  `direct_ha_api` under four specific read-only policies.
- Preserve facilitator dispatch, normalized envelopes, source coverage, timing, audit
  correlation, and provider counters for all four administrative reads.
- Document the verified stateless Home Assistant `/api/mcp` endpoint and reject
  approximate `GetLiveContext` mappings for exact entity, area, and service semantics.
- Honor dependency-analysis limits through 100 with explicit requested/effective limit
  metadata; separate current lookup/request timing from original build provenance.
- Clarify dependency health counters as cumulative truncation events versus current
  unresolved-reference index state.
- Keep production v1.1.2 and all write, physical-action, reload, delete, and governance
  boundaries unchanged.

## 2.0.0-beta.8

- Route canonical delegated, transitional, direct-required, and prohibited tools
  through the facilitator dispatcher while preserving all 33 tool input schemas.
- Fail delegated calls with a structured provider error when the Standard HA MCP
  gateway is unavailable; never silently invoke the legacy direct-HA implementation.
- Normalize routed responses and attribute provider request, success, failure,
  partial-result, and prohibited-fallback metrics.
- Enforce a reviewed tool-specific allowlist for direct Home Assistant exceptions and
  verify that `entity_dependency_analysis` is present and serializable in `tools/list`.

## 2.0.0-beta.7

- Add the read-only `entity_dependency_analysis` tool; the beta manifest now exposes
  33 tools.
- Build a bounded in-memory dependency index from automation configuration, blueprint
  input/source roles, entity state, and entity registry evidence.
- Add exact structured/template extraction, partial source coverage, cautious stale
  assessment, stable cursors, cache/refresh/invalidation, and bounded detail levels.
- Report unsupported source families and standard-MCP delegation honestly as
  unavailable while preserving all prior schemas and production v1.1.2.

## 2.0.0-beta.6

- Establish the Engineering MCP facilitator architecture, deterministic provider
  routing policy, and transport-independent evidence-provider contracts.
- Represent standard Home Assistant MCP delegation honestly as unavailable until a
  supported nested client transport is configured and verified.
- Add bounded, paginated, deduplicated response and evidence models for future
  analytical tools, plus safe provider-routing health counters.
- Replace free-text safety keyword matching with structured action, service, target,
  entity-domain, and blueprint-input risk evidence; harmless descriptive text no
  longer produces high-risk plans.
- Preserve all 32 beta tools, the original 25 schemas, the seven beta-native schemas,
  governance persistence compatibility, and production v1.1.2.

## 2.0.0-beta.5

- Map missing or invalid change-plan lookups to `change_plan_not_found` while
  reserving storage failures for real I/O, corruption, serialization,
  permission, and atomic-write failures.
- Treat the expected create-automation availability 404 as a successful probe
  branch so client responses, logs, plan events, and tool-call audits agree.
- Reject existing automation IDs as `configuration_conflict` and malformed or
  failed HA probe responses as real upstream failures.
- Replace transport-lifetime request latency with separate MCP operation, tool,
  and Home Assistant latency summaries; open stream lifetime is excluded.
- Preserve all 32 beta tools and all original 25 compatibility schemas.

## 2.0.0-beta.4

- Add approval-based change plans for creating and updating Home Assistant
  automations, with deterministic dry-run diffs and risk classification.
- Add hash-bound approval, stale-state protection, per-target concurrency,
  controlled apply, read-back verification, and separately approved rollback.
- Add atomic beta-only governance persistence, retention, corrupt-record
  quarantine, restart recovery, safe audit events, and bounded health metrics.
- Expose six governance tools for 32 total callable beta MCP tools while
  preserving all 25 production-compatible tool schemas.

## 2.0.0-beta.3

- Add fail-closed beta deployment and metadata validation for Windows development.
- Add a repeatable beta release checklist, optional health check, and cache-delay
  troubleshooting guidance.
- Keep the production v1.1.2 add-on and runtime unchanged.

## 2.0.0-beta.2

- Explicitly register `get_server_health` with the served FastMCP registry and
  verify its `tools/list`/`tools/call` exposure.
- Correlate upstream HA 4xx/5xx failures across structured tool responses,
  logs, and audit records; entity 404s now use `entity_not_found`.
- Add typed success and failure response contracts and a stable error taxonomy.
- Add request correlation, structured logging, bounded audit records, timing,
  and safe runtime metrics.
- Add beta-native `get_server_health` and migrate `server_info`,
  `list_capabilities`, and `get_error_log` to structured responses.

## 2.0.0-beta.1

- Add an isolated, parallel-installable v2 beta add-on.
- Introduce modular application, gateway, client, model, audit, capability, and
  version boundaries.
- Preserve the v1.1.2 25-tool catalog and public argument schemas.
