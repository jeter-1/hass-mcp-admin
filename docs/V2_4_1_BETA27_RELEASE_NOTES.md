# Engineering 2.4.1-beta.27 release notes

Engineering 2.4.1-beta.27 is a break-fix for Home Assistant Core authority
(HAMCP-215). It fixes two defects:

- Core authority now **recovers automatically** after a transient verification
  failure.
- Every failure, retirement, catalog withdrawal, recovery attempt and
  restoration is now **logged and visible in health**.

Fail-closed behavior during uncertainty is unchanged.

Design and the Part 1 root cause (every retirement path, with reproductions):
[`HAMCP_215_CORE_AUTHORITY_RECOVERY_DESIGN.md`](HAMCP_215_CORE_AUTHORITY_RECOVERY_DESIGN.md).
Acceptance: [`V2_4_1_BETA27_ACCEPTANCE.md`](V2_4_1_BETA27_ACCEPTANCE.md).

beta.27 follows 2.4.1-beta.26 (HAMCP-217, `certifi` 2026.10.19) and includes
everything in it. It changes no dependency. No tool, input
schema, description, signed registry, HOLD, support window, workflow or
stable-v1 change is included. The tool catalog is unchanged: 88 tools, or 89
with the optional YAML reader admitted.

**What happened (beta.24 live, 2026-10-20).** One verification failed out of 27
attempts. Core authority was retired, 29 Core-backed tools were withdrawn
(the YAML reader withheld `core_unavailable`), and the dependency index was
invalidated. Nothing was written to the add-on log.

- **Why it retired.** A single whole-snapshot transient retires the
  generation. That can be a REST `/config` or WebSocket timeout, a connection
  loss, an authentication failure, or a lifecycle-watcher socket move. This is
  the designed fail-closed behavior, and it is kept.
- **Why it did not visibly recover.** The only retry was a fixed 300 s
  `failure_retry`, with no backoff and no visibility. The counters show the
  failure was the last of 27 attempts. That it came shortly before the 17:49Z
  snapshot, so that authority had been withdrawn for minutes rather than about
  2 hours, is an **inference** from the counters, not an observation: no
  failure timestamp was recorded, and the reprobe counter also counts
  event-driven wakes. The pending 300 s retry gave no sign (design Part 1,
  finding 3).
- **Why it was silent.** The Core-authority code had no logger calls. Outcomes
  went only to the audit file, and the transport cause was discarded.

## Changes

1. **Automatic recovery with bounded backoff.** A failed verification is
   retried after 20 s, then 40, 80, 160, and 240 s thereafter. Each delay has
   ±20% jitter and never exceeds the cap. It replaces the fixed 300 s retry.
2. **Restore only after complete re-verification, with hysteresis.**
   - **One restoration gate.** Whenever no Core authority is admitted, for
     any reason, authority returns only after **2 consecutive complete
     background verifications** by the supervisor with the same Core identity,
     version, selected signed-registry authority and admitted reviewed
     contracts. This covers a transient retirement, an incompatible or
     unavailable Core, a Core not initialized at boot, a changed Core version
     and a moved signed-registry authority. A complete verification is a
     two-snapshot stable observation with the authenticated lifecycle watcher
     attached, evaluated against the reviewed contract.
   - **One-shot boot exception.** The only exemption is the first admission
     of the process's boot sequence, and it is a one-shot latch: it applies
     only while this process has never admitted Core authority, never
     retired one and never failed a verification. Any admission, failure,
     retirement or non-boot check spends it, and nothing but a new process
     re-arms it. A re-initialization of the running application (application
     rebuild, runtime reconfiguration, collector restart) therefore restores
     through the full gate. A failed first startup check ends the boot
     sequence's retries; the supervisor then recovers on backoff pacing
     (about 20 s, then the confirmations).
   - **Spacing.** The verifications are about 15 s apart. The count restarts
     on any failure or connection move, and on **any** observation, background
     or on demand, of a different Core identity, version, signed-registry
     authority or reviewed contract set.
   - **Flapping.** A retirement within 10 minutes of a restoration needs
     **3** verifications, at doubled spacing.
   - **On-demand verification.** A verification triggered on demand (mutation
     pre-dispatch or verification, configuration reverification, the fan
     authority, any tool or health caller) never confirms or restores, from any
     state. It is refused without publishing and counted in
     `recovery_on_demand_refusals`. It can **reset** the confirmation count
     (when it observes a changed key, as above) but never **add** to it.
