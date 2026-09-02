# ADR-023: Production ha-mcp capability-scoped automatic readmission

Status: implemented for the Beta 55 release candidate; production registry
publication and trust-anchor activation remain separately governed operations

## Context

ADR-020 froze the implementation-neutral capability, generation, and lease
contract. Its reference package remains test-only and non-authoritative. This
decision implements the first operational surface: the configured read-only
ha-mcp gateway. Home Assistant Core, configured proxy lifecycle, semantic
readmission, actions, mixed tools, and writes remain outside this decision.

ADR-021 governs the exact upstream source evidence used to compile ha-mcp
profiles; a live catalog remains observation rather than authority. ADR-022
places the internal provider behind the one supported client-facing Engineering
connector and preserves the owner's direction that compatible capability should
return without weakening exact provider or execution boundaries.

The compiled reviewed release registry remains sufficient authority for exact
known releases. A manual ha-mcp update otherwise remains unavailable until a
current Ed25519-signed release entry selects an existing binary-owned profile.
A version string and `tools/list` are observations, never authority.

## Decision

The production path is:

```text
observe ha-mcp identity, version, protocol, session and complete catalog
  -> retire the prior ha-mcp generation on material change
  -> verify compiled or signed release authority
  -> select one binary-owned policy profile and adapter
  -> compare each reviewed automatic-read contract independently
  -> atomically publish that generation's delegated routes and accounting
```

The production coordinator is under `ha_mcp_readmission/`. It contains no
transport or provider call. The gateway translates its already bounded MCP
observation into that coordinator and publishes only decisions that pass both
the existing five-part tool-contract comparison and the coordinator's exact
authority decision. Production never imports `tests/support/automatic_readmission`.
The implementation-neutral ADR-020 vectors are replayed through a test adapter
using the production coordinator, with their literal expected outcomes
unchanged.

### Binary-owned authority

A signed entry can select a profile only through an exact compiled
`policy_resource` plus `policy_sha256` binding. The selected profile supplies
the surface, profile version, adapter ID, identity, protocols, classifications,
argument restrictions, and complete capability fingerprints. Signed content
cannot define executable code, a tool, an adapter, a classification, arguments,
a route, or fallback.

For each automatic read, the signed entry must repeat the binary-known input,
description, annotation, output, and runtime fingerprints and exact argument
restrictions. One mismatch withholds that read only. Missing and duplicate
reviewed descriptors withhold the selected target. Unknown additions remain
unreviewed and unreachable without disabling compatible siblings. Every
non-read classification remains unreachable.

A signed revocation is denial-only and overrides compiled exact authority.
Expired positive authority cannot admit. Retained authenticated revocations do
not become positive authority after expiry or restart.

### Signed registry operation

The release registry has one repository-owned HTTPS location:

`https://raw.githubusercontent.com/jeter-1/hass-mcp-admin/main/upstream-trust/ha-mcp-release-registry.json`

The fetcher rejects redirects and applies fixed connection, total-time, and
byte bounds. The fetched artifact is a separately Ed25519-signed bounded
journal containing individually signed ADR-009 envelopes, an explicit signed
checkpoint, and bounded signed revocation sources. Strict outer and envelope
parsing, registry ID, schema, sequence, digest, every retained previous-digest
link, clock, expiry, duplicate, contradiction, and unknown-key checks run before
positive authority is available. A bare current envelope is not a bootstrap
shortcut. The signed checkpoint permits bounded compaction only while every
previously retained revocation remains represented as denial-only evidence.

Accepted content is cached in
`/data/ha-mcp-release-registry-cache.json` with a temporary write, file fsync,
atomic replacement, and directory fsync. A journal-bound lifecycle witness is
durably changed from committed to refreshing before a newer registry can be
observed. Before the first signed registry observation, the same bounded witness
uses a distinct no-signed-authority digest. A pre-validation fetch failure may
restore that committed empty baseline; once a candidate validates, persistence
failure leaves the refreshing witness as denial evidence. A retained witness
without its bound main cache is never interpreted as a clean first start. A
durable pending journal and prior checkpoint make an interrupted
replacement denial-only on restart, and a pending tip that is not strictly newer
and correctly linked to its authenticated base cannot regain authority. A
pending file beside a committed main cache is cleanup residue only when it
strictly verifies, names that exact committed journal, and adds no uncommitted
denial evidence. A cache
write failure prevents the candidate's positive entries from becoming
accepted, while any already authenticated revocations take effect immediately
as bounded process-local denial evidence. The verified candidate sequence and
digest also remain a process-local rollback/replay barrier after persistence
failure. The cache contains the authenticated bounded journal/checkpoint and
signed revocation sources. On restart every outer and envelope signature,
checkpoint, previous-digest link, sequence, duplicate, and source-membership
relationship is revalidated. Positive use is re-evaluated against the current
clock. Signed revocations remain denial-only when the accepted positive
envelope expires or after journal compaction; malformed cache state denies the
ha-mcp surface until a fresh valid authenticated journal is accepted.

