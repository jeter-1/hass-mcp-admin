# Engineering 2.4.0-beta.10 release notes

Bounded native logbook reads and large-response processing. These notes do not
establish publication, deployment or installed acceptance.

The shared formatter now sizes disjoint removable sections in bounded passes
instead of repeatedly serializing the remaining response after every removal.
The retained 300-entry synthetic case improved from 6.169 seconds to 0.010
seconds. That observation does not prove the complete cause of the historical
108-minute request or installed responsiveness.

`get_logbook` now sends an explicit start and end from one UTC clock capture.
The former omitted end selected Core's one-day default even for a wider request.
Finite positive windows through 168 hours remain supported, optionally scoped to
one validated entity. Each acquisition is capped at 1 MiB and the smaller of
30 seconds or the configured timeout; one bounded worker parses and projects
the result. No automatic retry, redirect, split query, waiting backlog or
fallback is introduced. A timeout does not prove Core stopped its database work.

Small complete results remain lists, including legitimate empty API results.
Large valid results preserve a source-order prefix of whole sanitized records,
with partial coverage, exact returned/omitted counts for the received response,
the requested interval and narrowing guidance. A nonempty response from which
no useful record fits returns non-retryable `logbook_response_limit_exceeded`.
Counts and an empty API response do not prove complete historical retention.
The new partial `data` object has `entries`; clients must support it as well as
the complete-result list. See [logbook bounds](LOGBOOK_READS.md).

A concurrent request now returns `logbook_busy`, retryable with HTTP error
classification 409, and the existing instruction to wait for the active read.
It makes no additional HTTP request. The one-slot/no-backlog design remains;
cancellation during processing holds the slot until the worker actually exits.
Retryability permits a later client request, not an automatic server retry.
Malformed arguments remain non-retryable `invalid_request`.

Client-visible changes are explicit:

- `get_logbook` keeps its input signature, registration and annotations, but its
  description now discloses the interval, download and partial-result bounds.
  Raw catalog acceptance must use the beta.10 descriptor, not beta.9's text.
- Shared formatter truncation metadata initializes `omitted_path_count` and
  `omitted_paths_complete` even before the first omission. This is additive
  metadata across tools' projected responses and can cause an additional removal
  near the response limit. Complete responses below the limit remain unchanged.
- The two additive logbook error codes distinguish resource exhaustion from
  temporary occupancy. Existing action identities, outcomes, dispatch and
  verification facts, unknown/null values and reconciliation guidance remain.

The catalog remains **80 = 55 static + 25 delegated reads**. Native routing,
provider admission, signed Core applicability, approval/dispatch, persisted
formats, dependencies, workflows, options and frozen stable-v1 are unchanged.
The reviewed Core 2026.9.4 interval evidence is a test fixture, not a new runtime
version pin or signed-registry replacement. Installed Core must remain admitted
by the existing reviewed signed authority for all 19 required profiles.

Beta.9's asynchronous public health correction and its remaining synchronous
costs are preserved. This release does not itself prove the earlier disconnect
cause or close unobserved approval-to-dispatch continuity. Preserve unclosed
beta.9 gates and stale household plans as evidence; do not replay them.
No Alarmo, newest-first logbook option, F5 disconnect-detail correction, household
configuration change or new physical action is included.

Preserve the last verified compatible artifact, exact current execution records
and supplementary verification receipts before separately authorized deployment
or rollback. No storage migration is introduced. Restoring stale execution
history is not rollback; inherited beta.6 expanded-lock limitations and the F027
historical qualification remain. Do not repeat completed beta.7/F028 verification.

The [beta.10 acceptance contract](V2_4_0_BETA10_ACCEPTANCE.md) separates source,
disposable Core integration, publication/image identity, raw catalog and useful
installed reads. It does not call for replaying the heavy historical query.
