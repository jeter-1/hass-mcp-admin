# ADR-015: F3 operational evidence and manual-review holds

## Status

Accepted for runtime-inert Beta 19 conformance; activation deferred to F3-D.

## Context

Operational effects can outlive a provider response or the Engineering
process. A second authority store or a write between durable intent and network
mutation would weaken the accepted F3 no-blind-redispatch boundary. Timer-based
hold release could also permit a second conflicting mutation merely because an
ambiguity aged past a deadline.

## Decision

One public task owns one durable F3 child per operation. Final locked preflight
occurs before the caller-owned idempotent complete-authorization callback. The
merged executor commits intent before the single provider mutation. Once intent
exists, cancellation and redispatch are prohibited; recovery is observation
and verification only.

The F3 child record and its bounded operation-evidence namespace are the sole
execution authority. C2 exposes only a frozen read-only projection. JSONL and
events are secondary audit evidence. No operation-specific ledger, task store,
coordinator, or background worker is created, and no operation evidence write
may occur between intent and provider mutation.

Only the affected resource key may be promoted for manual review. Observation
and escalation deadlines change outcomes but never release holds. Verified
resolution or explicit future authenticated private-Ingress reconciliation is
required for release. C2 declares selective keys but implements neither shared
promotion nor release authority.

Rollback is a separate governed F3 operation requiring its own approval and is
never automatic. Operational C2 capabilities expose no executable rollback.

## Consequences

Lost-response reload remains ambiguous without an independent effect signal;
readiness is insufficient. Add-on restart requires evidence beyond unchanged
running state. HA restart requires persisted outage/reconnect evidence. These
cases reach manual review without redispatch.

F3-D must implement authoritative child evidence mapping, one central startup
and periodic coordinator, selective hold administration, the private
reconciliation surface, governed rollback Option A where approved, and runtime
route migration. Issue #92 and dashboard execution remain outside this ADR.
