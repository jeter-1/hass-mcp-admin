# HAMCP-215: Core authority recovery and visibility

Status: implementation design for a break-fix beta. The owner direction
(ADR-022) for this task is: functionality and break-fix first; fail-closed
behavior during uncertainty stays; the defects are (1) no visible recovery and
(2) silence.

## Part 1: root cause

### Live evidence (beta.24, 2026-10-20, owner-reported)

| Field | Value |
|---|---|
| verification attempts / successes / failures | 27 / 26 / 1 |
| lease attempts / failures | 11 / 2 |
| retirements | 1 |
| Core disposition | unavailable; `initialized` false; `observed_core_version` and `current_generation` null |
| catalog | 29 delegated tools withdrawn, 0 restorations; `ha_config_get_yaml` withheld `core_unavailable`; dependency index invalidated `core_authority_changed` |
| add-on log | no warning or error |

### Every path that retires Core authority or withdraws the catalog

Line numbers refer to base `e42fa15` (2.4.1-beta.25).

| # | Path | Trigger |
|---|---|---|
| R1 | `ha_core_readmission/runtime.py:175-222` `request_reconciliation(connection_changed=True)` | retires the published generation (`:188`), clears `_observation`, `_initialized`; notifies listeners only if an observation existed (`:203`) |
| R1a | `runtime.py:258-269`, caller of R1 | any verification whose observation is not connected, authenticated, stable and versioned (`runtime.py:476-482`) or whose lifecycle watcher cannot attach within 30 s (`runtime.py:504-517`) |
| R1b | `runtime.py:536-574`, caller of R1 | the attached lifecycle WebSocket delivers any frame, closes, or errors (`source.py` `wait_for_connection_change`) |
| R2 | `runtime.py:576-591` `_retire_observed_version_change` | registry enabled and the observed Core version differs from the published one |
| R3 | `runtime.py:593-615` `_sync_registry_authority` | registry enabled and its selection token moved (expiry, revocation, refresh) |
| R4 | `coordinator.py:143-205` `begin_reconciliation` | any new material fingerprint retires the prior generation before publishing the new one (`:183-186`) |
| C1 | `providers/upstream_read_gateway.py:5176` `request_core_reconciliation` (listener registered at `application.py:351`) | each listener call bumps the gateway Core epoch; the gateway rebuild consults `CoreRuntime.route_status` and withholds every Core-dependent delegated tool (`upstream_read_gateway.py:1408-1450`, `:1550-1600`) and counts the delta (`:2336-2350`) |
| C2 | `dependency/runtime.py:56` listener | invalidates the dependency index (`core_authority_changed`) |

The observation collector turns any snapshot exception into one generic
`core_observation_unavailable` (`observation.py:314-321`), so the exact
transport cause was discarded before this change.

### Reproductions on unmodified base code

Script `repro_part1.py` (kept outside the repository) drives the real
`AiohttpCoreSnapshotSource`, observation collector, coordinator and lifecycle
watcher. It replaces only the network sessions and the supervisor retry clock,
using the existing `tests/test_rc3_postcore_regressions.py` harness. It ran 12
tests, all OK. Each row also asserts that no log record of any level was
emitted.

| Transient trigger | Retires authority? | Supervisor retry delays (s) | Recovers? |
|---|---|---|---|
| REST `/config` timeout | yes (R1a) | `[300]` | yes, after 300 s |
| REST `/config` HTTP 503 | yes (R1a) | `[300]` | yes, after 300 s |
| REST/WS connection loss | yes (R1a) | `[300]` | yes, after 300 s |
| WS `auth_invalid` | yes (R1a) | `[300]` | yes, after 300 s |
| WS `get_config` timeout | yes (R1a) | `[300]` | yes, after 300 s |
| one WS command timeout | partial: one capability withheld for one generation (R4); the reconnect absorbs it | none | yes, on the next periodic run |
| two WS command timeouts (reconnect exhausted) | yes (R1a) | `[300]` | yes, after 300 s |
| lifecycle socket closes, HA healthy | yes (R1b) | none (immediate reprobe) | yes, immediately |
| lifecycle socket closes, and the reprobe also hits the transient | yes (R1b + R1a) | `[300]` | yes, after 300 s |
| lifecycle watcher re-attach timeout | yes (R1a) | `[300]` | yes, after 300 s |
| watcher fires during a verification (connection-fence race) | yes (R1b; attempt refused `core_connection_generation_stale`) | `[300]` | yes, after 300 s |
| slow REST response that completes within `ha_timeout_seconds` | no; authority stays published while slow | none | n/a |
| slow response beyond the timeout | same as the REST timeout row | `[300]` | yes, after 300 s |
| release-registry fetch failure (enabled, cache empty) | no; `refresh` catches every error and returns False (`ha_mcp_readmission/registry.py:641-676`), and `aiohttp` total/connect timeouts bound it (`:770`) | none | n/a |
| lease contention (80 concurrent acquires) | no; `acquire` counts `lease_failures` (+16 at the 64-lease cap) and never retires | none | n/a |

