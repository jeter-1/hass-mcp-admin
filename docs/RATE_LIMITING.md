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
