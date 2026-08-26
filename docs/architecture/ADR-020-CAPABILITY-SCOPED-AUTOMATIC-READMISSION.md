# ADR-020: Capability-scoped automatic-readmission foundation

Status: accepted as a test-only executable specification; non-authoritative and
not integrated with production admission

## Context

Engineering currently binds upstream authority to exact reviewed releases.
That safely prevents an upstream version string or self-advertised schema from
authorizing itself, but an otherwise compatible manual update can remain held
until another Engineering release is built. The long-term product requirement
is to retire stale authority after an update and restore only capabilities that
can be matched to trusted, binary-known contracts.

This decision establishes a reusable executable reference model and
implementation-neutral compatibility vectors. The model lives only under
`tests/support/automatic_readmission/`. It is excluded from the packaged
Engineering runtime and is not imported from startup, routing, providers, tool
registration, health publication, or admission code. It changes no current
runtime behavior and cannot grant admission or execution authority.

ADR-010 is already assigned to knowledge-source provenance, so this decision
uses the next available architecture number, ADR-020.

## Decision

Automatic readmission is a per-capability reconciliation:

```text
observe change
  -> retire the prior decision generation
  -> verify identity, protocol, catalog and trusted authority
  -> compare only binary-known capability profiles and adapters
  -> publish an atomic exact, compatible, partial, quarantined or unavailable set
```

The executable specification is the non-authoritative
`tests.support.automatic_readmission` test-support package and a transport-free
synthetic harness. It has no network, credential, environment-authority,
filesystem-write, provider-call, registry-fetch, MCP-client, registration, or
dispatch behavior. The production package and image do not contain it.

The vectors in
`tests/fixtures/automatic_readmission/contract_vectors_v2.json` are versioned
data separate from the reference-model implementation. Their multi-step
scenarios state literal observations, authority and time inputs,
reconciliation and lease operations, admitted and quarantined outcomes,
sanitized health projections, and zero write/action reachability. The generic
adapter/runner boundary compares literal expected results and emits only bounded
mismatch fingerprints. A later production adapter can replay the same vectors
without importing or copying reference-model classes, constants, or decision
functions.

### Independently governed surfaces

1. **Home Assistant Core**: direct REST and WebSocket capabilities reached
   through the configured Core endpoint. Ordinary reads, registry reads,
   template semantics, configuration semantics, and governed helper writes are
   separate profiles. A compatible ordinary-read decision does not imply a
   compatible semantic or write decision.
2. **ha-mcp**: the configured secret-bearing stateless Streamable HTTP endpoint.
   Initialization identity, version, negotiated protocol and the complete
   bounded `tools/list` catalog are observations. Only a trusted release entry
   selecting a compiled profile and adapter can authorize a capability.
3. **Configured transport**: connectivity and authentication to the fixed
   configured endpoint. Transport recovery can restore only a transport
   profile. It cannot restore Core or ha-mcp provider authority.

The repository has no API for observing or controlling a Nabu Casa, webhook,
tunnel, or other external proxy lifecycle. The inbound proxy is therefore a
client/connector responsibility. Engineering accepts only its configured
end-to-end transport contract; it does not discover endpoints, trust proxy
headers as release identity, follow expanded redirects, or probe public URLs.

Home Assistant documents REST `GET /api/config`, WebSocket `auth_ok` version
evidence and WebSocket `get_config`. Compatible Core readmission will require
all three version observations to agree. The Core REST and WebSocket channels
remain separate from Home Assistant's MCP Server endpoint at `/api/mcp` and
from the independently configured ha-mcp endpoint.

Each surface owns an independent published decision, pending verification,
surface-local effective-authority fingerprint, bounded retirement diagnostics,
and bounded last-observation status. Generation numbers may come from one
global monotonic allocator, but authority is published and validated only
within its owning surface. A global signed-registry refresh can request
reevaluation of several surfaces; it does not retire a surface unless that
surface's effective authority changed. Decisions owned exclusively by Core are
not material to ha-mcp, and provider decisions are not material to configured
transport. A Core change therefore cannot retire ha-mcp authority, a ha-mcp
catalog change cannot retire Core authority, and transport restoration cannot
create provider authority. Capability ownership across surfaces must be unique;
duplicate or ambiguous ownership fails closed. Unknown profiles cannot acquire
surface ownership by naming an unknown adapter or capability.

