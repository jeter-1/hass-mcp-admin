# HAMCP-135 configuration fixtures

These are separately sanitized configuration bodies derived from the accepted
local R1 design and the preserved minimal removal pair. They are **not** records
written by an old release, historical persisted-format fixtures, or live reads.

The original private packet has manifest SHA-256
`c2e3dd494268aead82637714c7519309636cfcf5333c8ae28857721baafbc104`.
All entity/configuration identities, registry categories and household reporting
and metadata prose were replaced or removed before computing any test hashes.
The cover name deliberately retains the generic word `garage` to exercise the
existing safety-critical consequence classifier. Notification intent is
`FIXTURE_CLOSE`. No credentials or household addresses are present.

`minimal_retry_removal.json` preserves the unconditional first close, event wait
and successful branch, replaces the retry region with reporting, and supplies no
structured response. `caller_owned_retry.json` contains five complete before and
after bodies, including both exclusive Cleaner paths and all 11 positive guards.
Modes, predicates, durations, response dictionaries and control flow are retained.
These files are test input only; production code neither imports them nor uses
household/configuration-name allowlists.
