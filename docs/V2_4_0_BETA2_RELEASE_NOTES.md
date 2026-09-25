# Engineering 2.4.0-beta.2 release notes

Materialized source candidate: all three advertised version authorities agree
on 2.4.0-beta.2 and `.release/next-version` is consumed. This is not evidence
of publication or deployment. Engineering 2.3.0 remains the accepted stable baseline.

Adds `get_core_log_history(limit, offset)` for bounded, sanitized retained Core
journal evidence through the native Supervisor provider. It complements the
existing System Log snapshot and always reports partial coverage and unknown
retention. It cannot prove an absent event never occurred. There is no automatic
paging, retry, redirect, backend fallback or write operation.

The approved Supervisor permission grants Core administration to the process,
while the new MCP surface exposes only the fixed log GET. Provider attribution is
preserved in output, metrics and audit without adding log text to audit context.
The reader does not require a healthy Core API; public Nabu transport can still
depend on Core. Installed Supervisor compatibility requires verification.

Expected catalog: 54 static plus 25 admitted delegated reads = 79. Existing
schemas, signed authority and persisted formats remain unchanged; legacy v1.1.2
is frozen. Script inventory remains partial, and runtime OCI provenance still
requires external evidence. See [acceptance](V2_4_0_BETA2_ACCEPTANCE.md).
