# Engineering 2.4.2-beta.9 acceptance

**Status: source and offline acceptance recorded; release CI, publication
and installed acceptance pending.** This contract covers the 2.7.0 build,
released as 2.4.2-beta.9. It follows 2.4.2-beta.8 (HAMCP-332, #438). Its
content landed through item pull requests into the `release/2.7.0`
integration branch. This release-docs commit fills in the sections that were
marked pending; the stages that have not happened yet stay marked
**pending**.

See also the release notes:
[`V2_4_2_BETA9_RELEASE_NOTES.md`](V2_4_2_BETA9_RELEASE_NOTES.md).

**Release target.** `2.4.2-beta.9`, following 2.4.2-beta.8. Josh decided on
2026-11-14 (relayed by the architect) that HAMCP-371 ships as 2.4.2-beta.9,
and the value is authorized on the HAMCP-371 release path only. "2.7.0" names
the build, not the version string.

**Why the version is materialized now.** `main` shipped 2.4.2-beta.8 (#438),
and CI validates every pull request against `origin/main` with a
materialized, unstaged release whatever its base
(`scripts/validate_addon_metadata.py`, `.github/workflows/ci.yml`). So
2.4.2-beta.9 is materialized once on `release/2.7.0`, at its base (`main` at
e741f2ed), and item pull requests carry no version change of their own.

The release is materialized in all three version authorities, with no staged
declaration left. Nothing is published or deployed.

The contract has four stages:
1. source and offline acceptance;
2. release CI;
3. publication;
4. installed read-only acceptance.

It authorizes none of the later stages.

## Items

Each item is reviewed in its own pull request into `release/2.7.0`. An item
that has not landed is not part of this release, and this contract makes no
claim about its behavior. Landed items are listed by their pull request; the
final release pull request binds their evidence.

| Item | Scope | State |
| --- | --- | --- |
| HAMCP-371 | ha-mcp 9.2.0 admission, with the HAMCP-374 per-read real-output replay | landed: #443, head 067e443d, merged by Josh as ffe8c156 on 2026-11-16 at 16:36 CST |

## 1. Source and offline acceptance

**HAMCP-371** (the only landed item):
- **Owner decision and scope.** Josh decided on 2026-11-14 (relayed by the
  architect) that HAMCP-371 ships as 2.4.2-beta.9. Its scope, protected-path
  grants and exceptions are the architect rulings of 2026-11-11 to 2026-11-16
  listed in #443.
- **Item pull request.** #443, `agent/HAMCP-371-ha-mcp-9.2.0-admission` into
  `release/2.7.0`. Base d9a3fb39, reviewed head
  067e443df21107d2305048b838cd3d767d0d80cb. Josh merged it as
  ffe8c156af6b0223d678920b145c86ec89fa2e9a (parents d9a3fb39 and 067e443d).
  The merge commit's tree is the reviewed head's tree (55cc5a98).
- **Review record.**
  - Independent review by Cursor (`grok-4.7-xhigh`) of `e741f2ed..067e443d`,
    in two parts: **APPROVE**. Part A had no findings. Part B had one, F1
    (Low): four `s8/gate/*.txt` exemptions are in no SHA256SUMS. The owner
    accepted F1 as Low and kept the entries (ruling 2026-11-16 15:23 CST).
    The review made no edits.
  - The architect's independent review of #443 (head 067e443d, base
    d9a3fb39) passed, with no findings against HAMCP-371 (2026-11-16 16:39
    CST). The architect did not read the new `upstream_reads_9_2*` modules
    line by line; the Cursor review, the Full and Evidence tiers and CI
    43960418854 cover them.
- **Offline evidence** (local, scriptbox, head 067e443d, base d9a3fb39):
  - Full tier: rc 0; 15,158 tests run, 32 skipped, 0 failures; 32 protected
    paths declared, all authorized.
  - Evidence tier: rc 0, "Evidence validation passed"; 15,158 run, 32
    skipped. The scope declaration equals the recomputed one, and the
    snapshot is clean and stable at 067e443d. The metadata check passed
    against `origin/main` e741f2ed, and the secret scan was clean.
  - The refusal paths are described in #443's Security section and tested
    in its diff, for example `tests/test_hamcp371_unreachable_modes.py`,
    `tests/test_hamcp371_blueprint_9_2.py` and
    `tests/test_hamcp371_search_pair.py`.
- **CI.** Run 43960418854 on #443: success, 29 of 29 jobs, 15:28 to 15:46 CST
  on 2026-11-16. It built the pull request merge ref 2d458f63 (parents
  d9a3fb39 and 067e443d), whose tree (55cc5a98) equals the tree of the merged
  `release/2.7.0` head ffe8c156.
- **Security section.** In #443. In short:
  - no new write or service name;
  - the existing governed dashboard, power, fan and helper-value writes
    each gain an exact 9.2.0 contract row (`ha-mcp-v9.2.0-9de5bf6c`,
    upstream 51e3aac7), and `python_transform` stays prohibited;
  - no direct Home Assistant fallback and no upstream fallback;
  - no workflow or permission change;
  - unreachable 9.2.0 modes are refused before dispatch, unknown blueprint
    top-level keys are refused, and unknown platform-read keys are withheld
    and counted.
- **Archives** (scriptbox `~/archives/`):

  | Archive | sha256 |
  | --- | --- |
  | `hamcp371-fe2-067e443d-20261116T131052CST.tar.gz` (Full and Evidence records) | `dea27651019c4a14f39042e34b863d14e95e8f330bf87e19b9be86846d87819c` |
  | `hamcp371-s15-cursor-067e443d-20261116T141924CST.tar.gz` (independent review) | `706f03431e59807efd42b9788222ad683913fdfb1c8f52d134ec0f3644ffadf4` |
  | `hamcp371-067e443d.bundle` (heads d9a3fb39 and 067e443d) | `caa90c02b9005dc1caea2d9e9445acfa2dce4182870e1580375e44553eb7e8e7` |

**Release head.**
- `2.4.2-beta.9` is materialized in all three version authorities (b7c7cd75,
  at the base of `release/2.7.0`).
- The final head is this release-docs commit on top of ffe8c156. It changes
  only this file and the release notes. The release pull request names its
  SHA.
- **The Evidence tier did not run on the final head.** It ran once, on
  067e443d, whose tree the merge commit ffe8c156 keeps unchanged. No
  further Full or Evidence run was authorized. The final head's own
  `validate` run is reported in the release pull request.

## 2. Release CI

The exact materialized final head's `validate` check passes, including the
release transition. **Pending:** CI of the release pull request
(`release/2.7.0` into `main`).

## 3. Publication

Publication happens only through Josh's Ready action and the repository's
head-bound workflow. **Pending.**

## 4. Installed read-only acceptance (separately authorized, after deploy)

This stage runs only after Josh separately authorizes deploying this
release. Every step is a read.

1. **Identity.** `server_info` and `get_server_health` report:
   - `2.4.2-beta.9`;
   - the merged release SHA as build SHA;
   - `dirty: false`.
2. **Item checks.** HAMCP-371: the additional checks of Deploy step 7,
   below.
3. **Tool catalog.** A fresh `tools/list` is taken, and its Engineering,
   delegated and total tool counts are recorded. CI did not record the 9.2.0
   Engineering count or connector total; live acceptance step 7 records
   them. The CI-observed values are in the release notes' Tool catalog
   section.

PASS needs every check. If a step fails, record the response in the release
task. Whether to roll back is Josh's decision.

## Deploy (Engineering 2.4.2-beta.9 with ha-mcp 9.2.0)

Deploying is separately authorized by Josh. Nothing here authorizes it.

0. Export the beta.8 audit (`get_audit_log`). Take a FULL Home Assistant
   backup in the UI. List every object acceptance creates as it is created.
1. Deploy Engineering beta.9 while ha-mcp 8.5.0 and component 2.2.1 still
   run. Confirm it works on 8.5.0: health shows `reviewed_exact` and
   `admitted_exact`. Expected values, CI-observed on the 8.5.0 lane (run
   43960418854, job 133626243207 `exact-image-read-gateway`, artifact
   `exact-image-read-gateway-8.5.0`, file
   `exact-image-read-gateway-result.json`): Engineering 86, delegated 27,
   total 113, upstream 77, upstream catalog fingerprint
   `0d7ecdb7f58b9b3f92fe05e0f3afbd213da6d5841263219d8c12c3dea1d77845`. That
   lane exposed no optional upstream tools (`optional_exposed_tools` is
   empty). The house runs with the filesystem flag on, so record the live
   advertised and reviewed counts as observed.
2. Take a partial backup of the ha-mcp add-on plus the Home Assistant
   configuration folder.
3. Update the ha-mcp add-on to 9.2.0.
4. Update the HACS `ha_mcp_tools` component to 9.2.0, then restart Core.
   Server and component 9.2.0 deploy as a pair; never run one without the
   other.
5. Confirm 9.2.0 from Engineering's detected upstream version, not the store
   label (the v9.2.0 `config.yaml` still says 9.1.2). Confirm the HACS
   version.
6. Confirm that the installed ha-mcp image digest equals the amd64 manifest
   `sha256:928c55d6ba38d0219058be2e0ae4ae36375eb3db7aad0cb21c5b573848ed9244`
   (S8 record:
   `docs/evidence/hamcp371/s8/app-reads/provenance-gate.txt`).
7. Acceptance, per section 4 above, plus:
   - r27 `ha_get_app`, replayed live for the first time;
   - a blueprint get;
   - `ha_search`, with the unified variant recorded;
   - r20 and r26 withhold the 31 names of the P4-B table
     (`docs/evidence/hamcp371/sr/p4b-withheld-names.md`: 18 on r20, 13 on
     r26);
   - the filesystem flag still on;
   - `ha_get_history` with an explicit-offset `start_time` succeeds;
   - the same call with a date-only `start_time` is refused as
     `invalid_request` with the offset message;
   - the actual Engineering, delegated and total tool counts, recorded.

**Update holds until beta.9 is accepted:** the ha-mcp add-on, HACS
`ha_mcp_tools`, and Core on 2026.11.0 (HAMCP-367).

## Rollback

**Engineering.** Recovery from a bad 2.4.2-beta.9 is 2.4.2-beta.8, by
restoring the full Home Assistant backup taken right before deploy (Deploy
step 0).
- Export `get_audit_log` before any restore.
- In-place downgrade from beta.9 to beta.8 is not supported and is unproven.
- CI's rollback image is 2.4.2-beta.7. It is not the recovery target
  (HAMCP-405 moves it to beta.8). In CI 43960418854, that beta.7 image
  refuses the 9.2.0 server (`blocked_incompatible_upstream`, case
  `sbs_engineering_rollback_91_refused`).
- From source, not tested: beta.8 (`main` at e741f2ed) carries no ha-mcp
  9.2.0 admission. Beta.8 with the 9.2.0 pair still running is therefore not
  an admitted pairing.

**ha-mcp pair.** Restore the step-2 add-on backup, redownload component 2.2.1
in HACS, and restart Core. Restore the configuration folder only if the HACS
redownload fails, because it reverts every configuration change made since
the backup.