### Findings

1. **Which triggers retire.** Any transport-level transient that makes one
   whole snapshot fail retires the entire Core generation (R1a): REST
   `/config`, the WS handshake or `get_config`, or a second command timeout.
   So does any movement of the lifecycle watcher socket (R1b), for example a
   Supervisor proxy blip or a missed heartbeat during an event-loop stall. This
   is the designed fail-closed behavior, and it is kept. Failures of
   `/states`, `/services`, `check_config` and a single command timeout stay
   capability-local. Lease contention and registry fetch failures never retire.
2. **Re-initialization exists, but it is slow, fixed and invisible.** Every
   retirement path ends in `supervise()`'s single `failure_retry` branch
   (`runtime.py:433-441`), which sleeps a fixed
   `CORE_RECONCILIATION_INTERVAL_SECONDS` (300 s). It cannot be woken early,
   has no backoff or jitter, and does not record when the next attempt runs. A
   persistent failure retries every 300 s forever, silently.
3. **Which state the live health shows, and why it had not recovered.** The
   independent review of `4e677ec` confirmed that base already retried
   automatically (finding 2). The live snapshot is consistent with that, and
   identifies one exact state:
   - `initialized` false with `observed_core_version` and `current_generation`
     both null is produced only by R1 (`runtime.py:188-192` clears the
     generation, `_observation` and `_initialized`). A published unavailable or
     incompatible generation would show `initialized` true and a generation
     (`runtime.py:313-319`). R3 (`runtime.py:593-615`) keeps the observation, so
     the version would not be null. R2 needs the registry and a changed Core
     version, and HA did not restart.
   - One failure and one retirement with no further attempt is the R1a
     early-failure path: a verification fails (or the lifecycle watcher cannot
     re-attach), `runtime.py:261-269` calls R1 (retirements +1,
     `runtime.py:194-195`) and `_audit_early_failure` (failures +1,
     `runtime.py:372`). R1b followed by a failed reprobe ends in the same state.
     So does the connection-fence race (`runtime.py:296-303`).
   - Attempts 27 = successes 26 + failures 1, so no attempt was in flight. Base
     successes count only admitting publications (`runtime.py:321-328`), so
     every attempt before the last one admitted.
   - Reprobes 26 = attempts − 1 startup (`runtime.py:240-241`). Base counts
     every non-startup trigger as a reprobe, including event-driven wakes
     (`identity_or_connection_change`) and on-demand checks, so the counter
     does not prove 26 periodic intervals. If all 26 were periodic, they would
     span about 7,800 s, consistent with the reported uptime of about
     2 h 13 min (7,980 s); that the supervisor ran on schedule is an
     **inference**, not an observation. Supervisor death is excluded:
     `application.py:526-560` runs it under `FIRST_COMPLETED`, so its exit
     would have ended the server.
   - The failed attempt was the last one (the counters establish this). When
     it happened is **not observed**: no failure timestamp was recorded. Only
     under the all-periodic inference above would it fall about 2–8 minutes
     before the 17:49Z snapshot (no earlier than the 25th periodic interval,
     about 17:41Z); event-driven wakes would place it earlier. The register's
     "between 15:43Z and 17:49Z" is the window in which the failure could
     have happened, not a measured outage length.
   - While the failure persisted, base retried every 300 s (`failure_retry`,
     `runtime.py:436-438`, `:450-452`), each retry adding one attempt. Nothing
     records whether a retry was due, ran or failed before the owner restarted
     the add-on at about 17:55Z.
   - **Conclusion.** The counters do not support about 2 h of *failing
     verifications*: those would show about 24 failures at a 300 s retry, not
     1. That authority had been withdrawn for only minutes at discovery is an
     inference from the counters, not an observation. It looked permanent
     because base gave no signal at
     all: no log (base `runtime.py` has no logger call), no next-attempt time,
     no recovery state, and a flat 300 s wait. The exact transport cause stays
     unknown: the reason code went only to the audit file, and the exception
     class was discarded (`observation.py:318-320`).
   - This change makes the same state visible and short.
     `test_live_incident_state_recovers_automatically` boots the production
     source and watcher with the production delegated catalog (the reviewed
     ha-mcp 8.5.0 capture) and the dependency index attached. It runs 25
     successful event-driven supervisor wakes, then one failed wake (as a
     failed probe, and as a socket close followed by a failed reprobe). It
     asserts the live health counters (`initialized` false, version and
     generation null, attempts 27, successes 26, failures 1, retirements 1,
     reprobes 26, catalog withdrawals 29 with the YAML reader among them,
     catalog restorations 0, authority restorations 0) and an invalidated
     index (`core_authority_changed`). It does **not** reproduce lease
     attempts and failures (11/2), which came from live tool calls, nor the
     live timing (its wakes are not 300 s apart). The 29 matches the live
     count but does not establish which ha-mcp version ran live. It then
     shows the supervisor recovering
     automatically through the restoration gate: a 20 s backoff, then two
     complete background confirmations about 16.5 s apart, with
     `recovery_state`, the failure reason and `next_recovery_attempt_at`
     visible throughout, and the gateway restoring all 29 tools.
   - The pre-counter section of `reconcile_once` (the reconciliation lock,
     `refresh_if_due`, `_sync_registry_authority`) cannot block indefinitely:
     the registry is disabled by default, or bounded by the timeouts above. A
     hang inside an attempt would leave attempts above successes + failures.
