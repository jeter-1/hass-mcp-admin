# Engineering 2.4.1-beta.27 acceptance

This contract covers HAMCP-215: recovery and visibility for Home Assistant Core
authority (break-fix). It builds on 2.4.1-beta.26 (HAMCP-217, `certifi`
2026.10.19, split out of this work by Josh on 2026-10-20).

See also:
- Release notes:
  [`V2_4_1_BETA27_RELEASE_NOTES.md`](V2_4_1_BETA27_RELEASE_NOTES.md).
- Design and Part 1 root cause:
  [`HAMCP_215_CORE_AUTHORITY_RECOVERY_DESIGN.md`](HAMCP_215_CORE_AUTHORITY_RECOVERY_DESIGN.md).

**Release target.** `2.4.1-beta.27`. It is staged by one owner-authorized run
of `scripts/promote_next_release.py --apply` on a base that already carries
2.4.1-beta.26. The run consumes the declaration.

The contract separates four stages:
1. source and offline acceptance;
2. release CI;
3. publication;
4. installed Engineering acceptance.

It authorizes none of the later stages.

## 1. Source and offline acceptance

Bind the following:
- Josh's HAMCP-215 objective: recover Core authority automatically after
  transient failures, never fail silently, and keep fail-closed behavior
  during uncertainty;
- Josh's 2026-10-20 decisions:
  - ship HAMCP-217 (the certifi bump) separately as beta.26 and restage this
    work as beta.27;
  - accept the confirmation wait before Core authority returns after a
    lifecycle-watcher socket move (see **Accepted restoration delay**);
- `2.4.1-beta.27` materialized in all three version authorities, with exact
  acceptance and release-note resolution from `scripts/codex-context.py` and
  no staged declaration left over;
- the PR head SHA;
- the passing PR `validate` run for that exact head;
- the Evidence tier log named in the PR.

The review needs a security section covering:
- **No new reachability.** No new write, service call, reload, restart,
  fallback or forwarding is reachable. Recovery reuses the existing
  verification pipeline (design §A).
- **No admission during recovery.** While no authority is admitted, no route,
  lease, catalog entry or dependency evidence is admitted until the
  restoration gate passes. On-demand triggers cannot confirm or publish from
  any state.
- **Restoration requires complete re-verification.** That means a stable
  two-snapshot observation, the authenticated lifecycle watcher, the registry,
  connection and monitor fences, and reviewed-contract admission, with a
  matching confirmation key.
- **One restoration gate (H1).** Every non-admitted disposition (transient,
  incompatible, unavailable, never initialized after boot, changed version or
  registry authority) needs 2 background confirmations, or 3 when it flaps,
  agreeing on identity, version and reviewed contracts. Any failure,
  connection move, or identity, version or contract change restarts the
  count. Only the boot sequence's first admission is exempt.
- **Bounded logs and health.** Log and health content is closed-set reasons
  and details, timestamps and counts. There is no exception text, URL or token.

Preserve the following:
- stable v1.1.2;
- persisted formats;
- every tool descriptor and input schema (88 tools, 89 with the optional YAML
  reader admitted, as in beta.26);
- probe fingerprints, Core capability profiles, owner-signed Core registry
  entries and the signed ha-mcp registry;
- the HOLD and the support window;
- workflows;
- every dependency and both hashed locks (unchanged from beta.26).

The health `recovery` object and the four new counters are the only
public-surface change. They are additive and authorized by the task.

### Accepted restoration delay

After any lifecycle-watcher socket move, including a Home Assistant Core
restart or update, Core authority returns only after one confirmation
interval, about 15–18 s once Core is reachable. Core-backed tools refuse
during that wait. Josh accepted this on 2026-10-20. Acceptance treats a
refusal in that window as expected, not as a defect, provided the following
hold:
- health shows `recovery_state: confirming`;
- authority returns without restarting Engineering.

### Offline proof

No test reaches a live system.

- `tests/test_hamcp215_core_authority_recovery.py` (26 tests) covers:
  - each transient (REST HTTP failure, connection loss, timeout and
    authentication failure; lifecycle-watcher attach failure and timeout),
    going from retirement through backoff and confirmations to automatic
    restoration of the catalog and the index;
  - a persistent failure backing off to the cap, logging and refusing;
  - an incompatibility staying retired and being re-checked without backoff;
  - flapping escalating and never toggling between confirmations;
  - calls being refused during the outage, with zero dispatch, and succeeding
    afterwards;
  - log levels, reasons and health fields;
  - the independent review's reproductions, through the production collector
    and lifecycle watcher: an incompatible contract, and an unreviewed version,
    restored by neither one on-demand nor one periodic verification;
  - a matrix of every non-admitted disposition × {background, on-demand,
    mixed} with no restoration before the full background confirmations;
  - `recovery_confirmations` counting only accepted confirmations (L1);
  - the exact live beta.24 health state (Part 1) recovering automatically.
