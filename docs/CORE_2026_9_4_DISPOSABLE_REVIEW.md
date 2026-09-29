# Core 2026.9.4 disposable compatibility review

This test-only branch extends PR #219's synthetic continuity checks with actual
Core containers. It is not a release or a production registry update. Nothing in
this branch admits an installed Home Assistant system.

The baseline is Engineering `981fc82be5e8660dd97f58b1cea378d1e7e3d97f`
(2.4.0-beta.7), with PR #219's test-only commit
`14372b767287bcf9a90b378c439017820b5f8df0`. Runtime, stable-v1, production trust,
existing workflows, locked dependencies and release declarations stay unchanged.
The checked-out CI SHA identifies this test candidate, not a new runtime release.

## Exact inputs and boundaries

`tests/fixtures/core_2026_9_4_lane_provenance.json` binds reviewed Core commit
`9212531f40a0b7b23229a90d688dd79d9dfccff4`, source tree/archive and immutable
multi-architecture index/manifest identities. The source and image records were
checked separately against upstream public evidence; the fixture is an input to
testing, not a signed production admission. Provider images, source and vendored
skills remain the existing exact ha-mcp 8.5.0 selection.

The new workflow runs only on pushes to `codex/core-2026-9-4-contract-check` in
`jeter-1/hass-mcp-admin`. It uses `contents: read`, existing immutable action pins,
locked client dependencies and ephemeral test signing. No production credentials,
Home Assistant routes, host maintenance, package publication or deployment are
used. Upstream source retrieval and container pulls contact public providers.
Core and provider requests stay in the disposable runner topology. This does not
certify Supervisor lifecycle, Nabu Casa forwarding or physical-device feedback.

## Required execution evidence

1. General amd64 lane: the exact historical 2026.7.2 fixture writer, Core .4
   migration, configuration reads/writes, helper state, governed F3 verification,
   traces, device/child registry, dependency semantics and script-call scenario.
   The old writer is a synthetic migration control, never a household downgrade.
2. Native amd64 and arm64 typed lanes: actual Core .4 with both standalone and
   add-on ha-mcp 8.5.0, 25 admitted reads, complete provider catalog, typed fan and
   light/switch operations, exact readback/restoration, duplicate suppression,
   stale zero-dispatch rejection, response/readback loss and read-only recovery.
   The add-on path uses the existing narrowly scoped synthetic Supervisor relay.
3. In each applicable lane, the same Engineering instance changes from no Core
   authority to 19 admitted references only through an ephemeral signed test
   entry. No production compiled version is added. Unknown releases remain
   rejected; PR #219 uses .5 for the synthetic unknown-version control.
4. Final receipts must show successful cases and cleanup, no retained execution
   locks/leases/commits/fallback, exact CI checkout and Core/provider identities.
   Fault injection and simulated lease expiry are not process-crash evidence.

Finite job and command timeouts apply. `always()` cleanup removes only each
job's synthetic resources; the typed lane checks ownership labels. The general
lane uses fixed dedicated names on a fresh hosted runner and checks absence.
Administrator file deletion is restricted to root-owned disposable runner
stores, following the existing CI pattern; it does not grant Scriptbox Docker
access. Private stores/tokens are excluded from artifacts. Only bounded existing
JSON receipts are uploaded; useful general contract results remain in CI logs.
Failed or partial runs remain evidence and cannot count as compatibility PASS.

## Interpretation and later stages

Offline preparation and synthetic .4 tests are necessary but cannot substitute
for the three real container jobs. The .3-to-.4 source review found no established
runtime correction. Core's Tuya fan change turns off at zero percentage, while
speed feedback can retain a nonzero value: Engineering must still refuse to
claim an exact off/zero result when readback disagrees. A synthetic fan passing
does not establish real Tuya behavior.

After all actual jobs pass, independently review the receipts against the 19
unchanged capability contracts before preparing a production registry entry.
Owner signing and publication remain separate. Require observed installed .4
admission and a stable ordinary reconciliation interval before a fresh F029 plan
and approval. This test branch neither authorizes that apply nor reuses an old
plan. Keep the separate beta.8 release work and previous evidence intact.
