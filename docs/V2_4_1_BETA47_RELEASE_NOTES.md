# Engineering 2.4.1-beta.47 release notes

Engineering 2.4.1-beta.47 makes every pre-dispatch refusal name its reason
(HAMCP-249, HA Admin E021). It follows 2.4.1-beta.46 and includes it. It
adds no Home Assistant write, tool, input
schema, route, provider, Core command or signed registry change, and it does
not change what preflight accepts or refuses.

Acceptance: [`V2_4_1_BETA47_ACCEPTANCE.md`](V2_4_1_BETA47_ACCEPTANCE.md).

## Why

On 2026-10-28 seven owner-approved configuration plans (automation create
and update) ended `failed_pre_dispatch` with child outcome
`preflight_rejected`, dispatch count 0. Nothing was written, but each task
had `last_error: null`, no `failure_information`, a `preflight_failed`
lifecycle event without an error and an add-on log line with
`error_code: null`. Earlier delete, enable and dashboard tasks ended the same
way with `preflight_rejected` or `provider_unavailable_pre_dispatch`.

The executor already stored the adapter's diagnostic codes on the child's
durable `pre_dispatch_terminal` event. The codes were lost when the child was
projected onto the parent task, the lifecycle event, the add-on log and the
apply result.

## The change

**One reason object, everywhere.** A pre-dispatch refusal now carries
`pre_dispatch_refusal`, model `f3_pre_dispatch_reason` (schema 1):

- `error_code`: one closed-vocabulary code (the first present in a fixed
  priority order), or `unclassified_pre_dispatch_refusal` for an adapter
  code outside the vocabulary, or `unknown_legacy`;
- `check`: the preflight check that refused (for example
  `lock_acquisition`, `plan_expiry`, `home_assistant_config_check`,
  `readback_verifiability`, `target_state`, `dispatch_authority`);
- `detail`: fixed, actionable text for that check;
- `verifier_rule`: for `readback_verifiability`, the readback verifier's
  rule (`automation_verifier_<rule>`, from its fixed literal messages);
- the child id, operation id, ordinal, target type and id (bounded
  identifiers) and the stored diagnostic codes.

It is reported on:

- the F3 child view (`f3_children[*].pre_dispatch_reason`), derived from the
  child's durable terminal event codes;
- the parent task: `verification_summary.pre_dispatch_refusal`, and
  `last_error` = `{error_code, failure_category, failure_information}`;
- the `apply_change_plan` result, and the `DUPLICATE_APPLY_ATTEMPT` details
  of a repeat apply;
- the `preflight_failed` lifecycle event and its add-on log line and audit
  entry: non-null `error_code` and `check`, and `failure_information` (the
  reason's reviewed fields only);
- legacy F1 pre-dispatch refusals (apply and restart reconciliation):
  `last_error.failure_information`, the `preflight_failed` log line and
  audit entry as above, and the details of the first refusal error returned
  by `apply_change_plan` (its code, message and other details unchanged).

**A later child refused after an earlier child completed.** A multi-operation
F3 plan whose first operation was written and verified and whose next
operation was refused before its dispatch keeps its existing outcome:
`manual_review_required` (`f3_sequence_incomplete`), the completed child, the
consumed approval and every dispatch count are unchanged. The refused child's
reason is now reported on the child view, the parent's `last_error` and
`verification_summary`, the apply result and repeat-apply details, a new
`child_preflight_failed` parent event (no state change; it names the child's
operation, ordinal and target), and the add-on log and audit entries of that
event and of `manual_review_required`. A failure after a later child's
dispatch is not reported as a refusal.

**Where the reason is stored.**

- F3 child: derived on read from the child's persisted terminal diagnostic
  codes. No field is added to the child execution-record storage schema.
- F3 parent task: the reason object is stored in
  `verification_summary.pre_dispatch_refusal` and
  `last_error.failure_information` of the terminal event.
- Plan operation: `failure_information` stays unset on the F3 path.
- Legacy F1 task: stored in `last_error.failure_information`.
- Log and audit: the reason's reviewed fields, through the existing
  sanitizer; never Home Assistant payloads, exception messages or
  configuration content.

**Readback verifier detail.** A `configuration_not_verifiable` refusal also
stores the comparison's mismatch categories and the verifier rule, so a
single retry names the exact construct (for example a `wait_for_trigger`
given as a single mapping, which the verifier refuses).

**Legacy records.** Nothing is rewritten. A task refused before this release
is projected from its child's stored terminal event codes; with no codes it
reads `unknown_legacy`. Terminal recovery never adds a reason to a stored
task and keeps a persisted one verbatim.

## Not changed

- Preflight checks, approval binding, stale-state checks, locks,
  at-most-once dispatch and every refusal behave as before.
- The F3 execution-record schema and plan storage are not extended; the
  plan operation's `failure_information` stays unset on the F3 path.
- The parent state machine is unchanged: `child_preflight_failed` records a
  reason without a state transition, and a partial sequence still ends
  `manual_review_required`, never `failed_pre_dispatch`.
- Every field is additive. Static tools, the advertised catalog and input
  schemas are unchanged.

## Diagnosis of the seven plans (inconclusive)

From source, the likely candidates are Home Assistant `check_config` and the
strict readback verifier, which does not run at plan creation. Without the
plan bodies neither is proven. The post-deploy retry names the check.

## Rollback

Reinstall 2.4.1-beta.46. The added fields are ignored by the older reader,
and no stored record changes shape.
