# Engineering 2.4.1-beta.56 release notes

Engineering 2.4.1-beta.56 (HAMCP-271) extends the HAMCP-263 settlement
of stranded post-intent children from dashboards to every other F3
family with a 120 s evidence window: all configuration families and the
operational `set_input_boolean_state` and `set_input_select_option`. A
child of an already-terminal parent gets one read-only readback, reaches
a truthful terminal outcome and has its expired locks released. Nothing
is dispatched again and the parent is never changed. It builds on
2.4.1-beta.55 (#337, HAMCP-227), which includes 2.4.1-beta.54
(#339, HAMCP-263). No tool, input schema, route, provider,
persisted format, retention or Home Assistant write changes.

Acceptance: [`V2_4_1_BETA56_ACCEPTANCE.md`](V2_4_1_BETA56_ACCEPTANCE.md).

## Why

The beta.54 release notes reported a gap that it left open. Every F3
family has a 120 s apply lease. When the evidence window is also 120 s,
readback recovery can take over only at the deadline. For these
families, a dispatched child whose parent became terminal stayed
`observing` and kept its expired locks, including the shared
`home_assistant:core` lock. An expired holder blocks even a shared
request, and every configuration operation requests that lock. As a
result:

- every later governed configuration apply was refused with
  `lock_conflict` and zero writes;
- F3 health stayed `recovering`.

Reproductions on the beta.54 source confirm this for script update,
`set_input_boolean_state` and `set_input_select_option`. In each, an
unrelated governed apply is refused.

There was a second gap. Past its deadline, the configuration adapter's
own `recover` returned manual review without reading. Enabling the
executor's final read alone would therefore not have produced a read.

## The change

The change generalizes the HAMCP-263 mechanism. It does not fork it.

- **One family table.** `_terminal_parent_readback_operations` lists the
  eligible families:
  - `dashboard_update` with `update_dashboard`;
  - `configuration_operation`. This covers every capability the closed
    registry assigns to that adapter, with a configuration plan operation:
    - automation, script, `input_boolean` and `input_number` create and
      update;
    - automation and script delete;
    - helper options;
    - entity registry metadata, availability and assistant exposure;
    - labels;
    - dashboard metadata;
    - scene configuration edit;
  - `operational_administration`, limited to `set_exact_input_boolean_state`
    with `set_input_boolean_state` and `set_exact_input_select_option`
    with `set_input_select_option`.

  One predicate, `_terminal_parent_readback_family`, replaces each of
  the dashboard-only gates of beta.54 and beta.55:
  - the final read at the deadline;
  - the historical-scan collection;
  - the cleanup-marker resume;
  - the readback recovery gate;
  - the cleanup finish.

  The predicate requires three things to agree with one listed family:
  the durable child's adapter, the adapter the declaration registers, and
  (when known) the plan operation.
- **Final read for configuration.** The configuration adapter's `recover`
  now always performs its one exact `_observe`.
  - Past the deadline, a complete observation is decided by exact
    verification: a match is verified and a mismatch fails verification.
  - Past the deadline, an incomplete observation is manual review. Its
    diagnostics are the read's own codes plus
    `post_dispatch_evidence_deadline_expired`. No further read is
    attempted.
  - Delete and helper options inherit this `recover`. Each keeps its own
    exact readback: absence for delete, the options entry for helper
    options.
  - The operational adapter already reads once in `recover`, so it
    needed no change.
- **Unchanged from beta.55 (introduced in beta.54):**
  - the durable cleanup marker `terminal_parent_child_cleanup_pending`;
  - the readback result `terminal_parent_child_readback_settled`;
  - exact-owner lock release;
  - release of expired terminal owners only;
  - target-key retention for an unresolved manual-review owner;
  - the bounded retry on a binding failure;
  - at-most-once dispatch;
  - approval binding.

### Truthful outcomes per family

| Family | Readback | Match | Mismatch | Unavailable |
| --- | --- | --- | --- | --- |
| Configuration (create/update) | exact config + resource identity + configuration check | `succeeded_verified` | verification failure | `manual_review_required` (`adapter_recovery_requires_manual_review`; diagnostics include the read code and `post_dispatch_evidence_deadline_expired`) |
| Configuration delete | absence of config, registry entry and state | verified | verification failure | as above |
| Helper options | options entry `modified_at` and `loaded` | verified | verification failure | as above |
| `set_input_boolean_state` / `set_input_select_option` | entity state only (attributes are not compared) | verified | verification failure | `manual_review_required` (`adapter_recovery_requires_manual_review`; `evidence_deadline_expired`) |

### Operator-visible contract change

Past the deadline, an operator `reconcile_child(rerun_observation)` on
a configuration child now performs one final read instead of returning
manual review without reading. A readable target that matches therefore
verifies. An unreadable one stays in manual review and keeps its hold.

## Families not changed

- **Backup, reload, add-on restart and Core restart.** Their evidence
  windows are 86 400 s, 900 s, 1 800 s and 1 800 s, far longer than the
  120 s lease, so active recovery takes over long before the deadline.
  They are never eligible for terminal-parent readback.
- **Fan and power.** They use their own executor, with a 30 s lease and
  a final read, and are not in the F3 registry.
- **Governed rollback (HAMCP-135 inverse plans).** A rollback plan's
  post-intent children keep their existing recovery: past the deadline
  they go to manual review without a read, and their bundle locks stay
  conflict-held. Eligibility rejects any plan carrying inverse markers.
  When a scan has no plan in hand, it decides from the exact durable
  plan, and an unreadable plan is never eligible.

## Rollback

Reinstall 2.4.1-beta.55. No persisted format changed. Children settled
by this release stay settled; rolling back does not reopen them. A
non-dashboard child whose cleanup is still pending at rollback keeps its
remaining locks under beta.55. Reinstalling beta.56 resumes that cleanup.
