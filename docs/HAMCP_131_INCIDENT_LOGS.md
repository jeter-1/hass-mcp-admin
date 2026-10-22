# HAMCP-131: Incident-Window Log Reads and Continuable History

Status: implementation notes for the `2.4.1-beta.30` Engineering pull request.
These notes describe source behavior at exact pins and the reviewed read
contract. They are not deployment evidence; the live check is in
[`V2_4_1_BETA30_ACCEPTANCE.md`](V2_4_1_BETA30_ACCEPTANCE.md).

## Scope

Two read-only Engineering-native tools:

- `get_incident_logs` reads one bounded time window from Home Assistant
  journals through fixed Supervisor routes and merges them chronologically.
- `get_history_slices` reads continuable entity history through the admitted,
  gateway-sanitized delegated `ha_get_history` route. It does not use the native
  `get_history` REST tool, which is not under the strict sanitizer (HAMCP-194 is
  parked).

Neither tool writes anything, adds a permission, changes add-on configuration,
reads log files from the filesystem, or falls back to another provider.

## Part 1: Sources Report

All citations are from read-only source inspection at these pins. No live Home
Assistant was accessed.

| Repository | Ref | Commit |
|---|---|---|
| home-assistant/supervisor | `2026.09.1` | `40e3ee7640a3c44abe67f1a1397f39c3cd949806` |
| systemd/systemd | `v257.9` | `147c30b613960bc24595435bd902a5c4beb5aba9` |
| home-assistant/core | `2026.10.1` | `abe1fdd7cd6b4de1715c87b6fd87d92699514ef9` |
| homeassistant-ai/ha-mcp | `8.5.0`, `8.6.0` | admitted captures used by `tests/test_hamcp162_yaml_admission.py` and `tests/test_upstream_8_6_compatibility.py`; see [`HA_MCP_8_6_0_COMPATIBILITY.md`](HA_MCP_8_6_0_COMPATIBILITY.md) |

The Supervisor pin matches the Supervisor version the existing Engineering
documents treat as current. Production HAOS ships its own systemd build. Its
`journal-gatewayd` version is not pinned by any source this repository
controls, so the design relies only on Range forms that have existed in
gatewayd for many releases. It does not use `realtime=`.

### Supervisor log endpoints and parameters

Routes are registered by `_register_advanced_logs(path, identifier)` in
`supervisor/api/__init__.py` L167-222. Each path gets five routes:
`{path}/logs`, `/logs/follow`, `/logs/latest`, `/logs/boots/{bootid}` and
`/logs/boots/{bootid}/follow`.

| Endpoint | Registration | Journal identifier |
|---|---|---|
| `/core/logs` | `api/__init__.py` L648 | `homeassistant` |
| `/supervisor/logs` | L609-622 (local `get_supervisor_logs`, Docker-log fallback on error) | `hassio_supervisor` |
| `/host/logs`, `/host/logs/identifiers/{id}`, `/host/logs/boots/...` | L236-266 | default identifier set or the path identifier |
| `/addons/{slug}/logs` | L752-765 (local `get_app_logs`) | `addon_<slug>` and `app_<slug>` (L710-713) |

`self` has no route of its own. It is resolved inside `{slug}` by
`api/apps.py` L159-176, which returns the add-on whose token made the call.

All of these use the same handler, `APIHost.advanced_logs_handler`
(`supervisor/api/host.py` L284-404):

- `Accept` must be absent or exactly `text/plain`, `text/x-log` or `*/*`. Any
  other value returns 400 (L317-327).
- `verbose` takes effect when present; its value is ignored. It selects the
  verbose formatter: a UTC `YYYY-MM-DD HH:MM:SS.mmm`, then the hostname, then
  `identifier[pid]: message` (`utils/systemd_journal.py` L56-75).
- `no_colors` strips ANSI escapes when present.
- `lines` overrides any Range header (L335-349). Engineering never sends it.
- A `Range` header is forwarded to gatewayd unchanged (L352-353). Without one,
  the handler sends `entries=:-99:100` (the last 100 entries).
- The response is `text/plain`, one entry per line followed by `\n`.
  `X-First-Cursor` holds the journal cursor of the first entry. It is set only
  once an entry exists (L368-373).
- An upstream reset before any output returns 400. After output has started,
  the stream ends early with status 200 (L390-403).

### Time filtering

Supervisor has no time parameter. The only position controls are gatewayd
Range forms (`journal-gatewayd.c` v257.9):

- `entries=<cursor>:<skip>:<n>` seeks to the cursor, steps `skip` entries that
  match the filters, then returns up to `n` entries (L310-371, L581-590,
  L178-183). `n` must be at least 1.
- Skips count only entries matching the filters (`sd-journal.c` `next_with_matches`
  L978-998).