3. **Incompatibility stays retired.** A complete observation that no reviewed
   contract admits stays withdrawn (`recovery_state: incompatible`). It is
   re-checked every reconciliation interval (300 s) without backoff. When it
   becomes compatible, it passes the same gate.
4. **WARNING logs.** The `core_authority` logger emits one WARNING event for
   each of these:
   - a verification failure, with a bounded reason and transport detail such
     as `core_probe_timeout` or `core_lifecycle_monitor_attach_failed`
   - a retirement
   - a partial capability withdrawal
   - a catalog withdrawal and a catalog restoration (with tool counts)
   - a recovery attempt
   - a confirmation and a confirmation reset
   - a restoration (with the outage duration)

   Each scheduled retry, and each refused on-demand verification, is logged
   at INFO with its time. Every event carries
   an ISO-8601 UTC `at`. No exception text, URL or token is logged.
5. **Health recovery fields (additive).** `get_server_health` →
   `home_assistant_core_authority.recovery` reports:
   - `recovery_state`: one of `initializing`, `stable`, `retrying`,
     `backing_off`, `confirming`, `restored`, `incompatible`
   - `consecutive_failures`
   - `last_failure_reason`, `last_failure_detail`, `last_failure_at`
   - `retired_at`, `last_attempt_at`, `next_recovery_attempt_at`,
     `retry_delay_seconds`
   - `confirmations`, `confirmations_required`
   - `last_restored_at`

   Four new counters are added: `recovery_attempts`,
   `recovery_confirmations` (accepted background confirmations only),
   `recovery_on_demand_refusals` and `authority_restorations`. Existing
   counters are unchanged.
6. **Dependents resume.** Restoration publishes a new generation through the
   unchanged path:
   - the gateway restores every withheld delegated tool and the YAML reader
   - the dependency index rebuilds against the new generation
   - typed providers lease the new generation

## Accepted behavior change (owner decision)

Every lifecycle-socket movement now waits one confirmation interval (about
15–18 s) before Core authority returns. This includes the reconnect after a
Home Assistant Core update or restart. Previously the immediate reprobe
restored authority at once. During that wait, Core-backed tools are withheld
and fail closed, exactly as during any outage. This is the hysteresis the
HAMCP-215 brief requires. **Josh accepted this delay on 2026-10-20.** After a
Core restart or update, expect Core-backed tools to refuse for about 15–18 s
after Core is reachable again, with health showing `recovery_state:
confirming`, and then to resume without restarting Engineering.

The same gate applies to a changed Core version, a moved signed-registry
authority for the running Core, an incompatible Core that becomes compatible,
and a Core not initialized at boot. Each needs the same background
confirmations (about 15 s), and tools refuse until they complete. **Josh
accepted these additional cases on 2026-10-20 as well.** A Core not
initialized at boot first waits one backoff delay (about 20 s), because a
failed startup check spends the one-shot boot exception. A re-initialization
of the running application also passes the gate.

## Compatibility and rollback

- **Stored formats.** No stored format changes. Recovery state is in memory
  only and starts at `initializing` on each process start.
- **Health.** The `recovery` object and the four counters are additive.
- **Audit.** Audit events are unchanged in shape. Logging is additive.
- **Rollback** is reinstalling the beta.26 artifact. That returns to the fixed
  300 s retry and silent retirement, and keeps `certifi` 2026.10.19. Do not
  roll back past beta.26: beta.25 carries the vulnerable `certifi` 2026.7.22.
  No state needs migration.
- **Boundaries.** No write, service call, reload, restart or fallback becomes
  reachable. No route or lease is admitted while recovery is active.