### Capability profiles

A capability profile is binary-owned and versioned. It binds:

- one upstream surface;
- one profile ID and profile version;
- one compiled adapter ID;
- the expected server identity and supported protocol revisions; and
- capability IDs, classifications and complete contract fingerprints.

Signed data may select only those existing identifiers. It cannot add code,
adapters, tools, routes, classifications, arguments, writes or fallback.
Unknown additions are quarantined and never receive a route. One changed known
capability is quarantined independently while compatible siblings remain
admitted.

### Observation and authority

Live identity, version, protocol, catalog and connectivity are observations,
never authority. Authority precedence is:

1. a matching signed revocation or retained deny-only record;
2. a matching compiled exact entry;
3. a current valid signed entry selecting a binary-known profile and adapter;
4. no authority.

The coordinator consumes decisions only after the existing signed-registry
boundary has verified their signature, sequence, digest and expiry evidence.
The synthetic harness models that post-verification decision input; it does not
perform or replace cryptographic verification.

Revocation and retained deny-only evidence override either positive source.
An expired signed positive entry cannot admit a remote-only release. An expired
cached revocation remains denial-only until a valid higher-sequence update
supersedes it. Rollback, equal-sequence content conflict, malformed chains and
unsupported registry evidence fail closed. An idempotent equal-sequence,
equal-digest replay changes nothing.

Identity or protocol disagreement is a global prerequisite failure for its
surface. Catalog incompleteness is never converted into absence. Duplicate,
missing or drifted known capabilities are classified individually after the
global prerequisites pass.

### Core semantic separation

An unknown Core release may later regain a structurally proven ordinary REST or
WebSocket read through an admitted ordinary-read profile. It does not thereby
regain:

- template-semantic analysis;
- configuration-semantic behavior;
- configuration mutation; or
- governed helper writes.

Those require their own binary-known profile and trusted authority. This
separation prevents an ordinary API-shape match from making unreviewed Jinja or
configuration semantics authoritative.

### Generation and race contract

Any material identity, version, protocol, catalog, surface profile-registry,
authority or session observation creates a new monotonic generation for the
affected surface. The coordinator:

1. retires the published generation;
2. prevents new route leases from that generation;
3. creates a new generation in `verifying` state;
4. evaluates only the newest verification ticket; and
5. publishes one atomic capability decision set.

Repeated materially identical reconciliation is idempotent. Collection time is
not fingerprint material while authority remains on the same side of an expiry
boundary; crossing expiry, revocation, identity, catalog, contract, profile, or
disposition boundaries remains material. Semantically identical decision and
profile ordering is canonicalized by complete content.

A route lease binds the exact surface, capability, compiled adapter, decision
generation and sanitized session fingerprint. It is registered as an issued,
single-use value. Immediately before a logical commit, the coordinator requires
exact equality with that issued value, the same current generation, and the
same session for that surface. Atomic commit consumes the lease, so sequential
or concurrent replay cannot create a second logical commit. An unused lease has
an explicit release path. Retirement before commit prevents dispatch; a call
committed after validation may finish exactly once, but completion has no
authority to publish or revive a route. A late verification result cannot
replace a newer generation for its surface and cannot affect another surface's
lease.

Issued leases, active commits, generation and lease counters, and retained
retirement diagnostics have explicit deterministic limits. Capacity exhaustion
fails closed without retiring unrelated valid authority or partially creating a
commit. Retirement removes issued leases for the affected surface, release and
failed validation remove unused leases, and finish removes active commits.
Only a small bounded diagnostic retirement history is retained; current
generation equality, not an unbounded historical set, is the dispatch guard.

The reference model specifies these transitions only. It contains no real call,
provider dispatch, or production route-publication method.

### Catalog publication and clients

Future integration must publish runtime routing and `tools/list` from the same
atomic generation and preserve deterministic catalog ordering. The current
server contract requires clients to reconnect or explicitly re-list after a
dynamic catalog change.

This PR does not enable `notifications/tools/list_changed` and does not
advertise `tools.listChanged=true`. Although MCP defines that capability and
notification, activation requires a separate review of pinned `mcp==1.28.1`,
the negotiated protocol revision and actual client behavior. The server must
not advertise list-change delivery before it reliably emits the notification
after atomic publication.

### Health and audit evidence

The reference projections contain only:

