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
The supported deployment has one client-facing Nabu Casa connector. The
Engineering server must therefore present the unified public MCP surface.
Reviewed `ha-mcp` capabilities remain valuable, but they operate as internal
delegated providers behind Engineering rather than as a second public server the
AI client must select. Engineering-native capabilities extend beyond the
upstream provider when deeper inspection, analysis, administration,
implementation, validation, or recovery is required.

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
> concrete evidence to produce sound recommendations and complete
> owner-authorized implementation.

The intended end-to-end workflow is:

> Inspect the real system -> gather concrete evidence -> explain findings ->
> recommend a change -> explain material effects and uncertainty -> obtain the
> owner's decision -> implement exactly -> read back and validate -> report the
> exact result -> roll back or recover when necessary.

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
| Client-facing Home Assistant Engineering MCP | The one supported public MCP endpoint through the Nabu Casa connector. It presents one truthful catalog across admitted delegated and Engineering-native capabilities without hiding provider identity or fallback state. |
| Internal reviewed `ha-mcp` provider | Ordinary state reads, routine runtime actions, common device control, and other upstream capabilities whose exact contracts Engineering has admitted. It supplies capability; it does not independently establish authority, select fallback, or become a second public connector. |
| Engineering-native providers and orchestration | Deep evidence collection, configuration and dependency analysis, engineering diagnostics, typed administration and configuration changes, exact readback, verification, auditability, rollback, recovery, and capabilities absent from or semantically incompatible with the upstream provider. |
| AI client or prompt | Expressing the user's intent, choosing when to investigate, explaining effects and uncertainty, obtaining the owner's decision when needed, and presenting results. Provider selection belongs to Engineering rather than the client. |
| Shared contract | Truthful capability, provider, fallback, completeness, truncation, risk, approval, verification, and recovery metadata needed for a client to act correctly. |
| Deployment | Packaging, configuration, credentials, installation, upgrade, rollback, and exact runtime verification. |

Engineering may delegate a routine operation to an admitted upstream capability;
that is normal provider selection, not fallback. It must not silently substitute
an unreviewed provider when the selected contract is unavailable or incompatible.
It also must not omit a legitimate Engineering capability solely because the
upstream server does not provide it.

## Technical integrity and owner consequence authority

The system must separate two kinds of uncertainty.

**Execution uncertainty** concerns what the system will do and whether it can do
it reliably. It includes uncertainty about the exact target, operation,
arguments, authenticated authority, provider contract, current state, stale or
drifted evidence, concurrency, dispatch ownership, at-most-once behavior,
readback, and verification. Material execution uncertainty is a hard stop.
Neither owner preference nor a risk acknowledgement can make an ambiguous or
technically unsound dispatch safe.

**Consequence uncertainty** concerns what an otherwise exact operation may mean
for the household: a light may turn off, a door may close, an automation may
affect a safety-relevant device, or dependency analysis may be unable to prove
every downstream effect. Engineering must disclose those effects and the limits
of its evidence clearly. A `high`, `critical`, `safety_critical`, `unknown`, or
incomplete consequence classification does not by itself make an exact
operation non-actionable. Josh decides whether the disclosed operational
consequence is acceptable.

A classifier may recommend caution, acknowledgement, a recovery plan, or a more
specific operation. It must not silently replace the owner's decision with its
own product-policy judgment. Risk and consequence labels are decision support;
they are not independent execution authority and are not a substitute for the
technical integrity checks above.

One clear authenticated owner decision may accept the disclosed operational
consequences of an exact operation. Additional approval actions require a
distinct authority or recovery boundary, such as a separately authorized
rollback, or a separately justified destructive or difficult-to-recover step;
classifier severity alone is not a reason to collect duplicative approvals.

This distinction does not grant current runtime authority. The shipped policy,
approval, and provider code remains authoritative for current behavior until a
separately scoped implementation is reviewed, released, and deployed.

## Proportional safety

Governance is a safety mechanism, not the product identity. Controls must be
proportional to demonstrated risk:

- Ordinary, reversible, well-understood work should remain low-friction.
- Configuration changes should generally use an exact proposal, impact and
  consequence disclosure, an explicit owner decision when required, readback,
  validation, and truthful failure reporting.
- Broad-impact, destructive, registry, integration, restart, replacement, or
  difficult-to-recover operations may require stronger evidence, dependency
  analysis, target and state binding, separate approval, recovery planning, and
  verified rollback.

When a bounded, testable, maintainable, and recoverable write path is practical,
prefer governed capability over blanket prohibition. Do not weaken a necessary
safeguard without evidence, and do not retain a restriction merely because it is
conservative. Inability to act is not by itself the definition of safety.

## Development sequencing

Build capabilities as though they will be governed, but do not require the full
governance system to exist before the capability can be understood and proven.
The preferred sequence is:

1. define a typed, bounded capability and its provider boundary;
2. prove its functional behavior against disposable or otherwise authorized
   evidence, including authoritative readback and failure behavior;
3. normalize stale-state, dispatch, verification, error, and recovery semantics;
4. add the least governance necessary around the now-known operation; and
5. harden persistence, approvals, audit, rate limits, compatibility, and
   adversarial behavior in proportion to demonstrated risk.

This sequence does not permit arbitrary filesystem or shell access, generic
unbounded service forwarding, unreviewed providers, secret exposure, or blind
dispatch. Exact targets, typed operations, evidence capture, provider
attribution, and verification remain architectural boundaries from the start.
It does prevent speculative policy machinery from repeatedly redefining or
blocking functionality before the operation itself is understood.

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
future architecture decisions. It does not discard specific provider, fallback,
evidence, compatibility, audit, dispatch-integrity, verification, or recovery
controls already accepted by the repository. Existing approval mechanisms also
remain runtime truth until separately changed, but their future product-policy
interpretation is narrowed by the distinction between execution uncertainty and
consequence uncertainty in this ADR.

In particular:

- ADR-002 remains authoritative for truthful provider selection, reviewed
  provider contracts, direct-HA exceptions, and the prohibition on silent
  unsafe fallback. ADR-022 supersedes the assumption that the AI client can or
  should reach a separate standard MCP endpoint. In the supported one-connector
  topology, Engineering is client-facing and `ha-mcp` is an internal provider.
- ADR-012 remains authoritative for the currently shipped policy snapshots,
  approval records, hash binding, stale-state checks, and dispatch protections.
  For future implementation, ADR-022 supersedes the rule that unknown,
  incomplete, high, critical, or safety-critical consequence classification is
  by itself sufficient to prohibit an otherwise exact operation. Ambiguous
  execution or authority continues to fail closed.

Future controls and capability proposals must be evaluated for both risk
reduction and operational usefulness. A new architecture decision that limits
the north-star workflow must identify the demonstrated risk, why a more
proportional control is inadequate, and the evidence that would justify
reconsideration.

## Non-goals

This decision does not:

- add or change a runtime tool, schema, provider, route, fallback, write path, or
  approval mechanism;
- add or admit an ordinary device-control route by itself; the intended public
  topology may expose such a capability only after its internal provider and
  contract are separately implemented, reviewed, released, and deployed;
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
- Risk and consequence projections must inform the owner's decision without
  being confused with proof that an exact operation is technically unsafe.
- Existing runtime behavior is unchanged. This ADR and its corresponding agent
  rules are documentation-only policy.

## Reconsideration triggers

Revisit this decision only when Josh explicitly changes the product direction,
the supported one-connector deployment constraint changes, or new evidence shows
that the responsibility split or proportional-safety model cannot support safe,
useful Home Assistant engineering work.
