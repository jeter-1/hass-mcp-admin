# Engineering 2.4.1-beta.54 release notes

Engineering 2.4.1-beta.54 (HAMCP-263) lets a governed dashboard update
that was dispatched but not yet verified reach a truthful terminal outcome
through one actual read-only readback. Nothing is dispatched again. It
also releases locks retained by a child that will never reconcile again,
which today block every later governed configuration apply. It builds on
2.4.1-beta.53 (#333, HAMCP-071), which builds on 2.4.1-beta.52. No tool,
input schema, route,
provider, persisted format, retention or Home Assistant write changes.

Acceptance: [`V2_4_1_BETA54_ACCEPTANCE.md`](V2_4_1_BETA54_ACCEPTANCE.md).

## Why

Live task d63e3a87 (an `update_dashboard` on `main-operations`) is
`manual_review_required` with `dispatched_task_has_no_readback_reconciler`.
Its child 0ee87ea8 has stayed `observing` since dispatch. Its three locks
(`dashboard:main-operations` exclusive, `home_assistant:core` shared,
`addon:ha_mcp` shared) are retained with expired leases, and F3 health
reports `recovering`. An expired holder still blocks, even a shared
request against a shared holder. Every configuration operation requests
`home_assistant:core` shared, so a reproduction shows an unrelated
automation update refused with `lock_conflict` and zero writes.

Root causes (none is a HAMCP-106/beta.50 regression):

- **A. The readback binding never resolved for dashboards.** Readback-only
  recovery revalidates the persisted provider binding, which reads
  `provider_operation` from the prepared operation.
  `PreparedDashboardOperation` had no such attribute, so every dashboard
  readback recovery raised `POLICY_SNAPSHOT_MISMATCH` and retried.
- **B. The generic governance sweep pre-empted F3.** Its last branch marks
  any dispatched, nonterminal task `dispatched_task_has_no_readback_reconciler`
  in the same 30 s pass in which F3 recovery is still waiting for the
  apply lease.
- **C. Nothing settles a post-intent child of a terminal parent.** Active
  recovery skips terminal parents, and the historical scan only handled
  children with no dispatch or no intent.
- **D. Expired locks of a terminal post-intent child were never released.**
- The apply lease (120 s) equals the dashboard evidence window (120 s), so
  recovery can take over only at the deadline. Without a final read it
  ends in manual review without reading.

## The change

- **A.** `PreparedDashboardOperation.provider_operation` is a class
  constant (`ha_config_set_dashboard`). It is not a dataclass field, so
  the prepared-operation hash and every persisted record are unchanged.
- **B.** The sweep leaves an F3 task pending while one of its children
  holds a durable dispatch intent. The task's
  `maximum_post_dispatch_deadline` still bounds it. A runtime that cannot
  answer also leaves it pending, bounded by the same deadline. The parent
  then projects F3's own terminal outcome.
- **Dashboard final read.** Dashboard readback recovery performs one final
  read at the expired evidence deadline (`final_read_at_deadline`, as fan
  and power already do). An exact match verifies, a mismatch fails
  verification, and anything else is manual review with
  `observation_evidence_exhausted`.
- **C.** The historical scan collects a nonterminal post-intent
  dashboard child whose F3 parent is terminal. It runs exactly one
  read-only readback through the durable-intent recovery path, with
  dispatch unreachable and the exact prepared binding revalidated. The
  child reaches a truthful terminal state and its locks are released
  rather than held, because nothing will ever reconcile it again. The
  parent record is never read for projection or rewritten; acknowledging
  its outcome is HAMCP-253. A binding or preparation failure keeps a
  bounded retry (5 to 300 s) with a readiness fault and never terminalizes
  without a read. Other adapters are not collected (see "Other families").
- **D.** The expired-lock pass releases a lock whose owner child is
  terminal with a dispatch intent and a known outcome. For an unresolved
  manual-review owner it releases only keys outside that child's declared
  selective hold keys, so the target key stays held fail-closed. An
  active lease is never expired, conflict holds are never released, and
  the owner must match the lock's exact execution authority, so live-owner
  exclusivity is unchanged.
- **Crash-safe cleanup.** Before C's readback can terminalize the child,
  recovery durably marks it `terminal_parent_child_cleanup_pending` in the
  existing runtime field, and clears the marker only after the child's
  last exact-owner lock is released. If a release fails or the process
  stops anywhere in between, a later sweep (including the first after a
  restart) finishes the release without another read, dispatch or parent
  rewrite. Until then the child counts as pending reconciliation, so F3
  never reports the cleanup settled while a lock remains.

At-most-once dispatch, approval binding and the persisted formats are
unchanged.

## Other families (reported, not fixed)

The binding resolves for every other F3 family (configuration through
`provider_descriptor`, operational through direct fields). The same
"lease equals evidence window" gap remains for every family with a 120 s
evidence window:

- all configuration families (automation, script, input_boolean,
  input_number, entity metadata/availability/exposure, labels, delete,
  helper options, dashboard metadata, and the beta.53 scene configuration
  edit, which uses the same configuration adapter);
- operational `set_input_boolean_state` and `set_input_select_option`.

Enabling a final read is not enough for configuration families: their
adapter's own `recover` returns manual review past the deadline without
reading. Backup, reload and restarts have windows of 900 s or more, so
they have no gap. Fan and power use their own executor with a 30 s lease
and a final read, so they have no gap either.

## Rollback

Reinstall 2.4.1-beta.53. No persisted format changed. Children settled by
this release stay settled; rolling back does not reopen them. A child
whose cleanup is still pending at rollback keeps its remaining locks
under beta.53; reinstalling beta.54 resumes the cleanup.
