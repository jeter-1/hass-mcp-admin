# Audit-log contract

RC1 does not change this contract. Build SHA and build time remain
`server_info` identity fields and are not added to secret-redaction paths or to
health/capability output. Image provenance never contains a token, credential,
authenticated URL, or branch credential. Clean governance initialization and
inspection alone produce no audit write.

The beta writes bounded, recursively redacted JSONL records to beta-only add-on
storage. Records include request correlation, tool/category/access, safe caller
identity, result/error, duration, endpoint categories, server version, and
tool-specific bounded intent. They exclude access secrets, authentication
material, authenticated URLs, raw cursors, full configuration, traces, logs,
history, diffs, and unbounded analytical evidence.

`get_audit_log(lines=50, event="")` is read-only. Its effective line count is
always clamped to 1 through 500: zero and negative values produce at most one
line, the default remains 50, and values above 500 produce at most 500. Event
filtering occurs without disabling the bound, and existing global response
sanitization still applies.

A refused `upsert_automation` is recorded as write-capable intent rejected
before provider dispatch. Only the bounded automation ID and
`governance_required` refusal are retained; the configuration payload and HA
endpoint categories are absent. Provider policy refusals, authentication/rate
rejections, and cursor/validation failures are tool or gateway outcomes, not
fabricated upstream provider failures.

Beta 25 adds bounded external-approval lifecycle events: requested, optionally
viewed, granted, rejected, expired, invalidated and consumed. Records may include
safe plan/challenge IDs, kind, channel, bounded principal, result and timestamps.
They never include CSRF nonces, cookies, Ingress authentication material, raw
headers, MCP access secrets, request notes, full configuration/diffs, or
authenticated URLs. A preapproval apply/rollback refusal is not a provider
failure because no provider write was dispatched.
