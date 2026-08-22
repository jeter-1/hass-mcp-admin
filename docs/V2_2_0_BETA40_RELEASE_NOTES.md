# Engineering 2.2.0-beta.40 release notes

Beta 40 is a corrective F3 release for orphan-child recovery beneath a
terminal pre-dispatch parent. Beta 39 remains the advertised add-on version
until a separate protected promotion. Publication, deployment, and live Home
Assistant access remain separate decisions.

## Fail-closed persisted lifecycle classification

Closed-record classification now admits an intent-bearing execution only when
its persisted lifecycle has the exact grammar emitted by the shipped writer.
Intent lock evidence must be nonempty and match the acquired locks, preflight
must complete after lock acquisition, intent must follow preflight, and the
required events must occur in the writer-produced order and multiplicity.

A terminal success with missing, empty, reordered, duplicated, or otherwise
contradictory lifecycle evidence is corrupt. It cannot satisfy an operation
dependency, and recovery cannot prepare, execute, or call a provider for a
later child on the strength of that record.

## Bounded recovery navigation

Recovery checkpoint composition remains bounded to 16 entries, including
retained deferred entries and newly discovered candidates. Fresh immutable
evidence deadlines retain deterministic priority, while an entry displaced
from the nonauthoritative checkpoint remains reachable through authoritative
task, manifest, and execution-record discovery. Deferred work cannot cause an
oversized checkpoint or prevent a fresh candidate from receiving readback.

Malformed recovery cursor, checkpoint, or retry-sidecar JSON is treated as
corrupt nonauthoritative navigation evidence. Recovery records a diagnostic,
durably resets the affected navigation file, and resumes bounded discovery
from authoritative persistence. Corrupt task, manifest, and execution records
remain fail-closed and are not reset by this behavior. Resetting navigation
never creates dispatch authority.

## Bounded audit replay and deterministic limits

Persisted-event audit replay now holds the audit lock once for a batch, scans
the retained audit files once, and updates its in-memory idempotency set as it
appends. Acknowledgement advances only across the contiguous successfully
audited prefix, preserving retry behavior after an interrupted append without
repeated full-log scans for every event.

Batch-size and fairness tests use injected monotonic clocks. The transition
limit is therefore independent of host speed, while separate tests retain the
real elapsed-time boundary.

## Compatibility and security

Stable v1.1.2 is unchanged. Public tool accounting remains 51, and Beta 40 does
not change public schemas, MCP registration, provider routing, workflow
authority, release publication behavior, or deployment configuration. It adds
no direct-Home-Assistant path, upstream fallback, arbitrary forwarding, or new
write capability. The persisted execution-event vocabulary is unchanged.

Authentic persisted records used by the new lifecycle tests are emitted by the
current writer API before a test deliberately introduces the contradiction
under examination. No reconstructed historical compatibility fixture or claim
about a previously shipped writer is added by this release.

## Validation scope

Acceptance requires the focused F3 persistence, recovery, task, and packaging
suites; the repository Full gate; promotion-candidate validation; and all
required GitHub Actions jobs. The validate job must proceed past metadata
validation and run its Engineering unit-test and build steps. Linux locking and
directory-fsync behavior remain CI evidence rather than Windows-shim evidence.
