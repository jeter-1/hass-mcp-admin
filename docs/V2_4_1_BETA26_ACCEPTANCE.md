# Engineering 2.4.1-beta.26 acceptance

This contract covers HAMCP-215: recovery and visibility for Home Assistant Core
authority (break-fix). It builds on 2.4.1-beta.25 (HAMCP-212). It also covers
HAMCP-217, folded in by Josh: `certifi` 2026.7.22 → 2026.10.19 for
GHSA-6q48-6c7w-9gw2.

See also:
- Release notes:
  [`V2_4_1_BETA26_RELEASE_NOTES.md`](V2_4_1_BETA26_RELEASE_NOTES.md).
- Design and Part 1 root cause:
  [`HAMCP_215_CORE_AUTHORITY_RECOVERY_DESIGN.md`](HAMCP_215_CORE_AUTHORITY_RECOVERY_DESIGN.md).

**Release target.** `2.4.1-beta.26`. It is staged by one owner-authorized run
of `scripts/promote_next_release.py --apply` on a base that already carries
2.4.1-beta.25. The run consumes the declaration.

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
  - fold in HAMCP-217 (the certifi bump);
  - accept the confirmation wait before Core authority returns after a
    lifecycle-watcher socket move (see **Accepted restoration delay**);
- `2.4.1-beta.26` materialized in all three version authorities, with exact
  acceptance and release-note resolution from `scripts/codex-context.py` and
  no staged declaration left over;
- the PR head SHA;
- the passing PR `validate` run for that exact head;
- the Evidence tier log named in the PR.

The review needs a security section covering:
- **No new reachability.** No new write, service call, reload, restart,
  fallback or forwarding is reachable. Recovery reuses the existing
  verification pipeline (design §A).
- **No admission during recovery.** While recovery is active, no route, lease,
  catalog entry or dependency evidence is admitted. On-demand triggers cannot
  confirm or publish.
- **Restoration requires complete re-verification.** That means a stable
  two-snapshot observation, the authenticated lifecycle watcher, the registry,
  connection and monitor fences, and reviewed-contract admission, with a
  matching confirmation key.
- **Hysteresis.** Restoration needs 2 confirmations, or 3 when it flaps. Any
  failure, connection move, or version or capability-set change restarts the
  count.
- **Bounded logs and health.** Log and health content is closed-set reasons
  and details, timestamps and counts. There is no exception text, URL or token.

Preserve the following:
- stable v1.1.2;
- persisted formats;
- every tool descriptor and input schema (88 tools, 89 with the optional YAML
  reader admitted, as in beta.25);
- probe fingerprints, Core capability profiles, owner-signed Core registry
  entries and the signed ha-mcp registry;
- the HOLD and the support window;
- workflows;
- every dependency except `certifi`.

The health `recovery` object and the three new counters are the only
public-surface change. They are additive and authorized by the task.

### Dependency (HAMCP-217)

- `certifi==2026.10.19` with the single `py3-none-any` hash
  `42d35340cc5b9ba78e818ddbff0c5b1c2b779de448a340fd1b989f5fa8670e1b` must
  appear in both `hass_mcp_engineering_beta/requirements.lock` and
  `tests/requirements.lock`.
- `build-inputs.json` must record `certifi: 2026.10.19` and the new
  `runtime_lock_sha256`.
- `build_inputs.py source` must pass with 39 distributions.
- No other pin or hash changes.
- `requirements.txt` is unchanged.
- CI's `pip-audit --strict` on the runtime lock must report no known
  vulnerabilities.
- `tests/test_hamcp217_certifi_20261019_contract.py` (11 tests) pins:
  - the removal of the `emSign ECC Root CA - G3` root;
  - the retained unaffected emSign roots;
  - the exact bundle trust, `CERT_REQUIRED` and hostname checking of each
    Engineering httpx path;
  - refusal of a root outside the bundle, with zero requests and a positive
    control;
  - the aiohttp boundary (aiohttp does not read certifi).
- The test lock's `urllib3` 2.7.0 findings (PYSEC-2026-4175/4176/4177) are
  pre-existing. They are outside this release under the 2026-10-19 owner
  decision and are not in the image.

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

- `tests/test_hamcp215_core_authority_recovery.py` (16 tests) covers:
  - each transient (REST HTTP failure, connection loss, timeout and
    authentication failure; lifecycle-watcher attach failure and timeout),
    going from retirement through backoff and confirmations to automatic
    restoration of the catalog and the index;
  - a persistent failure backing off to the cap, logging and refusing;
  - an incompatibility staying retired and being re-checked without backoff;
  - flapping escalating and never toggling between confirmations;
  - calls being refused during the outage, with zero dispatch, and succeeding
    afterwards;
  - log levels, reasons and health fields.
- Two lifecycle tests in `tests/test_ha_core_2026_9_integration.py` now assert
  the confirming state before republication. Recovery-delay assertions in
  `tests/test_rc3_postcore_regressions.py` follow the new backoff.
- Mutations (`docs/evidence/hamcp215-mutations/`): 19 of 19 killed,
  including all of these:
  - no recovery loop (killed by the 120 s runner timeout: an unpaced retry
    chain)
  - fixed-interval retry
  - restoration without confirmation, the recovery gate or the lifecycle fence
  - no hysteresis or no flap escalation
  - fixed or uncapped backoff
  - each WARNING event (retirement, verification failure, attempt,
    restoration, catalog change) removed
  - health fields not recorded

## 2. Release CI and publication

Require exact-head CI before release acceptance. Pending or unavailable jobs
are not passing evidence.

Publication needs Josh's Ready authorization and the protected merge
handoff. Verify the following with the shipped verifiers:
- the merged source;
- v2.4.1-beta.26;
- the immutable version and commit images;
- provenance and SBOM.

Never invent future image identities. Retain the beta.25 artifacts and the
known-good earlier artifacts for recovery.

No stored format changes. Rollback is reinstalling the beta.25 artifact.

## 3. Installed Engineering

Steps 1 to 4 are read-only. Step 5 is an optional, owner-run controlled
transient.

1. **Installed-image binding.** Separately authorize installation of the
   published Engineering artifact. Bind the running add-on to:
   - the published image index digest;
   - the v2.4.1-beta.26 tag;
   - the protected `main` merge commit that contains this PR.
2. **Catalog.** Using fresh protocol enumeration, confirm:
   - 88 tools, or 89 with the optional YAML reader admitted;
   - every descriptor unchanged from beta.25;
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
   - the counters `recovery_attempts`, `recovery_confirmations` and
     `authority_restorations` present.
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
