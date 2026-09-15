# Private inbound topology observation

This optional diagnostic supplies receiver evidence for the Host/Origin policy
assessment. It does **not** enforce Host/Origin policy or resolve issue #62.
Source validation does not establish the installed forwarding topology. Release,
installation, activation, collection and cleanup require separate authorization.

## Boundary

The MCP listener can place a passive observer outside Uvicorn's loaded proxy
header middleware. It records the HTTP parser's original socket peer and selected
ASGI headers before proxy-header rewriting. Existing middleware, authentication,
rate limits, admission, provider dispatch and responses remain in force. The
observer never reads a path or body, calls ASGI receive/send, acquires Core
authority or retries a request. The original application is invoked once.

The approval listener on port 8110 remains unchanged. This adds no public route,
tool, port, health projection or configuration option. HTTP parser refusals can
occur before observation; a missing receiver record is not evidence that the
request contained the client-intended headers. Earlier proxies may also remove
or combine headers. Observed duplicates refer only to the receiver's ASGI list.

## One-use private arm

File absence leaves the ordinary MCP server construction unchanged. An approved
operator may later provision `/data/private-topology-probe/arm.json`; the server
never creates an arm or accepts an arm through a request. Directory permissions
must be 0700 and files 0600, owned by the running process's effective user.
Every path component is opened without following symlinks. Files must be regular,
single-link files, opened nonblocking and checked through the actual descriptor.

The closed JSON arm schema contains exactly:

| Field | Requirement |
| --- | --- |
| `schema_version` | Integer 1 |
| `capture_id` | 32 lowercase hexadecimal characters |
| `marker` | `topo-` followed by that capture ID |
| `expected_source` | Exact clean published Engineering Git SHA, 40 lowercase hexadecimal characters |
| `listener_port` | Actual configured MCP listener port, ordinarily 8100 |
| `issued_at`, `expires_at` | UTC `YYYY-MM-DDTHH:MM:SSZ`; current interval, at most 900 seconds |

An arm is limited to 4,096 bytes and read once during listener construction. A
valid arm exclusively creates `<capture_id>/consumed.json` before capture can
start. An existing attempt directory refuses reuse, including after a crash or
incomplete export. The claim is evidence of consumption, not successful capture.
Invalid or inaccessible arms refuse observation without changing service access.
Dirty or unknown build identity cannot activate observation.

Recording begins only after the MCP listener is ready. It ends at the earlier
of absolute arm expiry or 180 monotonic seconds. Startup delay consumes the arm's
remaining validity. A wall-clock rollback does not extend the monotonic bound.
A scheduled timer finalizes even when no requests arrive. Shutdown finalizes a
partial receipt; process termination may leave only the durable claim. There is
no arm polling, rearming, automatic retry or deadline extension.

## Selected evidence and limits

Only requests with one exact `X-Engineering-Topology-Probe` marker and one unique
`X-Engineering-Topology-Request` index from 1 through 48 are eligible. These are
synthetic correlation fields, never authentication or MCP session identities.
They do not authorize access to a tool or route. Arrival order and request index
are retained separately so concurrent requests can be reconciled.

The private report retains UTC/elapsed time, listener, method, immediate peer,
Host/Origin counts and safe original values, and counts for a fixed forwarding
header name allowlist. Forwarding values are excluded. Authentication, cookies,
session identifiers, paths, queries, bodies, options, environment values and
arbitrary exception text are excluded. Concrete hosts and addresses remain
private operational information; they must not be posted to GitHub or chat.

Host/Origin values containing credentials, paths, queries, fragments, controls,
non-ASCII or unsupported syntax are omitted entirely. Only original byte length
and a fixed omission category remain; no truncation, normalization or value hash
is substituted. Any omission leaves that topology fact unresolved. This export
allowlist is not the future request authorization policy.

Receiver limits are 48 records, 256 header entries, 256 bytes per header name,
four values each for Host and Origin, 512 bytes per disclosed value, 4,096 bytes
per record and 131,072 UTF-8 bytes for the complete JSON report. Exhaustion,
correlation conflict, malformed scope or identity drift retires capture with
prior complete records and a fixed failure category. Report JSON is never sliced.

Finalization exclusively creates `<capture_id>/report.json`, rechecking the
pinned private directory identity. It closes descriptors and cancels the timer.
Existing reports are not overwritten. Write, flush or namespace failures produce
only fixed log categories; preserve any partial file and the claim. File sizes
and capture scheduling are bounded; regular-file writes/fsync remain subject to
kernel/storage progress, not a hard I/O-abort guarantee. A crash or failed flush
cannot establish durable report completion. No worker thread is left running.

## Paired client and verification

Use a separately reviewed, source/hash-bound adaptation of the existing private
catalog collector. It adds correlation headers at the HTTP transport boundary
and exports the corresponding indices in its sanitized HTTP ledger. Connection
details remain in hidden private-terminal prompts with echo-failure refusal.
Never extract credentials or provide an endpoint/token through chat, arguments,
environment dumps or saved files.

Preserve one MCP session: initialize, initialized notification and tools/list
pagination only; zero tools/call requests. Preserve the existing 120-second
enumeration, 10-second connect, 30-second HTTP and 35-second session-read bounds;
16 pages, 128 tools, 128 protocol messages, 2 MiB per response, 8 MiB aggregate
and 48 HTTP requests. Existing bounded GET/session-cleanup behavior remains;
do not start another session after failure or combine attempts.

Offline verification must compare all raw protocol payloads and descriptors to
the exact candidate reference, prove final pagination, and account for every
client HTTP request against receiver records. Missing GET/DELETE records,
parser/proxy refusals and upstream cleanup behavior must remain explicit gaps;
do not silently discard them. Source/hash, marker, listener and unique index
bindings are required. Receiver `capture_complete` is deliberately false: the
receiver alone cannot prove client completion or runtime identity continuity.

Separate authorized identity observations immediately before/after collection
must bind the installed image, Core/provider admission and settled health. A
catalog count, displayed proxy version or successful client response is not a
substitute. Client/receiver verification alone must not claim live acceptance or
verified upstream header transformations.

## Later execution and recovery

Before a live proposal, independently review the candidate, complete release/CI,
and bind the published diagnostic-capable image and actual installed target.
Specify fixed operator/client/export commands, hashes, an absent evidence path,
one arm, one session, any separately authorized installation/restart, and recovery
access independent of this connector. Confirm existing work is settled. Do not
hot-patch a process, install packet capture, enable broad logging or infer a
container target from a historical receipt.

Preserve the report before authorized cleanup. Remove only the exact inactive
arm if approved; retain the consumed attempt to prevent replay. Expiry stops
recording but does not remove compiled instrumentation. Subsequent removal of
diagnostic history requires a separate retention/replay decision. No application
cache, governance record, proxy setting or trust option belongs to this cleanup.

A deployment regression requires the separately approved artifact recovery plan,
identity reconciliation and useful-read verification. Returning to the prior
artifact also restores its known Host/Origin gap. The eventual enforcement change
must explicitly remove this temporary observer or review retaining it dormant.
