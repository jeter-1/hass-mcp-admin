# Beta automation change governance

## 2.2.0-beta.14 preservation boundary

The special-provider runtime-admission correction changes no plan, approval,
task, policy, persistence, apply, or dispatch semantics. Valid backup and
lifecycle probes may again reach the existing proposal-only persistence
boundary, but planning cannot dispatch. Invalid catalogs and provider-tool
contracts still fail before persistence. Authority version 3, task schema 1,
same-administrator enforcement, one-task ownership, no-blind-redispatch, and
zero fallback remain unchanged.

## 2.2.0-beta.13 preservation boundary

The dependency-security release changes no plan, approval, execution-task,
provider, operation, persistence, recovery, or policy semantics. Authority
version 3, task schema 1, historical-plan compatibility, one-task ownership,
no-blind-redispatch, and zero fallback remain unchanged.

## 2.2.0-beta.12 preservation boundary

The exact 8.0.0 runtime-admission correction changes no plan, approval,
execution-task, provider, operation, persistence, recovery, or policy
semantics. Authority version 3, task schema 1, historical-plan compatibility,
one-task ownership and no-blind-redispatch remain unchanged.

## 2.2.0-beta.11 restart recovery boundary

Restart reconciliation retains the original authority-v3 consumption and
dispatch evidence and can only gather bounded readback evidence before the
persisted deadline. It does not request authority, create a second task, extend
the verification window, or redispatch. Expired or permanently ineligible
restart work terminalizes without provider access; unrelated plans and tasks
retain their existing governance behavior.

## 2.2.0-beta.10 legacy expired-automation compatibility

Beta 10 separately recognizes the exact contract-v1 prohibited/expired
`update_automation` form produced by Beta 6's legacy `create_plan` path. It
requires an intact authority-v3 prohibited policy snapshot, empty operations,
expired/invalidated legacy state, no authority or execution evidence, and one
of the two complete source-generated event sequences. Expiry is not accepted
by the generic event profile.

The detail, inventory, health, Ingress, startup, and handoff paths project a
matching record as terminal and non-actionable without modifying persisted
bytes. All contract-v2 predicates, projection-failure containment, health
reconciliation, and task-store failure propagation remain unchanged. See
[`V2_2_0_BETA10_ACCEPTANCE.md`](V2_2_0_BETA10_ACCEPTANCE.md).

## 2.2.0-beta.9 real Beta 6 prohibited-plan compatibility

Beta 9 recognizes the exact contract-v2 prohibited-plan form written by Beta 6
after same-target supersession, then projects it as terminal/non-actionable
across compatibility status, Ingress, handoff, and health without rewriting
storage. Exact historical-code fixtures replace the incomplete manual fixture
whose missing `contract_version` defaulted to 1. Prepared operations remain
non-execution evidence; any task, dispatch, response, apply, verification,
rollback, operation-state, event, or authority contradiction fails closed.

Inventory returns other valid plans alongside bounded per-record projection
failures, while systemic storage failure remains fatal. Health assigns every
loaded record to a policy bucket and surfaces an explicit projection warning.
Beta 7 response truthfulness remains unchanged. This changes no plan/policy
hash, classification, approval sequence, task schema, provider route, or
fallback. See
[`V2_2_0_BETA9_ACCEPTANCE.md`](V2_2_0_BETA9_ACCEPTANCE.md) for the historical
contract-v2 boundary.

## 2.2.0-beta.6 F2 policy and approval authority

Every new governed plan contains one deterministic server-derived `f2-v1`
policy snapshot. Risk delta (`none` through `critical`) and physical
consequence (`none`, `indirect`, `direct`, or `safety_critical`) remain
independent. The resulting class is `standard_admin`, `elevated_admin`, or
`prohibited`; callers cannot select or lower it.

Authority version 3 requires one `plan_approval` for standard plans. Elevated
plans require plan approval followed by a separate
`elevated_risk_acknowledgement` from the same authenticated Home Assistant
administrator. This is not two-person control. Prohibited plans cannot create
actionable approval, an execution task, or provider dispatch.

