# RC2dev5 acceptance

These steps are a deployment handoff, not authorization to deploy. PR validation
must not tag, publish, merge, or contact the live Home Assistant environment.
After review and merge, the controlled promotion must publish and anonymously
verify the immutable multi-architecture image before the operator updates only
Engineering Beta. Rollback uses the existing immutable RC2dev4 image; stable
v1.1.2 remains running and unchanged.

## Offline transport and fixture acceptance

The default harness is fixture-only and cannot contact Home Assistant:

```powershell
python scripts/rc2dev5_bake_harness.py --scenario all
```

Authentication and rate-limit transport checks require an explicitly configured
local/test MCP endpoint. The secret-bearing URL is consumed without being
printed or persisted:

```powershell
$env:RC2DEV5_TEST_MCP_URL = '<local-or-test-secret-bearing-mcp-url>'
python scripts/rc2dev5_bake_harness.py --network --scenario auth
python scripts/rc2dev5_bake_harness.py --network --scenario rate-limit
```

Non-loopback targets require both `--allow-nonlocal-test-target` and
`--acknowledge-test-system`. The harness implements no state-changing scenario;
its reserved state-change acknowledgement does not enable writes.

## Freshness acceptance

- A cold index build is single-flight and publishes one generation atomically.
- A valid cache hit performs zero Home Assistant requests.
- Soft-expired evidence returns immediately, identifies its exact age, and starts
  only one background refresh.
- A refresh failure preserves the previous generation only within hard TTL.
- Hard-expired or invalidated evidence is never silently returned as current.
- A successful refresh invalidates cursors bound to the old generation or
  fingerprint. Malformed, mismatched, expired, and stale cursors are
  non-retryable and trigger no rebuild.
- Startup prewarm is delayed, nonblocking, connectivity-gated, single-flight,
  observable, and retries no faster than the configured 300-second minimum.

Fixture benchmark output is synthetic, not Raspberry Pi evidence. Live targets
remain: warm dependency lookup under one second with zero HA requests;
soft-expired lookup under one second before response serialization; warm
integrity analysis preferably under ten seconds; and cold build under 25 seconds
or moved off the blocking request path.

## Focused live acceptance sequence

Phase 1 — identity and provenance:

1. Call `server_info`.
2. Confirm version `2.0.0-rc2-dev5`, the expected build SHA and build time, and
   `build_dirty: false`.
3. Call `list_capabilities`.
4. Call `get_server_health`.

Phase 2 — dashboard behavior:

5. Call `list_dashboards`.
6. Read one valid dashboard.
7. Request a nonexistent dashboard.
8. Confirm `dashboard_not_found`, `retryable: false`, idle session state,
   available operational status, and an unchanged provider-operational-failure
   count.
9. Read a valid dashboard again.

Phase 3 — index lifecycle:

10. Restart Engineering Beta.
11. Confirm foundation tools work during startup prewarm.
12. Observe prewarm scheduled, building, and completed states.
13. Run dependency analysis after prewarm.
14. Confirm a warm hit with zero HA requests.
15. Wait beyond soft TTL but not hard TTL.
16. Run dependency analysis.
17. Confirm a fast foreground response, `evidence_stale: true`, exact age, and
    active background refresh.
18. Issue a second concurrent or immediate request.
19. Confirm only one refresh build.
20. Confirm the generation changes after the background refresh.
21. Reuse an old cursor and confirm `stale_cursor`.
22. Simulate refresh failure only through a safe test mechanism where possible.
23. Confirm the previous index remains available within hard TTL.

Phase 4 — integrity performance:

24. Run warm configuration-integrity analysis.
25. Record foreground duration and the two inventory calls.
26. Run soft-expired integrity analysis.
27. Confirm it does not block on the dependency-index rebuild.
28. Confirm stale dependency evidence is explicitly labeled.

Phase 5 — taxonomy and reliability:

29. Request a missing entity.
30. Confirm a domain outcome without a provider operational failure.
31. Run Back Porch reliability analysis in summary mode.
32. Confirm one root-cause group, bounded representative paths, an informational
    intentional-unavailable-trigger note, no button unknown-state defect, and a
    materially smaller summary payload.

Phase 6 — sanitization:

33. Run `get_error_log`.
34. Verify RTSP credentials are masked, webhook identifiers follow the sensitive
    identifier policy, redaction metadata is truthful, and no raw secret appears.

Phase 7 — transport harness:

35. Run authentication-failure checks against the designated test endpoint.
36. Run rate-limit exhaustion and refill.
37. Run concurrent cold-build/single-flight checks.
38. Probe `upsert_automation`; require governed prohibition before HA dispatch.
39. Probe `call_service`; require provider unavailable and no fallback.
40. Run audit truncation and governance-record recovery checks.

Phase 8 — governed acceptance after separate authorization:

41. Create a metadata-only update plan.
42. Verify `approval_not_requested` through authoritative
    `approval_lifecycle`.
43. Attempt apply before approval and confirm rejection with zero mutation.
44. Submit an external challenge.
45. Approve it through Home Assistant Ingress as a distinct administrator.
46. Apply and verify the metadata-only change.
47. Repeat apply and confirm idempotence.
48. Request a separate rollback approval.
49. Approve rollback through Ingress.
50. Execute and verify rollback.
51. Restart Engineering Beta and confirm governance persistence.

Do not perform Phase 8 without explicit user authorization after the read-only
acceptance phases pass.

## Promotion and rollback gate

The promotion summary must show image published/verified, identical version and
SHA tag digest, all declared architectures, matching OCI revision/version/created
labels, `dirty=false`, platform provenance, SBOM status, tag created/verified,
and `release_complete: true`. If image publication succeeded but tagging failed,
stop and follow the digest/revision reconciliation instructions; never overwrite
an existing immutable version tag.

After promotion, update only Engineering Beta. Roll back to
`ghcr.io/jeter-1/hass-mcp-engineering-beta:2.0.0-rc2-dev4` if provider health,
freshness, governance, sanitization, or existing tool behavior regresses.
