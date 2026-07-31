# 2.2.0-beta.5 acceptance contract

Version: `2.2.0-beta.5`

Source baseline:
`6fbf5da2369836e327568c37bd040d6ae241ab1f`

This is the source and later operator-controlled acceptance contract for the E1
update-and-recovery preflight foundation. Source implementation and validation
must not access live Home Assistant, publish an image, create a tag or release,
merge, deploy, or perform an update or recovery action.

## Immutable boundaries

- The E1 package remains in the repository-level
  `update_recovery_foundation` namespace and has no production startup or
  application import.
- Evaluation consumes only explicit caller-supplied evidence and performs no
  I/O.
- `ready_for_governed_planning` is advisory only and grants no plan, approval,
  task, provider, update, backup, restart, restore, downgrade, safe-mode,
  firmware, or execution authority.
- E1 does not load C1 signed registries or K1 knowledge manifests and neither
  foundation supplies runtime authority to E1.
- No tool, capability, health field, provider route, public schema, task
  schema, write path, or fallback is added.
- The source catalog remains 25 canonical plus 23 Engineering-native tools,
  48 local registered tools. Exact configured admission may add 26 delegated
  reads for an expected 74 live tools.
- Stable v1.1.2 is unchanged.

## Evaluation acceptance

Require explicit target identity, installed and candidate versions,
caller-supplied version direction, authoritative candidate evidence,
compatibility status and evidence, active repairs and errors, backup status and
location where policy requires them, storage evidence, target-specific power
evidence, recovery availability, expected disruption, and an allowed
post-update verification profile.

Require `upgrade` as the only direction eligible for
`ready_for_governed_planning`. A `downgrade` or `unknown` direction requires
manual review; a `same` direction with matching bounded version strings is
blocked as a no-op. Direction/string contradictions require manual review and
must never become ready. E1 must consume direction as supplied evidence and
must not parse strings or retrieve version data. Downgrade findings must cite
`docs/runbooks/DOWNGRADE-VERSUS-BACKUP-RESTORE.md`.

Require CRITICAL repairs and errors to block. Require HIGH repairs and errors
to remain warnings that force `manual_review_required`. MEDIUM and lower
severities remain informational warnings.

A missing candidate version remains a blocker because it is required to define
the proposed destination. A missing installed version remains an unknown that
requires manual review: it prevents confirmed direction and compatibility
reasoning but does not, by itself, prove that no candidate exists.

Require deterministic `ready_for_governed_planning`, `blocked`,
`manual_review_required`, and `unsupported` verdicts. Blockers, warnings, and
unknowns remain separate ordered collections. Missing critical evidence cannot
reach readiness, unsupported target classes cannot enter a generic ready path,
and evaluation cannot mutate caller input.

Require the evaluator to remain free of providers, network clients, Home
Assistant clients, clocks, runtime state, plan or task services, and operation
dispatch.

## Required validation

Run and record:

- the focused E1 suite twice;
- two buffered and one verbose complete unittest discoveries;
- compilation, metadata, YAML, dependency consistency, secret, PowerShell,
  protected-path, whitespace, Full, and exact-head Evidence gates;
- strict Engineering dependency audit;
- deterministic compatibility-registry validation and regeneration/drift
  checks;
- disposable Home Assistant contracts;
- exact-image `ha-mcp` 7.14.1 and 7.14.2 lanes;
- stable-v1 and Engineering image builds; and
- amd64, arm64, and arm/v7 no-push builds where supported.

No failing test may be weakened, skipped, or converted to an expected failure.
The Engineering image must report `2.2.0-beta.5`.

## Later integration and rollback

Evidence collection, runtime loading, compatibility or knowledge integration,
public tools, observability, governance, approval, tasks, providers, update
execution, recovery actions, and generalized verification require separately
reviewed milestones. Beta 5 grants none of that authority.

Rollback to accepted `2.2.0-beta.4` requires no E1 state migration because
Beta 5 creates no runtime update or recovery state. Rollback does not authorize
redispatch, fallback, or evidence rewriting.
