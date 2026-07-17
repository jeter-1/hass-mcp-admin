# RC2dev4 release-hardening notes

Version `2.0.0-rc2-dev4` is a focused hardening release based on deployed
RC2dev3 source commit `7a619fb4da53ba47a0ebf087b20085d5bfa2b1a2`.
It adds no Engineering tools and preserves the 40 registered / 25 canonical /
zero planned catalog and public input schemas.

## Resolved bake findings

- Legacy `upsert_automation`, `delete_automation`, `call_service`, and
  `reload_domain` handlers have no reachable Home Assistant mutation. Their
  catalog entries now report governed redirect, prohibited, or unavailable
  provider status, with `fallback: none`.
- Plan output distinguishes `approval_not_requested` from
  `approval_pending_external`. Chat authorization never grants external Home
  Assistant approval. Principal separation is not evaluated until an Ingress
  administrator exists.
- Expected hashes are checked before approval-required or already-applied
  outcomes when supplied. Empty hashes receive explicit validation metadata.
- Dependency-index construction is single-flight. One state inventory, one
  entity-registry inventory, and one bounded automation inventory are reused
  per build. Health reports build state, reason, TTL, timestamps, generation,
  fingerprint, progress, and a per-operation profile.
- Domain outcomes, request validation, authorization, cursor failures, and
  provider operational failures have separate counters.
- Dashboard not-found is a non-retryable domain outcome. Cached reachability
  becomes `unknown` after its freshness interval; no background polling was
  added.
- Recursive System Log and structured-log sanitization covers credentials,
  setup material, auth flows, webhook paths, signed URLs, query tokens,
  exceptions, tracebacks, mappings, and arrays and fails closed.
- Summary plan and dependency responses omit detail available from their exact
  retrieval paths. Reliability groups repeated configuration references under
  one entity/state root cause.

## Coverage and limitations

Dependency analysis indexes automations and locally readable blueprint roles.
Script, scene, group, template, dashboard, and static-YAML dependency ingestion
remain unsupported. The cold Raspberry Pi target is provisional: under 25
seconds, or moved off the blocking call path. Warm dependency reads must remain
under one second and make zero Home Assistant requests.

Automation configuration reads use a bounded pool of eight. Optional
`dependency_index_prewarm` is disabled by default; when enabled, one background
prewarm first completes a safe Home Assistant `/config` connectivity probe.
Probe or build failure never fails startup and is recorded in bounded prewarm
health without a retry loop.

The upstream dashboard provider remains the reviewed
`ha_mcp_7_13_dashboard_read_v1` argument-constrained profile. It is not generic
Standard HA MCP delegation and cannot dispatch screenshots, preference writes,
services, or arbitrary tools.

Audit is bounded operational evidence. Governance storage is the authoritative
full plan record and requires no RC2dev4 migration; RC2dev3 records remain
readable.

Pull-request CI validates but cannot authenticate to GHCR or push. After an
accepted merge, the controlled main-push workflow detects the reviewed dev3 to
dev4 metadata transition, rebuilds the exact main commit for all three declared
architectures, verifies both immutable tags anonymously and checks provenance
before pushing the annotated release tag. No image or tag is created by this PR.
