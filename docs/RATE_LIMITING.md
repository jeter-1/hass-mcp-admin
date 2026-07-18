# Rate limiting and client identity

The beta gateway applies independent token buckets for each authenticated
client, each authentication-failure identity, and a global safety bucket.
Per-client sustained and burst limits remain configurable through
`rate_limit_per_minute` and `rate_limit_burst`.

## Client identity

The default identity is the canonical IPv4 or IPv6 direct socket peer.
`cf-connecting-ip` is ignored by default. Arbitrary strings never become bucket
keys, and `unknown` is used only when no valid direct peer exists.

Forwarded-header trust requires both add-on options:

```yaml
trust_cf_connecting_ip: true
trusted_proxy_cidrs:
  - "192.0.2.10/32"
  - "2001:db8:1234::/48"
```

The examples are documentation-only networks. Configure the actual proxy
address or CIDR only after confirming the deployed forwarding path. Keep trust
disabled for Nabu Casa or any tunnel path whose direct proxy source addresses
are not known. The header is used only when the direct peer matches a trusted
network and the value is exactly one valid IP. An untrusted peer, malformed
header, comma-separated chain, or missing peer falls back safely to the direct
peer (or bounded `unknown` identity).

## Bounded stores

Client and authentication-failure stores are independently capped at 1,000
entries. Existing access updates recency. Inserting a new identity at capacity
evicts only the least-recently-used entry needed to restore the bound; the store
is never cleared. Existing token state therefore is not reset merely because a
different identity creates eviction pressure. Creation, recency update, and
eviction are atomic across concurrent ASGI tasks.

The health response reports bounded store sizes, the maximum, whether forwarded
trust is enabled, and the number of configured trusted networks. It never
returns peer addresses or forwarded header values.

RC2dev6 keeps three gateway outcomes distinct:

| Outcome | HTTP | Error code | Audit event |
|---|---:|---|---|
| ordinary authentication rejection | 404 | `authentication_failure` | `auth_failure` |
| authentication-failure limiter exhaustion | 429 | `rate_limit_exceeded` | `auth_failure_throttled` |
| authenticated general rate limiting | 429 | `rate_limit_exceeded` | `rate_limited` |

Each rejected request produces exactly one matching audit record. Authentication
and rate-limit checks occur before MCP parsing, tool dispatch, provider routing,
Home Assistant access, or fallback. Records exclude request bodies, secret paths,
credentials, headers, query values, and raw client addresses. RC2dev6 does not
add `Retry-After`; that remains a deferred transport enhancement.

The authentication-failure bucket has a burst of five attempts and refills at
0.5 attempts per minute (one token every 120 seconds). It is keyed by the same
bounded client-identity rules above: the direct socket peer unless explicitly
trusted forwarded-header handling is enabled and validated. Successful
authentication does not bypass the bucket; normal recovery occurs through token
refill. Tests use an injected monotonic clock, so they verify refill without
sleeping or changing the deployed configuration.

Run the local fixture bake with
`python scripts/rc2dev6_bake_harness.py --scenario auth` and
`--scenario rate-limit`. Network probes require an explicitly configured
local/test endpoint and never perform a Home Assistant state change.