- A cursor that no longer exists is not an error. The seek resolves to the
  nearest entry in that direction (`sd_journal_seek_cursor` L1263-1364,
  `find_location_with_matches` L947-958). A vacuum can therefore silently shift
  a position by one entry. Engineering detects that with the digest check
  described below.
- `realtime=` exists at v257.9 (L453-455). Engineering does not use it,
  because HAOS gatewayd is unpinned and a gatewayd that ignores an unknown
  Range form returns the whole journal.

So `get_incident_logs` filters time itself. It finds the window start with a
tail probe (`entries=:-1:2`, the last two entries: a cursorless negative skip
runs `seek_tail` at L590, then `previous_skip(2)` at L179, whose first step
lands on the last entry), a head probe that gives the earliest retained entry, galloping
single-entry probes back from the tail (`entries=T:-K:1`) and a bounded
bisection. It then pages forward with `entries=C:k:P+1`. Each page re-reads the
last consumed entry, and that entry's keyed digest must match. The verbose UTC
millisecond timestamp is the time basis. Core's own line prefix, which is in
local time (`homeassistant/bootstrap.py` L586-588), is used only to parse the
level.

### Authority table

Engineering's add-on manifest declares `hassio_api: true` and
`hassio_role: homeassistant`. This change does not alter either. The role
patterns come from `supervisor/api/middleware/security.py` at the pin:
`api_bypass` L99-108, ROLE_HOMEASSISTANT L121-127, ROLE_MANAGER L134-163, and
the check order L365-383.

