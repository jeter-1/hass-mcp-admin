# Engineering 2.4.1-beta.32 acceptance

This contract covers HAMCP-220. `get_incident_logs` takes entry times from
the journal realtime, and reads local-time text in Home Assistant's
configured zone, which it reads from Core's `/api/config`. It follows
2.4.1-beta.31 (HAMCP-069B).

See also:
- Release notes:
  [`V2_4_1_BETA32_RELEASE_NOTES.md`](V2_4_1_BETA32_RELEASE_NOTES.md).
- Findings, citations, the owner's zone-source decision and the design:
  [`HAMCP_220_INCIDENT_LOG_TIMESTAMPS.md`](HAMCP_220_INCIDENT_LOG_TIMESTAMPS.md).

**Release target.** `2.4.1-beta.32` is staged by one owner-authorized run of
`scripts/promote_next_release.py --apply` on a base that carries
2.4.1-beta.31. The run consumes the declaration. If another release takes
beta.32 first, this PR merges that base with a merge commit and takes the
next free beta.

The contract separates four stages:
1. source and offline acceptance;
2. release CI;
3. publication;
4. installed Engineering acceptance.

It authorizes none of the later stages.

## 1. Source and offline acceptance

Bind the following:
- Josh's HAMCP-220 objective, his review 1 decisions (H1: the zone from
  `/api/config`; M1: ambiguous-span ordering) and his review 2 decision (the
  zone bound across the call by a second `/api/config` read);
- `2.4.1-beta.32` materialized in all three version authorities, with exact
  acceptance and release-note resolution from `scripts/codex-context.py` and
  no staged declaration left over;
- the PR head SHA;
- the passing PR `validate` run for that exact head;
- the Evidence tier log named in the PR.

The review needs a security section covering:
- **Sources and requests.**
  - The Supervisor routes, headers, `Range` arithmetic, request and byte
    budgets are unchanged.
  - Journal time comes only from data Supervisor already returns: the
    `X-First-Cursor` header and the line text.
  - Resolution reads are ordinary one-entry pages on the same routes,
    counted in the same budget.
  - No permission, role, Supervisor route or add-on configuration is added.
- **The Core read (owner-authorized, H1).**
  - A call that reads a journal makes one `GET /api/config` before the
    journal reads and one after them (review 2), at most two. Each goes
    through the existing `HomeAssistantRestClient` and the registered
    HAMCP-200 `/config` route, with a 5 s deadline.
  - Each runs under the zone reader's own `core.basic_rest_read` lease:
    acquired, committed, revalidated before dispatch and after the
    response, then finished.
  - The dispatch authorizer chains any prior one. The caller's authorizer
    and error code are restored afterwards.
  - Only `time_zone` is used, and it must be a bounded IANA name.
  - `routes.py` reclassifies `get_incident_logs` as
    `CORE_INDEPENDENT_DOWNSTREAM_AUTHORITY`. The route stays reachable while
    Core is down, and the lease is the tool's own.
  - The protected files are listed in the PR body.
- **Fail closed.**
  - An unreadable zone (lease refused, error, timeout or malformed
    payload) reads no journal: `time_zone_unavailable`, zero journal
    requests, partial. The add-on `TZ` and UTC are never substituted.
  - An add-on `TZ` whose offsets differ from Home Assistant's anywhere in
    the window makes every source partial with `zone_mismatch`.
  - A first-entry or resolution realtime that disagrees with the text has
    the same effect: the source is partial with `zone_mismatch`.
  - None of these reads is ever complete, even with zero entries.
  - If the zone read after the journal reads differs from the first, or
    cannot be read, every source that read the journal is withheld: no
    entries or times, partial, `zone_changed_during_read` or
    `zone_unverifiable_after_read`. A continued source resumes where this
    call started it. A change reverted between the two reads is not seen.
  - A page whose text matches no rendering is a failed page.
  - An unresolved time is never given one instant, and an order around it
    is never claimed (`ordering_uncertain`, partial).
- **One sanitized exit.** The new fields are assembled into the envelope
  before the single `_finish` sanitizer pass. `SANITIZER_SITES` is unchanged.
- **Reachability.** No new write, service call, reload, restart, deletion,
  forwarding or fallback is reachable. The read-only AST tests pass; they
  pin the zone reader's single `client.request('GET', rest_path('/config'))`.

Preserve the following:
- stable v1.1.2;
- persisted formats;
- every descriptor and input schema. The catalog is **93 tools, 94 with the
  optional YAML reader admitted**;
- probe fingerprints, Core capability profiles, signed registries, the HOLD
  and the support window;
- workflows, add-on permissions and dependencies.

**Review decisions** (see the findings document):
- the zone source is Home Assistant's `/api/config` `time_zone`, by Josh's
  review 1 decision, which supersedes the add-on `TZ`;
- `ts` is nullable under the unchanged schema id `incident-logs-v1`;
- by Josh's review 2 decision, the reviewer's `ZoneRaceProbe` is committed
  with only its lease-cycle count amended from one to two.

### Offline proof

