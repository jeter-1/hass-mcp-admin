# RC2dev6 acceptance

RC2dev6 validates one narrow change: authentication-failure limiter exhaustion
must be audited separately from ordinary authentication rejection.

## Local fixture gate

Run from the repository root:

```powershell
..\test-venv\Scripts\python.exe -m unittest -v tests.test_rc2dev6_auth_audit
..\test-venv\Scripts\python.exe scripts\rc2dev6_bake_harness.py --scenario auth
..\test-venv\Scripts\python.exe scripts\rc2dev6_bake_harness.py --scenario rate-limit
```

The deterministic sequence must prove:

1. Five invalid requests return HTTP 404 and one `auth_failure` record each.
2. The first request beyond the burst returns HTTP 429 and exactly one
   `auth_failure_throttled` record.
3. Audit error codes are respectively `authentication_failure` and
   `rate_limit_exceeded`.
4. A fake-clock refill restores ordinary rejection and valid authentication.
5. Authenticated general limiter exhaustion remains `rate_limited`.
6. Rejected requests invoke no MCP application, tool, provider, Home Assistant,
   dashboard-provider, or fallback path.
7. Serialized audit and responses contain none of the synthetic credential
   markers.
8. `get_audit_log` filters the three event classes exactly.

## Release and deployment sequence

After review and merge only:

1. Confirm `main` contains the accepted PR merge commit.
2. Let the protected promotion workflow build the exact merge SHA.
3. Confirm publication of `2.0.0-rc2-dev6` and `sha-<merge-sha>`.
4. Confirm both image tags resolve to one anonymous multi-architecture digest.
5. Confirm SLSA provenance and SBOM status.
6. Confirm annotated tag `v2.0.0-rc2-dev6` targets the merge SHA and the workflow
   reports `release_complete=true`.
7. Refresh the Home Assistant add-on store and update only Engineering Beta.
8. Keep stable v1.1.2 installed and unchanged.

Rollback image:

```text
ghcr.io/jeter-1/hass-mcp-engineering-beta:2.0.0-rc2-dev5
```

## Focused post-deployment smoke test

Use an isolated raw client identity; do not pressure a shared production client.

1. Call `server_info`; verify version `2.0.0-rc2-dev6`, expected merge SHA,
   populated UTC build time, and `build_dirty=false`.
2. Call `list_capabilities` and `get_server_health`; verify 40/25/0, HA connected,
   governance healthy, no provider failures, no fallback, and healthy dependency
   index state.
3. Send one invalid-auth request; verify 404, `authentication_failure`, one
   `auth_failure` record, and zero dispatch/HA/upstream work.
4. Continue only to the first throttled request; verify 429,
   `rate_limit_exceeded`, one `auth_failure_throttled` record, and no duplicate
   ordinary event.
5. Refill or advance the isolated limiter clock and verify a valid request.
6. Exhaust the authenticated general limiter with a cheap local read and verify
   only `rate_limited`.
7. Query `auth_failure`, `auth_failure_throttled`, and `rate_limited` separately;
   verify credentials are absent.
8. Recheck health and stop if any provider, governance, fallback, or secret
   exposure regression appears.

## Remaining isolated transport acceptance

After the focused dev6 test passes, resume the eight-client single-flight test,
raw `call_service` and `upsert_automation` envelopes, disposable exact-image
refresh failure, hard-TTL behavior, recovery, and final deployed health. These
are not production-pressure tests and must use a dedicated isolated environment.