The complete policy snapshot is bound into the immutable plan hash and is
recomputed before approval and dispatch. Approval consumption occurs only
after the durable F1 task owns the exact plan. Task schema version 1,
no-blind-redispatch, readback-only reconciliation, operation-specific
verification, provider routing, and zero fallback remain unchanged. See
ADR-012 in the architecture decision records.

## 2.1A Beta 2 operational plans

Four contract-v3 operational proposal types now share the same immutable
hash-bound external approval and apply authority: full backup, controlled
reload, exact add-on restart, and Home Assistant restart. Proposal creation
performs reads and persists governance state but dispatches no action. Approval
is granted only by the separate administrator Ingress principal.

Each apply revalidates the exact target and reviewed provider, commits dispatch
intent and approval consumption before the fixed action, and permits one
dispatch. Provider response loss or server termination can resume readback
only. Contract-v3 output names `operational.verification` as authoritative and
does not publish the unrelated configuration verification field.

For Home Assistant restart, `operational.verification.evidence` is cumulative:
a qualified post-dispatch Core outage is retained through later successful
reads and process restarts. The earliest outage timestamp never advances,
the latest timestamp and bounded count may advance, and reconnection evidence
is added without replacing outage evidence. Confirmed dispatch without a
qualified outage remains verification-pending. Reconciliation never restores
write authority or redispatches.

The initial active-probe budget is approximately 15 seconds, while new outage
evidence has an independent immutable 180-second interval derived from the
original persisted dispatch timestamp. Reconciliation cannot extend it, while
recovery may finish indefinitely later if a qualifying outage was already
recorded. A Core outage at `T+60s` can therefore qualify during later
readback, but an outage after `T+180s` cannot verify the old plan. The same
complete-evidence predicate validates current and historical records; an
incomplete raw outage flag is never authority. Reconnection is retained only
with the explicit timestamp from a successful post-outage Core identity read.

Beta 6 supersedes the earlier blanket high-risk rejection only for an already
supported, normalized configuration operation that policy can classify as
`elevated_admin`. Critical, safety-critical, unsupported, destructive,
arbitrary, or unclassifiable operations remain prohibited. This does not add a
resource type, arbitrary service, add-on operation, restore, deletion, or
fallback.

## RC2 governance freeze

Version `2.0.0-rc.2` preserves the accepted Beta 26 lifecycle byte-for-byte at
the public contract boundary. Persisted Beta 26 records load without migration,
terminal history is not rewritten, hashes and authority versions are preserved,
expired challenges are not actionable, and repeated reads remain idempotent.
Clean initialization creates no plans, challenges, or audit event. Authority
version 2 external approval remains the only executable trust path.

## Dev14 pre-deployment configuration-plan contract

Dev14 is a development scope, not a release declaration, deployment record, or
acceptance result. Repository release metadata remains authoritative. The
additive `create_configuration_plan` tool extends the existing external-approval
lifecycle to a deliberately narrow set of practical configuration operations:

- create or update an automation;
- create or update a script;
- create or update an `input_boolean` helper; and
- create or update an `input_number` helper.

It accepts one to eight ordered operation objects. Each object requires
`operation_id`, `resource_type`, `action`, `target_id`, and `proposed_config`.
`resource_type` is exactly `automation`, `script`, or `helper`; `action` is
exactly `create` or `update`. Helper operations additionally identify
`helper_type` as `input_boolean` or `input_number`. Optional `depends_on`
identifiers may reference only unique earlier operations. Unknown operation
fields, duplicate targets, forward dependencies, unsupported resource types,
and unsupported actions fail before an approvable plan is created.

Home Assistant helper-create commands generate an object ID from `name`; they
do not accept the desired ID. A helper create therefore requires a conservative
ASCII name whose deterministic slug exactly matches the approved full entity
ID. Apply checks both the storage collection and the entity-state namespace
immediately before creation. A known collision stops without a create call.
If Home Assistant nevertheless returns a suffixed ID because of a narrow
external race, apply stops, reports that exact unexpected ID and `orphan_risk`
in the partial receipt, and never attempts automatic deletion.

