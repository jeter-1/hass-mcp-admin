# ADR-011: Governed update and recovery preflight foundation

Status: accepted for 2.2.0-beta.5

## Context

Updates and disaster recovery can change software, interrupt Home Assistant,
invalidate compatibility assumptions, or make a system unavailable. A
provider-backed update helper would cross several authority boundaries before
the evidence, recovery policy, approval model, and post-update verification
contract have been reviewed together.

### Current source-derived facts

- Stable v1.1.2 remains frozen under `hass_mcp_admin/`.
- Engineering currently advertises 48 statically registered tools plus 26 reviewed
  delegated reads, for the existing 74-tool fully admitted contract.
- ADR-007 governs the existing operational backup, reload, add-on restart, and
  Home Assistant restart contracts. It does not authorize update, restore,
  safe-mode, firmware, or downgrade execution.
- ADR-008 governs durable execution tasks. E1 neither creates a task nor changes
  task, approval, dispatch, or reconciliation authority.
- The runtime-inert `update_recovery_foundation/` package is outside both product
  packages and is not imported by application, tool registration, capability,
  governance, routing, provider, or observability code.

### Proposed future behavior

Later integration may use an update-specific governed lifecycle, but only after
its execution providers, durable task model, approval requirements, recovery
verification, and negative reachability have been reviewed separately.

## Decision

Add an immutable, provider-free decision model that consumes already-collected
evidence and returns one deterministic assessment. It recognizes:

- Home Assistant Core;
- Supervisor;
- Home Assistant OS;
- add-on/app;
- HACS integration;
- HACS frontend component;
- Engineering MCP server;
- upstream `ha-mcp`; and
- firmware update entity.

The evaluator produces exactly one of:

- `ready_for_governed_planning`;
- `blocked`;
- `manual_review_required`; or
- `unsupported`.

Blockers, warnings, and unknowns are separate ordered collections. Unknown
decision-critical evidence requires manual review; warnings require review only
when the explicit target policy says so. The output includes a SHA-256
fingerprint of the canonical decision payload. It is not a signature,
authorization, plan ID, approval, or execution token.

Version direction is explicit caller-supplied evidence with the closed values
`upgrade`, `downgrade`, `same`, and `unknown`. E1 does not parse version
strings, retrieve versions, or infer direction. Only a confirmed `upgrade` is
eligible for `ready_for_governed_planning`. A `downgrade` requires manual
review under
`docs/runbooks/DOWNGRADE-VERSUS-BACKUP-RESTORE.md`, a `same` candidate is
blocked as a no-op, and `unknown` requires manual review. Contradictions
between supplied direction and the bounded installed/candidate strings fail
closed rather than being repaired or inferred.

An authoritative candidate is required to define the proposed destination, so
a missing candidate version remains a blocker. An unknown installed version
prevents confirmed direction and compatibility reasoning but does not, by
itself, prove that no candidate exists; it therefore remains an unknown that
requires manual review. A claimed known direction cannot override that
asymmetry.

Unresolved current issues retain their severity distinction: CRITICAL repairs
and errors are blockers, HIGH repairs and errors remain warnings but require
manual review, and MEDIUM-or-lower issues are informational warnings.

The proposed default policy is:

| Target | Backup prerequisite | Maximum age | Stale disposition | Recovery path | Known stable power |
| --- | --- | ---: | --- | --- | --- |
| Home Assistant Core | required | 24 hours | block | required | advisory |
| Supervisor | required | 24 hours | block | required | advisory |
| Home Assistant OS | required | 24 hours | block | required | required |
| add-on/app | required | 72 hours | manual review | manual review if absent | advisory |
| HACS integration | required | 72 hours | manual review | manual review if absent | advisory |
| HACS frontend component | required | 72 hours | manual review | manual review if absent | advisory |
| Engineering MCP server | required | 24 hours | block | required | advisory |
| upstream `ha-mcp` | required | 24 hours | block | required | advisory |
| firmware update entity | not applicable | n/a | n/a | required | required |

Every supported target also has one allowed post-update verification profile.
A missing or mismatched profile cannot reach planning readiness.

## Future lifecycle

The architecture sequence for a later implementation is:

```text
inspect
  -> compatibility review
  -> preflight
  -> backup prerequisite
  -> elevated approval when applicable
  -> durable update task
  -> expected disruption
  -> recovery verification
  -> rollback or restore decision
```

E1 implements only the pure `preflight` decision over supplied evidence. The
package is deliberately outside the stable and Engineering runtime products;
it does not collect evidence or implement any other stage.

## Safety boundary

This foundation does not:

- register a public MCP tool;
- call or select a provider;
- alter upstream admission, fallback, routing, or the 48/26/74 contract;
- create or approve a governance plan or durable task;
- perform an update, backup, restart, restore, safe-mode action, downgrade, or
  firmware operation;
- access Home Assistant, Supervisor, HACS, an add-on, an update entity, or the
  network.

Unsupported target strings return `unsupported`. Incompatible targets, absent
authoritative candidate versions, unavailable compatibility evidence, required
backup failures, insufficient storage, critical repairs or errors, and a known
absence of policy-required recovery paths return `blocked`. HIGH repairs or
errors, downgrades, unknown version direction, and inconsistent direction
evidence cannot reach readiness and require manual review.

## Consequences

Future integrations receive a small deterministic contract and explicit policy
instead of embedding update decisions in provider or tool code. Readiness means
only that the supplied evidence satisfies this proposed preflight policy; it
does not authorize planning or execution.

The default policy values remain proposed future behavior. Shipping this inert
foundation advances the Engineering source version to `2.2.0-beta.5`, but the
package is absent from the add-on runtime image and is not loaded by the
Engineering application.

The policy requires separate review against the merged governance/task
baseline before any runtime integration.
Provider contracts, evidence collection, approvals, persistence, execution,
reconciliation, and public schemas remain deliberately absent.
