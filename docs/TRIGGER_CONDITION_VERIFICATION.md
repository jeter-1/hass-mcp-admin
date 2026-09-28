# Trigger-condition verification

## Problem and correction

A standalone `condition: trigger` action with an `id` string or list of strings
was admitted by configuration planning but rejected by the stricter automation
readback verifier. Identical approved and observed configurations could therefore
produce `normalization_valid=false`, `semantic_match=false`, and a terminal
`verification_mismatch` after the configuration had already been saved.

The verifier now admits those two literal forms specifically for the trigger
condition subtype. It preserves all content: IDs, ordering, duplicates, empty
strings/lists, whitespace, modifiers, and scalar-versus-list representation.
It does not evaluate strings or equate a scalar with a singleton list. Removing
or changing a guard still produces a mismatch. An inner condition `id` remains
behavioral, unlike the top-level automation resource `id`.

Missing IDs, non-string scalar/list members, nested containers, and unknown
standalone trigger-condition fields remain unsupported. The change does not
add `id` to other standalone condition subtypes. Existing action-depth, payload,
provider, identity, stale-state, approval, and dispatch boundaries remain.

F3 configuration preflight also compares each proposed configuration with itself
using the same readback comparator, after existing configuration validation and
before the final mutable-state read. Unsupported comparisons return the fixed
`configuration_not_verifiable` diagnostic before that operation consumes
approval or records dispatch intent. No configuration content is included in
that diagnostic. Earlier operations in a non-atomic sequence may already have
completed; this change is not an all-or-nothing sequence guarantee.

## Exact Core authority

Reviewed Home Assistant Core 2026.9.3 source:
`6de5eb18cd4502f94af44cfff3a02250d88716ed`.

- [`helpers/config_validation.py:1616`](https://github.com/home-assistant/core/blob/6de5eb18cd4502f94af44cfff3a02250d88716ed/homeassistant/helpers/config_validation.py#L1616)
  defines the required trigger-condition `id` through `ensure_list` and `string`.
- [`helpers/config_validation.py:353`](https://github.com/home-assistant/core/blob/6de5eb18cd4502f94af44cfff3a02250d88716ed/homeassistant/helpers/config_validation.py#L353)
  and [`:697`](https://github.com/home-assistant/core/blob/6de5eb18cd4502f94af44cfff3a02250d88716ed/homeassistant/helpers/config_validation.py#L697)
  define those conversions. Core accepts additional coercions (including some
  non-string inputs); this bounded correction does not add those equivalences
  to Engineering's verification contract.
- [`helpers/condition.py:1954`](https://github.com/home-assistant/core/blob/6de5eb18cd4502f94af44cfff3a02250d88716ed/homeassistant/helpers/condition.py#L1954)
  checks membership of the current trigger ID in the validated ID list.
- [`tests/helpers/test_condition.py:2383`](https://github.com/home-assistant/core/blob/6de5eb18cd4502f94af44cfff3a02250d88716ed/tests/helpers/test_condition.py#L2383)
  covers scalar configuration validation and matching/nonmatching trigger data.

## Verification requirements

`tests/test_trigger_condition_verification.py` uses synthetic configurations and
the existing offline F3 executor/gateway fixtures to prove:

- Scalar/list equality, supported nested action positions, and unchanged input
  and binding normalization.
- Meaningful ID, order, guard, modifier, and trigger-definition differences.
- Missing/malformed IDs, wrong-subtype fields, and unknown-field rejection.
- Preserved service/action alias and registry-category handling.
- Create/update success with one dispatch and one approval consumption;
  duplicate execution does not dispatch again.
- Unsupported comparisons refuse before approval consumption or mutation.
- Changed authoritative readback remains failed without redispatch.

Run those tests with the existing configuration lifecycle, resource, identity,
migration, sequence, and verification-attribution suites. Exact-Core isolated
schema/condition execution supplements the source review; it does not establish
deployed behavior or a complete Core/ha-mcp container scenario. Final release CI
and installed acceptance remain separate gates.

## Historical task boundary

This correction adds no public tool or input schema and changes no immutable
plan hash, approval, original failure, task event, or provider dispatch count.
Deploying it does not reopen or resolve a terminal configuration task. The
existing task getter reads the configuration-task projection; it is not a
supported terminal re-verification operation.

Resolving a historical review requires a separately reviewed contract for fresh,
authoritative readback and durable supplementary evidence bound to the original
plan/task and approved candidate. Such a path must preserve the original failed
verification, never resend configuration or operate a device, and distinguish
current-state verification from proof of the state at the original execution.
Do not use `apply_change_plan`, direct store edits, or automatic terminal-state
rewriting to manufacture a successful historical result.

The separate [configuration-task re-verification contract](CONFIGURATION_REVERIFICATION.md)
defines an explicit supplementary receipt for eligible historical mismatches.
The normalizer fix alone does not change any retained task outcome; the additive
readback capability must be validated, reviewed and deployed before live use.