The older six governance tools retain their existing public schemas. Historical
single-automation records retain their original plan/hash contract and are not
silently migrated. A new ordered plan uses contract version 2 and binds the
complete ordered operation list, dependencies, typed targets, current-state
fingerprints, proposed hashes, normalization versions, risk, expiry, and
approval authority into one immutable plan hash.

Planning performs reads, validation, normalization, diffs, risk assessment,
and F2 policy only. `approve_change_plan` requests the first exact-hash
external action and never grants authority through MCP. Standard plans require
one action; elevated plans require the ordered same-administrator action pair.
The Home Assistant administrator sees the
complete bounded, configuration-free operation projection in the Approval tab.
For scripts and automations, that projection includes ordered trigger,
condition, service/action, explicit target, and key primitive-data semantics.
An incomplete or over-bound semantic projection cannot be approved.
The review explicitly states that execution is non-atomic, stops on the first
failure, and has no automatic or batch rollback.

Before the first write, apply locks and re-reads every typed target. Any stale
target or unavailable resource provider stops before approval consumption and
with zero writes. After those checks, the complete authority-v3 bundle is
consumed once and
operations execute in order. Because Home Assistant and its UI do not share the
Engineering process locks, each target is re-read again immediately before its
own operation; a later stale target stops without overwriting it and preserves
truthful receipts for earlier changes. Home Assistant does not provide a
compare-and-swap configuration write, so a narrow read-to-write race remains
and is reported as an ambiguous partial result rather than hidden. Every
attempted change receives exact identity and normalized readback verification.
A final Home Assistant configuration check is required and succeeds only on
the explicit response `{"result": "valid", "errors": null}`; missing or
malformed evidence fails closed. Successful writes invalidate the dependency
index so a subsequent `configuration_integrity_analysis` rebuilds current
evidence.

An ambiguous write response never permits the next operation to run. One
bounded readback records whether the desired state is proven. The overall
result remains `partial_failure`, later operations are `not_attempted`, and the
approval cannot be reused. Ordinary write or verification failure follows the
same stop-on-first-failure rule. Per-operation receipts distinguish attempted,
verified, failed, and not-attempted work without returning full configurations.
Remediation requires fresh inspection plus a new exact plan and external
approval.

The runtime uses a fixed internal `ConfigurationResourceGateway` with exact
automation, script, and helper methods. It does not register
`ha_config_set_automation`, `ha_config_set_script`, `ha_config_set_helper`, a raw
Home Assistant writer, arbitrary upstream tool names or arguments, or a direct
fallback. The generic upstream gateway remains read-only.

Dev14 does not add deletion, rename, enable/disable, backup, reload, restart,
add-on administration, dashboard mutation, registry mutation, integration
administration, or physical-action testing. Those remain unsupported rather
than becoming manual or ungoverned follow-up work. Batch rollback is
unavailable. A partial result must be inspected and remediated through a new
approved plan.

## Beta 22 handoff lifecycle interpretation

Change handoffs read persisted plan state without changing it. Proposed,
awaiting-approval, approved, and applying plans remain pending. `applied` counts
as completed only when required verification is `passed`. Verification failure is
failed/blocked; rolled-back work is labeled rolled back rather than active
completion. Expired, superseded, rolled-back, and terminal validation-only plans
are historical facts, not current pending work, blockers, or authorization needs.
Only active pending states can produce `change_pending`; only current unresolved
failures or requirements can block a handoff. Full proposed configuration,
unbounded diffs, secrets, authentication,
and prior approval as reusable authority are excluded.

Version 2.0.0-rc.2 requires external Home Assistant administrator approval for controlled
Home Assistant automation creation and updates. It did not alter the then-separate
v1.1.2 server, whose retained source is now historical and operationally retired.
It does not govern scripts, scenes, dashboards, helpers, integrations, devices,
add-ons, system configuration, arbitrary direct service calls, or automation
deletion.

## Architecture

The six governance tools are registered directly with the beta FastMCP server:

- `create_change_plan`
- `get_change_plan`
- `list_change_plans`
- `approve_change_plan`
- `apply_change_plan`
- `rollback_change`