- model and generation numbers;
- surface and disposition;
- bounded fingerprints;
- admitted, quarantined and unavailable counts;
- bounded reason-code and lifecycle counts;
- issued-lease, active-commit, retained-retirement and capacity-exhaustion
  summaries; and
- an explicit zero fallback count.

They exclude endpoints, sessions, raw identities, versions, catalogs, schemas,
descriptions, registry bodies, signatures, credentials and exception text.

## Dispositions

- `verifying`: prior authority is retired and no new route is published.
- `admitted_exact`: all reviewed compatible capabilities use compiled exact
  authority.
- `admitted_compatible`: all reviewed compatible capabilities use current
  signed selection of binary-known contracts.
- `partial`: at least one reviewed capability is admitted and at least one is
  withheld.
- `quarantined`: observed or reviewed contracts are materially incompatible and
  none is admitted.
- `unavailable`: identity, protocol, transport, authentication, catalog or
  positive-authority prerequisites are absent.

Unknown and write-capable capabilities remain unreachable under every source.
There is no generic forwarding or fallback disposition.

## Topology conclusion

Repository and deployment evidence establish:

- Core REST uses the configured Home Assistant URL, normally the fixed
  Supervisor Core API proxy; WebSocket derives from the same configured Core
  authority.
- ha-mcp uses one fixed secret-bearing Streamable HTTP URL. Every operation
  opens a bounded session and observes initialization plus catalog evidence.
- inbound proxy trust is limited to validated client-address handling. No
  proxy-release identity or lifecycle authority exists in Engineering.

Consequently a connector reconnect can restore transport availability but
cannot restore provider authority. No additional server-side proxy simulation
belongs in this foundation.

## Consequences

The architecture is capability-scoped, not generally version-tolerant. It can
model a harmless version movement only when trusted authority selects
binary-known profiles and every admitted capability satisfies its reviewed
contract. Self-advertisement never becomes authority. Independent native
Engineering tools and compatible capabilities remain available while unknown
or changed contracts are withheld. The affected surface has an explicit
verification gap; unrelated surfaces retain their own authority.

This PR provides an executable reference model, deterministic compatibility
vectors, and contract evidence only. It provides no deployed capability. The
advertised and staged Engineering versions, public schemas, tool registration,
routing, providers, fallback and stable v1 remain unchanged.

## Implemented now

- a test-only executable specification;
- synthetic implementation-neutral compatibility vectors;
- a deterministic per-surface authority, generation, lease and reconciliation
  model;
- single-use route leases and explicitly bounded lifecycle state;
- bounded sanitized reference health and audit projections; and
- tests proving zero runtime import, packaging, provider, registration, write,
  action, fallback and dispatch reachability.

None of this is initialized at startup or connected to a provider, gateway,
MCP server, registry fetcher, credential source, live upstream, or runtime
state.

## Deferred operational work

- a production runtime coordinator;
- upstream identity and capability observation wiring;
- signed-registry fetching, verification and caching;
- ha-mcp gateway integration;
- Home Assistant Core compatibility integration;
- proxy transport recovery;
- dynamic client catalog refresh and notification review;
- release staging, deployment, and runtime acceptance.

## First operational follow-on

The first PR that implements operational readmission must:

1. baseline against then-current `main`;
2. implement or move the reviewed behavior into Engineering runtime rather than
   importing test support;
3. connect it to exactly one bounded production surface;
4. replay these same implementation-neutral contract vectors through a
   production adapter without rewriting expected outcomes;
5. stage a new Engineering release; and
6. receive separate security and provider-boundary review.

Later Core, transport, registry, gateway and client-catalog work remains
separate. Every operational follow-on must retain the generation, lease,
revocation, zero-write and bounded-evidence contracts defined here.

## References

- [Home Assistant WebSocket API](https://developers.home-assistant.io/docs/api/websocket/)
- [Home Assistant REST API](https://developers.home-assistant.io/docs/api/rest/)
- [Home Assistant MCP Server](https://www.home-assistant.io/integrations/mcp_server/)
- [MCP 2025-06-18 schema](https://modelcontextprotocol.io/specification/2025-06-18/schema)
- [ADR-006](ADR-006-CONTRACT-LEVEL-UPSTREAM-COMPATIBILITY.md)
- [ADR-009](ADR-009-SIGNED-COMPATIBILITY-REGISTRY-FOUNDATION.md)
