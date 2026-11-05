# Engineering 2.4.2-beta.3 acceptance

This contract covers HAMCP-311: `control_helper`, which makes helper value
changes ordinary runtime control. It follows 2.4.2-beta.2 (HAMCP-306, #361).

See also:
- the release notes, [`V2_4_2_BETA3_RELEASE_NOTES.md`](V2_4_2_BETA3_RELEASE_NOTES.md);
- the design, [`TYPED_HELPER_CONTROL.md`](TYPED_HELPER_CONTROL.md).

**Release target.** `2.4.2-beta.3`, following 2.4.2-beta.2. The release
transition accepts only `2.4.2-beta.2 -> 2.4.2-beta.3` from the current base.

**Merge-order rule.** If another release PR is ordered ahead of this one at
Codex Ready, this branch:
1. merges `main` with a merge commit;
2. restages once to the next free beta;
3. runs final Evidence once, when it is next in line.

The release is materialized in all three version authorities, with no staged
declaration left. Nothing is published or deployed.

The contract has four stages:
1. source and offline acceptance;
2. release CI;
3. publication;
4. installed acceptance.

It authorizes none of the later stages.

## 1. Source and offline acceptance

Bind:
- **The product decision.** Josh, 2026-11-04, in the architect chat, relayed
  in the HAMCP-311 contract:
  - helper value changes are treated like switches, with no plan and no
    approval panel;
  - the chat warns before a safety-critical change;
  - the result shows known automation consumers as information;
  - the scope is all helper value types.
- **The non-goals:**
  - helper configuration plans are unchanged;
  - `create_helper_state_plan` and `create_input_select_option_plan` keep
    working and are unchanged, apart from a superseded note in their
    descriptions;
  - there is no new dependency analysis;
  - no generic service call is added.
- `2.4.2-beta.3` materialized before Ready, with exact document resolution.
- The PR head SHA, its `validate` run and the Evidence tier log named in the
  PR.

The review needs a security section covering:
- **New write reachability.**
  - One new tool, `control_helper`, reaches exactly the 15 helper services in
    the design's table, with fixed data keys.
  - The request is closed: no `service`, `domain` or `data` input, no
    selectors and no lists.
  - It has one reviewed internal provider and no fallback.
  - It has no Core-direct path, no reload, no configuration write and no
    other domain.
- **Authority.**
  - Authenticated connector authority binds the exact request. Another
    family's binding cannot authorize it.
  - Core authority is signed-only, through the new compiled profile
    `core.typed_helper_value_operation`.
  - With no signed sequence naming that profile, the tool refuses as
    `provider_unavailable` without a provider call. It ships dormant.
- **At most once.**
  - There is one dispatch per declaration.
  - An uncertain response never replays.
  - A repeated request only reconciles; changed arguments refuse.
  - A duplicate overlapping owner dispatches once.
- **Safety warning.**
  - Only exact safety-critical evidence warns, and nothing is saved or sent
    when it does.
  - The acknowledgement is audited as a model-conveyed chat confirmation,
    never as external approval.
  - Opaque readers and unavailable evidence never warn. Unavailable
    evidence proceeds with a note, so a call without the acknowledgement is
    not an unconditional no-dispatch guarantee.
- **Bounded output and redaction.**
  - Consumer lists are capped at 20 entries.
  - Template examples are capped at 3.
  - Names are capped at 80 characters.
  - Password-mode text is redacted in results.
  - Private provider and index failure text is not returned.

Required offline evidence is in `tests/test_typed_helper_value.py`. It runs
against ha-mcp 8.4.3, 8.5.0 and 8.6.0 catalogs:
- **Each helper type.** A positive set with readback, a no-op without
  dispatch, a live-invalid value refused without dispatch, and an
  unavailable entity refused.
- **Empty values.** An empty `input_text` value within the live length and
  pattern, and an empty `input_select` option in the live options, pass
  preparation, no-op detection and readback. Empty states that the live
  configuration does not allow, and `unavailable` or missing states,
  refuse.
- **Number steps.** The on-step check is exact decimal arithmetic with an
  allowance of at most 1e-9 of one step at any magnitude. A half-step value
  near 1e9 with step 1 refuses without dispatch; large on-step values and
  ordinary decimal steps such as 0.3 at step 0.1 succeed.
- **Transitions.** Press, increment and timer actions always dispatch.
  Counter limits, a timer state that would make the action a silent no-op,
  and unsupported patterns refuse.
- **Safety.**
  - A synthetic guest_mode-shaped fixture (never the live helper) returns
    `confirmation_required` without the
    acknowledgement, with no dispatch and no task. It succeeds with the
    acknowledgement, and the audit records `acknowledged`,
    `model_conveyed_chat_confirmation` and `external_approval: false`.
  - An engineering_acceptance-shaped (opaque-only) fixture never warns.
  - Unavailable evidence proceeds with the note, including on the
    guest-shaped fixture.
  - Installed acceptance (section 4) contains no changing call on the live
    `input_boolean.guest_mode`.
  - Both shapes are also proven against the shipped dependency index and
    binding builder (`HelperRealEvidenceTests`).
- **Closed request.** Arbitrary service, domain, data, target, selector or
  list input is refused. The schema rejects it, and the authenticated gateway
  refuses it with no provider call. Every allowed pair dispatches only its
  fixed service and data keys.
- **At most once and truthful verification.**
  - Retry and timeout never re-dispatch.
  - An effect that never arrives ends as `verification_failed`.
  - A wrong effect, an unchanged button timestamp and a wrong counter step
    are never reported as success.
- **Metadata agreement.**
  - The descriptor, routing, capability metadata and provider matrix agree.
  - The existing value plans carry the superseded note, and the
    configuration plan does not.
- **Signed authority.** The current signed registry fixture does not admit
  the helper value family.
- **Mutation testing** in a copied tree, counting only assertion failures as
  kills:
  - treat opaque evidence as exact;
  - skip the readback;
  - allow a non-allowlisted service;
  - drop the no-op check;
  - restore the global empty-state refusal;
  - restore the step-count-scaled tolerance;
  - reintroduce a live changing guest_mode step in installed acceptance.
- **The complete repository suite** in the Evidence tier.

## 2. Release CI

The exact materialized head's `validate` check passes, including the
release transition.

## 3. Publication

Publication happens only through Josh's Ready action and the repository's
head-bound workflow.

## 4. Installed acceptance (separately authorized, after deploy)

This stage runs only after Josh separately authorizes deploying this
release.

**Precondition.** An owner-signed Core registry sequence that names
`core.typed_helper_value_operation` must be published and admitted, for
example an amended sequence 16 or a sequence 17. That is a separate owner
decision. Without it, every `control_helper` call returns
`provider_unavailable` (`helper_core_authority_unavailable`). If so, record
that and stop after step 2.

1. **Identity.** `server_info` and `get_server_health` report:
   - `2.4.2-beta.3`;
   - the merged release SHA as build SHA;
   - `dirty: false`.

   Health shows `ordinary_helper_value` as configured.
2. **Tool catalog.** A fresh `tools/list` shows the 2.4.2-beta.2 tools plus
   `control_helper`.
   - `create_helper_state_plan` and `create_input_select_option_plan` carry
     the superseded note.
   - Their descriptors are otherwise unchanged.
3. **Ordinary toggle, no warning.** The architect calls `control_helper` on
   `input_boolean.engineering_acceptance` three times: off → on → off. Use a
   new operation ID for each change. If it is not off at the start, add one
   `turn_off` first. Expect each call:
   - `status: succeeded_verified`;
   - `provider_attempt_count: 1`;
   - `safety.safety_critical: false`;
   - no `confirmation_required`;
   - any template readers listed only as information.

   Ask for the current value again: it returns `no_change` with no dispatch.
4. **Safety-critical target, read-only.** `input_boolean.guest_mode` is
   never a `control_helper` target in this acceptance: make no changing call
   on it, with or without `acknowledge_safety_critical`.
   - Read its state with `get_entity`.
   - Run `entity_dependency_analysis` for it. The exact automation references
     must include the front-door lock consumers. Read each one with
     `get_automation_config` and record its lock actions.
   - Read the state again: it must be unchanged.

   A call without the acknowledgement is **not** an unconditional
   no-dispatch guarantee. When dependency evidence is unavailable, the owner
   rule makes the change proceed with a note and no warning. A successful
   earlier read does not prove the evidence will be available for the next
   call. `confirmation_required` with no dispatch is proven offline on
   synthetic guest-shaped fixtures (section 1, **Safety**), not live.

5. **Audit.** `get_audit_log` shows step 3's rows as
   `typed_helper_value_lifecycle` `succeeded_verified`, and no
   `control_helper` row for `input_boolean.guest_mode`.

PASS needs every check. If a step fails, record the response in HAMCP-311.
Whether to roll back is Josh's decision.
