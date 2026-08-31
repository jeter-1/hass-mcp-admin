# ADR-022: Owner-authoritative product direction

Status: accepted as product and repository-agent policy

Date: 2026-08-31

Decision owner: Josh (`jeter-1`)

## Context

The Engineering MCP project began before the community
`homeassistant-ai/ha-mcp` server was discovered. Its original purpose was to
give AI materially more capable, direct, structured access to Home Assistant so
that it could investigate real conditions, gather evidence, recommend useful
changes, implement approved work, and verify the result.

The discovery of `ha-mcp` changed the responsibility split, not that purpose.
The standard server should supply capabilities it implements safely and
accurately. The Engineering server should extend beyond it when deeper
inspection, analysis, administration, implementation, validation, or recovery
is required.

Governance mechanisms were added to address real risks: exact plans, impact and
risk classification, approval binding, drift checks, audit records, readback,
rollback, and fail-closed behavior. Those controls improved the design. They do
not redefine the Engineering MCP as primarily a governance product or a
read-only analysis server. A system that only proves it can refuse work does not
satisfy the intended product outcome.

Some earlier repository language can be read too narrowly. In particular,
[`ADR-002`](ADR-002-ENGINEERING-MCP-FACILITATOR.md) correctly separates ordinary
Home Assistant operations from Engineering responsibilities and preserves
governed direct-HA exceptions. Its terms "facilitator" and "governance layer"
must not be interpreted to prohibit useful bounded Engineering implementation.

This decision records the product owner's current direction and the rules agents
must use when requirements, prior AI-generated architecture, implementation
convenience, or conservative policy appear to conflict with it.

## Decision

The product north star is:

> Make AI usage with Home Assistant as capable and safe as practical, using
> concrete evidence to produce sound recommendations and complete approved
> implementation.

The intended end-to-end workflow is:

> Inspect the real system -> gather concrete evidence -> explain findings ->
> recommend a change -> obtain approval when risk requires it -> implement ->
> read back and validate -> report the exact result -> roll back or recover when
> necessary.

Evidence, recommendation, implementation, verification, and recovery are all
first-class outcomes. Read-only inspection is the default operating mode for
ordinary engineering and review work; it is not the final capability limit of
the product.

## Product authority

Josh is the product owner and final authority for:

- the desired product outcome and priorities;
- material architecture and responsibility boundaries;
- approved scope and risk policy;
- the meaning and strength of approval;
- release goals; and
- deployment and live-test authorization.

Josh may explicitly supersede a prior decision. Agents must record the new
decision and identify affected architecture or documentation rather than allow
an older AI-generated assumption to silently prevail.

This product authority is distinct from factual evidence and action authority:

- Josh's current explicit instruction is authoritative for desired product
  direction and for actions it expressly authorizes.
- GitHub `main`, its exact source, tests, schemas, manifests, workflows, and
  release declarations are authoritative for what the software implements.
- Exact read-only observation of the deployed Engineering server and Home
  Assistant path is authoritative for current operational behavior.
- A product decision does not make an unverified implementation or runtime claim
  true, and it does not authorize a merge, release, deployment, credential use,
  or live mutation that was not requested.

Agents, tests, documentation, prior conversations, review summaries, and pull
requests may supply evidence or recommendations. They do not independently
redefine product purpose, architecture, scope, risk policy, approval meaning, or
release goals.

If an approved objective cannot be implemented within its stated constraints,
the implementing agent must stop and report the conflict. It must not silently
narrow the objective, broaden the scope, weaken a safeguard, or approve its own
new architecture.

## Responsibility boundaries

Responsibility is selected by required semantics, not by a blanket preference
for either server:

| Responsibility | Intended boundary |
| --- | --- |
| Standard Home Assistant MCP | Ordinary state reads, routine low-risk runtime actions, common device control, and standard operations it implements safely and accurately. |
| Home Assistant Engineering MCP | Deep evidence collection, configuration and dependency analysis, engineering diagnostics, structured planning, bounded administration and configuration changes, exact readback, verification, auditability, rollback, and recovery. |
| AI client or prompt | Selecting the appropriate server, applying interaction policy, choosing when to investigate, asking the user for approval, and presenting results. |
| Shared contract | Truthful capability, provider, fallback, completeness, truncation, risk, approval, verification, and recovery metadata needed for a client to act correctly. |
| Deployment | Packaging, configuration, credentials, installation, upgrade, rollback, and exact runtime verification. |

