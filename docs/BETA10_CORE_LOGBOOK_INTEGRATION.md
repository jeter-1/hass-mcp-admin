# Disposable beta.10 Core logbook acceptance

This one-off validation branch closes only section 2 of beta.10 acceptance.
It changes no shipped runtime, provider authority, release or installed system.
The workflow runs on a push to `codex/beta10-core-logbook-integration`, with
contents-read permission, pinned actions, a 15-minute job deadline and no secrets,
publication, deployment or merge steps. Keep its pull request draft if one is
created; merging this branch is not required to obtain the acceptance receipt.

Engineering bytes are bound to published beta.10 commit
`bf50a45518c764d499c6681c018a145818aad095`. Exact Core 2026.9.4 source is
`9212531f40a0b7b23229a90d688dd79d9dfccff4`; the retained reviewed registry source
identified index `sha256:3e6710a7ab2a61311d9d899b719f6c3657791c63e8f4942cec4ebc42401d6b76`
and amd64 manifest `sha256:e47c978e1b801466e7f62f612fd552bc3a228e077b31a3f1c22c05cf63d754da`.
The lane verifies fresh index/manifest bytes and running configuration identity,
then verifies selected installed Core source hashes against the pinned source.
These are test identities, not new production version restrictions.

## Actual endpoint test

The immutable Core container boots real Core HTTP/auth/logbook/recorder components
using a fresh private SQLite database. Twelve explicitly synthetic logbook events
are fired via `EventBus.async_fire(time_fired=...)`: two entities at ages 6, 18,
48, 120, 192 and -1 hours. The real recorder commits them. No SQL rows are manually
constructed, Core query code is not replaced and no service or device is invoked.

The runner calls the shipped registered `get_logbook` implementation and native
REST client. Only its clock is fixed to the fixture anchor; the transport,
reader, sanitizer and response formatter remain real. Sixteen cases cover
12/24/72/168 hours, with and without an entity filter, using the anchor and an
end time shifted back 24 hours. Each compares all returned messages against
exact inclusion/exclusion expectations, explicit start/end query parameters,
one reader request, complete provider coverage and zero fallback. Future events
must not leak into the anchored interval. A direct old-style 72h request without
end_time is a negative control proving the old one-day behavior is distinguishable.

Source authority: Core `core.py:1567-1585` defines the timestamped event API;
`components/logbook/rest_api.py:57-100` selects explicit start/end versus the
one-day default; `components/recorder/core.py:1298-1312` waits for committed work.
Upstream `tests/components/logbook/test_init.py` contains period/entity and
explicit-end tests. File hashes are in `tests/fixtures/core_logbook_beta10.json`.

## Isolation, evidence and limits

Only a GitHub-hosted run for the exact repository, branch, event and job passes
the execution guard. Core has an internal Docker network, a loopback-only port,
CPU/memory/process limits, no added capabilities and no-new-privileges. Its only
writable fixture is unique to the run; no production paths are mounted. A token
created by this disposable Core stays in private runner temporary state and is
never uploaded. Always-cleanup checks resource ownership before removal and
verifies absence. Upload paths allow only synthetic receipts and public image
bytes, never raw Core logs, database, token, environment or arbitrary directories.

Offline tests cover wrong execution identity, source tampering, wrong interval
results, ownership refusal, cleanup, output bounds and workflow permissions.
They are not assembled-Core execution. The lane's result and GitHub job outcome
must both pass before closing the gate; failed runs remain evidence. Request
count comes from reader dispatch plus shipped telemetry, not a packet capture.
This lane does not establish deployed authority admission, physical behavior,
historical stall causality, Nabu transport latency or arm64 execution.

Rollback is removal/reversion of these validation-only files through normal
review if retained; no production recovery is needed. Existing beta.10 image,
catalog, panel, installed read and settlement evidence remains separate.
