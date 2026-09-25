# Engineering roadmap

## Current direction

2026-09-25: Josh selected **dashboard planning capacity of 256 semantic leaves
plus actionable diagnostics**. Source implements that bounded correction on
the 2.4.0-beta.2 baseline; this entry makes no new publication, deployment or
installed-acceptance claim. The advertised version remains beta.2, with 79
supported tools (54 static + 25 delegated). Script dependency coverage and
bounded retained Core-log access are already delivered in beta.1/beta.2; see
the [beta.2 acceptance contract](V2_4_0_BETA2_ACCEPTANCE.md). Stable 2.3.0 remains
the previously accepted stable Engineering release.

The dashboard correction retains current array-suffix accounting and complete
approval projection. The original four-operation Home fixture needs 54 leaves,
so a 16-leaf ceiling blocked useful work. A 256-leaf proposal must still pass
all other existing bounds and execution checks; an oversized proposal returns
its actual count and limit without configuration content. See the
[dashboard contract and decision](F3_DASHBOARD_WRITE_CONTRACT.md#semantic-capacity-and-safe-diagnostics).

PR [#132](https://github.com/jeter-1/hass-mcp-admin/pull/132) is historical
requirements/fixture evidence. Its array operations and complete approval
projection are already present; this fresh correction addresses the residual
capacity and diagnostic gaps without restoring its old counting implementation.
The old PR and branch remain preserved; this document does not close or merge
them. Candidate validation, independent review and Josh's Ready decision remain
separate from any future version transition, publication, deployment or live test.

After this increment, select one remaining exact helper operation from
[#92](https://github.com/jeter-1/hass-mcp-admin/issues/92) or one concrete blocked
administration task. General script inventory completeness, transitive dependency
analysis and broader configuration families remain separate decisions. No new
feature or release number is authorized by this roadmap entry.

### Retained September 24 release preparation record

2026-09-24 release preparation: Josh selected **script dependency coverage** as
the next increment after stable 2.3.0 acceptance. The materialized **2.4.0-beta.1** candidate
adds bounded, partial script diagnostics through the existing admitted reader;
it does not claim a complete all-script inventory or script execution. See the
[source contract](ENTITY_DEPENDENCY_ANALYSIS.md) and
[acceptance gates](V2_4_0_BETA1_ACCEPTANCE.md). Advertised source versions now agree
on 2.4.0-beta.1; 2.3.0 remains the last accepted stable release. Final candidate
validation/review, publication, deployment and installed acceptance remain
separate gates. The September 22 preparation record below is retained
as historical context, not an instruction to repeat completed stable acceptance.

### Retained September 22 release preparation record

Updated 2026-09-22 for **2.3.0** stable-release preparation. This source contract
is not evidence of publication or deployment. Engineering remains one public
connector with reviewed internal providers, concrete evidence, useful approved
implementation, exact readback and operation-specific recovery.

1. **Complete stable 2.3.0.** RC2 installed acceptance is closed with its original
   evidence attribution and qualifications retained. Prepare the version-only
   stable transition with fresh candidate Evidence/CI and independent review
   before Josh's Ready decision. Verify protected merge/publication, then obtain
   separate deployment authority and close stable installed-image, catalog,
   useful-read and settlement gates. Carry accepted RC2 functionality forward;
   no repeat beta campaign or new household device cycle is required.
2. **Proposed next feature: script dependency coverage.** Existing analysis reports
   unavailable script configuration coverage. Establish bounded complete inventory,
   canonical identifiers and provider provenance before adding readable scripts to
   the existing dependency index. Prove useful reference detection and truthful
   partial results for unreadable, truncated or dynamically opaque evidence. This
   is a separate implementation decision after stable, not part of this release.
3. **Then select one broader administration operation.** Prioritize a real blocked
   household task, with a typed target, exact effect, readback and a usable recovery
   contract. Registry metadata, integration options and additional configuration
   families are candidates, not approved scope. A newly urgent task can change
   their order through Josh's decision.

The existing foundations include governed configuration and operational plans,
exact input_boolean ON/OFF, bounded existing-dashboard updates, durable F3
execution and recovery, typed fan/light/switch actions, and reviewed signed Core
applicability. Coverage remains bounded: dashboard updates are non-atomic against
external editors, dependency configuration families are incomplete, and recovery
is operation-specific. General compensation and arbitrary administration are not
claimed as completed features.

Tracking reconciliation at this source baseline:

- [#176](https://github.com/jeter-1/hass-mcp-admin/issues/176): publication handoff
  and workflow_run corrections are merged. The real non-release path passed in
  run 35638306215. RC2 automatic publication run 35690377391 and installed-image
  binding passed; retain their original source and attribution. Stable requires
  its own publication and installed-image evidence.
- [#174](https://github.com/jeter-1/hass-mcp-admin/issues/174): dashboard provider
  identity work was incorporated in #179. Old draft #132 overlaps that work;
  its remaining scope needs reconciliation rather than another blind merge.
- [#92](https://github.com/jeter-1/hass-mcp-admin/issues/92): exact input_boolean
  ON/OFF already exists; toggle and other helper types need distinct scope.
  A second public provider connector is not the supported product topology.

This documentation does not close issues or pull requests. The
[2.3.0 acceptance contract](V2_3_0_ACCEPTANCE.md) defines the current release
and installed checks; historical milestones below retain their original context.

## Historical 2.1 and 2.2 foundation roadmap

The following record describes earlier milestones and priorities. Its future
work and version statements are historical, not current release authority.

Status: 2.1A and F2 accepted; live 8.0.0 lifecycle response correction in `2.2.0-beta.15`

The required milestone order is:

```text
2.1A Operational Administration
→ 2.1B Broader governed administration
→ 2.1C Upstream lifecycle automation
```

The signed compatibility registry may move ahead of 2.1B only if upstream
release churn becomes the higher operational risk.

## 2.1A staged delivery

1. Beta 1 established versioned operational plans, reusable
   configuration-check evidence, externally approved full-backup creation,
   independent backup verification, indeterminate recovery, and operational
   health/audit data.
2. Beta 2 completes the remaining family coherently: controlled reload with
   planning/apply validation, exact add-on restart, Home Assistant restart,
   operation-specific verification, expected-disruption handling, and durable
   background/startup reconciliation without blind redispatch.

2.1A completed its source and separately authorized deployed acceptance.

## 2.2.0-beta.1 F1 foundation

F1 separates immutable plans, existing exact-hash approvals, and mutable
durable execution tasks. It provides task lookup, bounded listing,
pre-dispatch cancellation, exact task idempotency, append-only lifecycle
events, startup rehydration, a 24-hour post-dispatch manual-review deadline,
and legacy plan projections. Existing operation-specific verification remains
authoritative.

F1 does not implement elevated/risk-aware approval UX (F2), shared locks (F3),
compensation (F4), generalized verification (F5), comprehensive historical
terminal migration (F6), later protocol/lifecycle work (F7), or MCP-native
Tasks. Those remain future milestones and receive no implied authority from
the task record.

## 2.2.0-beta.2 F1 corrective release

Beta 2 keeps the F1 architecture and public tool surface unchanged. It
separates original provider-response receipt from later readback verification
and reconciles operation counters with durable duplicate-apply task events.
It does not begin F2 or integrate C1, E1, K1, or another development lane.

## 2.2.0-beta.3 C1 foundation

Beta 3 defines the strict signed compatibility-registry format, canonical
serialization, content digest, Ed25519 verification, trust-anchor selection,
sequence and replay rules, expiry, previous-digest chaining, revocations, and
typed fail-closed validation. It does not load or retrieve a registry and does
not alter compiled admission, tools, health, providers, execution authority,
writes, or fallback behavior.

## 2.2.0-beta.4 K1 foundation

Beta 4 defines strict local knowledge manifests, provenance-only trust classes,
independent version relevance, bounded path/format/hash validation, expiration,
duplicate and version-conflict handling, deterministic ordering, and exact
citation identity. Knowledge content remains instruction-inert data.

K1 does not load at startup, retrieve remote content, register an MCP tool,
authorize recommendations or plans, access Home Assistant, integrate with the
signed compatibility registry, execute a write, or add fallback behavior.

## 2.2.0-beta.5 E1 foundation

Beta 5 defines immutable update and recovery evidence, explicit per-target
policy, deterministic findings, and the exact
`ready_for_governed_planning`, `blocked`, `manual_review_required`, and
`unsupported` advisory verdicts. Blockers, warnings, and unknowns remain
separate, and absent decision-critical evidence never becomes ready.

E1 is a pure evaluator. It does not collect evidence, load at startup, access
Home Assistant or the network, register a tool, create a plan, approval, or
task, call a provider, perform an update, backup, restart, restore, downgrade,
or safe-mode action, integrate C1 or K1 at runtime, or add fallback behavior.

## 2.2.0-beta.6 F2 policy foundation

Beta 6 derives deterministic risk delta, physical consequence, and
`standard_admin`, `elevated_admin`, or `prohibited` policy from each normalized
governed plan. Authority version 3 binds policy to the plan and requires one
administrator action for standard plans or separate plan approval and
elevated-risk acknowledgement from the same administrator for elevated plans.

F2 does not add a resource, MCP tool, provider, arbitrary service call,
update/recovery execution, shared F3 lock semantics, F4 transaction graphs or
compensation, F5 generalized verification, write fallback, or C1/K1/E1 runtime
authority. F1 task schema 1 and no-blind-redispatch behavior remain unchanged.

## 2.2.0-beta.7 F2 acceptance corrections

Beta 7 records affirmative configuration-provider response evidence before
readback and projects prohibited plans as terminal/non-actionable across legacy
compatibility fields, pending health counters, Ingress, and handoff evidence.
It does not change policy mapping, approval authority, task schema, resource or
provider reachability, update/recovery behavior, or fallback.

## 2.2.0-beta.8 persisted prohibited-plan compatibility

Beta 8 restores detail, listing, health, Ingress, startup, and handoff reads for
the exact validated Beta 6 prohibited-plan shape created by same-target
supersession. Compatibility is structural and read-only; no record is migrated
or rewritten, and any authority or execution contradiction fails closed.

Beta 8's manually reconstructed compatibility fixture omitted
`contract_version`, defaulted to contract v1, and did not model the contract-v2
records actually written by Beta 6. The deployed correction therefore did not
recognize those real records.

## 2.2.0-beta.9 real persisted prohibited-plan compatibility

Beta 9 generates neutral compatibility fixtures through the exact shipped Beta
6 writer, recognizes only its contract-v2 prohibited/superseded representation,
validates prepared operation evidence, retains every contradiction refusal, and
keeps reads byte-preserving. It also contains bounded per-record projection
failures in plan inventory and reconciles every loaded plan in health without
swallowing systemic storage errors.

Beta 10 corrects the separate legacy contract-v1 expired-automation form and
moves delta-aware safety-reducing policy beyond Beta 11. F3 begins only after
that separate milestone is accepted. Beta 9 changes no policy
classification, approval sequence, task ownership, provider, tool, resource,
or fallback boundary.

## 2.2.0-beta.10 legacy expired-automation compatibility

Beta 10 generates neutral fixtures through Beta 6's exact legacy `create_plan`
writer and expiration lifecycle. It recognizes only the source-established
contract-v1 prohibited/expired automation profile and its two complete event
sequences. It retains Beta 9 contract-v2 compatibility, partial listing, health
reconciliation, and byte-preserving reads. CI regenerates both historical
fixture families from the exact Beta 6 commit.

Delta-aware safety-reducing policy remains deferred beyond Beta 11. Beta 10 adds no
policy classification, approval sequence, task ownership, provider, tool,
resource, execution, recovery, or fallback authority.

## 2.2.0-beta.11 bounded recovery and exact upstream compatibility

Beta 11 bounds stale restart reconciliation without adding execution authority,
then adds one independently reviewed exact 8.0.0 upstream profile alongside
7.14.2. It does not begin delta-aware policy or F3. The two new 8.0.0 reads are
held for a subsequent production-canary decision and unknown 8.x releases
remain fail-closed.

## 2.2.0-beta.12 exact add-on runtime admission

Beta 12 corrects only the exact 8.0.0 standalone/add-on runtime-evidence
boundary. It validates then normalizes the documented dynamic upstream policy
state while retaining exact ordinary contracts and strict raw evidence.
Unknown 8.x, both held reads, protocol, fallback, provider and governance
boundaries remain unchanged. Delta-aware safety-reducing policy remains
deferred beyond Beta 12.

## 2.2.0-beta.13 dependency security remediation

Beta 13 updates only the Engineering `aiohttp` and `cryptography` pins to
stable security-fixed releases and retains strict dependency auditing. It
changes no provider, admission, governance, protocol, held-tool, dispatch, or
fallback boundary.

## 2.2.0-beta.14 exact special-provider runtime admission

Beta 14 retains the Beta 12 automatic-read model and Beta 13 security baseline
while correcting the separate dashboard, backup, and lifecycle paths. Backup
and lifecycle validate the complete exact release through the shared
model-aware per-tool catalog validator; Dashboard v3 reuses the reviewed
bounded policy projection while retaining its exact argument surface. Raw
fingerprints remain diagnostics, typed dashboard failures remain precise, and
an immutable add-on-runtime CI lane proves planning without dispatch. Unknown
8.x, held reads, protocol, authorization, fallback, governance, and stable-v1
boundaries do not change.

## 2.2.0-beta.15 live lifecycle response compatibility

Beta 15 preserves Beta 14 provider admission while correcting the exact
8.0.0 add-on inventory/detail response projection used by lifecycle identity
binding. Exact 7.14.2 and 8.0.0 accounting, held reads, dashboard reads,
backup, approval, protocol, fallback, security pins, and stable-v1 boundaries
remain unchanged.

## F3-0 contract freeze and F3 delivery

F3-0 freezes the operation-adapter lifecycle, lock/dispatch/recovery contract,
and the required governed update of one existing storage-mode dashboard. It is
a non-behavioral planning boundary: Engineering remains `2.2.0-beta.15`, task
schema 1 and 48 local tools remain unchanged, and no adapter is migrated.

The F3 contract documents are:

- [current-state inventory](F3_CURRENT_STATE_INVENTORY.md);
- [adapter and lock ADR](architecture/ADR-013-F3-OPERATION-ADAPTER-AND-LOCK-CONTRACT.md);
- [dashboard-write contract](F3_DASHBOARD_WRITE_CONTRACT.md);
- [parallel-development plan](F3_PARALLEL_DEVELOPMENT_PLAN.md); and
- [completion acceptance](F3_COMPLETION_ACCEPTANCE.md).

The required dashboard boundary is an exact existing storage-mode dashboard,
bounded declarative patch, external administrator approval,
`dashboard:<url_path>` lock, stale-hash rejection, durable intent, one setter
invocation, exact reread, and verified terminal outcome. Dashboard deletion,
resources, preferences, screenshots, arbitrary Python, service calls, and
cross-dashboard transactions are excluded.

The reviewed upstream setter currently performs hash validation and save as
separate operations. F3-B is therefore blocked from enabling writes until it
proves atomic compare/save or exclusion covering all dashboard writers (or a
separately authorized residual-risk decision revises the completion contract).
Readback alone cannot detect an external edit overwritten inside that window.

Delivery order is F3-0, then F3-A adapter/lock core, then independently
reviewable F3-B dashboard writes plus F3-C1/C2 adapter conformance, then F3-D
recovery/acceptance, followed by final integration/release. B, C1, C2, and D
may develop in parallel against the accepted contract, but merge dependencies
remain explicit. F4 retains graph execution and generalized compensation.

Each operation must remain operation-specific. This roadmap does not authorize
a generic administrator, arbitrary Supervisor command, service-call shortcut,
fallback, restore, or deletion.