The Engineering MCP must not become a generic fallback for routine device
actions merely because one client cannot reach the standard action tool. It also
must not omit a legitimate Engineering capability solely because the standard
server does not provide it.

## Proportional safety

Governance is a safety mechanism, not the product identity. Controls must be
proportional to demonstrated risk:

- Ordinary, reversible, well-understood work should remain low-friction.
- Configuration changes should generally use an exact proposal, impact
  analysis, approval appropriate to the risk, readback, validation, and truthful
  failure reporting.
- Broad-impact, destructive, registry, integration, restart, replacement, or
  difficult-to-recover operations may require stronger evidence, dependency
  analysis, target and state binding, separate approval, recovery planning, and
  verified rollback.

When a bounded, testable, maintainable, and recoverable write path is practical,
prefer governed capability over blanket prohibition. Do not weaken a necessary
safeguard without evidence, and do not retain a restriction merely because it is
conservative. Inability to act is not by itself the definition of safety.

Results must distinguish success, partial success, changed but unverified,
verification failure, unavailable provider, stale state, non-atomic execution,
rollback attempted, rollback completed, rollback failed, and complete failure
where those states apply. The product must not claim stronger atomicity,
approval, completeness, or verification than the implementation provides.

## Acceptance and roadmap consequences

Acceptance must prove both sides of the contract:

- legitimate authorized operations can succeed;
- successful changes can be read back and verified;
- unsafe, invalid, stale, or unauthorized operations are rejected;
- partial and failed operations are reported truthfully; and
- recovery or rollback behavior is exercised where the capability promises it.

Refusal-only coverage is insufficient evidence of product maturity.

Roadmap decisions should weigh practical AI usefulness, evidence quality,
implementation coverage, safety and recoverability, household operational value,
and gaps in the standard `ha-mcp`. Operational administration, registry and
integration work, dashboards, additional configuration families, and bounded
device-level changes are consistent with the mission when their contracts are
specific and their controls are proportional. Their scheduling remains a
separate product decision.

## Relationship to existing decisions

This ADR supplies the controlling product-intent interpretation for existing and
future architecture decisions. It does not discard the specific provider,
fallback, approval, evidence, compatibility, audit, or recovery controls already
accepted by the repository.

In particular, ADR-002 remains authoritative for the split between ordinary
Home Assistant operations and Engineering capabilities, for truthful provider
selection, and for the prohibition on silent unsafe fallback. ADR-022 clarifies
that its facilitator role includes implementing legitimate, bounded,
Engineering-specific work after the required authority is present.

Future controls and capability proposals must be evaluated for both risk
reduction and operational usefulness. A new architecture decision that limits
the north-star workflow must identify the demonstrated risk, why a more
proportional control is inadequate, and the evidence that would justify
reconsideration.

## Non-goals

This decision does not:

- add or change a runtime tool, schema, provider, route, fallback, write path, or
  approval mechanism;
- authorize ordinary device control through the Engineering MCP;
- weaken any existing safety or compatibility control by itself;
- authorize a source change, merge, release, deployment, credential access, or
  live Home Assistant mutation;
- claim that reviewed source is deployed or that deployed behavior matches
  source; or
- schedule any roadmap capability without a separate bounded decision and task.

## Consequences

- Repository agents must treat Josh's current explicit product direction as
  authoritative and report conflicts instead of silently redefining it.
- Architecture and review must distinguish product intent, source truth, and
  deployed operational truth.
- Implementation contracts must preserve useful positive paths alongside
  fail-closed behavior, verification, and recovery.
- Governance proposals must state both their risk reduction and their cost to
  legitimate operation.
- Existing runtime behavior is unchanged. This ADR and its corresponding agent
  rules are documentation-only policy.

## Reconsideration triggers

Revisit this decision only when Josh explicitly changes the product direction or
when new evidence shows that the responsibility split or proportional-safety
model cannot support safe, useful Home Assistant engineering work.
