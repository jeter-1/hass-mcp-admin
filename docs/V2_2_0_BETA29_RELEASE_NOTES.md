# HA MCP Engineering Server 2.2.0-beta.29

## Scope

Beta 29 is a focused correction for the dashboard-provider failure evidence
exposed by the Beta 28 live canary. It does not add a dashboard setter, change
governance authority, permit fallback, or alter the admitted ha-mcp 8.1.1
compatibility entry.

## Provider-result truthfulness

- A structured provider rejection received from ha-mcp remains a structured
  provider rejection instead of collapsing into a generic `upstream_error`.
- Dashboard execution evidence distinguishes a received structured rejection,
  transport silence or response loss, provider 5xx ambiguity, and other
  protocol or transport failures.
- Catching an exception no longer implies that no provider response was
  received. `provider_response_received` is derived from bounded evidence about
  the failure.
- When the setter returns a structured rejection and an authoritative reread
  proves the dashboard still has its exact pre-dispatch hashes, the result is
  `failed_post_dispatch` with `provider_rejection_confirmed_no_change`. It is
  not reported as `verification_mismatch`.
- The original upstream message and unrestricted response payload remain
  untrusted and are not persisted in execution diagnostics.

Exactly-one-dispatch and no-blind-redispatch behavior are unchanged.

## Known ha-mcp 8.1.1 compatibility boundary

ha-mcp 8.1.1 rejects an update to an existing dashboard when its resolved
`url_path` contains no hyphen, even though that restriction is intended only
for dashboard creation. While the selected exact upstream release is 8.1.1,
Engineering therefore rejects such an update during plan creation with
`dashboard_write_existing_hyphenless_path_incompatible`. No governed plan,
approval request, best-practices read, or setter dispatch is created by that
rejection.

This guard is exact-release compatibility policy, not a permanent dashboard
product rule. It must be reconsidered only after a corrected upstream release
is published, statically reviewed, and exactly admitted.

## Health contract

Dashboard health now describes two separate routes:

- `ordinary_dashboard_read_route` is the public, read-only dashboard provider;
- `governed_dashboard_write_route` is the non-public F3 route requiring exact
  per-operation admission and external approval.

The governed route continues to prohibit direct Home Assistant fallback,
upstream fallback, and blind redispatch.

## Preserved boundaries

- 25 Engineering-native tools remain registered.
- ha-mcp 8.1.1 admission and the provider/tool catalog are unchanged.
- Stable v1.1.2 is unchanged.
- No direct Home Assistant dashboard setter or alternate setter was added.
- No release, deployment, or live retry is authorized by this staged source
  change.