They use separate domain-model, normalization, risk, storage, and lifecycle
modules under `ha_mcp_engineering/governance`. Home Assistant access passes
through a narrow automation gateway, which tests replace with an isolated fake.
The original 25 compatibility tool schemas are unchanged. The transitional
`upsert_automation` tool remains registered for connector compatibility but
fails closed with `governance_required` before provider or Home Assistant work.
Automation writes use only the governed plan, approval, apply, verification,
and rollback lifecycle.

## Lifecycle and status transitions

```text
draft -> validation_failed
      -> awaiting_approval -> external_pending -> approved -> applying -> applied
                            |                  |            -> verification_failed
                            |                  |            -> failed
                            |                  -> rejected
                            -> expired / superseded

applied or verification_failed
  -> rollback_pending -> external_pending -> separately approved -> rolled_back
                                           |                    -> rollback_failed
                                           -> rejected / expired
```

Persisted statuses are `draft`, `validation_failed`, `awaiting_approval`,
`approved`, `applying`, `applied`, `verification_failed`, `failed`,
`rollback_pending`, `rolled_back`, `rollback_failed`, `expired`, and
`superseded`, plus terminal `rejected`. Approval states are `required`,
`external_pending`, `approved`, `rejected`, `consumed`, `expired`, and
`invalidated`. A newer plan for the same automation supersedes an older pending
plan so two proposals cannot silently overwrite one another.

RC2dev5 responses make `approval_lifecycle` the authoritative approval-state
field. The older persisted `status` value remains available and is marked with
`status_is_legacy: true` plus
`authoritative_lifecycle_field: approval_lifecycle`. This preserves RC2dev3 and
RC2dev4 records without a storage migration. Callers must not infer an external
challenge from legacy `status: awaiting_approval`; only
`approval_pending_external` means a challenge exists.

`status_is_legacy` describes the status value returned in that response. It is
not a record-age marker and does not mean that a record predates F2. Pre-F2
plans without an immutable policy snapshot intentionally retain their bounded
status-based actionability rules for backward compatibility; the F2 prohibited
compatibility projection does not infer policy for those records.

The Beta 7 compatibility projection treats a validated F2 `prohibited` policy
decision and `prohibited` approval bundle as terminal, visible, and
non-actionable. Public plan detail, inventory, and handoff evidence project
`status: prohibited` and `approval.state: prohibited`; pending approval filters,
Ingress queues, and health counters exclude it. The authority-v3 persisted enum
fields remain unchanged for schema compatibility, and historical authority-v1
and authority-v2 records retain their existing projection.

Read compatibility also recognizes the source-established Beta 6 shape in
which same-target supersession changed only a validated prohibited plan's
legacy fields to `status: superseded`, `approval.state: invalidated`, and
`approval.bundle_state: invalidated`. Recognition requires the validated
prohibited policy snapshot, empty acknowledgements, no authority, task,
provider, apply, verification, or rollback evidence, and the bounded
supersession event. It is an in-memory projection: no plan, event history,
hash, challenge, or timestamp is rewritten. Contradictory records continue to
fail closed.

## Planning, normalization, and fingerprints

`create_change_plan` accepts only `create_automation` and `update_automation`.
It validates the target ID and basic automation structure, reads existing state
for updates, and performs no write. Dictionary ordering is normalized;
behaviorally significant list ordering is preserved. Top-level automation `id`
is identity metadata and is removed before canonicalization, state fingerprints,
proposed-config hashes, plan hashes, and behavioral mismatch comparison. Identity
is checked separately against the requested target and any proposed or returned
ID. Empty optional conditions,
variables, and trace settings are treated consistently, while unknown fields are
retained. Structured diffs identify known top-level fields and summarize other
fields without dumping unbounded content.

The current-state fingerprint and proposed-config hash are SHA-256 hashes of
canonical JSON. They do not include the access secret. The approval-bound plan
hash also covers the plan ID and version, operation, target, expiry, risk,
current-state fingerprint, proposed content, approval kind, and approval
authority version. A material plan
mutation changes the hash and invalidates approval.

An update whose normalized current and proposed configurations are equal returns
`no_change`, creates no approvable plan, and cannot cause a meaningless write.

