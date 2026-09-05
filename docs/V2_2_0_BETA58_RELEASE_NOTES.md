# Engineering 2.2.0-beta.58 release notes

Beta 58 stages exact ha-mcp 8.4.3 continuity and the protected data-only
release-registry preparation path. The advertised Engineering release remains
2.2.0-beta.57 and stable remains 1.1.2. This source staging does not publish or
deploy Beta 58.

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

The new manual protected-main workflow resolves one exact official stable
ha-mcp source and image, captures its catalog twice against a read-only fixture,
requires zero mutation, selects only an existing binary profile, signs a
bounded chained registry journal, and opens a draft data-only PR. Its private
key input is scoped to the protected signing step. It has no package,
publication, merge, deployment, restart, live-system, route, or fallback
authority.

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
behavior is changed by this staged source candidate.
