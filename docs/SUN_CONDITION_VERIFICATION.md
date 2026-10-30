# Sun-condition step verification (HAMCP-252)

## Problem and correction

An owner-approved automation (PR030c) was refused before dispatch with
`configuration_not_verifiable` and verifier rule
`automation_verifier_unsupported_automation_simple_action_fields`. The
refused construct is a standalone `sun` condition step inside a `choose`
branch sequence:

```json
{"condition": "sun", "after": "sunset", "before": "sunrise"}
```

The location is `/action/0/choose/2/sequence/2`, and the offending fields are `after` and `before`. The readback verifier walks
only the action tree. Each simple directive has a fixed field allowlist, and
the condition-step allowlist did not contain the sun condition's fields. The
same condition in the top-level `condition` list, or in a branch's
`conditions` list, was already verifiable: condition lists are compared
exactly as opaque data and never allowlisted. That explains why a synthetic
probe that placed the sun condition in a branch `conditions` list passed.
No commit history records a deliberate exclusion. The only recorded rationale
is the generic rule that unreviewed families and fields fail closed.

The verifier now admits `after`, `before`, `after_offset` and
`before_offset` **only** on a step whose `condition` is `sun`. Values are
compared exactly as written. Case, offset form, a dropped field and step order
are never equated. Values are checked against Core's schema, so an unexpected
form is refused rather than compared:
- `after`/`before` must be strings whose lowercase is `sunrise` or
  `sunset`;
