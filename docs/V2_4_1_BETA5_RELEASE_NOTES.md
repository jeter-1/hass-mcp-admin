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

**Refusal diagnostics are deferred.** A refused scan still emits no
dedicated warning in this release. Engineering logging is synchronous on the
shared handler, so a refusal warning that cannot stall the event loop or
process shutdown needs a logging-isolation change first; the diagnostic will
return in that separate task.

The public error code, message, details and cause are unchanged
(`execution_task_storage_error`, "Execution-task storage is unavailable.").
No logging change and no tool, schema, routing, admission, registry, persisted format, workflow or
stable-v1 change is included. The healthy catalog remains **84 tools: 59
native and 25 delegated reads**.

Not included: the refusal warning (A-3, deferred to a logging-isolation
task), skipping no-information cursor writes (A-2), the long-phase
latency itself (the validation worker re-sanitizes every plan on every scan;
see the HAMCP-168 pull request for measurements and proposals), and the
delegated-read concurrency observation (defect B).

Downgrade: beta.4 reads every beta.5 record; nothing new is persisted. A
downgraded server returns to the beta.4 supersession behavior.

Follow the [beta.5 acceptance contract](V2_4_1_BETA5_ACCEPTANCE.md).