This registry uses the distinct add-on options
`ha_mcp_release_registry_enabled` and
`ha_mcp_release_registry_public_key`. It does not reuse the dashboard
attestation enable switch or trust key. The public key is not secret. No private
key exists in production code, fixtures, configuration, logs, or documentation.
Tests use ephemeral keys marked synthetic. Creating the production registry and
protected signing workflow requires separate authorization and is not part of
this implementation branch.

### Generation and dispatch boundary

Material identity, version, protocol, profile, authority, revocation, catalog,
session, or contract movement retires the old ha-mcp generation and all unused
leases. Ordering and collection time are not material. The coordinator accepts
only the newest verification ticket and keeps generations, leases, commits,
diagnostics, counters, and audit projections bounded.

Immediately before `tools/call`, the same MCP exchange has already:

1. rechecked initialize identity and negotiated protocol;
2. retrieved the complete bounded paginated `tools/list`;
3. reselected current compiled or signed authority;
4. required the selected tool exactly once;
5. revalidated its complete contract; and
6. acquired and atomically consumed a registered lease bound to the surface,
   capability, adapter, generation, and observed MCP transport session.

Sequential or concurrent reuse of a lease fails. Retirement invalidates unused
leases. A call committed after final validation may finish once, but it cannot
publish or revive authority. Validation, registry, capacity, or persistence
failure occurs before logical dispatch. There is no semantic retry, generic
forwarding, alternate provider, direct-HA path, or fallback.

The gateway retains one initialized upstream MCP exchange for the published
generation. Discovery, final `tools/list`, lease acquisition, commit, and
`tools/call` use the same actual post-initialize session identifier. A bounded
per-exchange opaque nonce is used only when the upstream provides no session
identifier. Session rotation or loss retires the generation before another
call; a later discovery may publish a new generation. Raw session values are
never published.

### Catalog and evidence

Gateway routing, delegated tool registration, and compatibility accounting use
the same coordinator generation. Each inbound MCP session is bound to the
dynamic generation returned by its most recent `tools/list`; a call from a new
or stale session is refused until it reconnects or lists the current generation.
This decision does not advertise
`tools.listChanged=true` and does not emit list-change notifications.

The new bounded health and internal audit projections expose only surface,
disposition, decision generation, binary profile/adapter IDs, authority source,
counts, fixed reason codes, registry freshness/sequence status, lifecycle
capacity summaries, and fallback count zero. They exclude endpoints,
credentials, raw identities, versions, catalogs, descriptors, schemas,
descriptions, registry bodies, signatures, sessions, and exception text.

## Consequences

A compatible manually updated ha-mcp release can restore matching delegated
reads without an Engineering restart or code release after trusted registry
publication. Changed siblings remain quarantined. Unknown releases without a
valid signed entry remain unavailable even when their catalog looks identical.
Core changes cannot retire ha-mcp authority because this runtime coordinator is
instantiated only for `ha_mcp` by the read gateway.

Operational freshness is pull-based at the bounded gateway reconciliation
cadence. When a newly observed release has no cached entry, separately
rate-limited per-release authenticated refreshes continue on a bounded retry
cadence so a publication that becomes visible just after the first fetch does
not wait for the ordinary refresh interval. Clients must
reconnect or re-list to observe a published catalog change. Production signing,
publication, release staging, deployment, and live acceptance are separate
governed steps.

## References

- [ADR-009](ADR-009-SIGNED-COMPATIBILITY-REGISTRY-FOUNDATION.md)
- [ADR-020](ADR-020-CAPABILITY-SCOPED-AUTOMATIC-READMISSION.md)
- [ADR-021](ADR-021-HOME-ASSISTANT-SOURCE-AUTHORITY.md)
- [ADR-022](ADR-022-OWNER-AUTHORITATIVE-PRODUCT-DIRECTION.md)
- [ha-mcp automatic-readmission acceptance](../HA_MCP_CAPABILITY_AUTO_READMISSION_ACCEPTANCE.md)
