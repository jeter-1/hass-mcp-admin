# Engineering 2.4.2-beta.6 release notes

Engineering 2.4.2-beta.6 bundles three Low fixes. It builds on 2.4.2-beta.5
(HAMCP-318, #376).

- **HAMCP-304.** Prohibited operational plans and stored approval kinds now
  project correctly in governance inspection and health.
- **HAMCP-305.** The repository secret scan no longer flags the ADR-012
  filename.
- **HAMCP-312.** Two superseded reviewer probes are rewritten to the owner
  decisions, so neither runs as an expected failure any more.

Only HAMCP-304 changes the runtime.

Acceptance: [`V2_4_2_BETA6_ACCEPTANCE.md`](V2_4_2_BETA6_ACCEPTANCE.md).
Per-release records: [release index](RELEASES.md).

## HAMCP-304: prohibited operational plans and approval kind

### What was observed

The architect inspected stored plans read-only with
`inspect_governance_record`. Plans `2ab4764f`, `faba2fbd` (both
`set_input_boolean_state`) and `4cf748d1` (`set_input_select_option`) shared
one shape:
- contract v3 operational administration plans;
- `policy_class: prohibited`;
- one creation event and no approval events;
- nothing ever dispatched.

All three showed:
- `approval_kind: unrecognized_value`;
- `projection_failed: true`, counted as approval-sequence failures in health.

Expired elevated plan `d3af624a` also showed `unrecognized_value`, without a
projection failure. That led to the working hypothesis that the expiry or
invalidation path lost or rewrote the stored approval kind.

### The cause

**The expiry hypothesis was wrong.** Expiry and invalidation never touched
the kind: every one of these plans still stores `approval_kind: apply`. The
fixtures reproduce this with the shipped 2.4.2-beta.3 writer. There were two
independent defects, both at 2.4.2-beta.3 and carried unchanged through
2.4.2-beta.5:

1. **Inspection vocabulary.**
   - `governance/record_inspection.py` screened the stored
     `approval.approval_kind` against `ApprovalActionKind`. That enum names
     approval *action* records, not the kind of approval a plan awaits.
   - A plan stores `apply` or `rollback`, neither of which is in that enum.
   - So every plan with an approval record showed `unrecognized_value`,
     whatever its status.
2. **Prohibited-shape check.**
   - `ChangeGovernanceService._prohibited_plan_has_execution_evidence`
     accepted only `change_plan_created` as a prohibited plan's first and
     only creation event.
   - Every contract-v3 operational plan's create path records its own
     operation's event instead, for example
     `set_input_boolean_state_plan_created`.
   - So a never-approvable prohibited operational plan read as carrying
     execution evidence. It failed projection with
     `approval_sequence_inconsistent` as soon as it was stored, and
     `approve` refused it with `approval_sequence_failure` rather than
     `prohibited_change`.

The category was misleading, not unsafe: no such plan was ever approvable
or dispatched.

### The change

- Inspection screens the stored kind against the plan's own vocabulary,
  `apply` and `rollback`. Any other stored value still shows the
  unrecognized marker.
- The prohibited-shape check accepts exactly one creation event. For a
  contract-v3 operational administration plan, that is the event its own
  operation's create path records
  (`OPERATIONAL_PLAN_CREATION_EVENTS`, one per operation). For every other
  plan, it is still `change_plan_created`. Other operations' creation
  events, unrelated `*_plan_created` events, a second creation event, and
  any other evidence still fail the check.
- So those plans now:
  - project as terminal prohibited plans with no projection failure;
  - are excluded from health's projection failures;
  - show `approval_kind: apply`.
- `approve` refuses them with `prohibited_change`. `apply` is still
  refused, and nothing is dispatched.

### Existing acknowledgements

The record acknowledgement ledger (HAMCP-253) binds the view fingerprint
that was reviewed. The corrected view changes the fingerprint of these
plans. Without a fallback, every acknowledgement made before this release
would turn stale.

- **Fallback.** When a stored acknowledgement does not match the current
  fingerprint, the server recomputes the plan's view exactly as 2.4.2-beta.3
  through 2.4.2-beta.5 rendered it and compares again.
  - If the stored record is unchanged, that fingerprint matches, and the
    acknowledgement stays acknowledged.
  - If the record has changed since, neither fingerprint matches, and the
    acknowledgement is stale, as before.
- **Bounds.**
  - The fallback runs only on a mismatch, and only for plans.
  - It is never shown and never stored.
  - New acknowledgements must bind the current fingerprint. One submitted
    against that earlier fingerprint is refused as
    `record_fingerprint_stale`.

## HAMCP-305: secret scan and the ADR-012 filename

`scripts/check.ps1` matched `sk-[A-Za-z0-9_-]{20,}` case-insensitively, so
`RISK-AND-ELEVATED-APPROVAL` in the ADR-012 filename read as an API key. The
pattern now:
- is case-sensitive;
- starts at a token boundary;
- requires a digit or capital letter in the key body.

Lowercase kebab-case anchors and the filename no longer match, while real
`sk-` keys are still rejected. This is a development tool, not runtime.

## HAMCP-312: superseded reviewer probes

Two reviewer methods expected behavior that the owner had decided against.
They ran as registered expected failures:
- `test_original_control_text_is_screened_before_ansi_removal` (HAMCP-224
  host journal: a coloured ordinary line is shown);
- `test_partial_internal_intent_never_submitted` (HAMCP-232: an incomplete
  internal options intent is refused at create).

Each is rewritten to assert the owner decision and now passes. The guard
modules re-pin both suites by hash, and fail if any reviewer method becomes
an expected failure again. The repository keeps five accepted expected
failures:
- three in `test_hamcp181_beta8_downgrade`;
- two registered by `test_pr314_delta_cleanup_accepted`.

## Unchanged by design

- Approval, apply and dispatch rules for every plan, including prohibited
  plans, which stay never approvable.
- Persisted plan, task, approval and acknowledgement formats. No stored
  record is rewritten.
- Tool and input schemas, providers, signed profiles, contract fingerprints
  and add-on options.

Static tools stay at 81. The advertised catalog stays at 109, or 110 with
the optional YAML reader.

## Known limitations

- **Fallback metric.** Recomputing the pre-fix view of an acknowledged
  prohibited operational plan reruns that release's prohibited-shape check.
  This may record one classified `approval_sequence_failure` metric each
  time. The displayed view, the health projection failures and the
  acknowledgement state are not affected.
- **Redaction pattern.** `scripts/pr-evidence.py` redacts with
  `sk-[A-Za-z0-9_-]{16,}`, which is already case-sensitive. It is
  deliberately left unchanged: redacting a little too much is safe.
- The known issues carried by 2.4.2-beta.5 are not addressed here.

## Rollback

Reinstall 2.4.2-beta.5. It has the same storage, plan, task and ledger
formats. The four plans show `unrecognized_value` and the three projection
failures again. An acknowledgement made under 2.4.2-beta.6 on a plan whose
view changed binds the corrected fingerprint, so 2.4.2-beta.5 shows it as
stale. An image rollback
does not restore household configuration or physical state.
