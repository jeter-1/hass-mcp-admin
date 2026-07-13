# Beta 19 release notes

Version: `2.0.0-beta.19`

Beta 19 adds one read-only Engineering-native tool: `incident_correlation`.
Registered tools increase from 36 to 37; canonical tools remain 25, and every
existing public schema remains unchanged. `handoff_generation` remains planned.

Correlation composes bounded automation configuration and traces, current state,
history, logbook, structured System Log, entity registry, shared dependency,
configuration-integrity, and automation-reliability evidence. Ranked hypotheses
cite stable evidence, retain contradiction, report missing evidence and coverage,
and never claim causation from timing alone. Dynamic references remain targetless.

Pagination uses the corrected signed, five-minute bounded snapshot lifecycle.
Continuation is entirely local and performs no HA access, provider dispatch, index
lookup/build, evidence retrieval, classification, or recorrelation. The snapshot
is not a general result cache.

No write capability, service execution, remediation, monitoring, subscription,
or background task was added. Production v1.1.2 remains unchanged. A beta
connector reconnect or recreation may be needed if the client caches the old tool
catalog.

See [`INCIDENT_CORRELATION.md`](INCIDENT_CORRELATION.md) for the complete contract
and deployed read-only acceptance procedure.
