# Engineering 2.4.1-beta.17 release notes

Engineering 2.4.1-beta.17 makes `get_server_health` much cheaper on a large
plan store, and stops the 30 s recovery sweep from reading parent tasks it
does not need (HAMCP-171). It builds on beta.16 (HAMCP-162), beta.15
(HAMCP-189 PR B + C) and beta.14 (HAMCP-189 PR A).

The investigation is
[`HAMCP_171_HEALTH_PERFORMANCE_INVESTIGATION.md`](HAMCP_171_HEALTH_PERFORMANCE_INVESTIGATION.md).
Josh's 2026-10-18 decisions D1–D6 on HAMCP-171 apply. The exact rule is in
[`HEALTH_PERFORMANCE.md`](HEALTH_PERFORMANCE.md#health-safety-verdict-cache-hamcp-171).

No signed registry, HOLD, support window, Core profile, probe fingerprint,
persisted format (D6), workflow or stable-v1 change is included. No new
write, reload, restart, deletion, service call or fallback path is reachable.
The catalog stays at **88 tools (59 native, 29 delegated)**.

**Live baseline (beta.16).** One health call took 36.5 s, of which 33.8 s
(93%) waited for persisted-plan safety validation. Each call deserialized 437
plans and 433 tasks, and `health_cache_hit_count` stayed at 0. The 30 s
sweep deserialized about 7 task records per second, and 239 of 239 parent
reads per pass were for children whose own status decided the outcome.

**Within one health check (D5).** Each validation job memoizes the pure
per-segment sanitizer scan. The memo is bound to the job's exact
sensitive-value set, is bounded (65,536 segments of at most 4,096
characters) and is discarded when the job ends. Results are identical to the
unmemoized scan.

**Between health checks (D1).** Health keeps, in process memory only, the
verdict "these exact bytes passed the persisted-plan safety detector". A
verdict is reused only when all of these match the record this call just read
and parsed:
- the canonical record filename;
- `st_ino`, `st_size` and `st_mtime_ns`, from `fstat` of the handle whose
  bytes were parsed, with the size equal to the bytes read;
- the SHA-256 of those bytes;
- the detector context (detector version and the configured sensitive
  values, as a per-process keyed digest).

Anything else validates fresh: a content change (even with the same size,
inode and mtime), an inode, size or mtime change, a new record, a failed
`fstat`, or a changed detector context. A deleted record's verdict is
pruned. Any failed scan, storage error, quarantine or unexpected worker
failure discards every verdict. Verdicts are empty at process start, are
never persisted and are never used by an authoritative read, save, approval
or execution path.

Every health call still reads and parses every record and runs the
lifecycle, projection and fence checks. Tampered, corrupt and partially
written records are detected exactly as before.

**Deep re-scan.** `get_server_health(deep_storage_audit=true)` discards every
verdict, re-validates every record and repopulates the cache. The input is
optional and defaults to `false`. It is this release's only public schema
change: the `get_server_health` input schema gains this one boolean, and
every other descriptor is unchanged. The internal `deep_audit_plan_store`
reconciliation also discards every verdict.

**Reporting.** `governance.plan_store_scaling.safety_verdict_cache` reports:
- the scope (`process_memory_only`) and `persisted: false`;
- the key description and current entries;
- the last run: `mode` (`cached` or `deep_rescan`), `hits`, `misses`,
  `not_applicable` (pre-contract-v2 records) and `pruned`;
- process totals: hits, misses, deep re-scans and invalidations.

`health_cache_hit_count` now also counts async health calls that reused at
least one verdict. No digest, path or key material is reported.

**Recovery sweep.** The periodic orphan sweep reads the child's own status
first. It reads the parent task only when the child exists and either has no
dispatch intent (orphan cleanup) or is terminal with a recorded readiness
fault. Each parent is read at most once per pass. Terminal, dispatched,
fault-free children cost no parent read. The following are unchanged:
- at-most-once dispatch, with no blind redispatch;
- HAMCP-189's local-first expiry and durable cancel.

A corrupt parent the sweep does not need is no longer read by the sweep.
Health and every authoritative read still detect it.

**Measured effect (synthetic 435-plan store, offline).**
- Health: 23.0–24.0 s on beta.16, against 5.5 s for the first call (memo
  only), 1.5 s for the following calls (435 of 435 verdicts reused) and
  5.6 s for a deep re-scan.
- The validation wait fell from 21.6 s to 4.0 s cold and 1.5 ms warm.
- Sweep parent reads per pass fell from 239 to 0.
- During a warm call, the event-loop echo p99 fell from 20.6 ms to 2.8 ms.
- The longest single loop stall (about 0.5 s, from the existing synchronous
  snapshot and assembly phases) is unchanged.
- Projected live: about 9 s for the first and deep calls and about 3 s
  after that (against 36.5 s).

The tables and method are in
[the acceptance contract](V2_4_1_BETA17_ACCEPTANCE.md#measured-effect).

**Not included.** A separate validation process (D2, deferred), a direct
`automations.yaml` read (D4) and the dependency index (D3, HAMCP-195).

Downgrade: beta.16 reads every beta.17 record, since no persisted format
changes. A downgraded server loses the verdict cache and the
`deep_storage_audit` input, and health returns to beta.16 timing.

Follow the [beta.17 acceptance contract](V2_4_1_BETA17_ACCEPTANCE.md).