4. **Why nothing logged.**
   - `ha_core_readmission/` and the gateway's Core wiring contain no logger
     calls at all.
   - Reconciliation outcomes go only to the audit sink, the audit file
     (`runtime.py:366-394`, `:924-962`).
   - Connection- and monitor-driven retirement (R1) writes no audit event
     either; it only appends to the in-memory `recent_events` ring
     (`runtime.py:197-201`).
   - Public health exposes counters only.
5. **Stop condition.** The evidence shows no genuine Core incompatibility: the
   26 successes ran against the same Core version and reviewed contract. Part 2
   proceeds.

## Part 2: design

All constants live in `ha_core_readmission/runtime.py`.

### A. Recovery

- **Backoff.** After a failed verification, the supervisor waits
  `min(cap, base × 2^level × (1 + jitter × (2u − 1)))`, where `u` is in
  [0, 1] and comes from an injectable source (`random.random` by default).
  - base `CORE_RECOVERY_RETRY_BASE_SECONDS = 20`
  - cap `min(CORE_RECOVERY_RETRY_CAP_SECONDS = 240, interval)`, where the
    interval is the 300 s reconciliation interval
  - jitter `CORE_RECOVERY_RETRY_JITTER = 0.2` (±20%)
  - `level` increments after each scheduled retry, up to
    `CORE_RECOVERY_MAX_BACKOFF_LEVEL = 8`, and resets to 0 on publication.
    With `u = 0.5` the sequence is 20, 40, 80, 160, 240, 240, … s.

  Hints that arrive during a backoff wait are still coalesced, as before. The
  wait does not end early. `supervise()` rejects non-positive base, cap or
  confirmation delays.
- **Fail closed throughout.** Every retirement path in Part 1 is unchanged.
  While recovery is active, no route, lease, catalog entry or dependency
  evidence is admitted.
- **Restoration needs complete re-verification.** A recovery verification runs
  the full existing pipeline:
  - two-snapshot stable observation
  - authenticated lifecycle watcher attached
  - registry, connection and monitor fences
  - coordinator evaluation against the reviewed compiled or signed authority
    for the observed version

  The attempt counts toward restoration only if the coordinator's pure
  evaluation (`CoreReadmissionCoordinator.preview_admitted`, which has no side
  effects) admits at least one capability.
