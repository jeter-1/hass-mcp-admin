# Engineering 2.2.0-beta.33 release notes

Beta 33 corrects the F2/F7 risk-delta defect exposed by the live garage
risk-reduction exercise. An existing automation was prohibited merely because
its complete proposed configuration still contained an unchanged retained
safety-critical effect. This release adds one bounded path for that effect
without relaxing policy for new, changed, broadened, or ambiguous effects. It
is staged from `2.2.0-beta.32`.

Publication, deployment, live Home Assistant access, and a live garage canary
are not part of this source change.

## Reviewed retained-effect proof

An existing automation update qualifies only when Engineering can prove all of
the following from the normalized authoritative before/after records:

- the operation is an update, not a create;
- the complete action and nested control-flow graph is byte-for-byte equal;
- triggers, mode, maximum runs, variables, trace settings, and every other
  behavioral top-level field are equal;
- neither side uses a blueprint;
- the existing top-level condition list is an exact prefix of the proposal;
- at least one reviewed, enabled condition guard is appended;
- condition structure contains no action-like directive;
- action/service/target structure is fully bounded, with no policy warning.

This proves that the retained effect cannot run in a state in which the current
automation could not already run. It does not prove the added condition will be
false in a particular live state.

## Proportional governance

The complete proposed automation remains structurally `high` risk and the
physical consequence remains `safety_critical`. The proven configuration delta
is `moderate`, and the policy is `elevated_admin`. The same authenticated Home
Assistant administrator must therefore complete the existing exact-plan
approval and elevated-risk acknowledgement before F3 can execute.

The immutable policy decision records bounded reason codes for the retained
effect and appended guard. The existing current-state fingerprint, complete
Beta 22 semantic projection, policy and plan hashes, stale-state preflight, F3
task authority, exactly-one-dispatch rule, exact readback verification, and
duplicate-apply behavior are unchanged.

## Preserved prohibitions and boundaries

The exception does not apply to:

- a new automation or new physical effect;
- any changed service, action, target, or action position;
- a trigger, mode, maximum-run, variable, trace, blueprint, or unknown-field
  behavioral change;
- removed, replaced, reordered, inserted-before-existing, disabled, unknown,
  or action-like conditions;
- dynamic, unresolved, unsupported, or otherwise ambiguous action/target
  structure.

Those proposals keep the existing `safety_critical_effect_not_reviewed`
prohibition and cannot create an approval challenge, execution task, provider
attempt, service call, or fallback.

No public MCP tool or schema, provider route, ha-mcp admission, Home Assistant
compatibility declaration, dashboard behavior, notification behavior,
approval authority, or F3 execution contract changes. Stable v1.1.2 is
unchanged.