- Two lifecycle tests in `tests/test_ha_core_2026_9_integration.py` now assert
  the confirming state before republication. Recovery-delay assertions in
  `tests/test_rc3_postcore_regressions.py` follow the new backoff.
- Existing tests and disposable-lane scripts that admitted Core on an
  on-demand trigger, or expected one reconciliation to restore authority after
  a version or signed-registry change, now admit with `startup` (as production
  does) or restore through `tests/support/core_gate.py`
  `reconcile_through_gate`. The full list is in the design document's
  Validation section. No refusal, dispatch or authority assertion was
  weakened. The script changes are evidenced only by the disposable CI lanes.
- Mutations (`docs/evidence/hamcp215-mutations/`): 28 of 28 killed,
  including all of these:
  - no recovery loop (killed by the runner's default 900 s timeout: an
    unpaced retry chain)
  - fixed-interval retry
  - restoration without confirmation, the recovery gate or the lifecycle fence
  - no hysteresis or no flap escalation
  - fixed or uncapped backoff
  - each WARNING event (retirement, verification failure, attempt,
    restoration, catalog change) removed
  - health fields not recorded
  - an on-demand verification counted as a confirmation; an incompatible
    Core bypassing the gate; the boot window left open; a changed version
    replacing authority without the gate; refused calls counted as
    confirmations (L1)

## 2. Release CI and publication

Require exact-head CI before release acceptance. Pending or unavailable jobs
are not passing evidence.

Publication needs Josh's Ready authorization and the protected merge
handoff. Verify the following with the shipped verifiers:
- the merged source;
- v2.4.1-beta.27;
- the immutable version and commit images;
- provenance and SBOM.

Never invent future image identities. Retain the beta.26 artifacts and the
known-good earlier artifacts for recovery.

No stored format changes. Rollback is reinstalling the beta.26 artifact.

## 3. Installed Engineering

Steps 1 to 4 are read-only. Step 5 is an optional, owner-run controlled
transient.

1. **Installed-image binding.** Separately authorize installation of the
   published Engineering artifact. Bind the running add-on to:
   - the published image index digest;
   - the v2.4.1-beta.27 tag;
   - the protected `main` merge commit that contains this PR.
2. **Catalog.** Using fresh protocol enumeration, confirm:
   - 88 tools, or 89 with the optional YAML reader admitted;
   - every descriptor unchanged from beta.26;
   - Core signed selection, storage and audit health, and zero fallback.
3. **Health recovery fields (live).** Read `get_server_health` and record
   `home_assistant_core_authority`:
   - `initialized: true`;
   - `recovery.recovery_state` `stable`, or `restored` if a recovery has
     occurred since start;
   - `recovery` contains exactly these keys:
     - `recovery_state`, `consecutive_failures`
     - `last_failure_reason`, `last_failure_detail`, `last_failure_at`
     - `retired_at`, `last_attempt_at`, `next_recovery_attempt_at`,
       `retry_delay_seconds`
     - `confirmations`, `confirmations_required`
     - `last_restored_at`
   - timestamps that are ISO-8601 UTC or null;
   - `consecutive_failures: 0` when stable;
   - the counters `recovery_attempts`, `recovery_confirmations`,
     `recovery_on_demand_refusals` and `authority_restorations` present.
4. **Logs.** Confirm that the add-on log at startup contains no
   `core_authority_*` WARNING events unless a failure occurred. If a failure
   did occur, confirm that each WARNING event names a bounded reason and an
   `at` timestamp and contains no token or exception text.
5. **Optional controlled transient (owner-run).** This step is a live Home
   Assistant action. Only Josh may authorize and perform it. It is not part of
   source acceptance.
   1. Restart Home Assistant Core. Do not restart Engineering.
   2. While Core is down, `get_server_health` should show:
      - `initialized: false`;
      - `recovery_state` `backing_off` or `retrying`;
      - a bounded `last_failure_reason` and `last_failure_detail`;
      - `retired_at` set;
      - `next_recovery_attempt_at` within the backoff schedule (20, 40, 80,
        160, 240 s, ±20%).
   3. Core-backed tools should be refused, and the log should contain
      `core_authority_verification_failed`, `core_authority_retired` and
      `core_catalog_tools_withdrawn`.
   4. Once Core is back, health should pass through `confirming` with
      `confirmations_required: 2`, then reach `restored` with
      `last_restored_at` set. Expect the accepted wait of about 15–18 s
      between Core being reachable and restoration. The log should contain
      `core_authority_recovery_attempt`,
      `core_authority_recovery_confirming`, `core_authority_restored` and
      `core_catalog_tools_restored`.
   5. All of this must happen without restarting Engineering.
   6. Repeat step 2 (catalog) and one read-only Core-backed call; both should
      succeed.

No push, CI trigger, signing, publication, installation or live action is
authorized by source completion.
