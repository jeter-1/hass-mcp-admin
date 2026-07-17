# RC2dev4 acceptance

## Offline and test transport bake

The default harness runs committed fixtures and cannot contact Home Assistant:

```powershell
python scripts/rc2dev4_bake_harness.py --scenario all
```

Network authentication and rate-limit probes require an explicitly configured
local/test MCP endpoint. The secret-bearing value is read without being printed:

```powershell
$env:RC2DEV4_TEST_MCP_URL = '<local-or-test-secret-bearing-mcp-url>'
python scripts/rc2dev4_bake_harness.py --network --scenario auth
python scripts/rc2dev4_bake_harness.py --network --scenario rate-limit
```

Non-loopback targets require both `--allow-nonlocal-test-target` and
`--acknowledge-test-system`. The harness contains no write scenario. Its reserved
`--allow-state-change` acknowledgement does not activate a write.

## Governance lifecycle

The exact flow is:

1. `create_change_plan` creates evidence only and returns
   `approval_not_requested`, `plan_id`, and `plan_hash`.
2. `approve_change_plan` requests an external challenge. It cannot approve the
   plan itself; state becomes `approval_pending_external`.
3. A distinct Home Assistant administrator approves in Ingress. The decision is
   hash-bound, single-use, time-bounded, and principal-separated.
4. `apply_change_plan` rechecks hash and target fingerprint, applies once, and
   verifies read-back.
5. A duplicate apply is idempotent and reports hash-validation status.
6. `rollback_change` first creates a separate rollback approval request. A
   separately approved, hash-bound rollback verifies restoration and rejects
   replay.

Lifecycle meanings are `approval_not_requested`, `approval_pending_external`,
`approved`, `approval_consumed`, `approval_expired`, `approval_rejected`, and
`approval_invalidated`. High-risk plans remain reviewable but cannot be approved
or applied under the current milestone policy. Persisted writes are atomic;
corrupt files are quarantined; restart recovery and RC2dev3 compatibility are
tested.

## Dependency index and error semantics

`build_state` is `unbuilt`, `building`, `valid`, `expired`, `invalidated`, or
`failed`. `validity_reason` explains TTL expiry, configuration invalidation,
explicit refresh, process restart, or build failure. Concurrent cold callers
share one build. A valid warm snapshot and cursor continuation perform zero HA
requests. Stale or invalid cursors are non-retryable and never trigger a rebuild.

Optional `dependency_index_prewarm` defaults to `false`. When enabled, it runs
once in the background only after a safe Home Assistant `/config` connectivity
probe succeeds. `prewarm_state` and its bounded failure category remain visible;
the server starts and non-index tools remain usable if the probe or build fails.

Validation, domain outcomes, authorization, cursor errors, provider operational
failures, and internal failures are counted separately. Expected not-found
answers do not make a healthy provider look unreachable.

## Live acceptance after a reviewed release is published

Do not run these steps from this PR. After review, publication, anonymous image
verification, and an operator-approved Engineering Beta update:

Before updating Home Assistant, require the post-merge promotion workflow to
pass, record the immutable `2.0.0-rc2-dev4` digest, and confirm anonymous
inspection includes `linux/amd64`, `linux/arm64`, and `linux/arm/v7`. PR CI is
validation-only and must never publish.

1. Call `server_info`; verify version `2.0.0-rc2-dev4`, exact non-unknown build
   SHA, UTC build time, and `build_dirty: false`.
2. Call `list_capabilities`; verify 40 registered, 25 canonical, zero planned,
   and truthful legacy enforcement metadata.
3. Call `get_server_health`; verify governance storage, redaction, audit,
   dependency build state, classified counters, and dashboard freshness fields.
4. Call `list_dashboards`, then read one exact non-critical dashboard twice;
   verify both hashes are stable and no screenshot/rendering metadata exists.
5. Perform one direct `get_entity` read.
6. Probe the four legacy write schemas; require prohibition/unavailability,
   zero HA write/service/reload calls, and zero fallback.
7. Run a cold `entity_dependency_analysis`; record wall time, request breakdown,
   concurrency, queue time, parsing time, generation, and fingerprint. Target:
   under 25 seconds on the Raspberry Pi, or a non-blocking building response.
8. Repeat warm; require under one second and zero HA requests. Run warm integrity
   analysis, preferably under ten seconds.
9. Test malformed and stale cursors; verify non-retryable classified outcomes
   and no terminal-analysis failure increment.
10. Exercise domain not-found cases; verify domain counters without
    provider-health degradation.
11. Create a metadata-only plan. Verify `approval_not_requested`, no challenge,
    no mutation, and principal separation not evaluated.
12. Request a challenge. Verify `approval_pending_external` and panel/health
    counts agree. Verify apply-before-approval is rejected with zero mutation.
13. Obtain a separate external Ingress administrator approval, apply once,
    verify, and repeat apply to confirm idempotence.
14. Request and separately approve rollback; verify restoration and replay
    rejection.
15. Restart only Engineering Beta and verify governance persistence.
16. Exercise upstream outage/recovery; require dashboard fail-closed, other
    tools available, no fallback, freshness truth, and demand-driven reconnect.
17. Run authentication and rate-limit harness checks on the designated test
    endpoint; verify throttling/refill, redacted audit, and valid-client use.

Do not modify or disable production v1.1.2. Roll back Engineering Beta to the
previous immutable RC2dev3 image if any hard gate fails.
