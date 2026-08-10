# Engineering 2.2.0-beta.31 release notes

Beta 31 workstream 1 corrects the shared authoritative self-add-on identity
resolver used by approval notifications and governed self-add-on operations.
It is staged from `2.2.0-beta.30`; publication, deployment, live Home Assistant
access, and reuse of the failed Beta 30 notification canary are not part of this
change.

## Corrected Supervisor self identity

Supervisor `/addons/self/info` remains the sole identity authority. The resolver
now accepts a complete response up to a fixed 512 KiB ceiling instead of the
Beta 30 32,000-byte limit. Only the validated installed slug, name, version,
and optional repository identity are retained. Long descriptions, options,
configuration, schema, translations, tokens, and every other response field are
discarded as untrusted data and are never logged or persisted.

There is no manifest, environment, add-on-name, repository-metadata, inferred
prefix, or other fallback identity. A missing authority input, oversized
response, non-success HTTP status, malformed response, timeout, or transport
failure still prevents notification dispatch and self-add-on classification.

Both approval notifications and operational lifecycle planning receive the
same resolver instance. No second identity implementation or identity cache is
an authorization source.

## Truthful bounded diagnostics

The resolver distinguishes the safe internal categories
`configuration_unavailable`, `response_too_large`, `http_status`,
`malformed_response`, `timeout`, and `transport_failure`. Notification status,
health, audit, and structured logs may report only the safe category. They do
not report Supervisor bodies, status text, endpoints, options, configuration,
translations, credentials, or tokens.

The established operational `self_addon_identity_unavailable` error remains the
top-level fail-closed self-restart result. Notification delivery remains
best-effort and advisory with authority `none`; no delivery result can approve,
reject, consume, extend, recover, execute, or otherwise change a plan.

## Exact-image regression

CI now runs the baked Engineering image against a synthetic Supervisor
self-info response larger than 32 KiB. It creates a fresh synthetic approval
challenge, verifies exactly one allowlisted mobile-app notification call, and
matches a hash of the exact Ingress plan link without persisting the URL or the
Supervisor response. Unit coverage exercises the same large response through
the self-add-on lifecycle path and covers the bounded negative categories and
redaction boundary.

## Preserved behavior

- The configured notification service is unchanged.
- The public MCP tool catalog, tool schemas, and compatibility admission are
  unchanged.
- Approval authority, principal separation, governance, F3 sequencing,
  exactly-once dispatch, and no-blind-redispatch behavior are unchanged.
- ha-mcp 8.1.1 admission, dashboard routing, and zero fallback are unchanged.
- Stable v1.1.2 is unchanged.
