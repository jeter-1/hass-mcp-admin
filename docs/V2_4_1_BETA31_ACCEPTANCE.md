# Engineering 2.4.1-beta.31 acceptance

This contract covers HAMCP-220: `get_incident_logs` entry times from the
journal realtime, with local-time text read in Home Assistant's zone. It
follows 2.4.1-beta.30 (HAMCP-131).

See also:
- Release notes:
  [`V2_4_1_BETA31_RELEASE_NOTES.md`](V2_4_1_BETA31_RELEASE_NOTES.md).
- Findings, citations and design:
  [`HAMCP_220_INCIDENT_LOG_TIMESTAMPS.md`](HAMCP_220_INCIDENT_LOG_TIMESTAMPS.md).

**Release target.** `2.4.1-beta.31` is staged by one owner-authorized run of
`scripts/promote_next_release.py --apply` on a base that carries
2.4.1-beta.30. The run consumes the declaration.

HAMCP-132 slice 2 (#294) and HAMCP-069B (#297) also target beta.31. If
another release takes beta.31 first, this PR merges that base with a merge
commit and takes the next free beta.

The contract separates four stages:
1. source and offline acceptance;
2. release CI;
3. publication;
4. installed Engineering acceptance.

It authorizes none of the later stages.

## 1. Source and offline acceptance

Bind the following:
- Josh's HAMCP-220 objective;
- `2.4.1-beta.31` materialized in all three version authorities, with exact
  acceptance and release-note resolution from `scripts/codex-context.py` and
  no staged declaration left over;
- the PR head SHA;
- the passing PR `validate` run for that exact head;
- the Evidence tier log named in the PR.

The review needs a security section covering:
- **Sources and requests.** The routes, headers, `Range` arithmetic, request
  and byte budgets are unchanged. Time comes only from data Supervisor already
  returns: the `X-First-Cursor` header and the line text. No permission, role,
  route or add-on configuration is added.
- **Zone.** The zone is read from the add-on process environment (`TZ`, set
  by Supervisor) and resolved with `zoneinfo`. The name must be a bounded IANA
  name. No file path is accepted, and nothing falls back to UTC. No Core or
  Supervisor request is added; the Core-independence declaration stands.
- **Fail closed.**
  - A page whose text matches no rendering of the first entry's realtime is
    a failed page (`timestamp_basis_mismatch`).
  - A missing or invalid zone makes Core and `addon:self` unavailable with
    zero requests.
  - Uncertain times are never resolved to one instant.
- **One sanitized exit.** The new fields are assembled into the envelope
  before the single `_finish` sanitizer pass. `SANITIZER_SITES` is unchanged.
- **Reachability.** No new write, service call, reload, restart, deletion,
  forwarding or fallback is reachable. The read-only AST tests pass.

Preserve the following:
- stable v1.1.2;
- persisted formats;
- every descriptor and input schema. The catalog is 92 tools, 93 with the
  optional YAML reader admitted;
- probe fingerprints, Core capability profiles, signed registries, the HOLD
  and the support window;
- workflows, add-on permissions and dependencies.

**Review decisions** (see the findings document):
- the zone source is the add-on `TZ`, not `/api/config`;
- `ts` is nullable under the unchanged schema id `incident-logs-v1`.

### Offline proof

No test reaches a live system. All data is synthetic.

- `tests/test_hamcp220_incident_log_timestamps.py` (30 tests) uses text from
  the 2026.10.3 formatter, the 2026.09.1 formatter and the offset form, with
  systemd v257.9-shaped cursors. It covers:
  - the live reproduction: Chicago text `07:34:40.940` for 12:34:40.940Z, an
    11:30Z-12:34:48Z window returning all its Core and `addon:self` entries
    in order, with `latest_available` 12:34:40.940Z and 12:34:41.230Z and no
    `window_after_tail`;
  - Home Assistant in UTC, in Asia/Tokyo and in Asia/Kolkata (+05:30);
  - the pinned UTC rendering recognised under a Chicago zone;
  - DST fall-back (2026-11-01 01:30 Chicago): both candidates
    06:30Z and 07:30Z, `ts: null`, ambiguous;
  - spring-forward (2026-03-08 02:30): nonexistent, both candidates;
  - a first entry inside the repeated hour, exact from its realtime;
  - an uncertain entry at a window edge: returned, marked, read partial; a
    tail that may be inside the window is read, not `window_after_tail`;
  - window edges given in UTC and as local offsets;
  - ordering across Core and `addon:self`;
  - zone missing or invalid: unavailable, zero requests, partial; zone lost
    between pages;
  - Supervisor rendering in a different zone from the add-on's:
    `timestamp_basis_mismatch`;
  - no realtime in the cursor: read in the zone, partial;
  - the explicit offset form, its realtime check and mixed forms;
  - zone-name validation.
- `tests/test_hamcp131_incident_logs.py` (78 tests) runs the 37 HAMCP-131
  service tests twice: once with the 2026.09.1 UTC rendering, and once with
  2026.10.3 local rendering in America/Chicago. Cursor, coverage, gaps,
  authority reporting and sanitization are otherwise unchanged.
- Mutations: see `docs/evidence/hamcp220-mutations/`. The runner covers:
  - text parsed as UTC;
  - an ambiguous or nonexistent time resolved silently;
  - zone fallback to UTC;
  - calibration bypassed;
  - the 40 HAMCP-131 mutants, with five moved fragments retargeted.

  The results are recorded in `results.json`.

## 2. Release CI and publication

Require exact-head CI before release acceptance. Pending or unavailable jobs
are not passing evidence.

Publication needs Josh's Ready authorization and the protected merge handoff.
Verify the following with the shipped verifiers:
- the merged source;
- v2.4.1-beta.31;
- the immutable version and commit images;
- provenance and SBOM.

Never invent future image identities. Retain the beta.30 artifacts for
recovery.

No stored format changes. Rollback is reinstalling the beta.30 artifact.

## 3. Installed Engineering

All installed checks below are read-only. None of them writes, reloads,
restarts or calls a service.

1. **Installed-image binding.** Separately authorize installation of the
   published Engineering artifact. Bind the running add-on to:
   - the published image index digest;
   - the v2.4.1-beta.31 tag;
   - the protected `main` merge commit that contains this PR.
2. **Catalog.** Using fresh protocol enumeration, confirm:
   - 92 tools, or 93 with the optional YAML reader admitted;
   - every descriptor unchanged from beta.30;
   - zero fallback.
3. **Live recent-window read (the defect).**
   1. Note the current UTC time `T`.
   2. Call `get_incident_logs` with `sources: ["core", "addon:self"]`,
      `start: T - 60 minutes` and `end: T`, both in UTC (`Z`).
   3. In the same minute, read the newest Core log text with
      `get_core_log_history`.

   Confirm and record:
   - `time_basis.time_zone` is Home Assistant's configured zone, with
     `time_zone_status: loaded`;
   - for `core` and `addon:self`: `status`, `latest_available` within a few
     minutes of `T` (not `T` minus the zone offset), the reason not
     `window_after_tail`, and `timestamp_basis.text_rendering`;
   - at least one returned Core entry whose message matches a line in the
     `get_core_log_history` output, and whose `ts` equals that line's local
     time converted to UTC;
   - every returned `ts` within `[T - 60 min, T)`, with `ts_basis` and
     `time_zone` on each entry;
   - `omissions` and `completeness`;
   - that the serialized response contains no token, credential or secret
     value.
4. **Continued read.** If `has_more` is true, follow `next_cursor`. Confirm the
   first entry follows the last entry of the previous page, with no
   duplicate.
5. **No change.** Steps 2 to 4 modify no configuration, add-on, journal or
   recorder state.

No push, CI trigger, signing, publication, installation or live action is
authorized by source completion.
