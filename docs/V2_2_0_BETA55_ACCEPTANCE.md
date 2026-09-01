# Engineering 2.2.0-beta.55 acceptance

Beta 55 is the staged source candidate for ADR-023 ha-mcp
capability-scoped automatic readmission, based on exact protected main
`2429a457e533ab370a1b341be09e80e591f99272`. Engineering continues to
advertise 2.2.0-beta.54 until a separately authorized promotion. Stable
remains 1.1.2.

This document is the exact staged acceptance authority. Merge,
materialization, publication, deployment, production trust-anchor
configuration, registry signing or publication, live Home Assistant access,
and every read or write canary remain outside this source boundary.

## Product boundary

The first operational update-tolerance surface is the internal read-only
ha-mcp provider. After a manual ha-mcp update, Engineering can observe the
changed identity, version, negotiated protocol, actual MCP transport session,
and complete bounded catalog; retire the prior ha-mcp decision generation and
unused leases; verify compiled or signed release authority; select an existing
binary-owned profile and adapter; and publish only independently matching
automatic reads.

Home Assistant Core compatibility, Nabu Casa or other proxy recovery,
template-semantic readmission, helper or configuration writes, actions, mixed
tools, generic forwarding, provider changes, and fallback remain outside Beta
55. Engineering remains the only client-facing connector. The official ha-mcp
App remains an internal capability provider.

The feature is disabled by default. With the release-registry feature disabled,
compiled-exact ha-mcp 8.2.0 behavior remains the Beta 54 behavior: 51 public
Engineering tools, 25 delegated automatic reads, 76 combined tools,
`ha_get_operation_status` as the sole held and unregistered read, and fallback
zero. No public MCP tool, input schema, registration contract, task schema,
approval authority, provider write, route, or stable-v1 behavior changes.

## Authority and per-capability admission

Positive authority is either a current compiled exact-release entry or a valid
Ed25519-signed compatibility entry. A signed entry can select only a profile
and adapter already compiled into the Engineering binary through its exact
policy-resource and policy-digest binding. It cannot provide code, tools,
argument shapes, classifications, routes, actions, writes, forwarding, or
fallback.

Release-wide error and entity-lookup semantics and any declared provider
argument-constraint fingerprint must match the selected compiled adapter.
Binary response normalization and compatibility adapters are selected by that
compiled binding, not by the newly observed release version.

Live identity and catalogs are observations rather than self-authority. Each
reviewed automatic-read contract is compared independently across input schema,
description, annotation, output, runtime descriptor, classification, and exact
argument restrictions. A changed, missing, malformed, duplicate, or
unauthorized read is withheld without disabling compatible reviewed siblings.
Unknown additions and every held, action, mixed, persistent-write, prohibited,
or unsupported capability remain unregistered and unreachable.

Registration, routing, compatibility accounting, profile/adapter selection,
and health evidence are published from one decision generation. Clients must
reconnect or explicitly re-list; inbound sessions that have not listed the
current dynamic generation are refused before upstream dispatch. Beta 55 does
not advertise
`tools.listChanged` and emits no list-change notification.

## Registry, restart, and revocation

The optional registry uses one fixed HTTPS location, refuses redirects, and
enforces connection, total-time, journal, envelope, entry, revocation, cache,
and retention bounds. The fetched artifact is an independently signed bounded
journal/checkpoint containing individually signed envelopes. Strict parsing and
Ed25519 verification precede exact registry-ID/schema, sequence, digest, every
retained previous-digest link, expiry, rollback, equal-sequence conflict,
duplicate, compaction-checkpoint, and revocation checks. A bare tip cannot
bootstrap or catch up a client.

Accepted state uses temporary write, file fsync, atomic replacement, directory
fsync, a journal-bound lifecycle witness written before each refresh, a durable
pending signed journal, and a prior checkpoint. A failed persistence step cannot
activate the candidate's positive entries. Authenticated
revocations remain effective as bounded denial-only state even if candidate
persistence fails, and the verified candidate remains a sequence/digest barrier
against conflicting replay. Restart reparses and reverifies every cached outer
and envelope signature, the lifecycle witness, and the bounded
journal/checkpoint topology. A stale pending tip that is not strictly newer than
its authenticated base is permanently denial-only. Unexpired positive authority
survives a registry outage, expired positive authority becomes unavailable, and
retained valid revocations remain denial-only across bounded compaction.
Malformed, conflicting, interrupted, oversized, or capacity-exhausted cache
state denies the ha-mcp surface until a fresh valid registry is accepted. A
valid revocation overrides compiled exact authority.

Only synthetic ephemeral private keys appear in tests. No production private
key, registry entry, signing workflow, GitHub secret, public trust anchor, or
registry publication is created by this source candidate. Production trust
activation is separate later operational work.

## Dispatch and concurrency boundary

Immediately before each delegated `tools/call`, the same bounded MCP exchange
rechecks initialize identity and protocol, retrieves the complete paginated
catalog, reselects current release authority and its binary profile, validates
the exact selected descriptor and argument restrictions, requires the current
generation and actual retained MCP session, and atomically consumes one
registered lease.

Sequential or concurrent lease reuse fails. Generation retirement invalidates
unused leases. A call committed after final validation may finish once, but its
completion cannot restore, republish, or extend authority. Late verification
cannot replace a newer decision. There is no semantic retry, alternate
provider, direct-HA path, generic forwarding, or fallback.

Health and internal audit projections are bounded and sanitized on success,
partial admission, and refusal. They expose
only fixed dispositions and reason codes, generation, compiled profile and
adapter IDs, authority source, admitted/held/unavailable and lifecycle counts,
registry freshness/sequence status, and fallback zero. They exclude endpoints,
credentials, raw identities, versions, sessions, catalogs, schemas, registry
bodies, signatures, keys, and exceptions.

## Acceptance evidence

Acceptance requires exact agreement for all 20 ADR-020 vectors and 136 steps
through the test-only reference adapter and the production coordinator adapter.
It also requires operational tests for no-restart compatible updates, one-read
partial compatibility, exact current-contract preservation, unknown and
non-read negative reachability, identity/protocol/catalog/descriptor refusal,
all signed-registry trust and ordering failures, durable restart behavior,
generation and lease races, bounded evidence, and zero rejected dispatch.

The complete gateway, registry, provider, transport, schema, metadata,
held-read, exact-image, and historical regressions must pass, followed by
complete unit discovery, Fast Instructions and Validation, protected Full,
clean-head Evidence, isolated promotion-candidate validation, compilation,
JSON/YAML/PowerShell validation, dependency consistency, strict pinned
dependency audit, secret and whitespace checks, stable-v1 comparison, and
exact tool/schema/authority/route/fallback/workflow/container/deployment
comparisons.

Exact-head CI must pass central validation, every disposable Home Assistant
lane, exact ha-mcp 8.0.0 through 8.2.0 lanes, exact-image/readmission lanes,
packaging, and architecture checks. Separate exact-head code, security, and
provider-boundary reviews must resolve every Critical and High finding before
ready state. The pull request remains unmerged.

## Later activation boundary

After a future separately authorized merge, promotion, publication, deployment,
trust-anchor configuration, and production registry publication, read-only
post-deployment acceptance may verify one compatible manual ha-mcp update. Home
Assistant Core readmission and Nabu Casa transport recovery remain separate
follow-ups.