No test reaches a live system. All data is synthetic, and Core is a loopback
peer.

- `tests/test_hamcp220_incident_log_timestamps.py` (63 tests). It uses text
  from the 2026.10.3 formatter, the 2026.09.1 formatter and the offset form,
  with systemd v257.9-shaped cursors. It covers:
  - the live reproduction (Chicago);
  - the Chicago/Panama review reproduction: Home Assistant's zone is used
    when the add-on `TZ` is stale. The read is clean when offsets agree in
    the window, and fails as `zone_mismatch` when they differ;
  - zones that agree except across a DST transition (Denver/Phoenix);
  - a Home Assistant zone change at runtime, with and without the
    Supervisor still rendering the old zone;
  - a one-hour rendering error refused;
  - a zone change between pages, during a continuation and across DST,
    and the second zone read failing: withheld, partial, never complete;
  - the zone read refused, malformed, raising or unreadable: unavailable,
    zero requests, partial, never UTC;
  - DST fall-back and spring-forward, resolved exactly within the budget,
    and kept in journal order and partial beyond it;
  - the reviewer's fall-back reproduction;
  - a multi-page fall-back with two sources, and one through the whole
    repeated hour that drains every entry with every page advancing;
  - resolution reads keeping a request for the other source's first page;
  - ordering uncertainty carried to the next page;
  - an uncertain tail resolved;
  - a disagreeing resolution read failing closed;
  - window edges, Core and `addon:self` ordering, no realtime, the offset
    form and zone-name validation;
  - `ZoneReaderCases` and `ZoneRebindReaderCases`, which drive the real
    REST client against a loopback `/api/config`, including both lease
    cycles and a refused or failing second read.
- `tests/test_hamcp220_review1_probes.py` and
  `tests/test_hamcp220_review1_tool_local_probe.py`: the review 1 probes,
  unchanged.
- `tests/test_hamcp220_review2_zone_race_probe.py`: the review 2
  `ZoneRaceProbe`, with only the lease-cycle count amended (1 to 2).
- `tests/test_hamcp131_incident_logs.py` (81 tests) runs the HAMCP-131
  service tests under both renderings. It adds:
  - the configure wiring;
  - a refused Core lease reading no zone and no journal, through the
    gateway stack.
- Mutations: see `docs/evidence/hamcp220-mutations/`. `results.json`
  records each mutant and the tests that killed it. The runner covers:
  - text parsed as UTC;
  - an ambiguous or nonexistent time resolved silently;
  - zone fallback to UTC;
  - calibration bypassed;
  - Home Assistant's zone bypassed (`H1*`): the add-on `TZ` used when it
    differs, or preferred, or used when the zone is unreadable; the
    reviewer's widened tolerance; the reader's lease checks;
  - ambiguous-span ordering asserted (`M1*`): earliest-candidate ordering
    across ambiguous pages; the ordering mark, carry or omission dropped;
    the tail left unresolved;
  - the zone not bound across the call (`Z*`): the post-read revalidation
    removed, a changed or unreadable second zone accepted, withheld
    sources publishing or losing their cursor, the omission or partial
    status dropped;
  - the HAMCP-131 set, with moved fragments retargeted.

## 2. Release CI and publication

Require exact-head CI before release acceptance. Pending or unavailable jobs
are not passing evidence.

Publication needs Josh's Ready authorization and the protected merge handoff.
Verify the following with the shipped verifiers:
- the merged source;
- v2.4.1-beta.32;
- the immutable version and commit images;
- provenance and SBOM.

Never invent future image identities. Retain the beta.31 artifacts for
recovery.

No stored format changes. Rollback is reinstalling the beta.31 artifact.

## 3. Installed Engineering

All installed checks below are read-only. None of them writes, reloads,
restarts or calls a service.

1. **Installed-image binding.** Separately authorize installation of the
   published Engineering artifact. Bind the running add-on to:
   - the published image index digest;
   - the v2.4.1-beta.32 tag;
   - the protected `main` merge commit that contains this PR.
2. **Catalog.** Using fresh protocol enumeration, confirm:
   - 93 tools, or 94 with the optional YAML reader admitted;
   - every descriptor unchanged from beta.31;
   - zero fallback.
3. **Live recent-window read (the defect).**
   1. Note the current UTC time `T`.
   2. Call `get_incident_logs` with `sources: ["core", "addon:self"]`,
      `start: T - 60 minutes` and `end: T`, both in UTC (`Z`).
   3. In the same minute, read the newest Core log text with
      `get_core_log_history`.

   Confirm and record:
   - `time_basis.time_zone` is Home Assistant's configured zone, with
     `time_zone_status: loaded`, `time_zone_reads: 2` and
     `time_zone_after_read_status: agrees`;
   - `time_basis.addon_time_zone_status` (`agrees` is expected);
   - for `core` and `addon:self`:
     - `status`;
     - `latest_available` within a few minutes of `T`, not `T` minus the
       zone offset;
     - a reason other than `window_after_tail`;
     - `timestamp_basis`;
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