- **Hysteresis: one restoration gate.** Correction after the independent
  review of `4e677ec` (H1). Every state in which no authority is admitted
  passes the same gate, whatever the reason:
  - a transient retirement or connection movement
  - an incompatible Core (an unreviewed version, or evidence failing its
    reviewed contract)
  - an unavailable generation
  - a Core never initialized after the boot sequence
  - a changed Core version, or moved registry authority

  Restoring authority requires `CORE_RECOVERY_CONFIRMATIONS = 2` (3 after a
  flap) consecutive complete background verifications with the same
  confirmation key: the Core identity, the observed version and the exact set
  of admitted reviewed contracts (capability and contract fingerprint). Each
  withdrawal of admitted authority arms recovery (`_enter_recovery_locked`,
  which records `retired_at` and the flap count), including the incompatible
  and material-retirement paths. A changed Core version now retires the
  admitted generation with or without the registry, so the new version passes
  the gate. The background check that observed the new version counts as its
  first confirmation.
  - A complete verification that does not yet publish sets
    `recovery_state = confirming` and logs `core_authority_recovery_confirming`.
    The supervisor then waits `CORE_RECOVERY_CONFIRMATION_SECONDS = 15` × (1 +
    0.2u) × 2^flap_count, capped as above, before the next verification.
  - The key is (identity, version, selected signed-registry token, admitted
    reviewed contracts).
  - The confirmation count restarts on any of these:
    - a failed verification
    - any verification, background **or on demand** (H2, review of
      a6e66a8), whose complete observation has a different key; it logs
      `core_authority_recovery_reset` with reason `core_authority_key_changed`
      and the trigger (an incomplete observation restarts the count as the
      failed verification above, not as a key change)
    - a material (version or registry) retirement, admitted or not
    - a connection or lifecycle movement during confirmation, which logs
      `core_authority_recovery_reset` with reason `core_connection_changed`
  - On-demand checks may therefore **reset** the streak but never **add** to
    it.
  - **Flap.** If authority is retired again within
    `CORE_RECOVERY_FLAP_WINDOW_SECONDS = 600` (monotonic clock) of a
    restoration, the next restoration requires
    `CORE_RECOVERY_FLAP_CONFIRMATIONS = 3`. The confirmation spacing doubles
    for each consecutive flap, and the backoff starts at level `flap_count`
    (40 s on the first flap). A restoration outside the window returns to 2
    confirmations and normal spacing. A flapping Core therefore cannot toggle
    the catalog every few seconds.
  - **Supervisor-paced only.** Only the `periodic`,
    `identity_or_connection_change` and `failure_retry` triggers can count a
    confirmation or publish. Every other trigger is on demand:
    `mutation_pre_dispatch`, `mutation_verification`,
    `configuration_reverification`, `fan_authority`, any unknown name (for
    example a health or tool probe), and `startup`/`startup_retry` once the
    boot sequence has ended. From any non-admitted state, an on-demand
    verification that would admit is refused without publishing. It is not
    counted, never adds to the streak (a changed key resets it, above) and
    does not change the supervisor's next attempt.
    It is counted separately in `counters.recovery_on_demand_refusals` and
    logged at INFO as `core_authority_recovery_on_demand_refused`. Its failures
    still restart the count.
  - **Counter (L1).** `counters.recovery_confirmations` counts only accepted
    background confirmations, including the final one that restores. One
    transient recovery adds exactly 2 (3 after a flap).
  - **Trade-off.** Every lifecycle-socket movement now waits one confirmation
    interval (about 15–18 s) before authority returns. This includes the
    reconnect after a Core update or restart. Previously authority returned on
    the immediate reprobe. This is the hysteresis the brief requires.
    **Josh accepted this delay on 2026-10-20**; Core-backed tools refuse
    during it.
- **First admission is not restoration: a one-shot latch (H1, review of
  a6e66a8).** The production boot sequence (`reconcile_until_initialized`:
  `startup`, then `startup_retry`) publishes on its first complete
  verification, because no previously admitted authority is being restored.
  So does the first background verification of a runtime that has made no
  attempt yet. The exception is `_boot_exception_spent`, set False only in
  `CoreRuntime.__init__`; its only other writer,
  `_spend_boot_exception_locked`, sets it True. It is spent by:
  - any admission (`_record_publication_locked`);
  - any failed verification (`_record_failure_locked`), including one inside
    the boot sequence;
  - any withdrawal of admitted authority (`_enter_recovery_locked`) or
    retirement (`_material_retirement_locked`, `request_reconciliation`);
  - any attempt that is not a boot attempt: an on-demand attempt when it
    starts, and a background attempt (`periodic`,
    `identity_or_connection_change`, `failure_retry`) when it ends, so that
    its own first-admission claim survives the publication re-check below
    (H1, review of 2ad04a3). "When it ends" includes cancellation and an
    exception, unless the attempt was superseded;
  - the end of the boot sequence.

  `configure` never re-arms it. Production constructs exactly one
  `CoreRuntime`, the module-level `CORE_READMISSION`, so the latch's lifetime
  is the process's (pinned by
  `test_production_constructs_exactly_one_runtime_and_never_rearms`). A
  re-initialization after the latch is spent (application rebuild,
  `configure`, collector restart) keeps the recovery and flap bookkeeping;
  admitted authority it discards enters recovery like any withdrawal, and
  restoration needs the full gate. Counters are zeroed by `configure`, as in
  base.

  A failed first startup check spends the latch, so the boot sequence stops
  retrying (a `startup_retry` could no longer admit). The supervisor then
  starts on recovery pacing, not the 300 s interval: one backoff delay (about
  20 s), then the background confirmations. Josh accepted this pause for a
  Core not initialized at boot on 2026-10-20. A startup failure is logged.
