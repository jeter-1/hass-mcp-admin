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
3. **The counters do not support "never recovered for ~2 h".**
   - Attempts 27 = successes 26 + failures 1, and the snapshot shows one
     retirement, so no attempt was in flight. After each failure, base code
     makes another attempt within 300 s (finding 2); that attempt increments
     `verification_attempts`, and each further failure increments
     `verification_failures`.
   - A Core outage that lasted about 2 h would therefore show about 24
     failures. One failure followed by no further attempt means the retirement
     happened within roughly one retry interval before the snapshot.
   - The start of the outage cannot be determined from the counters. Its exact
     transport cause cannot be determined from any existing surface: the
     reason code went only to the audit file, and the exception class was
     discarded (`observation.py:318-320`).
   - The pre-counter section of `reconcile_once` (the reconciliation lock,
     `refresh_if_due`, `_sync_registry_authority`) cannot block indefinitely:
     the registry is disabled by default, or bounded by the timeouts above.
   - If the owner saw `initialized: false` at observations more than about
     5 minutes apart with unchanged counters, that would indicate a stalled
     supervisor. No code path reproduces such a stall. This change adds
     `last_attempt_at` and `next_recovery_attempt_at`, so a stall is
     distinguishable next time.
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
- **Hysteresis.**
  - After admitted authority is withdrawn by a transient failure or a
    connection movement, recovery is active (`_enter_recovery_locked`).
    Restoring authority then requires `CORE_RECOVERY_CONFIRMATIONS = 2`
    consecutive complete verifications with the same confirmation key: the
    observed version and the exact set of admitted capabilities.
  - A complete verification that does not yet publish sets
    `recovery_state = confirming` and logs `core_authority_recovery_confirming`.
    The supervisor then waits `CORE_RECOVERY_CONFIRMATION_SECONDS = 15` × (1 +
    0.2u) × 2^flap_count, capped as above, before the next verification.
  - The confirmation count restarts on any of these:
    - a failed verification
    - a verification with a different key
    - a connection or lifecycle movement during confirmation, which logs
      `core_authority_recovery_reset`
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
    confirmation or publish. On-demand verifications during recovery, such as
    `mutation_pre_dispatch` and `configuration_reverification`, are refused
    without publishing and cannot shorten the spacing. Their failures still
    restart the count.
  - **Trade-off.** Every lifecycle-socket movement now waits one confirmation
    interval (about 15–18 s) before authority returns. This includes the
    reconnect after a Core update or restart. Previously authority returned on
    the immediate reprobe. This is the hysteresis the brief requires;
    shortening it is an owner decision.
- **Startup is unchanged.** First admission after process start publishes on
  the first complete verification, because no previously admitted authority is
  being restored. A startup failure is logged and paced by the same backoff.
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
  - A later admitting verification restores it and logs
    `core_authority_restored`.

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

  All existing counters stay. Three new counters are added:
  `recovery_attempts`, `recovery_confirmations` and `authority_restorations`.
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

- `tests/test_hamcp215_core_authority_recovery.py` (16 tests) covers:
  - every transient trigger going from retirement to automatic restoration,
    with the catalog and index back:
    - REST HTTP failure, connection loss, timeout and authentication failure
    - lifecycle-watcher attach failure and timeout
  - one supervisor recovering without a restart, with the exact event sequence
  - a persistent failure backing off to the cap, logging and refusing
  - a startup failure being logged and paced
  - an incompatibility staying retired and being re-checked without backoff
  - the confirmation count restarting on each of these:
    - a failure
    - a connection move
    - a monitorless failure
    - a different version
  - on-demand verification during recovery being refused and not counting
  - flap escalation and window expiry
  - rapid flapping never publishing between confirmations
  - jitter bounds
  - invalid delays being rejected
  - the catalog and YAML reader being withheld during the outage, with zero
    dispatch, and resuming afterwards
  - the dependency index refusing during the outage and resuming
- Updated existing tests:
  - `tests/test_rc3_postcore_regressions.py` pinned the fixed 300 s retry.
  - `tests/test_ha_core_2026_9_integration.py` had two lifecycle tests that
    expected immediate republication after a connection movement. They now
    assert the confirming state first.
- `docs/evidence/hamcp215-mutations/run_mutations.py` (19 mutations, 19
  killed; results in `results.json`) must fail on each of these:
  - no recovery loop. This one is killed by the runner's 120 s timeout,
    not by an assertion. Without the paced loop, the retirement's own
    reprobe hint re-wakes the supervisor immediately: 12,557 attempts in
    15 s were measured. The unpaced chain starves the event loop. This is
    the retry chain that base code's fixed sleep also prevented.
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
