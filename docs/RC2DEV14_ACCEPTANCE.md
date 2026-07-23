# RC2dev14 pre-deployment acceptance contract

Version: `2.0.0-rc2-dev14`
Status: development-candidate verification procedure only; not published,
deployed, or accepted

This document does not declare a release, record an acceptance pass, authorize
publication, authorize deployment, or authorize access to a live Home Assistant
system. Run any deployed step only after separate authorization tied to an exact
candidate identity and exact environment.

## Repository and contract gates

1. Review the complete diff from the exact base.
2. Confirm stable v1.1.2 is unchanged.
3. Confirm no raw `ha_config_set_*` tool, arbitrary Home Assistant writer,
   arbitrary upstream dispatch, write fallback, deletion, reload, restart, or
   physical-action tool became reachable.
4. Confirm the generic upstream gateway still admits reviewed reads only.
5. Confirm `create_configuration_plan` is additive and its operation list is
   bounded to one through eight objects.
6. Confirm the nested public schema requires `operation_id`, `resource_type`,
   `action`, `target_id`, and `proposed_config`; constrains resource and action
   enums; exposes optional bounded `depends_on` and helper type; and forbids
   extra operation fields.
7. Snapshot and compare the six pre-existing governance tool schemas. They must
   remain byte-for-byte equivalent.
8. Confirm contract-v1 persisted automation plans remain readable without
   migration, rehashing, approval transfer, or lifecycle rewriting.
9. Run focused configuration-plan, resource-adapter, Approval-tab, routing,
   registration, negative-reachability, persistence, audit, and schema tests.
10. Run the complete unittest, compilation, metadata, YAML, dependency,
    secret-scan, PowerShell, protected-path, whitespace, and Evidence gates from
    a clean committed head.

## Deterministic ordered-plan acceptance

Use an offline fake or disposable Home Assistant environment. Do not inject a
failure into the deployed household system.

1. Capture exact initial states for one helper, one script, and one automation
   target.
2. Create a three-step plan ordered helper, script, automation. The script
   depends on the helper operation; the automation depends on the script
   operation.
3. Confirm planning creates no write, service call, reload, restart, or generic
   upstream dispatch.
4. Confirm the plan hash changes when operation order, dependencies, target,
   action, helper type, proposed content, normalization version, or risk
   changes.
5. Request approval twice. Require one idempotent external challenge and zero
   writes. Apply before the administrator decision and require
   `external_approval_required` with zero writes.
6. Inspect the Approval tab. It must show all ordered operation IDs,
   dependencies, typed targets, per-step risks, bounded diffs, ordered trigger,
   condition, service/action, target, and key primitive-data semantics, and
   warnings without full configurations. Two same-length action or trigger
   lists with different consequential content must render differently. An
   incomplete or over-bound semantic projection must not be approvable. The
   page must state that the plan is non-atomic, stops on first failure, has no
   automatic or batch rollback, and requires a newly inspected and approved
   plan for remediation.
7. Approve the exact aggregate hash once through Home Assistant Ingress.
8. Apply it and require deterministic write/readback order:
   helper, script, automation.
9. Require exact identity and normalized desired-versus-actual fingerprints
   after every write, one valid final Home Assistant configuration check, one
   consumed approval, complete per-operation receipts, and overall `applied`.
10. Repeat apply and require `already_applied` with zero additional writes.
11. Run `configuration_integrity_analysis` after apply. Require rebuilt
    dependency evidence and honest coverage limitations. Do not convert partial
    global source coverage into a complete claim.
12. Reconcile plan events, bounded audit, and health. Every attempted operation
    must be attributable, while proposed/current full configurations, secrets,
    credentials, approval notes, CSRF material, and authenticated URLs remain
    absent.

## Failure and ambiguity gates

1. Make any target stale after planning but before apply. All targets are
   re-read under locks; require `stale_target_state`, zero approval consumption,
   and zero writes.
2. Change a later target from an independent Home Assistant client after the
   all-target preflight but before its individual operation. Require the target
   to be re-read, no write to that stale target, truthful retention of earlier
   verified work, a final configuration check, and an overall partial result.
   Repeat with a planned no-op and require that it is not falsely marked
   verified. Record that Home Assistant supplies no compare-and-swap write and
   a narrower read-to-write race therefore remains.
3. Refuse an unsupported resource, action, helper type, duplicate target,
   duplicate operation ID, forward or unknown dependency, unknown operation
   field, malformed target identity, prohibited sensitive field, and high-risk
   executable content before any write.
4. For helper create, require the target object ID to match the conservative
   deterministic slug of `name`. Reject a mismatch with no transport I/O.
   Detect storage and entity-state namespace collisions before create. Simulate
   a returned `_2` ID and require the exact unexpected ID plus `orphan_risk` in
   the partial receipt, no later operation, and no automatic cleanup.
5. Fail the script write after a helper change verifies. Require the helper
   receipt to remain applied-and-verified, the script receipt to be failed, the
   automation receipt to be not attempted, the overall outcome to be
   `partial_failure`, and the approval to remain consumed.
6. Return a successful write with mismatched readback. Require
   verification-failed status, a bounded mismatch, no later operation, and no
   success claim.
7. Raise an ambiguous write response. Require exactly one bounded readback. If
   it proves desired state, record
   `state_proven_desired_after_ambiguous_write` for the step but still stop and
   report aggregate `partial_failure`. If state is not proven, report it as
   unconfirmed. Never continue or reuse approval.
8. Return `{}`, `null`, a scalar, a missing `errors` field, or any result other
   than exact `{"result": "valid", "errors": null}` from the final Home
   Assistant configuration check. Require overall verification failure even
   when all per-step readbacks matched.
9. Advance the clock past expiry after a contract-v2 verification failure.
   Require the failure status and partial receipts to remain terminal facts,
   not be relabeled expired.
10. Interrupt the process during apply in a disposable environment. On restart,
   require persisted receipts, explicit failed/partial state, no automatic
   continuation, and no renewed authority.
11. Confirm no automatic or batch rollback is offered. Any remediation begins
   with fresh read-only inspection and a new exact plan plus a new external
   approval.

## Separately authorized real-work acceptance

Use the pending HVAC request only after the user provides its current exact
desired state and separately authorizes the deployed acceptance procedure.
Historical references such as `climate.mudroom_thermostat`, automation
`1782920111688`, or `input_text.ha_stale_alert_signature` are old read-only
fixtures, not current targets or approval.

1. Discover the actual helper, script, automation, references, and current
   configurations through read-only tools.
2. Stop if the request requires an unsupported helper type, delete, rename,
   enable/disable, backup, reload, restart, add-on action, dashboard change,
   registry change, integration option, or physical behavioral test.
3. Present the exact ordered plan, aggregate risk, diffs, limitations,
   non-atomic behavior, and lack of batch rollback.
4. The user performs the one consequential approval in the existing Home
   Assistant Approval tab.
5. Apply, require exact per-step readback and valid configuration check, then
   run configuration-integrity analysis and report every result.
6. Do not force HVAC actuation to prove configuration behavior. Any physical
   observation or action requires a separate explicit authorization.

Acceptance passes only when the AI can inspect, plan, request one external
approval, apply in order, verify, and report the supported real change without
manual configuration editing. Any unsupported required operation, hidden write,
fallback, missing readback, invalid configuration check, ambiguous completeness,
or manual Engineering restart fails the procedure.
