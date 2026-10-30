# Engineering 2.4.1-beta.50 acceptance

This contract covers HAMCP-252: exact verification of a `sun` condition
used as an action step, and a located verifier refusal. It follows
2.4.1-beta.49 and includes it.

See also:
- Release notes:
  [`V2_4_1_BETA50_RELEASE_NOTES.md`](V2_4_1_BETA50_RELEASE_NOTES.md).
- Core authority:
  [`SUN_CONDITION_VERIFICATION.md`](SUN_CONDITION_VERIFICATION.md).

**Release target.** `2.4.1-beta.50` is the next free beta after
2.4.1-beta.49 (#329, HAMCP-198/120). This branch merged `main` at beta.49
and then materialized beta.50 with `scripts/promote_next_release.py --apply`;
`--validate-transition 2.4.1-beta.49 2.4.1-beta.50` passes. Nothing is
published or deployed. If another release reaches `main` first, this change
is restaged at the next free beta and these documents are renamed.

The contract separates four stages:
1. source and offline acceptance;
2. release CI;
3. publication;
4. installed Engineering acceptance.

It authorizes none of the later stages.

## 1. Source and offline acceptance

Bind:
- Josh's HAMCP-252 direction:
  - extend the verifier for the exact construct with exact comparison; never
    loosen it;
  - anything genuinely unsupported is still refused;
  - the refusal location is additive, bounded and value-free;
- `2.4.1-beta.50` materialized in all three version authorities, with exact
  document resolution and no staged declaration left over;
- the PR head SHA, its passing `validate` run, and the Evidence tier log
  named in the PR;
- the mutation evidence under `docs/evidence/hamcp252-mutations/`.

The review needs a security section covering:
- **No new reachability.** No Home Assistant write, service call, reload,
  provider, route, fallback or upstream dispatch. The only preflight change
  is admitting a `condition: sun` step's four sun fields, compared exactly.
- **Fail closed.** Sun values outside Core's schema, malformed offsets
  (`automation_verifier_invalid_automation_sun_condition_offset`), an offset
  without `after`/`before`, the sun fields on any other condition type, and
  unknown fields stay refused before dispatch. A changed, added or missing
  value on readback is a mismatch.
- **Bounded, sanitized output.** Location codes are built only from
  structural keys, indices and a fixed field vocabulary (anything else is
  `unrecognized_field`). They are at most 96 characters and seven codes per
  refusal, within the 16-code bound. Every verifier refusal carries a path; a
  whole-configuration refusal is located at the root pointer `""`. They are
  decoded only when fully valid, and never carry a value or template.

Required offline evidence:
- `tests/test_hamcp252_sun_condition_verification.py`;
- the existing verification, configuration execution and HAMCP-249 reason
  suites;
- the complete repository suite in the Evidence tier.

## 2. Release CI

The exact materialized head's `validate` check passes.

## 3. Publication

Publication happens only through Josh's Ready action and the repository's
head-bound workflow.

## 4. Installed Engineering acceptance (separately authorized)

1. With Josh's approval, replay PR030c as a fresh plan. Expect
   `succeeded_verified` with exact readback.
   - It creates a notification-only automation.
   - Recovery is a governed delete
     (`create_configuration_delete_plan`).
2. The HA Admin chat then re-plans the A109 updates.
3. If any plan is refused, record `verifier_rule` and `verifier_location`
   in HAMCP-252. Whether to extend the verifier further is Josh's decision.