- **Superseded in-flight work is discarded (H1, review of 2ad04a3).** The
  review reproduced, through the application factory, a startup verification
  suspended in its snapshot capture that outlived two rebuilds and then
  admitted authority with zero confirmations. Every verification therefore
  carries a `_WorkStamp`, taken when it is requested (before it queues for
  the reconciliation lock):
  - the runtime generation, `_runtime_generation`, incremented by every
    `configure` (application rebuild, re-initialization, collector restart)
    and by the retirement of the current configuration's supervisor;
  - the configuration owner, a fresh object on every `configure`;
  - whether the boot exemption was available at start (a boot or
    background trigger, latch unspent).

  At publication, under the same lock as admission, lease issue, listener
  notification and catalog restoration, the work must still have the current
  generation and owner, and a boot-exemption claim must still find the latch
  unspent. Otherwise its result is discarded. The resume point after every
  await (the reconciliation lock, a registry refresh, each observation, the
  watcher attachment) checks the generation and owner too, so superseded work
  stops before it touches the current configuration's counters or watcher. A
  discard is logged at INFO as `core_authority_stale_work_discarded` with a
  closed-set `reason` (`core_configuration_replaced`,
  `core_runtime_generation_replaced`, `core_boot_exemption_spent`), `phase`
  and `trigger`, and counted only in `stale_work_discards`. It is never a
  verification, a failure or a confirmation, and it never changes recovery
  state. The current configuration restores only through its own background
  confirmations.

  Where possible, in-flight work is also stopped. `configure` cancels the
  previous lifecycle watcher; base only forgot it. The connection epoch is
  never reused across configurations. A supervisor whose configuration was
  replaced stops at its next wake and logs `core_authority_supervisor_retired`
  at INFO, without touching the new configuration's watcher or counters. A
  boot sequence whose configuration was replaced neither retries nor spends
  the new configuration's latch. The publication re-check is the guarantee
  either way.

  "Retirement" here means retiring the runtime (a superseded configuration
  or supervisor). Retiring Core authority within one configuration
  (a connection move, version change or registry move) stays fenced by the
  existing connection epoch, watcher epoch and registry token. Those remain
  recorded failures, as before.
- **Incompatibility.** A complete, identity-agreeing observation that admits
  nothing publishes the existing fail-closed generation. Examples are an
  unreviewed Core version, or a reviewed version whose evidence fails its
  contract.
  - It sets `recovery_state = incompatible` and logs
    `core_authority_verification_failed` with reason
    `core_authority_incompatible`. It also logs `core_authority_retired` if
    admitted authority was replaced.
  - It is re-checked at the normal periodic interval (300 s) without backoff.
    `next_recovery_attempt_at` records that time.
  - It stays withdrawn while incompatible. Once a background re-check admits,
    that check is the first confirmation of the same gate. Confirmation
    spacing then applies, and the final confirmation logs
    `core_authority_restored`. One successful verification never restores it,
    and an on-demand one never counts.

  Identity disagreement or an incomplete observation remains a failed
  verification and backs off. It never publishes admitted authority.

### B. Visibility

