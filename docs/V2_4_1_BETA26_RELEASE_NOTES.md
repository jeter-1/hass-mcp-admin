# Engineering 2.4.1-beta.26 release notes

Engineering 2.4.1-beta.26 is a break-fix for Home Assistant Core authority
(HAMCP-215). It also carries one dependency-security fix (HAMCP-217, folded
in by Josh). It fixes two defects:

- Core authority now **recovers automatically** after a transient verification
  failure.
- Every failure, retirement, catalog withdrawal, recovery attempt and
  restoration is now **logged and visible in health**.

Fail-closed behavior during uncertainty is unchanged.

It also advances `certifi` from 2026.7.22 to **2026.10.19** for
GHSA-6q48-6c7w-9gw2 (critical; see
[Dependency security](#dependency-security-hamcp-217)).

Design and the Part 1 root cause (every retirement path, with reproductions):
[`HAMCP_215_CORE_AUTHORITY_RECOVERY_DESIGN.md`](HAMCP_215_CORE_AUTHORITY_RECOVERY_DESIGN.md).
Acceptance: [`V2_4_1_BETA26_ACCEPTANCE.md`](V2_4_1_BETA26_ACCEPTANCE.md).

beta.26 follows 2.4.1-beta.25 (HAMCP-212) and includes everything in it. Its
only dependency change is `certifi` 2026.10.19 in both hashed locks. No tool, input
schema, description, signed registry, HOLD, support window, workflow or
stable-v1 change is included. The tool catalog is unchanged: 88 tools, or 89
with the optional YAML reader admitted.

**What happened (beta.24 live, 2026-10-20).** One verification failed out of 27
attempts. Core authority was retired, 29 Core-backed delegated tools and the
YAML reader were withdrawn, and the dependency index was invalidated. Nothing
was written to the add-on log.

- **Why it retired.** A single whole-snapshot transient retires the
  generation. That can be a REST `/config` or WebSocket timeout, a connection
  loss, an authentication failure, or a lifecycle-watcher socket move. This is
  the designed fail-closed behavior, and it is kept.
- **Why it did not visibly recover.** The only retry was a fixed 300 s
  `failure_retry`, with no backoff and no visibility.
- **Why it was silent.** The Core-authority code had no logger calls. Outcomes
  went only to the audit file, and the transport cause was discarded.

## Changes

1. **Automatic recovery with bounded backoff.** A failed verification is
   retried after 20 s, then 40, 80, 160, and 240 s thereafter. Each delay has
   ±20% jitter and never exceeds the cap. It replaces the fixed 300 s retry.
2. **Restore only after complete re-verification, with hysteresis.**
   - **What re-verification means.** Authority withdrawn by a transient
     returns only after **2 consecutive complete verifications** with the same
     Core version and the same admitted capability set. A complete verification
     is a two-snapshot stable observation with the authenticated lifecycle
     watcher attached, evaluated against the reviewed contract.
   - **Spacing.** The verifications are about 15 s apart. The count restarts
     on any failure, a different version or capability set, or a connection
     move.
   - **Flapping.** A retirement within 10 minutes of a restoration needs
     **3** verifications, at doubled spacing.
   - **On-demand verification.** A verification triggered on demand, for
     example by mutation pre-dispatch, is refused during recovery and cannot
     confirm it.
3. **Incompatibility stays retired.** A complete observation that no reviewed
   contract admits stays withdrawn (`recovery_state: incompatible`). It is
   re-checked every reconciliation interval (300 s) without backoff.
4. **WARNING logs.** The `core_authority` logger emits one WARNING event for
   each of these:
   - a verification failure, with a bounded reason and transport detail such
     as `core_probe_timeout` or `core_lifecycle_monitor_attach_failed`
   - a retirement
   - a partial capability withdrawal
   - a catalog withdrawal and a catalog restoration (with tool counts)
   - a recovery attempt
   - a confirmation and a confirmation reset
   - a restoration (with the outage duration)

   Each scheduled retry is logged at INFO with its time. Every event carries
   an ISO-8601 UTC `at`. No exception text, URL or token is logged.
5. **Health recovery fields (additive).** `get_server_health` →
   `home_assistant_core_authority.recovery` reports:
   - `recovery_state`: one of `initializing`, `stable`, `retrying`,
     `backing_off`, `confirming`, `restored`, `incompatible`
   - `consecutive_failures`
   - `last_failure_reason`, `last_failure_detail`, `last_failure_at`
   - `retired_at`, `last_attempt_at`, `next_recovery_attempt_at`,
     `retry_delay_seconds`
   - `confirmations`, `confirmations_required`
   - `last_restored_at`

   Three new counters are added: `recovery_attempts`,
   `recovery_confirmations` and `authority_restorations`. Existing counters
   are unchanged.
6. **Dependents resume.** Restoration publishes a new generation through the
   unchanged path:
   - the gateway restores every withheld delegated tool and the YAML reader
   - the dependency index rebuilds against the new generation
   - typed providers lease the new generation

## Dependency security (HAMCP-217)

`pip-audit --strict` failed CI's `validate_prerequisites` on the runtime lock
for `certifi` 2026.7.22 (GHSA-6q48-6c7w-9gw2). This failure affected `main`
and every PR. Earlier certifi versions bundle the
`emSign ECC Root CA - G3` root, and eight eMudhra intermediates under it have
compromised keys. An on-path attacker holding one could present a trusted
certificate for any hostname to a client that trusts the certifi bundle.

- **Change.** `certifi` 2026.7.22 → 2026.10.19 in
  `hass_mcp_engineering_beta/requirements.lock`, `tests/requirements.lock` and
  `build-inputs.json`. The wheel is `py3-none-any` and is the same on amd64
  and arm64: `sha256:42d35340cc5b9ba78e818ddbff0c5b1c2b779de448a340fd1b989f5fa8670e1b`.
  certifi has no `Requires-Dist`, so no other package changes.
  `requirements.txt` is unchanged, because certifi is transitive.
- **Upstream delta** (2026.07.22...2026.10.19).
  - `cacert.pem` loses exactly one root, `emSign ECC Root CA - G3`
    (SHA-256 `86a1ecba…a36bbe6b`), going from 121 roots to 120.
  - No root is added.
  - The other three emSign roots, removed in 2026.10.18, are restored.
  - `certifi/core.py` and the `where()`/`contents()` API are unchanged.
- **Where Engineering is affected.** Engineering's httpx-based MCP clients
  build their verifying SSL context from `certifi.where()`. These are the SDK
  default client used by `clients/mcp.py` and `clients/upstream_read.py`, and
  the dashboard-analysis delegate transport. When a configured upstream URL is
  `https`, a chain to the removed root is now refused.
- **Where it is not affected.** aiohttp REST and WebSocket clients, including
  the signed-registry fetch, use the interpreter default (base image) store
  and do not read certifi. The registries are additionally Ed25519
  signature-verified.
- **Pinned by** `tests/test_hamcp217_certifi_20261019_contract.py`.
  - The installed version, and agreement between both locks and the inventory.
  - The G3 root is absent and the unaffected emSign roots are present.
  - Each Engineering httpx path trusts exactly the bundle, with
    `CERT_REQUIRED` and hostname checking.
  - A loopback peer whose synthetic root is outside the bundle is refused with
    zero requests, with a positive control.
  - aiohttp does not import certifi.
  - The tests fail on 2026.7.22 and on 2026.10.18.
- **Not changed.** The base image's own CA store is not changed. No base
  image update is part of this release.

## Accepted behavior change (owner decision)

Every lifecycle-socket movement now waits one confirmation interval (about
15–18 s) before Core authority returns. This includes the reconnect after a
Home Assistant Core update or restart. Previously the immediate reprobe
restored authority at once. During that wait, Core-backed tools are withheld
and fail closed, exactly as during any outage. This is the hysteresis the
HAMCP-215 brief requires. **Josh accepted this delay on 2026-10-20.** After a
Core restart or update, expect Core-backed tools to refuse for about 15–18 s
after Core is reachable again, with health showing `recovery_state:
confirming`, and then to resume without restarting Engineering.

## Compatibility and rollback

- **Stored formats.** No stored format changes. Recovery state is in memory
  only and starts at `initializing` on each process start.
- **Health.** The `recovery` object and the three counters are additive.
- **Audit.** Audit events are unchanged in shape. Logging is additive.
- **Rollback** is reinstalling the beta.25 artifact. That returns to the fixed
  300 s retry and silent retirement. It also returns to the vulnerable
  `certifi` 2026.7.22. No state needs migration.
- **Boundaries.** No write, service call, reload, restart or fallback becomes
  reachable. No route or lease is admitted while recovery is active.
