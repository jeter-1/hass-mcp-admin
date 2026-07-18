# RC2dev6 release notes

Version: `2.0.0-rc2-dev6`

Baseline: `082ba36a2651201f62b57f9b4ab08366c5682280`

RC2dev6 is a narrow corrective release for a Medium-severity security
observability defect found during RC2dev5 external-transport acceptance.
Authentication remained fail-closed and throttling worked, but the first
throttled invalid-authentication request was incorrectly audited as
`auth_failure` instead of `auth_failure_throttled`.

## Corrected audit contract

| Gateway outcome | HTTP | Error code | Audit event |
|---|---:|---|---|
| invalid authentication before limiter exhaustion | 404 | `authentication_failure` | `auth_failure` |
| authentication-failure limiter exhausted | 429 | `rate_limit_exceeded` | `auth_failure_throttled` |
| authenticated general limiter exhausted | 429 | `rate_limit_exceeded` | `rate_limited` |

The correction changes only the event name selected by the active throttled
authentication branch. Response status and body, error code, token-bucket
behavior, identity derivation, and recovery semantics are unchanged.

The existing authentication-failure limiter permits a burst of five attempts
and refills at 0.5 attempts per minute (one token every 120 seconds). Identity is
derived from the bounded direct-peer identity unless explicitly configured and
validated forwarded-header trust applies. Forwarded-header trust remains
disabled by default. Recovery occurs through normal token refill; RC2dev6 does
not reset or weaken the limiter.

Every rejected request remains before MCP initialize, notifications,
`tools/list`, `tools/call`, canonical or compatibility dispatch, provider
selection, Home Assistant access, upstream dashboard access, and fallback.

## Security and audit handling

Audit records retain a generated request ID, hashed bounded caller identity,
result status, safe error code, and redacted bounded path. They exclude access
secrets, candidate credentials, authorization headers, bearer values, query
secrets, request bodies, raw client addresses, and exception text containing
credential material. Ordinary, throttled-authentication, and authenticated
rate-limit events are exactly and separately queryable.

RC2dev6 adds no `Retry-After` header. That remains a deferred transport
enhancement because no existing release contract requires it.

## Compatibility

- Registered tools: 40.
- Canonical tools: 25.
- Planned tools: zero.
- Public MCP input schemas: unchanged.
- Governance storage: unchanged and backward compatible.
- Provider routing, fallback policy, approval authority, dashboard trust
  profile, dependency-index behavior, and runtime provenance: unchanged.
- Stable v1.1.2 source and packaging: unchanged.

RC2dev5 tags and images remain immutable. This release does not backfill or
rewrite prior release history.

## Deferred acceptance

The fixture transport suite covers the corrected audit sequence without live
credentials. The remaining eight-client single-flight, raw legacy-tool envelope,
and disposable exact-image failure-injection scenarios still require an isolated
non-production transport environment. No production authentication pressure is
authorized by this release implementation.
