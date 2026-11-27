# Audit-log contract

RC2 does not change this contract. Build SHA and build time remain
`server_info` identity fields and are not added to secret-redaction paths or to
health/capability output. Image provenance never contains a token, credential,
authenticated URL, or branch credential. Clean governance initialization and
inspection alone produce no audit write.

The beta writes bounded, recursively redacted JSONL records to beta-only add-on
storage. Records include request correlation, tool/category/access, safe caller
identity, result/error, duration, endpoint categories, server version, and
tool-specific bounded intent. They exclude access secrets, authentication
material, authenticated URLs, raw cursors, full configuration, traces, logs,
history, diffs, and unbounded analytical evidence.

`get_audit_log(lines=50, event="", cursor="", summary=false)` is read-only. Its effective
page size is always clamped to 1 through 500: zero and negative values produce
at most one record, the default remains 50, and values above 500 produce at
most 500. Event
filtering occurs without disabling the bound, and existing global response
sanitization still applies. RC2dev7 parses each bounded JSONL object and
compares only the exact, case-sensitive top-level `event` value. Nested tool
arguments, messages, exceptions, and metadata cannot satisfy a filter. The
reader returns the most recent matching records in their existing order.

Blank, malformed, truncated, non-UTF-8, non-object, and records larger than
64 KiB are skipped without returning their raw content. Only two skips are
counted in the trailer (see below): a non-UTF-8 row (`skipped_undecodable`) and
a record that alone exceeds the response limit (`omitted_oversized`). The other
skips are silent, so `complete: true` does not mean every line in the log was a
record. Later valid records remain
queryable. Valid historical objects without a string event remain available to
unfiltered reads but cannot match a filtered read. Records are returned as the
verbatim JSONL lines written to the log; `No audit log yet.` and
`No matching entries.` remain the empty-page messages.

## Pages and the cursor (HAMCP-475)

A page is fitted to `response_size_limit` (UTF-8 bytes, trailer included). When
the newest `lines` matching records do not fit, the page keeps the newest
records that do and drops older ones, which the cursor then returns. A record
is never cut. Every response ends with exactly one trailer line:

```json
{"_page": {"returned": 12, "next_cursor": "<opaque>", "complete": false}}
```

- `returned` is the number of records on the page (`0` on an empty page).
- `next_cursor` is `null` when no older matching record remains. Otherwise,
  passing it back with the same `event` returns the next older page. `lines`
  may change between pages.
- `complete` is `true` exactly when `next_cursor` is `null`.
- A record that alone exceeds the limit cannot be returned. The page then holds
  no records, the cursor moves past that record, and the trailer adds
  `"omitted_oversized": 1`. The key is absent otherwise. Records are written
  at most `audit_max_payload_chars` (default 8,192) and the default limit is
  60,000, so this applies only when the limit is configured below the payload
  bound, or to a historical record larger than the limit and within the
  64 KiB read bound.
- `skipped_undecodable` counts the non-UTF-8 rows skipped in the byte range the
  page covered. Over a walk each such row is counted once, on one page. The key
  is absent when the count is zero.

A client walks the whole log by calling with no cursor (the newest page) and
following `next_cursor` until `complete` is true. Every matching record present
in the file when the walk started is returned exactly once, in file order
across pages read oldest-last. Records written during a walk are not returned
by it; a new walk starts at the then-newest record. A page reads only whole,
newline-terminated rows inside the file size captured when it opens the log. A
last row still being written, or one completed after that point, is left out
and returned by a later walk.

The cursor is opaque and signed (HMAC-SHA256 with a key generated per process).
It carries a format version, a byte offset into the log, a truncated SHA-256 of
the log file's identity (device and inode), a truncated SHA-256 of the `event`
filter, and a truncated keyed digest (HMAC-SHA256 with the same per-process
key) of the log bytes before the offset. It carries no record content, no event
name and no path, and is at most 512 characters. The keyed digest cannot be
matched against log bytes offline. The cursor's signature is checked before the
log is read:

- a malformed, tampered or overlong cursor, or one signed by another process
  (for example, after a restart), is refused with `invalid_cursor`;
- a different `event` filter, a rotated or removed log (rotation replaces the
  file at 5 MiB), a log shorter than the cursor's offset, or a log whose bytes
  before the offset changed is refused with
  `stale_cursor`. The last check covers a log rewritten in place and a second
  rotation whose new file reuses the freed inode, since the file identity alone
  can repeat. Appending to the log does not make a cursor stale. Start a new
  walk.

Cursors do not survive an add-on restart or upgrade: the signing key exists
only in process memory, so every outstanding cursor is then `invalid_cursor`.
After a restart, start a new walk without a cursor.

Redaction is unchanged: records are redacted when written, and the reader
returns them as written. The reader reads only the current log, not the
rotated `audit.jsonl.1`.

A `get_audit_log` call's own audit record stores `lines`, `event` and
`cursor_present` (a boolean), as the other cursor tools do. The cursor itself
is never recorded, whether the call succeeds or is refused.

### Migration review (public input schema)

- Additive only: one optional input, `cursor` (string, default empty). `lines`
  and `event` keep their names, types and defaults; no input is required.
- Output: every response now ends with the `_page` trailer line, including
  the two empty-page messages. A client that parsed every line as an audit
  record must skip the last line (its only top-level key is `_page`). This is
  the one visible change for existing calls. The trailer's three fields are
  always present; `omitted_oversized` and `skipped_undecodable` are added only
  when nonzero.