- **Log events.**
  - Logger: `ha_mcp_engineering.core_authority`, through the existing
    `logging_config.log_event`. Each record carries `event` and a bounded
    `safe_context`.
  - Every context includes:
    - a closed-set `reason` and `detail` where applicable
    - an ISO-8601 UTC `at` timestamp
    - `recovery_state`, `consecutive_failures`, `confirmations` and
      `confirmations_required`
  - No exception text, URLs, tokens or session identifiers are logged.

  | Event | Level | Emitted when |
  |---|---|---|
  | `core_authority_verification_failed` | WARNING | a verification fails, including early refusals and incompatibility (`reason`, `detail`, `phase`) |
  | `core_authority_retired` | WARNING | a generation is retired (`reason`, `detail`, `generation`, `admitted`) |
  | `core_authority_capabilities_withdrawn` | WARNING | a new generation admits fewer capabilities than the one it replaces (`withdrawn_count`, `admitted_count`) |
  | `core_catalog_tools_withdrawn` / `core_catalog_tools_restored` | WARNING | the gateway changes the Core-withheld tool set (`count`) |
  | `core_authority_recovery_attempt` | WARNING | a recovery verification starts |
  | `core_authority_recovery_confirming` | WARNING | a recovery verification succeeds and more confirmations are needed (`trigger`) |
  | `core_authority_recovery_reset` | WARNING | a connection movement restarts confirmation (`reason`) |
  | `core_authority_restored` | WARNING | authority is restored (`admitted_count`, `failed_attempts`, `verified_confirmations`, `outage_seconds`) |
  | `core_authority_recovery_scheduled` | INFO | the supervisor schedules a retry or confirmation (`delay_seconds`, `next_attempt_at`) |
  | `core_authority_stale_work_discarded` | INFO | superseded in-flight work is discarded (`reason`, `phase`, `trigger`) |
  | `core_authority_supervisor_retired` | INFO | a supervisor of a replaced configuration stops (`reason`) |

- **Failure detail.** `CoreObservationCollector.last_failure_detail`
  classifies the snapshot exception (`observation.classify_probe_failure`) into
  the closed `PROBE_FAILURE_DETAILS` set:
  - the `core_probe_*` codes the source raises
  - `core_probe_timeout`, `core_probe_connection_failed`,
    `core_probe_client_error`, `core_probe_malformed_response`
  - `other`

  A lifecycle-watcher attach failure is reported as
  `core_lifecycle_monitor_attach_failed` or
  `core_lifecycle_monitor_attach_timeout`. The exception text is still
  discarded.
- **Health fields.** Both `health_snapshot()` and the public
  `health_projection()` (the `home_assistant_core_authority` section of
  `health`) gain a bounded `recovery` object:
  - `recovery_state`: one of `initializing`, `stable`, `retrying`,
    `backing_off`, `confirming`, `restored`, `incompatible`
  - `consecutive_failures`
  - `last_failure_reason`, `last_failure_detail`, `last_failure_at`
  - `retired_at`, `last_attempt_at`, `next_recovery_attempt_at`,
    `retry_delay_seconds`
  - `confirmations`, `confirmations_required`
  - `last_restored_at`

  All existing counters stay. Five new counters are added:
  `recovery_attempts`, `recovery_confirmations` (accepted background
  confirmations only), `recovery_on_demand_refusals`,
  `authority_restorations` and `stale_work_discards` (diagnostic only; added
  for H1, review of 2ad04a3).
  This is an additive public-health change, authorized by the task.

### C. Dependents

Restoration publishes a new generation through the unchanged coordinator path,
so `changed` is true and every registered listener runs:

- The gateway rebuilds its catalog. All Core-withheld tools and the optional
  YAML reader return. Its delta is counted as `catalog_restorations` and logged
  through `CoreRuntime.record_catalog_change`, which the gateway already calls.
- The dependency index invalidation epoch is already advanced. Its next build
  binds to the new generation.
- Typed providers acquire leases against the new generation. Leases from the
  retired generation stay refused.

## Scope and protected files

Protected runtime files changed under the task's explicit authorization (Core
readmission, gateway wiring, health and logging):

- `hass_mcp_engineering_beta/ha_mcp_engineering/ha_core_readmission/runtime.py`
- `hass_mcp_engineering_beta/ha_mcp_engineering/ha_core_readmission/observation.py`
- `hass_mcp_engineering_beta/ha_mcp_engineering/ha_core_readmission/coordinator.py`
  (adds the pure `preview_admitted` only)

Release materialization files, changed only through
`scripts/promote_next_release.py --apply` (owner-authorized):

- `hass_mcp_engineering_beta/config.yaml`
- `hass_mcp_engineering_beta/ha_mcp_engineering/version.py`
- `scripts/validate_addon_metadata.py`
- `.release/next-version` (consumed)

The gateway code is not changed. This change makes no changes to:

- workflows
- signed registries
- the support window
- stable v1
- provider routing
- tool registration
- public tool input schemas

## Validation

