# Engineering 2.2.0-beta.58 release notes

Beta 58 is the materialized source candidate for exact ha-mcp 8.4.3 continuity
and the protected data-only release-registry preparation path. The advertised
Engineering version is 2.2.0-beta.58, stable remains 1.1.2, and
`.release/next-version` was consumed. Materialization has occurred. Beta 57 is
the prior and rollback release.

Materialization did not merge, publish, deploy, restart anything or access a
live system. The remaining separately owner-authorized actions are merge,
publication, deployment, production-key creation, environment/secret
configuration, registry publication, trust-anchor activation, and live
updates.

## Exact ha-mcp 8.4.3 support

The exact official 8.4.3 source, immutable standalone and Home Assistant App
images, 78-tool catalogs, error envelopes, and runtime contracts are now
binary-owned compatibility evidence. All 25 reviewed delegated reads are
admitted. Together with 51 Engineering tools, the exact client catalog remains
76. `ha_get_operation_status` stays held, and every App, mixed, action, write,
destructive, unknown, and generic-forwarding capability stays unreachable.

Seventy-seven descriptors are identical to 8.4.1. `ha_search` retains read-only
semantics with a stricter positive `config_time_budget` minimum and therefore
uses an exact 8.4.3 compiled contract. Error compatibility remains
capability-scoped, session and lease validation remain mandatory, and fallback
remains zero.

## Update continuity and protected preparation

Signed future catalogs no longer lose compatible reads merely because unrelated
catalog order or membership changes. Every known read still requires its exact
compiled descriptor, argument restrictions, error binding, generation,
session, and single-use lease. A missing or changed capability is withheld
without exposing unknown replacements or disabling exact siblings.

The new manual protected-main workflow has closed `add` and `revoke`
operations. Add resolves one exact official stable ha-mcp source and observes
the version tag once to obtain its OCI index digest. Every raw-index,
architecture, pull, label, runtime-capture, and evidence operation thereafter
uses that immutable digest reference. It captures the catalog twice against a
read-only fixture, requires zero mutation, selects only an existing binary
profile, signs a bounded chained registry journal, and opens a draft data-only
PR containing exactly the journal, version evidence, and generated index.

Revoke does not inspect source or OCI state and cannot run an upstream image.
It requires exactly one existing positive signed entry, replaces that authority
with a denial-only tombstone, preserves release evidence, and opens a draft
data-only PR containing exactly the journal and generated index. Re-addition of
a revoked version remains prohibited. The private-key input is scoped to the
protected signing step. Neither operation has package, publication, merge,
deployment, restart, live-system, route, or fallback authority.

The journal provides bootstrap, monotonic linkage, expiry, checkpointing,
denial-only revocation, strict bounds, signature revalidation, and durable
atomic replacement. Production key creation, environment/secret setup,
registry publication, public-key activation, and live update remain separate
operator actions.

## Dashboard continuity

Exact 8.4.3 separately retains the reviewed Beta 57 dashboard getter and setter
authority. The generic read registry cannot authorize dashboards; a future
dashboard release requires its own exact binary-owned or signed attestation.
Planning, owner approval, F3 preflight, durable one-dispatch ownership,
authoritative reread, duplicate suppression, response-loss recovery, and zero
fallback are unchanged.

No stable-v1, public tool schema, provider route, generic write authority,
Core readmission, Nabu transport, container, deployment, or live Home Assistant
behavior is changed by this materialized source candidate.