Beta 24 increments the normalization version. Hashes created with Beta 23 rules
are not silently rewritten or accepted. **Re-create any pending or approved
automation change plans after upgrading to Beta 24.** Terminal historical plans
remain readable; approval or apply of an incompatible record fails closed with
the existing hash-mismatch contract.

## Risk model

Risk classification is deterministic:

- **Low:** alias or description-only changes and other non-behavioral metadata.
- **Medium:** new automations, trigger/condition/time-window changes, mode or
  maximum-run changes, notification recipient behavior, HVAC/lighting changes,
  and non-critical physical actions.
- **High:** structured lock, garage-cover, alarm, valve/water-shutoff, host/core,
  broad-target, destructive, or immediate safety-sensitive actions.

Risk is based on actionable structure: action service/domain, target entity/device/area,
entity domain, trigger/condition type, and structured blueprint inputs. Alias,
description, event text, log messages, notification text, approval notes, caller context,
and non-actionable template literals cannot independently make a plan high risk.
Unresolved dynamic service or target construction is conservatively medium with a
warning and structured evidence. Evidence identifies the triggering field and category
without echoing complete target identifiers or secrets.

Low and moderate configuration changes without direct physical consequence use
`standard_admin`. An already-supported high-risk or direct-consequence
configuration change uses `elevated_admin`. Critical, safety-critical,
unsupported, destructive, unknown, or policy-evasive changes are
`prohibited`. Caller text or an approval note cannot lower calculated policy.
Planning and configuration apply store a future automation action; they do not
trigger it.

The fixed reviewed service set `lock.unlock` and
`alarm_control_panel.alarm_disarm` is safety-critical from the service name
alone. Entity, device, area, data-based, broad, templated, unresolved, mixed,
or omitted targets cannot downgrade those services. Target analysis never
resolves a device or area identifier into an assumption of safety. Traversal
is limited to reviewed automation action positions; conditions are not scanned
as executable actions, and unsupported action nesting fails closed. Other
high-risk service names retain their existing policy mapping.

Governed configuration reads, writes, verification, and rollback are
`direct_ha_required` facilitator capabilities. They do not route through ordinary
service execution or fall back to an unverified write. See ADR-002 for provider rules.

## External approval and expiration

Approval authority version 3 is external to MCP. The client must pass the exact
`plan_hash` returned by planning or rollback request to
`approve_change_plan`, but that call only creates or returns a 60-minute bounded
external review challenge and reports `approval_pending`. It never marks the
plan approved. Repeated requests are idempotent and do not extend an active
challenge. `approval_note` is untrusted request context, not human approval.

An authenticated Home Assistant administrator reviews the bounded escaped plan
through the admin-only Ingress panel on internal port `8110`. Approval or
rejection is POST-only, protected by a one-time CSRF nonce, and revalidates the
exact persisted plan/version/hash/policy/action/kind/target/operation/risk.
Approval records the honest Ingress principal and principal-separation flag. A
standard plan becomes fully approved after that action. An elevated plan then
creates a separate acknowledgement challenge that only the same administrator
may complete. It is single-use.
Rejection is terminal. A plan defaults to a 120-minute expiry; clients may
request 5 to 1,440 minutes. Neither a plan nor a challenge can be approved after
expiry.

Beta 26 resolves plan and external-challenge expiry through one lifecycle path.
`expired` is terminal: after the first transition, plan/list/health/Ingress/
handoff reads do not save the record, update `updated_at`, or duplicate events,
audit entries, or structured logs. Challenge expiry is reflected before public
projection, so dead challenges are not returned as actionable, are excluded
from health pending counts and the Ingress inbox, and fail closed in apply or
rollback. A still-eligible plan may request a fresh challenge bounded by its
own expiry; the replaced challenge remains unusable.

The MCP access secret does not authorize the approval listener, and approval
routes are absent from port `8100`. See
[`EXTERNAL_APPROVAL.md`](EXTERNAL_APPROVAL.md) for the complete boundary.

## Apply, verification, and concurrency

