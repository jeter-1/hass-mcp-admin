# Engineering 2.4.1-beta.19 release notes

Engineering 2.4.1-beta.19 stops typed fan and power tasks from staying
`observing` after a device that reports a few seconds late (HAMCP-192). It
applies Josh's decisions 1–6 to the defects in
[`HAMCP_192_OBSERVING_INVESTIGATION.md`](HAMCP_192_OBSERVING_INVESTIGATION.md).
The design is in
[`HAMCP_192_OBSERVING_FIX_DESIGN.md`](HAMCP_192_OBSERVING_FIX_DESIGN.md). It
builds on beta.18 (HAMCP-171, health performance), beta.17 (HAMCP-200,
typed Home Assistant routes) and beta.16 (HAMCP-162).

No tool, input schema, description, provider route, signed registry, HOLD,
support window, workflow or stable-v1 change is included. The tool catalog and
every public descriptor are unchanged from beta.18. No new
dispatch, service-call or fallback path is reachable. Acceptance:
[`V2_4_1_BETA19_ACCEPTANCE.md`](V2_4_1_BETA19_ACCEPTANCE.md).

**What was wrong (beta.15 and earlier, pre-existing since v2.3.0).** On
`light.back_guest_room_lamp`, a turn_on and a turn_off each returned
`observing` although the lamp changed about 2 s and 6 s after the provider
response. The cause:
- The service call is sent without waiting, then exactly one state read is
  made. Any device that reports more than about 1 s later leaves the task
  `observing`.
- The task keeps the `entity:<id>` EXCLUSIVE lock under a 120 s lease. Both
  the 30 s background sweep and a same-ID replay wait for that lease, so
  resolution took about 2.5 minutes.
- A new command on the same entity is refused as `lock_conflict` while the
  task observes, and the receipt did not say which task blocks it.
- The evidence deadline was checked before the read, so a device that
  reported just before the deadline could still go to manual review.
- A manual-review entity hold had no governed release.

## Changes

All changes are in the shared typed fan service. `control_power`
(`PowerService`) inherits every change from `control_fan` (`FanService`).

1. **Bounded inline poll (decision 1).** After dispatch, the task reads the
   entity at about 0, 1, 2, 4, 6, 8 and 10 s and stops at the first match:
   at most 7 reads in about 10 s plus one read's latency. A match returns
   `succeeded_verified` with the lock released. It is one observation attempt;
   `attempt_count` reports the reads. Every read is the existing delegated
   `ha_get_state` read. Recovery sweeps still make one read per pass.
2. **30 s entity lease (decision 2).** Typed fan and power locks use a 30 s
   lease renewed every 10 s (was 120 s). A task that yields `observing`
   is re-read by the first sweep after the lease expires, about 40–70 s
   after dispatch instead of about 150 s. The 120 s task claim is unchanged
   and is yielded when the task leaves `observing`.
3. **Deadline aligned with one final read (decision 3).** The typed services
   opt into a new `final_read_at_deadline` executor flag. At or after the
   180 s evidence deadline, one more read is made first: a match is
   `succeeded_verified`; a mismatch goes to manual review with the existing
   reason `post_dispatch_evidence_deadline_expired`. F3 governed executions
   do not set the flag and keep their deadline behaviour exactly.
4. **Blocking task named, no superseding (decision 4).** A new command on an
   entity held by another task is still refused as `lock_conflict`, with no
   superseding and no queueing. Its receipt now adds `blocking_tasks` (up to
   4: task ID, lock key, holder state, conflict-hold flag, expected release
   time, decision deadline and how it is released) and
   `blocking_tasks_observed_at`. The list uses the lock store's own
   acquisition predicate (`DurableLockStore.holder_blocks`), so it names
   exactly the holders that caused the refusal, including an expired lease
   that recovery has not yet reconciled. Each entry has a `lease_status`:
   `active`, `conflict_hold` or `expired_pending_recovery`. Expiry alone
   never frees a key, even between two shared holders; only task-aware
   recovery releases, transfers or holds it. This predicate is unchanged; it
   is only shared and documented.
5. **Additive receipt hints (decision 5).** Once dispatch intent exists, the
   receipt adds `observation_status`: why the task is still observing, the
   earliest re-read time, the evidence deadline, `final_read_at_deadline:
   true`, the observation attempts, and the last-observed and verified times.
   A manual-review receipt adds `hold_release`. These fields are derived when
   the receipt is built; nothing new is stored in the execution record.