- `tests/test_hamcp215_core_authority_recovery.py` (56 tests) covers:
  - every transient trigger going from retirement to automatic restoration,
    with the catalog and index back:
    - REST HTTP failure, connection loss, timeout and authentication failure
    - lifecycle-watcher attach failure and timeout
  - one supervisor recovering without a restart, with the exact event sequence
  - a persistent failure backing off to the cap, logging and refusing
  - a startup failure being logged, paced and restored only through the gate
  - a successful boot sequence admitting without confirmation, and a failed
    boot sequence leaving a never-initialized Core gated
  - an incompatibility staying retired and being re-checked without backoff,
    then restored only after two background confirmations once compatible
  - the reviewer's H1 reproductions, through the production collector and
    lifecycle watcher: an incompatible contract, with and without one prior
    periodic confirmation; an unreviewed version followed by on-demand
    verification; the counter after on-demand attempts; partial and stale
    observations
  - a matrix of every disposition (transient, socket moved, incompatible,
    unreviewed version, unavailable, never initialized, version changed) ×
    {background success, on-demand success, mixed}
  - a pristine runtime never admitting on demand
  - the live incident's counters (initialized false, version and generation
    null, 27 attempts, 26 reprobes, 1 failure, 29 catalog withdrawals with the
    production 8.5.0 catalog, an invalidated index) recovering automatically
    through the gate, for both the probe and the socket fault (L2; lease
    counters and live timing are not reproduced, see Part 1 finding 3)
  - the one-shot boot exception (H1, review of a6e66a8): the reviewer's
    application-factory and configure reproductions; re-initialization after
    a normal admission, after a retirement, after a lone admitting startup
    check, and repeatedly (two, then three within the flap window,
    background confirmations each time); a re-initialization showing a
    withdrawal in health; a supervisor started after a re-initialization
    running on recovery pacing; the boot sequence repeated without
    re-initialization; the boot sequence's end spending the exception when
    no attempt reached a verification outcome; and an AST pin that
    production builds one runtime and only `__init__` and
    `_spend_boot_exception_locked` write the latch
  - superseded in-flight work (H1, review of 2ad04a3), every case ending
    with the current configuration restoring only through its own
    background confirmations:
    - the reviewer's production-path reproduction, through the application
      factory and through `configure`
    - work suspended at every await (reconciliation lock, registry refresh,
      observation, watcher attachment, the recollection after attachment)
      for a boot, two background and an on-demand trigger
    - a rebuild made from inside authority selection, which only the
      publication re-check sees; for a boot trigger the exemption it could
      not use still admits the current boot (positive control)
    - work from several generations, released newest first
    - stale work inside a flap window (3 confirmations)
    - a concurrent boot attempt of one configuration claiming an exemption
      that another attempt's failure spent; two healthy concurrent attempts
      as a positive control; a fresh runtime's first background verification
      still admitting, and one that is cancelled or raises still spending the
      exemption, so the next background verification is gated
    - a superseded supervisor stopping without touching the new
      configuration, and on-demand work outliving the current supervisor's
      retirement
  - bounded recovery (R1, adopted from the review of 2ad04a3): each
    background recovery attempt is scheduled within one second
  - a changed key observed on demand restarting the streak (H2, review of
    a6e66a8): the reviewer's version A→B→A reproduction; a changed reviewed
    contract; a moved signed-registry authority; concurrent background and
    on-demand checks counting only the background ones; a same-key on-demand
    check neither resetting nor adding; fresh background confirmations
    required after the reset; and a failed probe restarting the count as a
    failure, not as a key change
  - the confirmation count restarting on each of these:
    - a failure
    - a connection move
    - a monitorless failure
    - a different version, background or on demand
  - on-demand verification (every on-demand trigger name, plus unknown
    names) being refused, not counting, and counted only in
    `recovery_on_demand_refusals`
  - flap escalation and window expiry
  - rapid flapping never publishing between confirmations
  - jitter bounds
  - invalid delays being rejected
  - the catalog and YAML reader being withheld during the outage, with zero
    dispatch, and resuming afterwards
  - the dependency index refusing during the outage and resuming
