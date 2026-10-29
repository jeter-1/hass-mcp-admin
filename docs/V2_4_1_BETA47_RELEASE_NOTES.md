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
- the `preflight_failed` lifecycle event and its add-on log line
  (`error_code` non-null);
- legacy F1 pre-dispatch refusals: `last_error.failure_information`.

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
- Every field is additive. Static tools, the advertised catalog and input
  schemas are unchanged.

## Diagnosis of the seven plans (inconclusive)

From source, the likely candidates are Home Assistant `check_config` and the
strict readback verifier, which does not run at plan creation. Without the
plan bodies neither is proven. The post-deploy retry names the check.

## Rollback

Reinstall 2.4.1-beta.46. The added fields are ignored by the older reader,
and no stored record changes shape.
