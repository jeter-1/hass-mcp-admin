# Engineering 2.4.1-beta.59 acceptance

This contract covers HAMCP-274: `set_input_select_option` relevance by
evidence, so that an unreferenced input_select is no longer prohibited. It
follows 2.4.1-beta.58 and includes it.

See also the release notes:
[`V2_4_1_BETA59_RELEASE_NOTES.md`](V2_4_1_BETA59_RELEASE_NOTES.md).

**Release target.** `2.4.1-beta.59`, following 2.4.1-beta.58 (#342,
HAMCP-278). The release is materialized in all three version authorities
with no staged declaration left. Nothing is published or deployed. If
another pull request takes beta.59 first, this one restages to the next free
beta; that collision is expected and is not a finding.

The contract has four stages:
1. source and offline acceptance;
2. release CI;
3. publication;
4. installed acceptance.

It authorizes none of the later stages.

## 1. Source and offline acceptance

Bind:
- Josh's direction for HAMCP-274: fail closed only for execution
  uncertainty, meaning an ambiguous target, stale state, an unverified
  provider, a race, a duplicate dispatch or an unverifiable result.
  Consequence and risk classifications are decision support;
- the non-goals: HAMCP-253, HAMCP-275, signing, `ci.yml` and new
  capabilities;
- signed contracts unchanged: the 62 Core contract fingerprints, including
  `core.typed_input_select_option_operation`, are identical to beta.58;
- `2.4.1-beta.59` materialized in all three version authorities before
  Ready, with exact document resolution;
- the PR head SHA, its `validate` run and the Evidence tier log named in
  the PR.

The review needs a security section covering:
- **No new reachability.** No tool, route, provider, service or dispatch
  path is added. `input_select.select_option` remains the only call sent.
- **Execution refusals.** Each of these is still refused with its existing
  code:
  - stale state;
  - an ambiguous target;
  - an unadmitted provider;
  - a dependency index failure;
  - dependency drift between approval and apply;
  - a no-op.
- **Locks.** A bounded set of named consumers, opaque ones included, takes
  precise per-automation locks plus the existing shared stability fences.
  The coarse `reload:automation` exclusive fence applies only when the
  consumer set is over the 50-lock budget, a consumer's lock identity is
  unavailable, or a coverage gap is unattributable. An input_select v2
  projection that does not state its fence is invalid, and nothing is
  dispatched.
- **Approval binding.** The plan hash and the evidence fingerprint bind
  every relevant consumer, including those beyond the disclosure bound,
  through `relevant_consumer_fingerprint`. A v1 plan is `replan_required`
  and cannot be approved or applied. Applying a v1 plan that beta.58
  already approved returns `OPERATIONAL_VALIDATION_FAILED` /
  `helper_dependency_evidence_superseded_replan_required` before any task,
  lock or provider call; the approval is not consumed.

Required offline evidence, `tests/test_hamcp274_input_select_relevance.py`:
- **Reproduction.** An installation-shaped fixture has 65 automations with
  templates, call_service filters and label selectors, and a malformed
  registry record for another entity. The plan is `prohibited`, `critical`
  and `safety_critical` at beta.58 (`d09fc8a6`), and `standard_admin`, `low`
  and `none` with no `safety_critical` label after the fix.
- **Classification matrix.** One test each:
  - literal reference;
  - label;
  - group and `expand()`;
  - area;
  - domain-wide iteration;
  - dynamic or opaque ID: elevated, not prohibited;
  - template-heavy, not touching the target: not relevant;
  - unrelated malformed record: no effect;
  - related malformed record: reported and elevated;
  - stale state, ambiguous target and unadmitted provider: refused with
    their precise codes.
- **Labels.** Opaque consumers are `unknown`, with the potential
  consequence disclosed. A literal consumer is `safety_critical`, and a
  `safety_critical` plan still executes after approval.
- **Locks.** Precise locks; the coarse fence; and no dispatch without a lock
  set.
- **Size.** The response stays under 60,000 characters, with truncation
  flags and counts and a `get_change_plan` page.
- **End to end**, using real governance and F3:
  - plan → approve → apply → `succeeded_verified` with exact readback;
  - restore B → A;
  - a no-op is refused with `no_change` and no plan created;
  - exactly one provider call per applied path.
- **Mutation testing** of the relevance, classification, label, lock-fence,
  malformed-record and response-size code, in a copied tree, with every
  kill classified.
- The unchanged HAMCP-145 suite and the complete repository suite in the
  Evidence tier.

## 2. Release CI

The exact materialized head's `validate` check passes, including the
beta.58 → beta.59 release transition.

## 3. Publication

Publication happens only through Josh's Ready action and the repository's
head-bound workflow.

## 4. Installed acceptance (separately authorized)

After Josh deploys beta.59 and refreshes the connector:

1. **Read-only.** Create a plan on an input_select that an automation
   references, and never approve or apply it. PASS if it is
   `elevated_admin`, its consumers are named in `downstream_profiles` with
   `evidence_class` and relationships, and the response is under 60,000
   characters.
2. **With Josh approving,** on `input_select.engineering_acceptance`:
   - A → B plans as `standard_admin`. Approve and apply it: PASS if the
     result is `succeeded_verified`.
   - B → A, by a fresh plan or `rollback_change`. Approve and apply it:
     PASS if the result is `succeeded_verified`.
   - Plan the current option again: PASS if it is refused as a no-op with
     `status: no_change`, `plan_created: false` and
     `provider_dispatch_occurred: false`.
3. Helper options re-plan: not covered. Helper options keep the shipped
   helper-wide rule, and their fix is a follow-up.

If a step fails, keep the plan IDs, responses and audit records, and record
them in HAMCP-274. Whether to roll back to beta.58 is Josh's decision.
