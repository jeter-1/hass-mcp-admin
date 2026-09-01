# Engineering 2.2.0-beta.55 release notes

Beta 55 is staged with the first capability-scoped automatic-readmission
implementation for delegated ha-mcp reads. Engineering remains client-facing;
ha-mcp remains an internal read provider.

Engineering still advertises 2.2.0-beta.54 until a separately authorized
promotion. Stable remains 1.1.2. This staging change does not merge, promote,
publish, deploy, configure production trust, access live Home Assistant, or run
a canary.

## Compatible manual updates

Engineering can now reconcile a manually updated official ha-mcp App without
an Engineering restart. It observes a complete bounded MCP catalog, verifies
compiled or signed release authority, selects only an existing binary-owned
profile and adapter, and republishes independently matching reviewed reads from
one atomic generation.

The selected compiled adapter now remains the execution binding for response
normalization and provider semantics even when the observed compatible release
has a newer version.

A changed or missing read stays held while compatible reviewed siblings return.
Unknown tools and every held, action, mixed, write, prohibited, or unsupported
capability remain unreachable. Clients must reconnect or explicitly re-list;
Beta 55 does not add dynamic list-change notifications.

When the optional registry is disabled, exact ha-mcp 8.2.0 behavior is
unchanged: 51 Engineering tools, 25 delegated automatic reads, 76 combined
tools, `ha_get_operation_status` as the sole held read, and fallback zero.

## Signed data selects compiled behavior

The optional Ed25519-signed release registry can select only a profile and
adapter already compiled into Engineering. Signed data cannot add code, tools,
arguments, classifications, routes, actions, writes, forwarding, or fallback.
Live schemas remain observation and never authorize themselves.

The fixed HTTPS registry refuses redirects and enforces strict parsing,
signature, identity, schema, sequence/digest chain, expiry, rollback, replay,
revocation, and capacity checks. Its accepted cache uses a bounded verified
authority chain, a durable pending marker, prior checkpoint, file fsync, and
directory fsync. Restart reverifies signatures and chain topology; interrupted
or malformed state denies positive authority. Expired positive authority
disappears while valid retained revocations remain denial-only, including
authenticated revocations whose positive cache transaction could not commit.

No production key, registry entry, signing workflow, secret, trust-anchor
activation, or registry publication is included. Tests use synthetic ephemeral
keys only.

## Dispatch safety

Each delegated call revalidates current identity, protocol, complete catalog,
authority, profile, exact tool contract, actual retained MCP session, and
generation before atomically consuming a single-use lease. Retired generations
invalidate unused leases; duplicate sequential and concurrent commits fail.
A committed call may finish once but cannot republish authority.

No semantic retry, direct-HA route, alternate provider, generic forwarding,
write reachability, or fallback is added. Health and audit additions are bounded
and sanitized. Each inbound client session must list the current dynamic
generation before a restored delegated read can dispatch.

Home Assistant Core compatibility, Nabu Casa transport recovery, semantic
readmission, writes, deployment, and production registry operation remain
separate future work.
