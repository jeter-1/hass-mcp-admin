# Bounded native logbook reads

`get_logbook(hours=12, entity_id="")` uses the existing native Home Assistant
REST provider and signed Core read authority. It requests an explicit interval
from one captured UTC end time back by `hours`. Older code omitted `end_time`,
which selected Core's default one-day interval even for a 72-hour request.

HAMCP-294 adds optional window and filter arguments (see **Bounded window and
entity filter** below); the tool count is unchanged. A call without them keeps
the original arguments and result shapes. Hours must be finite,
positive and no greater than 168. An optional entity ID selects one entity and
must be at most 255 characters. Sparse 72-hour and 168-hour requests, filtered
or unfiltered, remain supported. Invalid input makes no Home Assistant request.

Each operation performs one GET, with no automatic retry, redirect, split query
or fallback. Acquisition uses the smaller of the configured HA timeout and
30 seconds. The body is streamed with a 1 MiB cap independent of Content-Length;
only identity content encoding is accepted for successful responses. Received
HTTP errors retain their status without reading the error body. A client timeout or cancellation
does not prove that Core has stopped its database query.

JSON processing permits at most 10,000 records, 50,000 examined values and
object keys, and 32 nested containers. Duplicate keys, nonfinite numbers,
invalid UTF-8/JSON and non-object logbook records are rejected. Budget accounting
includes malformed/rejected members. Processing runs off the event loop; one
logbook operation per process may acquire or process at a time. There is no
waiting backlog. Cancellation during processing retains that slot until the
worker actually finishes; it does not claim to terminate a running thread.
A concurrent read returns retryable `logbook_busy` (HTTP classification 409)
without another HTTP attempt. Wait for the active read to finish before making
a later request; this classification does not schedule an automatic retry.

Small complete responses keep their existing `data` list. Large valid responses
return a source-order prefix of whole sanitized records in `data.entries`, with
`truncated=true`, `returned`, `omitted`, `requested_interval`, `ordering`, and
`required_action`. The existing provider wrapper attributes these responses as
partial. Source order is not a promise of chronological order. Counts describe
the received API response, not all historical events; absence cannot establish
that an event never happened.

The reader sizes its payload allowance from the same minimal envelope used by
final formatting, including the current request ID and provider completeness.
This permits useful whole-record partial results even at small response limits;
optional timing/envelope detail may be omitted with explicit truncation. If even one useful record cannot fit, it returns the
non-retryable `logbook_response_limit_exceeded` error instead of an empty success.
The same error covers byte, record and structural budgets with safe fixed
reasons. Narrow the request by reducing `hours` or selecting one entity. A
legacy call has no continuation; a windowed call names one (below). There is no
automatic continuation request.

## Bounded window and entity filter (HAMCP-294)

`get_logbook(hours=null, entity_id="", start_time=null, end_time=null,
entity_ids=null, name=null)`. When `start_time`, `end_time`, `entity_ids` or
`name` is set, the call is windowed. With none of them set, `hours` defaults
to 12 and the original behavior and shapes apply unchanged.

- `start_time` and `end_time` are ISO-8601 times with seconds and an explicit
  offset (`Z` or `+HH:MM`), at most microsecond precision; naive or
  date-only values are refused rather than guessed. Both are normalized to UTC.
- `end_time` defaults to now; `start_time` defaults to `end_time` minus
  `hours` (default 12). `hours` with `start_time` is refused.
- The window must have `start < end`, be no longer than 168 hours, and start
  before now. An `end_time` in the future is clamped to now and reported as
  `effective_interval.end_clamped=true`; the clamped end is what Core receives.
- At most one entity filter: `entity_id` (one ID), `entity_ids` (1-10 unique
  valid IDs whose comma-joined length is at most 512 characters), or `name`
  (1-100 characters, not blank).
- `name` is resolved inside the logbook slot with one current-state read: a
  case-insensitive substring of the entity ID or `friendly_name`, the same
  basis as `search_entities`. Matches are sorted. Zero matches, more than 10,
  or a joined list over 512 characters refuse with `invalid_request` and a
  safe `details.reason` (`name_matched_no_entity`, `name_matched_too_many`,
  `name_matches_exceed_filter_bound`) plus `match_count`, and at most the first
  10 matched IDs, without a logbook request. A malformed state inventory fails
  closed. Names reflect current state, not historical names.

Every windowed result is one object: `entries`, `truncated`, `returned`,
`omitted`, `requested_interval`, `effective_interval` (`start`, `end`,
`end_clamped`), `interval_bounds` (`exclusive`), `ordering` (`source_order`),
`filter` (`kind`, `entity_ids`, sanitized `name`, `match_basis`,
`matched_count`), `continuation`, `completeness_notes` and `required_action`.
The object is sized exactly against the same receipt allowance, so at the
smallest response limits only an empty result may fit and a record fails closed
with `logbook_response_limit_exceeded`.

When a windowed result is truncated, `continuation` names the next request:
`start_time` is one microsecond before the earliest `when` among the omitted
records (computed over all of them, so Core ordering is not assumed) and
`end_time` is the effective end. `overlap_possible=true`: repeated rows are
possible and absence is still not proof. If any omitted record has no usable
`when`, or the computed start falls outside the window, `continuation` holds
nulls with a `reason` (`omitted_when_unavailable` or
`omitted_when_outside_interval`); narrow the window or filter instead.

`completeness_notes` always include `recorder_retention_not_checked` and
`core_exclusions_not_observed`, plus `name_resolved_from_current_states` in
name mode and `continuous_entities_produce_no_rows` when a selected entity is a
counter, image or proximity entity or a sensor with a unit or state class.

The window and filter use the same single guarded `GET /api/logbook/{start}`
route and `end_time`/`entity` parameters as before; the name lookup uses the
existing `GET /api/states` read. No new Core route, write or fallback exists.

Sanitization occurs before retained records are returned. No log bodies or raw
provider exceptions are added to audit. Complete API-response delivery, public
response truncation, provider completeness and historical retention remain
different claims.

The shared response formatter sizes disjoint removable sections in at most two
candidate passes and tracks serialized length incrementally. It retains
execution identity, outcomes, dispatch/verification facts and read-only
reconciliation guidance. Small responses remain byte-compatible. Optional
envelope detail can be omitted at the minimum response budget, while selected
logbook records remain whole.

Validation uses synthetic formatter scaling tests, a disposable loopback HTTP
server, public-wrapper result checks, cancellation barriers, receipt regression
tests and exact-Core interval evidence. No heavy production query is needed to
validate the correction. Source validation alone does not establish installed
responsiveness or the cause of a historical incident.
