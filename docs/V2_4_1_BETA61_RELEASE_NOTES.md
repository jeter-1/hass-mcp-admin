# Engineering 2.4.1-beta.61 release notes

Engineering 2.4.1-beta.61 (HAMCP-285) applies evidence-based classification
to three more planners: helper options, entity availability and exposure, and
input_boolean state. It builds on 2.4.1-beta.60 (#344, HAMCP-270) and the
beta.59 relevance model (#346, HAMCP-274). No tool,
input schema, route, provider, signed profile, contract fingerprint,
persisted format, add-on option or Home Assistant write changes.

Acceptance: [`V2_4_1_BETA61_ACCEPTANCE.md`](V2_4_1_BETA61_ACCEPTANCE.md).

## Why

- **Helper options.** On beta.57, a helper options plan for the template
  sensor `sensor.local_outdoor_temperature_delta_vs_forecast` was labelled
  `safety_critical`, risk `high`, `elevated_admin`. It had no consumers. The
  only evidence was incomplete: the config-entry helper chain cannot be
  covered. Elevated approval with disclosure was right. The
  `safety_critical` label was not, because nothing positively showed a
  protected consequence.
- **Availability and exposure.** The planner used the same rule: any
  elevated basis was labelled `safety_critical`.
- **input_boolean.** `set_input_boolean_state` still used the helper-wide
  reader that beta.59 replaced for input_select. Any opaque template
  anywhere, and any coverage gap, made every automation relevant to every
  input_boolean. In addition, applying an input_boolean plan approved under
  a superseded binding failed with an unstructured lock-calculator error
  instead of a structured replan result.

Josh's direction is to fail closed only for execution uncertainty and to
treat consequence as decision support.

## The change

- **input_boolean relevance by evidence.** The input_boolean reader (binding
  model `helper-dependency-risk-v14`) uses the beta.59 target-scoped rules:
  an automation is relevant only when it references this target literally,
  through a group, `expand()`, an area, a label, a device, or domain-wide
  iteration such as `states.input_boolean`, or when an obligation stays
  genuinely opaque for this target. Classification, consumer naming and
  locks follow the beta.59 input_select table:

  | Relevant consumers | Result |
  |---|---|
  | none | `standard_admin`, risk `low`, physical `none` |
  | bounded, none consequential | `elevated_admin`, risk `medium`, physical `none` |
  | bounded, consequential | `elevated_admin`, risk `high`, physical `direct` or `safety_critical` |
  | opaque | `elevated_admin`, risk `high`, physical `unknown`, with disclosure |

  A bounded consumer set takes per-automation locks. An opaque or
  over-budget set takes `reload:automation` in exclusive mode instead of
  being refused, so the plan may over-lock but never under-locks.
- **Positive evidence for `safety_critical`.** Helper options plans, and
  elevated availability plans, now carry
  `consequence_model: positive_evidence_v1` in their risk evidence. A
  standard availability plan carries no marker: its label is `none` under
  both rules, and leaving the marker out keeps its response within the
  beta.60 size envelope. `safety_critical` requires positive evidence:
  - helper options: a dependency reason naming a protected object, a
    proposed protected object, or a protected automation the helper feeds;
  - availability and exposure: a protected object basis, or a dependency
    reason naming a protected automation the entity feeds.

  Otherwise an elevated plan is labelled `unknown` and its gaps are stated.
  A plan with no elevated basis is `none`. Positive reasons are kept first
  when the dependency-reason list is bounded.
- **Floors unchanged.** Helper options plans stay elevated: the config-entry
  helper chain gap is always present. Availability keeps its protected-object
  floors; a standard availability plan is risk `low`.
- **Persisted plans.** Helper options and availability plans created before
  beta.61 carry no `consequence_model`. They keep the shipped rule and
  re-classify identically. Any other `consequence_model` value is incomplete
  evidence and is refused.
- **Superseded input_boolean plans.** An input_boolean plan created under the
  v13 binding is `replan_required`. It cannot be approved. Applying one that
  beta.59 or beta.60 already approved returns `OPERATIONAL_VALIDATION_FAILED` /
  `helper_dependency_evidence_superseded_replan_required` before any task,
  lock or provider call, and the approval is not consumed. Its recovery
  readback still reconstructs the shipped v13 lock graph.
- **Existing tasks under a superseded binding.** A task that an earlier beta
  already created for a superseded input_boolean or input_select binding no
  longer fails with an unstructured `ValueError` while its locks are
  rebuilt. Its shipped lock graph is rebuilt for readback. A task that had
  not reached intent is refused with the same structured replan result and
  can still be cancelled; a task past intent continues through recovery.
  This also fixes the same latent defect for input_select tasks created on
  beta.58 and recovered on beta.59 or beta.60. It follows the existing
  historical-policy precedent for pre-intent recovery.
- **Bounded, non-consequential consumers now apply.** Since beta.59 the
  planner labels a helper value change with a bounded relevant consumer and
  no physical consequence `elevated_admin`, risk `medium`. The F3 apply check
  did not list that family, so an approved plan was always refused at apply
  with `policy_snapshot_mismatch` and nothing was dispatched. This affected
  input_select on beta.59 and beta.60 and would have affected input_boolean
  here. F3 now
  accepts exactly that family for helper value operations. Every other
  operation keeps its fixed family.
- **Response size.** The input_boolean create response is bounded the same
  way as input_select: oversized paged evidence lists are left out and named
  in `response_paging`. The plan hash binds the complete persisted inputs.
- **Global references stay informational.** The beta.60 scene, group and
  dashboard references in helper options and availability plans do not feed
  any HAMCP-285 classification, label, statement, marker or fingerprint.
- **Real HA contract harness.** The live-contract harness builds the
  input_boolean reader with the v14 binding, as the runtime does, and checks
  the plan's dependency model.
- **Execution uncertainty unchanged.** Stale state, an ambiguous target, an
  unadmitted provider, a dependency index failure, drift between approval
  and apply, and a no-op still refuse with their existing codes. Approval
  binding, at-most-once dispatch and exact readback are unchanged.

## Not changed

- input_select (beta.59 binding bytes unchanged), input_number and other
  helper types.
- Availability `disable` with bounded consumers is not newly elevated. That
  would change the consumer-evidence fingerprint bound into already-approved
  plans; it is left for Josh to decide.
- Configuration delete planning keeps its shipped consequence rule.

## Rollback

Reinstall 2.4.1-beta.60. From source (not exercised against an
installation): beta.60 executes only v13 input_boolean bindings, so it
refuses beta.61 input_boolean plans before dispatch; and beta.60 policy
treats an elevated helper options or availability plan whose label is not
`safety_critical` as incomplete, so it refuses those plans too. A fresh plan
is needed. No persisted format changed.
