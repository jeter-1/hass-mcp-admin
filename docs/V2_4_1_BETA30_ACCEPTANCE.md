# Engineering 2.4.1-beta.30 acceptance

This contract covers HAMCP-131: incident-window log reads and continuable
history. It adds two read-only tools, `get_incident_logs` and
`get_history_slices`. It follows 2.4.1-beta.29 (HAMCP-213 PR 1).

See also:
- Release notes:
  [`V2_4_1_BETA30_RELEASE_NOTES.md`](V2_4_1_BETA30_RELEASE_NOTES.md).
- Sources report, authority table and design:
  [`HAMCP_131_INCIDENT_LOGS.md`](HAMCP_131_INCIDENT_LOGS.md).

**Release target.** `2.4.1-beta.30` is staged by one owner-authorized run of
`scripts/promote_next_release.py --apply` on a base that carries
2.4.1-beta.29. The run consumes the declaration. HAMCP-132 slice 2 also
targets beta.30. If another release takes beta.30 first, this PR merges that
base with a merge commit and takes the next free beta.

The contract separates four stages:
1. source and offline acceptance;
2. release CI;
3. publication;
4. installed Engineering acceptance.

It authorizes none of the later stages.

## 1. Source and offline acceptance

Bind the following:
- Josh's HAMCP-131 objective;
- `2.4.1-beta.30` materialized in all three version authorities, with exact
  acceptance and release-note resolution from `scripts/codex-context.py` and
  no staged declaration left over;
- the PR head SHA;
- the passing PR `validate` run for that exact head;
- the Evidence tier log named in the PR.

The review needs a security section covering:
- **New sources.**
  - Supervisor `GET /core/logs` and `GET /addons/self/logs`, through the
    HAMCP-200 typed routes, with `Accept: text/plain`, `verbose`, `no_colors`
    and a gatewayd `Range: entries=...` header.
  - The add-on keeps `hassio_role: homeassistant` and `hassio_api: true`. No
    permission is added.
  - Supervisor, host and other add-on logs are reported `unavailable`
    (`authority_not_granted`, pinned ROLE_MANAGER pattern) with zero requests.
- **Delegated history.** The new gateway `history_reader` selects only the
  admitted `ha_get_history` automatic-read route at ha-mcp 8.5.0 or 8.6.0. It
  builds every argument itself:
  - one entity;
  - absolute UTC bounds;
  - `order: asc`, `minimal_response: true`,
    `significant_changes_only: true`, `source: history`;
  - a bounded `offset` and `limit`.

  There is no direct Home Assistant fallback, and the native `get_history`
  tool is not used.
- **One sanitized exit point.** Each service assembles its complete envelope,
  including entries or states, coverage, omissions, cursor and limitations.
  It then runs the strict sanitizer once, in `_finish`, with the known
  secrets. A redaction makes the result partial.
  `SANITIZER_SITES` pins the only call sites.
- **Slugs.** `addon:<slug>` must pass the HAMCP-200 `path_segment` guard plus a
  slug pattern before anything else runs. A traversal attempt is refused with
  zero requests.
- **Cursors.** Cursors are HMAC-signed with a process key and expire after
  1 hour. They bind the query fingerprint, the fixed end and, for history,
  the provider contract digest. Continuity breaks fail closed as
  `STALE_CURSOR`.
- **Reachability.** There is zero new write, service call, reload, restart,
  deletion, forwarding or fallback reachability. The AST read-only tests and
  routing policies are `writes: none`, `fallback: none`.

Preserve the following:
- stable v1.1.2;
- persisted formats;
- every existing descriptor and input schema. The catalog is **92 tools,
  93 with the optional YAML reader admitted**: 64 static plus 28 delegated.
- probe fingerprints, Core capability profiles, owner-signed Core registry
  entries and the signed ha-mcp registry;
- the HOLD and the support window;
- workflows, add-on permissions and dependencies.

### Offline proof

No test reaches a live system. Every fixture is synthetic, and any
credential-shaped string is assembled at test time.

- `tests/test_hamcp131_incident_logs.py` (33 tests) covers:
  - the motivating window: the thermostat 503 at about 12:01 on 2026-10-19,
    plus the Presence Simulation exception;
  - window edges (start inclusive, end exclusive), the retention edge, an
    empty journal and an after-tail window;
  - locating the start in a large journal within budget, and a start beyond
    probe reach as a disclosed gap;
  - pagination with identical timestamps, with no duplicates or skips;
  - the level and literal text filters with skip counts;
  - a vacuum between pages (stale) and inside a call (gap);
  - cursor integrity, query binding and expiry;
  - per-source HTTP refusals (403, 404, 400), and a stopped add-on still
    readable;
  - all sources statically unavailable, refused with zero requests;
  - slug traversal refused with zero requests;
  - a planted-secret sweep across every output field;
  - message truncation that keeps markers whole, and the response byte
    ceiling;
  - the single-exit AST test, the exit pass's known secrets and
    redaction-makes-partial, the read-only static test, routing, the Core
    gate and the catalog;
  - a literal text filter cannot probe a redacted secret;
  - policy drift.