`apply_change_plan` rechecks expiry, immutable plan and policy hashes, current
policy recomputation, authority version, external channel/principal and
separation flag, required action sequence, same-administrator evidence,
approval use and kind, and the live current-state fingerprint. It reserves the
single durable F1 task before consumption, obtains the target lock, captures
the pre-change evidence, consumes the complete bundle, writes through Home
Assistant's configuration endpoint, and reads the stored resource back.

Verification requires target existence, an explicitly matching automation ID
when Home Assistant returns one, normalized desired-versus-read-back behavioral
equivalence, Home Assistant configuration validation, and recorded duration and
mismatch fields. Home Assistant may canonicalize a reviewed automation action
step from `service` to `action`; the post-write verifier treats only that
schema-positioned alias as equivalent. Raw, binding, and verification-normalized
fingerprints remain distinct bounded evidence. Targets, data, ordering,
triggers, conditions, and other behaviorally meaningful differences remain
mismatches under the established optional-empty rules; unsupported or
ambiguous structures fail closed.
Each action step must match exactly one reviewed family. Identical unknown
mappings are not evidence of semantic equality; extra fields on service or
device actions and malformed choose, repeat, parallel, or if/then/else shapes
are rejected. The verifier retains a bounded
`unsupported_automation_action_family` mismatch without serializing the
unreviewed mapping. Service/action aliasing remains limited to recognized
simple call steps in action positions.
This verifier does not change plan or policy hashes, stale-state fingerprints,
or the configuration dispatched to Home Assistant, and it is not a general
YAML-equivalence engine. A successful HTTP write with a real behavioral
verification mismatch preserves the provider response, produces a post-dispatch
verification failure, and never reports a successful governed change.

Home Assistant commonly injects the correct top-level `id` into stored readback;
that does not create an `other:id` behavioral mismatch. A different ID produces
the explicit `automation_id` mismatch. If desired state is already present after a completed apply, a duplicate request
returns `already_applied` without another write. Approval is consumed before the
write so an ambiguous upstream failure cannot be retried as an unrestricted
duplicate. Per-plan and per-target locks prevent concurrent duplicate writes.
Current-state fingerprints reject stale plans. On restart, an abandoned
`applying` record is marked failed and requires a new plan.

## Rollback

Rollback is available only for governed updates with a pre-change snapshot.
The first `rollback_change(plan_id)` call creates `rollback_pending` state and a
new plan hash. The client requests review of that exact rollback hash with
`approve_change_plan`; a human separately approves kind `rollback` in Ingress;
then the client calls `rollback_change` again with the hash. Apply authority
never authorizes rollback.

Before restoring the exact snapshot, rollback verifies that live state still
matches the post-apply fingerprint. It writes, reads back, compares normalized
configuration, and runs configuration validation. External changes cause
`stale_target_state` instead of being overwritten. Rollback of a newly created
automation would require deletion, which is explicitly excluded; create
rollback returns `rollback_not_available`. No automatic rollback occurs.

## Persistence, retention, and recovery

Plans are stored only in beta add-on data, by default under
`/data/governance/change_plans`. Each uses a random 128-bit ID and an atomic
write-and-replace operation. Records survive restarts. Terminal records are
retained for 90 days by default. Invalid or corrupt records are quarantined and
counted in health output rather than preventing startup.

Proposals containing access-secret, token, authorization, cookie, password,
API-key, webhook-ID, or authenticated MCP URL fields are rejected before
persistence. Caller context is bounded to safe scalar metadata. IDs and hashes
never use the secret.

## Audit, health, and stable errors

Lifecycle events include `change_plan_created`,
`change_plan_validation_failed`,
`change_plan_expired`, `change_apply_started`, `change_apply_rejected`,
`change_apply_succeeded`, `change_apply_failed`,
`change_verification_failed`, `rollback_requested`, `rollback_started`,
`rollback_succeeded`, and `rollback_failed`, plus
`external_approval_requested`, optional `external_approval_viewed`,
`external_approval_granted`, `external_approval_rejected`,
`external_approval_expired`, `external_approval_invalidated`, and
`external_approval_consumed`.

Events contain only request ID, plan ID, target type/ID, operation, risk,
result status, stable error code, duration, caller ID, approval state, bounded
policy class, risk delta, physical consequence, policy version, approval
action, and same-principal requirement/result. The
gateway excludes proposed configs, caller context, approval notes, and hashes
from generic audit parameters. `get_server_health` returns bounded governance
counts and storage status without plan content.