- Updated existing tests:
  - `tests/test_rc3_postcore_regressions.py` pinned the fixed 300 s retry.
    Its failed-first-attempt test now shows the never-initialized Core
    passing the gate (two background confirmations).
  - `tests/test_ha_core_2026_9_integration.py` had two lifecycle tests that
    expected immediate republication after a connection movement. They now
    assert the confirming state first; the version-transition test restores
    through the gate. Its loop-reconfiguration test (H1) now expects the
    second event loop's re-initialization of the same runtime to restore
    through supervisor confirmations, not at startup.
  - `tests/test_core_semantic_continuity.py`
    `test_reconfiguration_cannot_reuse_old_generation_evidence` (H1) now
    asserts that a reconfiguration leaves nothing admitted after startup and
    restores through `reconcile_through_gate`.
  - `tests/test_rc3_postcore_regressions.py`
    `test_cancellation_during_probe_or_monitor_attachment_closes_sessions`
    (H1): the second supervisor, started after the first attempt spent the
    boot exception, starts on recovery pacing; the test releases that one
    delay before cancelling.
  - The H1 gate removes two test-only shortcuts that production never used:
    admission on an arbitrary named (on-demand) trigger, and a single
    reconciliation restoring authority after a version or signed-registry
    change. `tests/support/core_gate.py` adds `reconcile_through_gate`, which
    runs background verifications while the gate is confirming. Fresh
    runtimes now admit with `startup`, as production does
    (`application.py` `reconcile_until_initialized`), and restorations use the
    helper. Affected: `test_alarmo_inspection_core_authority`,
    `test_automation_baseline_capture`, `test_beta_v2`,
    `test_core_2026_10_device_registry` (also used by
    `test_hamcp069a_entity_metadata_readmission` and
    `test_upstream_8_6_compatibility`), `test_core_semantic_continuity`,
    `test_core_update_continuity`, `test_dashboard_analysis_catalog`,
    `test_hamcp162_yaml_admission`, `test_hamcp175_power_fan_admission`,
    `test_hamcp200_path_safety`, `test_rc4_device_identifier_contract`,
    `test_typed_core_container_contract` and `test_typed_core_continuity`.
    No assertion about refusal, dispatch counts or authority was weakened.
  - The disposable-lane scripts get the same change (CI evidence only):
    `scripts/core_registry_contract_lane.py` (shared by the real-HA,
    ha-mcp 8.5.0 container and dashboard-analysis lanes),
    `scripts/alarmo_inspection_contract_acceptance.py`,
    `scripts/entity_metadata_contract_acceptance.py` and
    `scripts/automation_baseline_contract_acceptance.py`.
- `docs/evidence/hamcp215-mutations/run_mutations.py` (45 mutations, 45 killed;
  results in `results.json`) must fail on each of these:
  - no recovery loop. Without the paced loop, the retirement's own reprobe
    hint re-wakes the supervisor immediately: 12,557 attempts in 15 s were
    measured, and the unpaced chain's memory grows without bound. This is
    the retry chain that base code's fixed sleep also prevented. The
    `BoundedRecovery` assertion (R1, adopted from the review of 2ad04a3)
    now kills it within about a second. Before it, the mutant was killed
    only by exhausting a 3 GB cgroup memory cap or the 900 s timeout. Keep
    the runner under a memory cap anyway.
  - fixed-interval (base 300 s) retry
  - restoration without confirmation or without the recovery gate
  - restoration without the lifecycle fence
  - the confirmation key ignored
  - confirmations not reset on a failure or a connection move
  - on-demand triggers counted as confirmations
  - no flap escalation
  - no backoff growth
  - uncapped backoff
  - each of the retirement, verification-failure, attempt, restoration and
    catalog log events dropped
  - `last_failure_reason` or `retired_at` not recorded
  - H1 gate (G1-G8): an incompatible Core bypassing the gate; the boot
    window left open after boot, after a failed attempt, or opened by an
    on-demand trigger; a version-change retirement applied only with a
    registry; an incompatible withdrawal or a material retirement not
    entering recovery; confirmation spacing applied only during transient
    recovery
  - L1: refused on-demand attempts counted in `recovery_confirmations`
  - H1 one-shot latch (E1-E4, G2, G3, G3b, G3c): `configure` re-arming the
    latch; an admission, a failed verification or a non-boot check not
    spending it; the boot sequence's end not spending it; boot retries
    continuing after it is spent; a re-initialization dropping the
    withdrawal of admitted authority; a supervisor started after a re-init
    waiting a full interval
  - H1 superseded work (S1-S7, review of 2ad04a3): publication not
    re-checking generation and owner; a boot-exemption claim taken from the
    start-time snapshot without a publication re-check; resume points not
    checked; supervisor retirement keeping the generation; `configure`
    leaving the old watcher running; a superseded supervisor continuing; a
    background attempt never spending the exemption
  - H2 changed key (K1-K4): an on-demand changed key ignored; the
    signed-registry authority left out of the key; the reset logged with the
    wrong reason; a failed (incomplete) probe reported as a key change
