# Engineering 2.4.1-beta.60 acceptance

This contract covers HAMCP-285: evidence-based classification for helper
options, entity availability and exposure, and `set_input_boolean_state`. It
follows 2.4.1-beta.59 and includes it.

See also the release notes:
[`V2_4_1_BETA60_RELEASE_NOTES.md`](V2_4_1_BETA60_RELEASE_NOTES.md).

**Release target.** `2.4.1-beta.60`, following 2.4.1-beta.59 (#346,
HAMCP-274). The release is materialized in all three version authorities
with no staged declaration left. Nothing is published or deployed. If
another pull request takes beta.60 first, this one restages to the next free
beta; that collision is expected and is not a finding.

The contract has four stages:
1. source and offline acceptance;
2. release CI;
3. publication;
4. installed acceptance.

It authorizes none of the later stages.

## 1. Source and offline acceptance

Bind:
- Josh's direction for HAMCP-285: fail closed only for execution
  uncertainty, meaning an ambiguous target, stale state, an unverified
  provider, a race, a duplicate dispatch or an unverifiable result.
  Consequence and risk classifications are decision support and do not
  independently overrule an exact owner-authorized operation;
- the non-goals: HAMCP-253, HAMCP-275, HAMCP-243, signing, `ci.yml`, new
  capabilities, input_number and other helper types;
- signed contracts unchanged: the Core contract fingerprints are identical
  to beta.59;
- `2.4.1-beta.60` materialized in all three version authorities before
  Ready, with exact document resolution;
- the PR head SHA, its `validate` run and the Evidence tier log named in
  the PR.

The review needs a security section covering:
- **No new reachability.** No tool, route, provider, service or dispatch
  path is added.
- **Execution refusals.** Stale state, an ambiguous target, an unadmitted
  provider, a dependency index failure, dependency drift between approval
  and apply, and a no-op are still refused with their existing codes.
- **Labels.** `safety_critical` requires positive evidence. Incomplete
  evidence is `unknown` with its gaps stated, and stays elevated where the
  planner floor requires it. Plans persisted without `consequence_model`
  re-classify under the shipped rule; an unknown `consequence_model` is
  refused.
- **Locks.** A bounded input_boolean consumer set takes precise
  per-automation locks. The coarse `reload:automation` exclusive fence
  applies when the consumer set is opaque or over budget. A v14 projection
  that does not state its fence is invalid, and nothing is dispatched.
- **Approval binding.** A v13 input_boolean plan is `replan_required` and
  cannot be approved. Applying a v13 plan that beta.59 already approved
  returns `OPERATIONAL_VALIDATION_FAILED` /
  `helper_dependency_evidence_superseded_replan_required` before any task,
  lock or provider call; the approval is not consumed, and recovery still
  reconstructs the shipped v13 lock graph.

Required offline evidence, `tests/test_hamcp285_evidence_labels.py`:
- **Reproductions** that fail at beta.59 (`3df4ec63`) and pass after the
  fix: the unreferenced input_boolean in an installation-shaped fixture;
  the template-sensor helper options plan with only incomplete evidence;
  the availability analogue; and the approved beta.59 input_boolean record.
- **Per-planner matrix**, one test each: literal reference; group,
  `expand()`, area and label; domain-wide iteration; opaque (elevated,
  disclosed); template-heavy but unrelated; a positive safety-relevant
  consumer (still `safety_critical`); stale state, ambiguous target and
  unverified provider (still refused with their precise codes).
- **Fixtures** written by exact beta.59 (`8615ad1`) with provenance:
  pending and approved v13 input_boolean records; exact refusal, zero
  provider calls, approval unconsumed, files byte-unchanged and the recovery
  graph unchanged.
- **End to end**, using real governance and F3: the unreferenced
  input_boolean and the template helper, with one provider call per
  applied path.
- input_select binding bytes unchanged; response size bound.
- **Mutation testing** in a copied tree, with every kill classified.
- The complete repository suite in the Evidence tier.

## 2. Release CI

The exact materialized head's `validate` check passes, including the
beta.59 → beta.60 release transition.

## 3. Publication

Publication happens only through Josh's Ready action and the repository's
head-bound workflow.

## 4. Installed acceptance (separately authorized)

After Josh deploys beta.60 and refreshes the connector:

1. **Read-only.** Re-plan helper options on
   `sensor.local_outdoor_temperature_delta_vs_forecast` and never approve
   it. PASS if it is `elevated_admin` with physical consequence `unknown`
   (not `safety_critical`) and its gaps stated.
2. **Read-only.** Create a plan on an input_boolean that an automation
   references, and never approve it. PASS if it is `elevated_admin` and its
   consumers are named in `downstream_profiles`.
3. **With Josh approving,** on a throwaway
   `input_boolean.engineering_acceptance`:
   - off → on plans as `standard_admin`. Approve and apply it: PASS if the
     result is `succeeded_verified`.
   - on → off, by a fresh plan or `rollback_change`. Approve and apply it:
     PASS if the result is `succeeded_verified`.
   - Plan the current state again: PASS if it is refused as a no-op with no
     plan created and no provider dispatch.

If a step fails, keep the plan IDs, responses and audit records, and record
them in HAMCP-285. Whether to roll back to beta.59 is Josh's decision.
