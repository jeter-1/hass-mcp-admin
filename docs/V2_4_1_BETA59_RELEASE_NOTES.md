# Engineering 2.4.1-beta.59 release notes

Engineering 2.4.1-beta.59 (HAMCP-274) decides `set_input_select_option`
relevance per target from evidence. An input_select that no automation
references now plans as `standard_admin` instead of `prohibited`. It builds
on 2.4.1-beta.58 (#342, HAMCP-278). No tool, input schema, route, provider,
signed profile, contract fingerprint, persisted format, add-on option or Home
Assistant write changes.

Acceptance: [`V2_4_1_BETA59_ACCEPTANCE.md`](V2_4_1_BETA59_ACCEPTANCE.md).

## Why

The signed `core.typed_input_select_option_operation` capability (sequence
15) could not be used. On beta.54, a plan to change the unreferenced
`input_select.engineering_acceptance` from A to B came back `prohibited`,
`critical` and `safety_critical`. The reasons were
`helper_execution_contract_incomplete` and
`technical_execution_authority_required`, with execution block
`helper_lock_graph_resource_limit_exceeded`, 198 opaque obligations, 50
relevant automations and a 658,879-character response.

The cause was that the reader shared the input_boolean rule. Any opaque
template anywhere, and any coverage gap, made every automation relevant to
every helper. That is consequence uncertainty, not execution uncertainty:
the target, option, provider, state, dispatch and readback were all exact.
Josh's direction is to fail closed only for execution uncertainty and to
treat consequence as decision support.

## The change

- **Relevance by evidence.** The input_select reader (binding model
  `helper-dependency-risk-input-select-v2`, `relevance_model:
  target_scoped_evidence`) counts an automation only when it references this
  target. That covers:
  - a literal reference;
  - an indirect reference through a group, `expand()`, an area, a label, a
    device, or domain-wide iteration such as `states.input_select`;
  - an obligation that stays genuinely opaque for this target, such as a
    dynamically built ID.

  A template that cannot touch the target is not relevant. A coverage gap
  that belongs to a named automation scopes only that automation. Only an
  unattributable gap still scopes every automation.
- **Classification.**

  | Relevant consumers | Result |
  |---|---|
  | none | `standard_admin`, risk `low`, physical `none` |
  | bounded, none consequential | `elevated_admin`, risk `medium`, physical `none`, reason `relevant_helper_consumer_disclosed` |
  | bounded, consequential | `elevated_admin`, risk `high`, physical `direct` or `safety_critical` |
  | opaque | `elevated_admin`, risk `high`, physical `unknown`, with disclosure |

  Each consumer is named with its `evidence_class` (`referenced`, `opaque`
  or `conservative_scope`) and its relationships. Positive evidence alone
  sets the physical consequence. An opaque consumer contributes at most
  `unknown`, and its own profile's consequence is disclosed separately as
  `potential_physical_consequence`.
- **Locks.** A bounded consumer set takes the same per-automation locks as
  before. If the set exceeds the 50-lock bound, or a consumer has no lock
  identity, or an unattributable gap remains, the plan takes the existing
  `reload:automation` key in exclusive mode instead of being refused. Every
  automation mutation and reload holds that key, so the plan may over-lock
  but never under-locks. An input_select v2 projection must state
  `coarse_automation_fence`; if it does not, the lock set is invalid and
  nothing is dispatched.
- **Malformed registry records.** A malformed entity-registry record that
  names another entity no longer leaves a label selector undecided for this
  target. A malformed record that names the target, or one with no entity
  ID, still leaves it unresolved. It is reported with
  `malformed_record_entity_ids` and elevates the plan.
- **Response size.** If a governed create response would exceed 60,000
  characters, it leaves out the paged evidence lists. These are
  `obligation_evidence`, `downstream_profiles` and
  `selector_authority_diagnostics`. `response_paging` names each one with its
  total count, `truncated` and the `get_change_plan` section that returns
  it. The plan hash and the evidence fingerprint still bind the complete
  persisted inputs.
- **Execution uncertainty unchanged.** The following still refuse with
  their existing codes:
  - stale state;
  - an ambiguous target;
  - an unadmitted provider;
  - a dependency index failure;
  - dependency drift between approval and apply;
  - a no-op.

  Approval binding, at-most-once dispatch and exact readback are unchanged.
- **Superseded plans.** An input_select plan created under the v1 binding
  is `replan_required`. It cannot be approved or applied. Its recovery
  readback still reconstructs the shipped v1 lock graph.

## Not changed

- `set_input_boolean_state` keeps its shipped helper-wide rule.
- Helper options planning keeps its shipped helper-wide rule. Beta.57 live
  evidence showed it can still label a sensor with no consumers
  `safety_critical`. That rule does not use this reader, and its fix is
  reported as a follow-up.
- Signed profiles and contract fingerprints: all 62 contracts are identical
  to beta.58.

## Rollback

Reinstall 2.4.1-beta.58. Plans created on beta.59 use the v2 binding.
Beta.58's lock calculator accepts only the v1 model (from source; not
exercised against an installation), so beta.58 refuses those plans before
dispatch, and a fresh plan is needed. No persisted format changed.