6. **Governed hold release (decision 6).** A manual-review task's entity hold
   can be released through the add-on's Ingress panel at `/typed`, with the
   same external-approval pattern as the F3 reconciliation page. There is no
   MCP tool and no plan type.
   - Only an authenticated Ingress principal can open the page, through the
     same identity check as the existing approval pages; anything else is
     403 and causes no read. **H1 is deferred to HAMCP-205: the /typed pages
     share the existing pages' identity check; the system-wide administrator
     check lands in HAMCP-205.** No approval page checks the Home Assistant
     administrator role today. Practical exposure is near zero, because the
     one non-administrator account cannot see the panel.
   - The review page reads the device once and shows the **requested** state
     and the **freshly read** state side by side, field by field (`state`,
     plus `percentage` for fan), with `same` or `DIFFERENT` and a verdict
     line, above the release button (Josh's added requirement). If that read
     fails, the page is 503 with no release button.
   - The button carries a single-use token bound to the Ingress user, the
     task record, the prepared operation hash, the exact hold and the
     reviewed readback. A stale or reused token is 400.
   - The release makes one more read. If the state changed after the
     review, the release is refused (`<kind>_hold_release_state_changed`,
     409) and nothing changes; review again.
   - The release writes a durable sidecar
     `<namespace>/hold-releases/<task_id>.json`, releases only that hold
     through the same fenced primitive F3 uses, and writes one
     `<kind>_hold_release_action` audit entry with `dispatch: "none"`.
   - **Exactly one audit entry.** The entry's event ID derives from a
     stable `hold_release_id` (a digest of the authorized decision, not of
     the completion fields). The audit logger deduplicates by that ID, and
     the sidecar's `audit_recorded` is set only after a confirmed write.
     Completion is serialized and resumable: after a crash at any step, or a
     failed audit write, recovery finishes the release with exactly one
     audit entry. The receipt adds `hold_release_id` and `audit_recorded`.
   - **Truthful audit status.** `audit_recorded` is `true` only when an
     enabled audit logger confirmed the entry. With auditing disabled
     (`audit_enabled: false`) the release still completes, `audit_recorded`
     is `false` with `audit_unrecorded_reason: audit_disabled`, and nothing
     retries while auditing stays off. A failed write gives
     `audit_write_failed` and recovery retries it. When auditing is enabled
     again, the recovery sweep backfills the one missing entry for each such
     release (same event ID, original `released_at` timestamp) and then sets
     `audit_recorded`. The result page and the receipt show both fields.
   - "Exactly once" holds within the retained audit log (the live file and
     its `.1` rotation). A crash between the entry write and the flag
     update, followed by enough audit traffic to rotate the entry out of
     both files before recovery runs, re-emits the same event ID; external
     archives should deduplicate by `audit_event_id`. No hold is released
     twice and nothing is dispatched.
   - Release is allowed whether or not the read state matches the request
     (confirmed by Josh). The receipt says `hold_released_state_matches` or
     `hold_released_state_differs`. The task's terminal outcome stays
     `manual_review_required`; it is never relabeled verified.
   - Nothing is ever dispatched, and recovery never starts a release. A
     release interrupted after its approval was recorded is finished by
     recovery, without any provider access.

## Compatibility and rollback

- **Stored formats.** The declaration, execution record, lock record and
  audit formats are unchanged. The only addition is the hold-release sidecar
  (`typed-hold-release-v1`, closed fields including `audit_recorded` and `audit_unrecorded_reason`, ≤ 4 KiB, strictly validated; an
  invalid sidecar fails closed and keeps the hold).
- **Rollback** is reinstalling the beta.18 artifact. Beta.18 reads every
  record this release writes and ignores the sidecar directory. Its lease is
  120 s again. For a task whose hold was released, beta.18 finds no owned
  hold and counts one `recovery_failures` per sweep for that task; it never
  dispatches.
- **Receipts** gain only additive fields. `dispatched_at` stays the literal
  `null`, and the HAMCP-181 outcome strings and dispatch facts are unchanged.
- **HAMCP-200.** This release adds no Engineering REST or WebSocket call.
  Every state read goes through the reviewed delegated `ha_get_state` tool,
  which builds no Home Assistant URL, so the typed `Route` rules apply
  unchanged.
