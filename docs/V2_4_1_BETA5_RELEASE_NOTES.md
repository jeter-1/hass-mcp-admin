# Engineering 2.4.1-beta.5 release notes

Engineering 2.4.1-beta.5 corrects HAMCP-168 defect A on top of beta.4: an
explicit deep-health scan (`get_server_health`) on an idle server could fail
with `execution_task_storage_error` (`health_snapshot_superseded`) because the
periodic F3 recovery sweep rewrote its historical recovery cursor inside the
fenced children namespace every 30 seconds. A scan that outlasted one sweep was
refused, silently, every time.

**A-1, bounded cursor deferral.** While a deep-health scan holds its fence, an
idle recovery sweep (one that processed no orphaned child) defers only its
historical recovery-cursor write. At most three consecutive fenced sweeps
defer (`RECOVERY_CURSOR_HEALTH_DEFERRAL_LIMIT`; any sweep with no scan
in flight restores the full bound); the next sweep advances the cursor
even under continuous health load, so historical recovery navigation still
progresses (about every two minutes in the worst case). Re-examining the same
idempotent page is the only cost. Real recovery writes are not deferred:
orphan terminalization, active-recovery checkpoints, lock recovery, child
updates, lock races and in-place file movement still supersede the scan and
fail closed exactly as before.

**A-3, diagnostic warning.** Every superseded refusal and every real
health-storage failure now emits a WARNING `governance_health_refused` log
event, rate-limited to one per 60 seconds per reason. Its context is bounded
to `reason` (`health_snapshot_superseded` or `storage_failure`), `error_code`,
`source_category` (`plans`, `tasks`, `f3_children`, `f3_locks` or `unknown`),
`phase`, `elapsed_ms`, rounded `phase_elapsed_ms`, `plan_count`, `task_count`
and, for real failures, `error_type` (exception class name only). It contains
no path, identifier, record content or exception text. The category comes
from provenance, never from the shared error code: the fence records which
fenced source moved or failed (including a failure of its own initial read),
a generation check records which repository advanced, a plan or task storage
error is attributed to the repository or scan stage that raised it, and an F3
storage error to its class. Anything else reports `unknown`. F3 storage
errors still propagate unconverted. The F3 storage classes stay behind the
reviewed `f3_runtime` import boundary.

The warning is delivered best-effort off the event loop: the refusal hands it
to one on-demand daemon worker through a queue of at most four warnings and
drops (and counts) any warning that does not fit. The health call returns its
original refusal and releases the fence without waiting for the log sink, so a
slow or stalled log handler cannot delay the refusal, the recovery heartbeat
or later health calls. A warning dropped while the sink is stalled is not
retried.

The public error code and message are unchanged
(`execution_task_storage_error`, "Execution-task storage is unavailable.").
No tool, schema, routing, admission, registry, persisted format, workflow or
stable-v1 change is included. The healthy catalog remains **84 tools: 59
native and 25 delegated reads**.

Not included: skipping no-information cursor writes (A-2), the long-phase
latency itself (the validation worker re-sanitizes every plan on every scan;
see the HAMCP-168 pull request for measurements and proposals), and the
delegated-read concurrency observation (defect B).

Downgrade: beta.4 reads every beta.5 record; nothing new is persisted. A
downgraded server returns to the beta.4 supersession behavior.

Follow the [beta.5 acceptance contract](V2_4_1_BETA5_ACCEPTANCE.md).
