# Bounded native logbook reads

`get_logbook(hours=12, entity_id="")` uses the existing native Home Assistant
REST provider and signed Core read authority. It requests an explicit interval
from one captured UTC end time back by `hours`. Older code omitted `end_time`,
which selected Core's default one-day interval even for a 72-hour request.

The public arguments and tool count are unchanged. Hours must be finite,
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
reasons. Narrow the request by reducing `hours` or selecting one entity. The
interface has no offset/start-time cursor; there is no automatic continuation.

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