- `tests/test_hamcp131_history_slices.py` (36 tests) covers:
  - the motivating window;
  - window edges, a state exactly at a slice boundary, and the retention
    edge;
  - pagination with identical timestamps at limits 1 to 100;
  - the cursor across slices with per-page coverage, and the empty-slice call
    cap with resume;
  - a defaulted end fixed by the cursor;
  - purge, late write, a changed slice-start state and a purged start state,
    each stale;
  - a break after the first check: partial, no cursor;
  - a provider contract change: stale;
  - cursor integrity, query binding and expiry;
  - upstream failure: partial and resumable;
  - unpinned shapes: `representation_invalid`;
  - gateway partial disclosed;
  - the byte ceiling with resume;
  - planted secrets;
  - the single-exit AST test, the exit pass's known secrets and
    redaction-makes-partial;
  - invalid arguments, route unavailable, unreviewed version and anonymous
    caller, each sending nothing;
  - the route retired mid-read;
  - the static read-only test, routing, Core gate and catalog, and policy
    drift;
  - the admitted reader, using the 8.5.0 and 8.6.0 captures: the fixed
    argument shape, argument refusal, parent authority refusal, route drift
    and gateway sanitization.
- Count and catalog pins in the existing suites are updated from 62 to 64
  static tools.
- Mutations: `docs/evidence/hamcp131-mutations/` (31 mutants across window
  edges, cursor skip/duplicate, silent omission, sanitizer placement and slug
  handling; 31 killed, recorded in `results.json`).

## 2. Release CI and publication

Require exact-head CI before release acceptance. Pending or unavailable jobs
are not passing evidence.

Publication needs Josh's Ready authorization and the protected merge handoff.
Verify the following with the shipped verifiers:
- the merged source;
- v2.4.1-beta.30;
- the immutable version and commit images;
- provenance and SBOM.

Never invent future image identities. Retain the beta.29 artifacts and the
known-good earlier artifacts for recovery.

No stored format changes. Rollback is reinstalling the beta.29 artifact.

## 3. Installed Engineering

All installed checks below are read-only. None of them writes, reloads,
restarts or calls a service.

1. **Installed-image binding.** Separately authorize installation of the
   published Engineering artifact. Bind the running add-on to:
   - the published image index digest;
   - the v2.4.1-beta.30 tag;
   - the protected `main` merge commit that contains this PR.
2. **Catalog.** Using fresh protocol enumeration, confirm:
   - 92 tools, or 93 with the optional YAML reader admitted;
   - `get_incident_logs` and `get_history_slices` present, with read-only
     annotations;
   - every other descriptor unchanged from beta.29;
   - Core signed selection, storage and audit health, and zero fallback.
3. **Live log read across Core, Supervisor and one add-on.** Call
   `get_incident_logs` with
   `sources: ["core", "supervisor", "addon:self", "addon:<another installed slug>"]`
   over a recent window of at most 6 hours that contains a known Core error.
   Record:
   - for `core` and `addon:self`: `status`, `requests`,
     `earliest_available`, `latest_available`, `retention_edge_reached`,
     `gaps`, and at least one entry with timestamp, level and sanitized
     message;
   - for `supervisor` and the other add-on: `status: unavailable`,
     `reason: authority_not_granted`, `requests: 0` and the
     `required_route_pattern`;
   - `omissions` and `completeness`;
   - that the serialized response contains no token, credential or secret
     value.
4. **Continued log read.** If `has_more` is true, call again with the same
   arguments and `next_cursor`. Confirm the first entry follows the last
   entry of the previous page, with no duplicate.
5. **Continued history read.** Call `get_history_slices` for one entity known
   to change state, with a window longer than one 6-hour slice (for example
   24 hours) and `limit` small enough to force a cursor. Follow `next_cursor`
   until `has_more` is false. Record:
   - `state_at_window_start`;
   - per-page `coverage.complete_until` and the slice rows;
   - `provider.upstream_version` and `fallback: none`;
   - that states across pages are strictly in order with no duplicates.

   Compare the states with the delegated `ha_get_history` output for the same
   window.
6. **No change.** Steps 3 to 5 modify no configuration, add-on, journal or
   recorder state.

No push, CI trigger, signing, publication, installation or live action is
authorized by source completion.
