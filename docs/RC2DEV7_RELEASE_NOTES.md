# RC2dev7 release notes

Version: `2.0.0-rc2-dev7`

## Purpose

RC2dev7 is a narrow security-observability correction based on the verified
RC2dev6 merge and deployed runtime SHA
`b6ef6e53e5d5b6eaa95399ee45997fd05b4377b8`.

During isolated RC2dev6 acceptance, five invalid-authentication requests were
correctly rejected with HTTP 404 and wrote five genuine top-level
`auth_failure` records. The sixth throttling request was not sent. Before it,
the acceptance client queried `get_audit_log(event="auth_failure_throttled")`.
The routed read was audited as a top-level `tool_call` whose nested parameters
contained the requested event name, and the serialized-line substring filter
returned that record as a false match.

Authentication remained fail-closed. No bypass, Home Assistant request,
dashboard-upstream request, provider dispatch, fallback, service call, or write
occurred. The finding is Medium severity because it compromises audit-evidence
classification, not authentication enforcement.

## Correction

The audit reader now:

- reads the JSONL stream one record at a time;
- parses each nonempty bounded record with `json.loads`;
- accepts only JSON objects;
- compares the requested filter with the exact, case-sensitive top-level
  `event` value;
- never searches nested arguments, messages, exception text, metadata, or raw
  serialized content;
- keeps only the most recent requested number of matching records in memory.

`get_audit_log` continues to audit itself. Its record remains visible with
`event="tool_call"`, but nested query arguments cannot satisfy another event
filter.

Supported security/transport filters remain `tool_call`, `auth_failure`,
`auth_failure_throttled`, and `rate_limited`. Governance lifecycle events are
not added to the public filter contract in this release.

## Malformed historical records

Blank, malformed, truncated, non-object, and records above the 64 KiB read
bound are skipped. A malformed record is never returned or treated as a match,
and later valid records remain readable. Valid JSON objects without a string
event remain visible in unfiltered reads for historical compatibility but
cannot satisfy a filtered read. The public response remains bounded JSONL or
the existing `No audit log yet.` / `No matching entries.` text; no new metadata
envelope is introduced.

The writer remains responsible for structural redaction before persistence.
The reader never emits malformed raw content, including malformed content that
contains synthetic secret markers.

## Compatibility

- Public MCP input schemas are unchanged.
- Catalog remains 40 registered, 25 canonical, and zero planned tools.
- Schema version remains 1.
- Authentication status codes, error codes, limiter thresholds, refill rates,
  caller identity, and response bodies are unchanged.
- Provider routing and no-fallback policy are unchanged.
- Governance storage requires no migration.
- Stable v1.1.2 source and packaging are unchanged.
- RC2dev6 tags and images remain immutable.

## Deferred acceptance

After deployment, resume the isolated authentication sequence from the
beginning. General authenticated rate-limit acceptance, eight-client
dependency-index single-flight, raw legacy-tool envelopes, exact-image refresh
failure, and direct running-container digest proof remain separate bake items.