- an offset needs `after` or `before` (Core's `AtLeastOne`); otherwise the
  step is refused with `automation_verifier_invalid_automation_sun_condition`;
- each offset must be one of the reviewed `cv.time_period` forms below;
  otherwise the step is refused with
  `automation_verifier_invalid_automation_sun_condition_offset`, located at
  the step with the offending field.

| Form | Admitted | Core path |
| --- | --- | --- |
| Clock string | `[+-]H:M` or `[+-]H:M:S[.f]`, ASCII digits only | `time_period_str` |
| Seconds | an int or finite float (not a boolean), or an ASCII decimal string `[+-]D[.D]` | `time_period_seconds` |
| Mapping | nonempty; keys only `days`, `hours`, `minutes`, `seconds`, `milliseconds`; each value a seconds-form number or decimal string | `time_period_dict` |

Every admitted form must also build a Python `timedelta` without overflow.
The admitted set is a reviewed subset of what Core accepts. Core's
`int()`/`float()` coercion also takes surrounding whitespace, underscores,
exponents, `inf`/`nan` and non-ASCII digits; the verifier refuses those so
that only plain, unambiguous forms are compared. A refusal is fail-closed:
it never admits a form Core rejects. Valid forms are compared exactly as
written: `"00:30:00"`, `"0:30:00"`, `1800` and `{"minutes": 30}` are
different values and a change between them on readback is a mismatch.

Not admitted:
- the fields on any other condition type;
- unknown fields on a sun step (for example `weekday`).

The allowlist is a superset of the previous one, so no previously verifiable
step is newly refused.

## Refusal location (reporting only)

A verifier refusal now also stores bounded location codes with the child's
durable diagnostic codes:
- `automation_verifier_path_<tokens>`: a JSON-pointer-like path built only
  from structural keys (`action`/`actions` root, `choose`, `default`,
  `then`, `else`, `repeat`, `parallel`, `sequence`) and list indices.
  It is at most 96 characters, truncated at whole tokens and marked with
  `automation_verifier_truncated_path`. A refusal about the whole
  configuration (unsupported keys, or both spellings of `action`,
  `trigger` or `condition`) is `automation_verifier_path_root`, decoded as
  the root pointer `""`. Every verifier refusal carries exactly one path
  code; a refusal that names no position falls back to the root.
- `automation_verifier_field_<name>`: at most four offending field names.
  A name appears only when it is in a fixed vocabulary of known Home
  Assistant step and condition fields; any other name becomes
  `unrecognized_field`. Extra fields are marked with
  `automation_verifier_truncated_fields`.

`pre_dispatch_refusal.verifier_location` (and so
`last_error.failure_information`) decodes these codes into:

```json
{"path": "/action/0/choose/2/sequence/2", "path_truncated": false,
 "fields": ["weekday"], "fields_truncated": false}
```

The decoder accepts only codes it can fully validate. Otherwise the path or
location is `null`. Configuration values and templates are never stored, so
they cannot be reported. A refusal carries at most ten codes (the refusal,
one category, the rule and seven location codes), which is within the
16-code bound. The location therefore never displaces the refusal or its rule.
The location changes no accept or refuse decision.

## Exact Core authority

Reviewed Home Assistant Core 2026.10.3 source:
`1f214b8461dd1fbbd66e10765c6905f49d3c1d86`.

- [`components/sun/condition.py:66`](https://github.com/home-assistant/core/blob/1f214b8461dd1fbbd66e10765c6905f49d3c1d86/homeassistant/components/sun/condition.py#L66)
  declares the sun options:
  - optional `before`/`after` (`cv.sun_event`);
  - optional `before_offset`/`after_offset` (`cv.time_period`);
  - `AtLeastOne(before, after)` at
    [`:77`](https://github.com/home-assistant/core/blob/1f214b8461dd1fbbd66e10765c6905f49d3c1d86/homeassistant/components/sun/condition.py#L77).
- [`components/sun/condition.py:172`](https://github.com/home-assistant/core/blob/1f214b8461dd1fbbd66e10765c6905f49d3c1d86/homeassistant/components/sun/condition.py#L172)
  moves the legacy top-level fields into `options` during validation, in
  memory only.
- [`helpers/config_validation.py:194`](https://github.com/home-assistant/core/blob/1f214b8461dd1fbbd66e10765c6905f49d3c1d86/homeassistant/helpers/config_validation.py#L194)
  `sun_event = All(Lower, Any(sunset, sunrise))`, and
  [`:566`](https://github.com/home-assistant/core/blob/1f214b8461dd1fbbd66e10765c6905f49d3c1d86/homeassistant/helpers/config_validation.py#L566)
  `time_period = Any(time_period_str, time_period_seconds, timedelta, time_period_dict)`.
  The parts are
  [`time_period_str` (`:523`)](https://github.com/home-assistant/core/blob/1f214b8461dd1fbbd66e10765c6905f49d3c1d86/homeassistant/helpers/config_validation.py#L523):
  optional sign, then `H:M` or `H:M:S` via `int`, `int`, `float`;
  [`time_period_seconds` (`:558`)](https://github.com/home-assistant/core/blob/1f214b8461dd1fbbd66e10765c6905f49d3c1d86/homeassistant/helpers/config_validation.py#L558):
  `timedelta(seconds=float(value))`; and
  [`time_period_dict` (`:475`)](https://github.com/home-assistant/core/blob/1f214b8461dd1fbbd66e10765c6905f49d3c1d86/homeassistant/helpers/config_validation.py#L475):
  `days`/`hours`/`minutes`/`seconds`/`milliseconds`, each `Coerce(float)`,
  at least one. Lines 470-570 are identical in Core 2026.10.1
  (`abe1fdd7cd6b4de1715c87b6fd87d92699514ef9`).
- [`components/config/view.py:114`](https://github.com/home-assistant/core/blob/1f214b8461dd1fbbd66e10765c6905f49d3c1d86/homeassistant/components/config/view.py#L114):
  the config POST only validates. "We just validate, we don't store that
  data". Then [`:130`](https://github.com/home-assistant/core/blob/1f214b8461dd1fbbd66e10765c6905f49d3c1d86/homeassistant/components/config/view.py#L130) writes the posted
  dict.
- [`components/config/automation.py:59`](https://github.com/home-assistant/core/blob/1f214b8461dd1fbbd66e10765c6905f49d3c1d86/homeassistant/components/config/automation.py#L59)
  stores `id` and the ordered keys, then the posted dict unchanged
  ([`:85`](https://github.com/home-assistant/core/blob/1f214b8461dd1fbbd66e10765c6905f49d3c1d86/homeassistant/components/config/automation.py#L85)).
- [`components/config/view.py:86`](https://github.com/home-assistant/core/blob/1f214b8461dd1fbbd66e10765c6905f49d3c1d86/homeassistant/components/config/view.py#L86) GET
  returns the stored dict.

Readback is therefore verbatim. No defaulting or migration reaches storage,
so exact comparison is sufficient and no semantic-equivalence judgement is
needed.

## Verification requirements

`tests/test_hamcp252_sun_condition_verification.py` uses a synthetic replica
of the reported body. It has the same keys, nesting, positions and
non-entity values; entity ids and template text are synthetic. It also uses
the offline F3 executor and gateway fixtures. It proves:
- **Exact readback succeeds.** Preflight admits the replica, and exact
  readback ends `succeeded_verified` with one dispatch and one approval
  consumption (create and update), with duplicate safety.
- **Changed readback mismatches.** An altered, missing or dropped sun field
  on readback ends `verification_mismatch`.
- **Variants are admitted.** The Part 1 variants and every supported nested
  position are admitted.
- **Invalid values are refused.** Invalid sun events, and an offset without
  an event, are refused with
  `automation_verifier_invalid_automation_sun_condition`. Each malformed
  offset (wrong type, bad string, bad mapping key or value, overflow,
  non-finite) is refused with
  `automation_verifier_invalid_automation_sun_condition_offset` at preflight
  with no approval and no dispatch.
- **Valid offsets compare exactly.** Each admitted offset form verifies;
  a different representation, a changed value or a malformed readback is a
  mismatch.
- **Every refusal is located.** One case per verifier rule, pinned to the
  normalizer's refusal literals, decodes to its expected JSON pointer, so a
  new rule without a location fails. A whole-configuration refusal reports
  `""` through the durable child reason.
- **Real-body structure.** The replica's value-free structural fingerprint at
  `/action/0/choose/2/sequence/0..5` and `/condition/1` equals the real
  PR030c body's (`8b92ae0a…`). Only the digest is committed.
- **Neighbors stay refused.** Unsupported neighbors are still refused with
  their location: the time condition's `after`/`before`/`weekday`,
  sun plus `weekday`/`zone`, and the sun fields on other condition types.
- **Location bounds hold.** Location codes are bounded and structural only,
  map unknown names to `unrecognized_field`, leak no values, and are
  decoded only when valid. The vocabulary is pinned between the normalizer
  and the reason module.
