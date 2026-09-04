# Engineering 2.2.0-beta.57 release notes

Beta 57 materializes the reviewed source transition that restores the existing
governed dashboard-update path for exact ha-mcp 8.4.1. The advertised
Engineering version is 2.2.0-beta.57, the staged declaration has been consumed,
and stable remains 1.1.2. Materialization does not publish or deploy the release.

## Dashboard provider restoration

Dashboard inventory and configuration reads now carry a binary-owned exact
provider identity derived from the reviewed 8.4.1 release, complete catalog,
getter and setter descriptors, attestation, source and image, protocol, and
provider generation. Planning consumes that same identity instead of
reconstructing authority from unrelated lifecycle health.

Read and write authority remain separate. A getter can remain available when
setter authority is absent, but no actionable plan can then be created. The
setter remains reachable only through the existing dashboard plan, owner
approval, complete F3 locks and preflight, durable single-dispatch intent, and
authoritative reread. It is not a delegated tool and has no generic forwarding,
direct-HA substitute, or fallback.

## Patch and approval correctness

The bounded RFC 6902 subset now accepts array insertion at canonical indices
zero through the current length and final `-` append. Invalid indices,
intermediate append tokens, executable transforms, broad unsupported
operations, and oversize changes still fail closed.

Authenticated dashboard approval now renders the complete bounded value for
every declared operation as inert escaped JSON. This projection is bound to the
exact preread, canonical patch, result, and plan. Missing, protected, malformed,
tampered, or oversized approval evidence cannot authorize execution.

An exact elevated dashboard plan uses one authenticated owner plan approval;
classifier severity remains disclosed and no longer creates a duplicate
severity-only acknowledgement for this exact f2-v2 operation. Historical
approval records remain unchanged.

The current tighter semantic and material bounds were retained; stale-state
refusal, one-dispatch ownership, sequential and concurrent duplicate
suppression, response-loss readback recovery, exact verification, and zero
fallback are unchanged.

## Preserved boundaries

Exact ha-mcp delegated-read admission remains 25 reads, yielding 76 combined
tools with the 51 Engineering tools. `ha_get_operation_status` remains held.
No public input schema, general delegated-read route, Core readmission, Nabu
transport, workflow, container, stable-v1 behavior, dashboard
creation/deletion/resource/metadata authority, arbitrary forwarding, or
fallback changes in this candidate. Deployment metadata changes only for the
authorized 2.2.0-beta.56 to 2.2.0-beta.57 materialization.

This source materialization does not merge, publish, deploy, access live Home
Assistant, or run the reversible dashboard canary.
