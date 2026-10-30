# Engineering 2.4.1-beta.51 release notes

Engineering 2.4.1-beta.51 lets the readback verifier compare a `sun`
condition used as an action step, and makes a verifier refusal name where it
refused (HAMCP-252). It builds on 2.4.1-beta.50 (HAMCP-106/255, #330). It
adds no Home Assistant write, tool, input schema,
route, provider, Core command or signed registry change.

Acceptance: [`V2_4_1_BETA51_ACCEPTANCE.md`](V2_4_1_BETA51_ACCEPTANCE.md).
Core authority: [`SUN_CONDITION_VERIFICATION.md`](SUN_CONDITION_VERIFICATION.md).

## Why

The PR030c replay of the A109 camera automations was refused before
dispatch:
- outcome `configuration_not_verifiable`;
- rule `automation_verifier_unsupported_automation_simple_action_fields`.

The refused construct is a standalone sun condition step,
`{"condition": "sun", "after": "sunset", "before": "sunrise"}`, inside a
`choose` branch sequence. The condition-step allowlist lacked the sun
fields. The same condition in a `conditions` list was already verifiable,
which is why an earlier synthetic probe passed.

## The change

- **Sun steps.** `after`, `before`, `after_offset` and `before_offset` are
  admitted only on a `condition: sun` step and compared exactly. Values
  outside Core's sun schema are refused with
  `automation_verifier_invalid_automation_sun_condition`; a malformed
  `after_offset`/`before_offset` (outside Core's `cv.time_period` forms, or
  outside the reviewed subset of them) is refused before dispatch with
  `automation_verifier_invalid_automation_sun_condition_offset`. A negative
  clock offset is checked after its sign is applied, as Core does, so one
  beyond the negative duration bound is refused. The fields stay
  refused on every other condition type, and unknown fields on a sun step
  stay refused.
- **Refusal location.** Every verifier refusal also stores bounded codes for
  its structural path and offending field names. A refusal about the whole
  configuration (for example both `action` and `actions`) is located at the
  root pointer `""`. Names outside a fixed Home
  Assistant field vocabulary are stored as `unrecognized_field`. The codes
  are decoded into `pre_dispatch_refusal.verifier_location` (`path`,
  `path_truncated`, `fields`, `fields_truncated`). That object is also
  reported on `last_error.failure_information`, the apply result, the event
  and the log line. No value or template is stored.

## Not changed

- Every other preflight decision, plus approval binding, stale-state checks,
  locks, dispatch, at-most-once execution and other adapters.
- Comparison stays exact. There is no wildcard, ignore-list or equivalence
  rule.
- Every field is additive. Stored records keep their shape; older refusals
  read `verifier_location: null`.

## Rollback

Reinstall 2.4.1-beta.50. A sun condition step is refused again before
dispatch. The added field and codes are ignored by the older reader.