| Source token | Route | Supervisor rule that decides | Status for role `homeassistant` | Reported as |
|---|---|---|---|---|
| `core` | `/core/logs` | ROLE_HOMEASSISTANT `/core/.+` (L124) | **readable** | entries |
| `addon:self` | `/addons/self/logs` | `api_bypass` `/addons/self/(?!security\|update)[^/]+` (L101) | **readable** (Engineering's own log) | entries |
| `supervisor` | `/supervisor/logs` | ROLE_MANAGER `/supervisor/.+` (L160) | refused (403) | `unavailable`, `authority_not_granted` |
| `host` | `/host/logs` | ROLE_MANAGER `/host/.+` (L149) | refused (403) | `unavailable`, `authority_not_granted` |
| `addon:<slug>` (other add-on) | `/addons/<slug>/logs` | ROLE_MANAGER `/addons/<slug>/(?!security).+` (L137) | refused (403) | `unavailable`, `authority_not_granted` |

**Reading Supervisor, host or other add-on logs would need role `manager`.**
The task does not allow adding a role. Each such source is therefore reported
as unavailable with exact data and **zero requests**:

- `reason: authority_not_granted`;
- `detail`: "requires Supervisor role manager; Engineering runs with
  hassio_role homeassistant and this read adds no permission";
- `required_role: manager`;
- `required_route_pattern`: the pinned pattern.

It is never silently omitted. A call that selects only such sources is not
refused generically: it returns a partial result with no entries, one
`unavailable` row per source carrying the reason and required role above, and
zero requests (HAMCP-131 review M5). Because Core logs and Engineering's own add-on log
are readable, the STOP condition ("nothing beyond Core logs is readable") is not
met.

#### Disclosed quirk, deliberately not used

`/.+/info` (L123) also matches `/host/logs/identifiers/info`. Any role can
therefore read host journal entries whose syslog identifier is literally
`info`. This is an accident of pattern matching, not a granted authority, so
Engineering never requests it.

#### Not installed or stopped add-ons

These cases matter only for `addon:<slug>`, which is not readable here:

- An unknown slug returns 404 (`apps.py` L170-172).
- A slug in the store but not installed returns 400 (L173-174).
- A stopped but installed add-on still serves its retained journal: `get_app_logs`
  checks neither app nor container state (L707-714).

The service maps an HTTP refusal on a readable route to a per-source
`unavailable` or `failed` row with the status code. Tests cover
404/400/403 refusals, stopped-add-on readability and wrong-shape responses.

### History continuation

`ha_get_history` is admitted at ha-mcp 8.5.0 and 8.6.0. Its `_fetch_history`
has an identical AST in both versions. It sends one
`history/history_during_period` WebSocket call for the whole requested window,
then slices `[offset:offset+limit]` on the client side and reports
`total_count`. Offsets are therefore stable only over fixed absolute bounds.
`_convert_timestamp` (8.6.0 `tools_history.py` L54-69) renders the WebSocket
epoch timestamps with `datetime.fromtimestamp(value, tz=UTC).isoformat()`, so
times arrive in UTC with a `+00:00` offset.

Home Assistant 2026.10.1 recorder semantics for one entity with
`minimal_response`:

- The window-start state is the state of the last row with
  `last_updated < start`. It is returned only when the database holds a row
  older than `start`, and it is reported AT `start`, not at its own time: the
  start-state query selects `literal(value=0)` as `last_updated_ts`
  (`recorder/history/__init__.py` L115-117) and the state is built with
  `last_updated_ts or start_time_ts` (`recorder/models/state.py` L56, L157).
  `state_at_window_start.since` is therefore `null` for that state; a real
  change inside the 1 ms overlap before the window start keeps its own time
  (review L2).
- Changes are rows with `start < last_updated < end`. A row exactly at `start`
  is in neither set.
- Consecutive equal states are collapsed.

`get_history_slices` cuts the window into fixed 6-hour slices. Each slice is
one absolute-bounded request `[S − 1 ms, E)`. The 1 ms overlap places a row
exactly at a slice boundary inside one request. Requests are ascending, minimal
and offset-paged. Continuity checks:

- The cursor binds a keyed chained digest of every offset range the previous
  page consumed: its range in the current slice (from the state before the
  page's first offset), and each finished slice the page returned states
  from, with that slice's total (review M1). A continued page re-reads the
  current-slice range in its first request and each finished-slice range in
  one verification call (at most 12, reported as `verification_calls`). Any
  difference, including an in-place change or a balanced insert and delete
  that keeps every offset and the last state, fails as stale. History
  returned two or more pages back is checked only through offset, overlap
  and slice-start continuity.
- Reads after the first in the same page overlap by one state and compare
  its keyed digest of (timestamp, state).
- More than 100 entries before a slice start (the start state plus rows in
  the 1 ms overlap) is not the pinned shape and fails as
  `representation_invalid`; the bound keeps every verification range inside
  one bounded request.
- A new slice requires the state prevailing before its start to equal the last
  state already read.
- The signed cursor binds the query fingerprint, the fixed end, the provider
  contract digest (upstream version and admitted fingerprint) and the position.
  A changed provider contract therefore fails as stale.

Outcomes:

| Event | Result |
|---|---|
| A verification range or the first check of a continued call differs | `STALE_CURSOR`, reason `history_continuity_broken` |
| A later check in the same call fails | `partial`, omission `continuity_broken`, no cursor |
| Upstream failure, including a verification read | `partial` with a resumable cursor |
| Response byte ceiling | the page shrinks, then resumes after the last kept state |

### Secrets risk per source

| Source | Risk | Control |
|---|---|---|
| Core journal | Integration errors can echo URLs with credentials, tokens, headers and payloads | `sanitize_log_lines` (multi-line context window), then the strict `sanitize_untrusted_data` over the whole envelope with `known_secrets` (HA token and access secret) and a shared `ReferenceMarkerRegistry`. Redaction makes the result partial (`redacted_by_policy`). |
| Engineering add-on journal | Engineering never logs credentials, but upstream exception text may appear | Same pipeline as Core. |
| Supervisor, host and other add-ons | Not read | Static refusal with zero requests. |
| Recorder history (delegated) | State strings are user- or integration-controlled and can contain secrets | Gateway sanitization on delegated output (`UpstreamReadGateway.execute`), then the strict sanitizer at the single exit with `known_secrets`. Redaction makes the result partial. |
| Cursors | Could carry raw positions | HMAC-signed with a process key and a 1-hour TTL. The query fingerprint is an HMAC, so the text filter is not revealed. The cursor itself is registered as an output and passes through the sanitizer. |

## Design

### `get_incident_logs`

Inputs (raw-validated before SDK coercion; `additionalProperties: false`):

| Input | Bound |
|---|---|
| `sources` | 1-4 distinct tokens: `core`, `supervisor`, `host`, `addon:<slug>` |
| `start`, `end` | ISO 8601 with offset, at most millisecond precision; `[start, end)`; **maximum span 6 hours**; `end` defaults to now and at most 120 s in the future |
| `min_level` | `debug`, `info`, `warning`, `error`, `critical` |
| `text` | case-insensitive literal match, 1-128 characters, no control characters, matched against **sanitized** text only |
| `limit` | 1-200, default 100 |
| `cursor` | the signed continuation, at most 4096 characters |

The slug in `addon:<slug>` must pass the HAMCP-200 `path_segment` guard and
`[a-z0-9][a-z0-9_-]{0,63}`. Anything else is refused before any request.

Output:

- `entries[]`: `ts`, `source`, `process`, `level`, `level_basis` (`parsed`,
  `inherited` or none) and the sanitized `message`, truncated at 2000
  characters without splitting a redaction marker.
- `sources[]`, one row per requested source:
  - `status` (`complete`, `partial`, `failed`, `unavailable`) and `reason`;
  - `route`, `requests`, `http_status`;
  - `earliest_available` and `latest_available` (the retention edges);
  - `retention_edge_reached`, `scanned_from`, `scanned_to`;
  - `entries_consumed`, `entries_returned`, `skipped_by_filter`,
    `outside_window`;
  - `gaps[]`, each with a reason;
  - for unavailable sources: `required_role`, `required_route_pattern` and
    `detail`.
- `omissions[]` with reasons, plus `truncated`, `has_more`, `next_cursor`,
  `complete` and `completeness`.
- `requests`, `downloaded_bytes`, `bounds`, `pins` and `limitations`.

Call bounds:

| Bound | Value |
|---|---|
| Requests per source | 16 |
| Requests per call | 24 |
| Bytes per page | 256 KiB |
| Bytes per call | 1 MiB |
| Per-request deadline | 10 s |
| Per-call deadline | 25 s |
| Filter scan | 2000 entries |
| Response ceiling | 44,000 bytes |

### `get_history_slices`

Inputs:

| Input | Bound |
|---|---|
| `entity_id` | `domain.object_id`, at most 255 characters |
| `start`, `end` | **maximum span 7 days**; `end` defaults to now |
| `limit` | 1-100, default 50 |
| `cursor` | at most 4096 characters |

Output:

- `state_at_window_start`;
- `states[]`, each with `at`, `state` (at most 255 characters) and `slice`;
- `coverage`, with `continued`, `complete_until` and a per-slice row.
  `complete_until` claims its timestamp inclusively only when the next unread
  state is known to be later. When more states may share that timestamp (a
  page that ends inside a tie, a byte cut, or an unknown next state), the
  bound is exclusive and the cursor offset is the tie-break (review M3);
- `omissions`, `truncated`, `has_more`, `next_cursor`, `complete`;
- `upstream_calls` (verification calls included), `verification_calls` and
  `provider` (upstream tool and version, route
  `upstream_read_gateway`, fallback `none`);
- `bounds` and `limitations`.

Each call makes at most 12 slice reads plus at most 12 verification reads.

### Shared architecture

- **One exit.** Each service builds its response only in `_finish`, which is
  the only place a sanitizer runs. Merging, filtering and limiting happen
  before it, on raw values. An AST test pins this, and
  `test_hamcp191_shared_sanitizer.SANITIZER_SITES` lists exactly these call
  sites.
- **Time-ordered merge across pages (review M2).** A page emits nothing
  later than the earliest unread point across all sources: an open source's
  last read entry, or the window start for a deferred source. Finished and
  failed sources obey the same bound; a finished source's held entries resume
  from its cursor position, and a failed source's are disclosed in its
  `unread_after_failure` gap. When the request budget ends before a source's
  window start is located, the source is deferred (`status` `partial`,
  `reason` `request_budget_exhausted`, `start_location` `deferred`, cursor
  state `{"u": 1}`) instead of being failed or anchored past an unread span,
  and the next page locates it. A later page therefore never returns an
  entry earlier than one already returned. A deferral happens only once the
  shared call budget is spent, so no source reads more on that page; the
  merge bound and the fill stop are defense in depth. Continued sources make
  no request before the merge is filled, so the first deferred source has the
  whole call budget on the next page, which covers the costliest locate
  (tail, head, every probe and every bisection step, 16 requests): a deferred
  source is always located on the next page.
- **Typed routes.** Journal requests use HAMCP-200 `rest_path` with the
  registered templates `/core/logs` and `/addons/self/logs`. No caller value
  becomes a path segment. History uses only the gateway reader, which builds
  every upstream argument.
- **Routing, policy and Core gate.**
  - `get_incident_logs` uses capability `incident_logs` and policy
    `incident_logs_read_v1`. It is classified Core-independent, Supervisor-only,
    beside `get_core_log_history`, so it stays available while Core is down.
  - `get_history_slices` uses capability `history_slices` and policy
    `history_slices_read_v1`. It is mapped to `core.basic_rest_read` in
    `STATIC_TOOL_CORE_REQUIREMENTS`.
  - Both are `ENGINEERING_NATIVE`, with writes `none` and fallback `none`.
- **Tool count.** 62 → 64 static Engineering tools.

## Disclosed limitations

- Supervisor, host and other add-on logs are unavailable without role
  `manager`. This is reported, not worked around.
- A journal source ends at its first entry at or after `end`. An out-of-order
  earlier timestamp after that entry is not read.
- A short page is taken as the current tail. An interrupted stream that still
  returns 200 cannot be told apart from the end of the journal (Supervisor
  L390-403).
- Multi-line journal messages appear as separate lines. Lines without a
  timestamp inherit the previous entry's level (`level_basis: inherited`).
- History continuation can be stale when no history changed. Causes:
  - an exact row at `S − 1 ms`;
  - a late recorder commit near the present;
  - a purge between pages.

  Each refuses safely. The caller restarts without the cursor.
- Empty output never proves an event did not occur.

## Rollback

Revert the pull request, or deploy `2.4.1-beta.29`. Neither tool persists
state. Cursors are process-keyed and become invalid after a restart.