- A non-UTF-8 row used to fail the whole call; it is now skipped and counted.
- The tool's own audit record replaces a raw `cursor` with `cursor_present`.
- A default call (`lines=50`) whose newest 50 records exceed the response limit
  now returns fewer records plus a cursor, instead of an oversized response.
- The description gains two lines naming the trailer and the walk. Tool count,
  category, risk, route, provider and policy are unchanged.
- Cursors live in process memory (the signing key) and never survive a
  restart or an upgrade. There is no persisted format.

A refused `upsert_automation` is recorded as write-capable intent rejected
before provider dispatch. Only the bounded automation ID and
`governance_required` refusal are retained; the configuration payload and HA
endpoint categories are absent. Provider policy refusals, authentication/rate
rejections, and cursor/validation failures are tool or gateway outcomes, not
fabricated upstream provider failures.

RC2dev6 assigns the authentication audit classes, and RC2dev7 makes their
retrieval semantically exact and separately filterable. Ordinary pre-dispatch
rejection records `auth_failure` with
`authentication_failure`; rejection by the exhausted authentication-failure
bucket records `auth_failure_throttled` with `rate_limit_exceeded`; authenticated
general limiter rejection remains `rate_limited`. Each request produces one
event class. `get_audit_log` compares the parsed top-level field rather than
serialized text. These gateway outcomes do not change provider, analysis, or governance
failure counters.

Beta 25 adds bounded external-approval lifecycle events: requested, optionally
viewed, granted, rejected, expired, invalidated and consumed. Records may include
safe plan/challenge IDs, kind, channel, bounded principal, result and timestamps.
They never include CSRF nonces, cookies, Ingress authentication material, raw
headers, MCP access secrets, request notes, full configuration/diffs, or
authenticated URLs. A preapproval apply/rollback refusal is not a provider
failure because no provider write was dispatched.

Beta 2 classifies every create-plan tool as `access=proposal` with
`analysis_summary.operation_class=proposal`. Proposal creation persists
governance state, so it is not a pure read, but it dispatches no operational
action. Approval requests and governed apply remain `access=write`.
Operational lifecycle events correlate proposal, approval, dispatch intent,
provider completion or expected disruption, readback-only reconciliation,
verification, and final outcome. They include bounded provider and fallback
attribution but never raw provider responses, credentials, authenticated URLs,
arbitrary service data, or unbounded Home Assistant payloads.

## Summary (HAMCP-518)

`summary=true` returns one bounded JSON object instead of a page: counts for
the whole current audit file, so a client need not walk every page to count
calls. The response is one line, with no `_page` trailer:

```json
{"_summary": {"scope": "current_audit_file", "event_filter": null, "records": 6,
  "first_timestamp": "...", "last_timestamp": "...",
  "events": {"tool_call": 4, "auth_failure": 1, "rate_limited": 1},
  "tools": [{"tool": "ha_get_state", "calls": 3, "result_status": {"success": 2, "error": 1}}],
  "tools_total": 2, "tools_omitted": 1, "calls_omitted": 1,
  "untracked_tool_calls": 0, "skipped_undecodable": 0, "skipped_unparsed": 0}}
```

- It reads the same whole-row snapshot a page reads, in one pass, and writes
  nothing. It is derived from the audit file, so it survives a restart as the
  file does; rotation (at 5 MiB) starts it again from the new file. `scope`
  says so. The rotated `audit.jsonl.1` is not read.
- `event` still filters: only matching records are counted, and
  `event_filter` names the filter (`null` for none). `lines` is ignored.
- `records` counts the matching records. `first_timestamp` and
  `last_timestamp` are the first and last record timestamps in file order
  (`null` when none), at most 64 characters.
- `events` counts records by top-level `event`. For `tool_call` records,
  `tools` gives each `tool_name` with its call count and a count per
  `result_status`. A missing or non-string name counts as `unknown`.
- Bounds:
  - every name is cut to 128 characters;
  - `events` holds at most 32 names and each `result_status` map at most 16;
    further names are counted under `other`;
  - at most 2,048 distinct tools are tracked; calls to further tools are
    counted in `untracked_tool_calls`;
  - `tools` lists at most 200 tools, ordered by calls (most first) then name,
    and fewer when needed to fit `response_size_limit`. `tools_total` is the
    number of tracked tools; `tools_omitted` and `calls_omitted` count the
    tracked tools left out and their calls. A tool is left out whole, never
    cut.
- `skipped_undecodable` counts non-UTF-8 rows and `skipped_unparsed` counts
  other non-blank rows that are not a bounded JSON object. Their content is
  never returned. Only counts and the bounded names above leave the file; no
  record content, parameters or cursor material does.
- `summary=true` with a `cursor` is refused with `validation_failure`
  (`field: cursor`, `reason: not_allowed_with_summary`), before the log is
  read.
- The call's own audit record adds `summary`: the flag as a boolean, or
  `null` when the value sent was not a boolean. It is recorded alongside
  `lines`, `event` and `cursor_present`; the cursor is still never recorded.

### Migration review (public input schema)

- Additive only: one optional input, `summary` (boolean, default `false`).
  `lines`, `event` and `cursor` keep their names, types and defaults; no
  input is required.
- Output: with the default, every response is byte-identical to before.
  Only `summary=true` returns the `_summary` object.
- The description gains one line naming `summary`. Tool count, category,
  risk, route, provider and policy are unchanged.
- There is no persisted format: the summary is derived on each call.
