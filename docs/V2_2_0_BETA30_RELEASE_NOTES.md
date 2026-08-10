# Engineering 2.2.0-beta.30 release notes

Beta 30 adds an optional, dedicated approval-notification adapter and improves
navigation inside the existing administrator-only Home Assistant Ingress
approval panel. It is staged from `2.2.0-beta.29`; publication, deployment, and
live mutation are not part of this change.

## Approval notification boundary

When `approval_notification_service` is empty, notification delivery is
disabled and governance behaves as before. When configured, the value must
match exactly one Home Assistant Companion App notify service in the form
`notify.mobile_app_<device>`. No other notify service, domain, service call,
entity action, upstream provider, arbitrary forwarding path, or fallback is
reachable through this adapter.

After a new approval challenge is durably persisted, a bounded asynchronous
worker sends one normal-priority, privacy-minimal notification. Its sole action
is **Open Approval Panel**, which opens the exact plan review route inside the
add-on's authenticated, administrator-only Ingress panel. The notification
contains no Approve or Reject action and carries no CSRF nonce, plan hash,
challenge ID, credential, secret, configuration diff, or approval authority.
The route uses the exact running add-on slug resolved from Supervisor's bounded
`/addons/self/info` response; it never guesses a repository prefix. The opaque
plan ID in the relative Ingress route is a locator only; the server
still resolves current persisted authority and issues a fresh one-time CSRF
nonce before rendering an actionable review.

The notification is not approval. Delivery, replacement, clearing, failure,
queue pressure, and startup reconciliation are advisory side effects only.
They cannot create, grant, consume, reject, invalidate, extend, recover, or
execute a plan. Notification failure never blocks an otherwise valid approval
challenge. Persisted plans remain the sole authorization authority.

One deterministic tag per challenge replaces an earlier notification after an
add-on restart instead of accumulating duplicates. The worker clears that tag
when its challenge is approved, rejected, expired, invalidated, or consumed.
A restart rebuilds notification navigation from the bounded active approval
inventory; the in-memory delivery projection is not execution authority.

Health and audit output report the configured state, verified self-identity
status, bounded queue/state sizes,
delivery and clear counts, normalized failure category, provider response
status, and explicit zero fallback. They omit the configured device service,
unrestricted payloads, challenge IDs, authenticated URLs, and secrets.

## Ingress navigation

Authenticated Ingress pages now provide consistent links to the pending
approval inbox and F3 reconciliation. Exact review pages have an explicit back
link. After a decision, the server resolves the current inbox rather than using
a stale browser snapshot. A standard terminal decision returns to the current
pending inventory; the first step of an elevated approval links directly to
the newly created elevated-risk acknowledgement for that same plan.

Navigation uses GET-only, authority-free links. Approve, Reject, and F3 actions
remain POST-only with their existing CSRF, principal, exact-hash, policy,
sequence, and persisted-authority enforcement.

## Preserved behavior

- The public MCP tool catalog and all tool schemas are unchanged.
- Approval authority version, governance policy, F3 authority and sequencing,
  exactly-once dispatch, and no-blind-redispatch behavior are unchanged.
- ha-mcp 8.1.1 admission, Beta 29 dashboard routing and provider-error
  truthfulness, and zero upstream fallback are unchanged.
- Stable v1.1.2 is unchanged.
