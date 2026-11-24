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

`get_audit_log(lines=50, event="", cursor="")` is read-only. Its effective
page size is always clamped to 1 through 500: zero and negative values produce
at most one record, the default remains 50, and values above 500 produce at
most 500. Event
filtering occurs without disabling the bound, and existing global response
sanitization still applies. RC2dev7 parses each bounded JSONL object and
compares only the exact, case-sensitive top-level `event` value. Nested tool
arguments, messages, exceptions, and metadata cannot satisfy a filter. The
reader returns the most recent matching records in their existing order.

Blank, malformed, truncated, non-object, and records larger than 64 KiB are
skipped without returning their raw content. Later valid records remain
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

A client walks the whole log by calling with no cursor (the newest page) and
following `next_cursor` until `complete` is true. Every matching record present
in the file when the walk started is returned exactly once, in file order
across pages read oldest-last. Records written during a walk are not returned
by it; a new walk starts at the then-newest record.

The cursor is opaque and signed (HMAC-SHA256 with a key generated per process).
It carries a format version, a byte offset into the log, a truncated SHA-256 of
the log file's identity (device and inode) and a truncated SHA-256 of the
`event` filter. It carries no record content, no event name and no path, and is
at most 512 characters. The cursor is checked before the log is read:

- a malformed, tampered or overlong cursor, or one signed by another process
  (for example, after a restart), is refused with `invalid_cursor`;
- a different `event` filter, a rotated or removed log (rotation replaces the
  file at 5 MiB), or a log shorter than the cursor's offset is refused with
  `stale_cursor`. Start a new walk.

Redaction is unchanged: records are redacted when written, and the reader
returns them as written. The reader reads only the current log, not the
rotated `audit.jsonl.1`.

Known limitation, pending a decision: `get_audit_log` has no reviewed audit
argument shape in `routing.py`, so its own audit record stores its arguments
as given, including a raw `cursor`. The cursor carries no record content, but
this departs from the rule above that records exclude raw cursors. Recording
only `cursor_present`, as the other cursor tools do, requires a change to
`routing.py`, a protected file outside this item's authorized files.

### Migration review (public input schema)

- Additive only: one optional input, `cursor` (string, default empty). `lines`
  and `event` keep their names, types and defaults; no input is required.
- Output: every response now ends with the `_page` trailer line, including
  the two empty-page messages. A client that parsed every line as an audit
  record must skip the last line (its only top-level key is `_page`). This is
  the one visible change for existing calls.
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