Stable error codes are:

```text
change_plan_not_found
change_plan_expired
change_plan_not_approved
change_plan_rejected
approval_hash_mismatch
approval_already_consumed
external_approval_required
approval_authority_mismatch
external_approval_invalid
external_approval_expired
policy_snapshot_required
policy_snapshot_mismatch
prohibited_change
elevated_risk_acknowledgement_required
approval_principal_mismatch
approval_sequence_failure
stale_target_state
change_in_progress
unsupported_change_operation
high_risk_change_rejected
automation_validation_failed
automation_apply_failed
automation_verification_failed
rollback_not_available
rollback_approval_required
rollback_failed
change_plan_storage_error
```

An absent record, including a lookup string that is not a generated plan ID,
returns `change_plan_not_found` and is non-retryable. It does not degrade storage
health. `change_plan_storage_error` is reserved for real read/write,
serialization, corruption, permission, or atomic-replacement failures.

For `create_automation`, a Home Assistant 404 during the ID-availability probe
is expected and does not set request failure telemetry. An existing ID returns
`configuration_conflict`; upstream 4xx/5xx or malformed success responses remain
real Home Assistant failures.

## MCP client example

These examples use generic IDs and no credentials or private entity names.

1. Call `create_change_plan`:

```json
{
  "title": "Adjust example notification",
  "description": "Change safe notification text",
  "operation": "update_automation",
  "automation_id": "example_notification",
  "proposed_config": {
    "alias": "Example notification",
    "trigger": [{"platform": "state", "entity_id": "binary_sensor.example_motion"}],
    "condition": [],
    "action": [{"service": "notify.example", "data": {"message": "Example activity detected"}}],
    "mode": "single"
  },
  "expiration_minutes": 60
}
```

2. Review the diff, risk, warnings, validation, expiry, fingerprints, and hash.
3. Call `approve_change_plan(plan_id, expected_plan_hash)` with the exact hash;
   confirm it reports `approval_pending_external`, not approved.
4. A Home Assistant administrator reviews and approves the exact plan through
   the Ingress panel.
5. Call `apply_change_plan(plan_id, expected_plan_hash)`.
6. Confirm `applied`, verification `passed`, and matching request IDs.
7. Call `rollback_change(plan_id)` to request rollback and review its new hash.
8. Request review of that hash, have the administrator approve the separate
   rollback in Ingress, call `rollback_change` with it, and confirm `rolled_back`.

Clients should always present diff, risk reasons, expiry, and exact hash before
external review. Create a new plan after rejection, expiry, stale-state rejection, ambiguous apply
failure, or external target changes.

## Beta 6 authority migration

New plans use approval authority version 3 and carry a validated F2 policy
snapshot. Authority-version-1 and authority-version-2 plans remain readable,
but active legacy plans cannot receive new approval, create a task, or apply.
They are not silently upgraded or rehashed; recreate active plans. Terminal
historical plans and tasks remain readable without fabricated F2 evidence.

## Historical Beta 25 migration

New plans use approval authority version 2. Beta 24 pending or MCP-approved
records use legacy authority version 1 (including a missing field) and cannot be
applied. They are not silently upgraded or rehashed; recreate active plans.
Terminal historical applied, rolled-back, expired, superseded and failed records
remain readable. Automation behavioral normalization remains version 2.

Successful governed apply and rollback now invalidate the process-local entity
dependency index so the next analysis rebuilds configuration evidence. This adds no
write and does not change the persisted governance-plan format.

RC2dev4 adds an unambiguous public approval lifecycle without rewriting the
stored RC2 plan status. A newly created valid plan is
`approval_not_requested`; only `approve_change_plan` creates an
`approval_pending_external` Ingress challenge. Chat authorization is not that
external approval. Principal separation is `not_evaluated` until a distinct
administrator exists. Approval is hash-bound and single-use; rollback requires
its own plan-version/hash challenge. `list_change_plans` is a bounded summary;
`get_change_plan` remains the authoritative full-detail retrieval path.
