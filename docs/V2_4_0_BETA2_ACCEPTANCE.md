# Engineering 2.4.0-beta.2 acceptance

Materialized source candidate. Implementation base:
`c828a2d8082809cf23a1202f74054ea299cf20e5`. No gate is asserted passed by
this document. Preserve accepted stable 2.3.0, published beta.1 and prior evidence.

## Source and disposable acceptance

Require clean-candidate Full/Evidence validation, exact-head CI and separately
tasked independent implementation/security review. Record skips and unavailable
lanes. One additive tool, `get_core_log_history`, changes the expected catalog to
54 static + 25 admitted delegated reads = 79. Existing public schemas, provider
routes, signed Core authority, persisted records, frozen stable-v1, Dockerfile
and workflow permissions must remain unchanged. Approved manifest additions are
`hassio_api: true` and `hassio_role: homeassistant`.

The platform role permits broader Core administration to the process; MCP exposes
only a fixed Supervisor log GET. Prove strict argument validation before dispatch,
useful retained evidence, redaction, output and time bounds, concurrency refusal,
provider attribution in results/metrics/audit, authentication and transport failure,
no retry after a dropped connection, cancellation cleanup and zero fallback or
write dispatch. Always preserve unknown retention and partial completeness,
including late journal failure after an HTTP stream has started. Absence of a log
entry cannot establish that an event never occurred.

Run `scripts/core_log_supervisor_probe.py` with exact Supervisor 2026.09.1 source
`40e3ee7640a3c44abe67f1a1397f39c3cd949806`, its pinned Python/dependencies and
actual Engineering reader. Retain full API routing, middleware and app-model
behavior; upstream disposable fixtures may supply synthetic Docker, D-Bus and
journal boundaries. Require useful success, missing permission, wrong role and
late streaming failure. Verify licensed source fragments against that exact Git
revision. These checks are assembled API integration, not booted HAOS evidence.
Retain existing Core/ha-mcp CI lanes; do not repeat historical script deployment
acceptance as a substitute for this new provider boundary.

The reader and authenticated internal gateway must work without Core API
authority. This does not prove public Nabu transport availability during a Core
outage: the deployed webhook path can still depend on Core.

All three advertised version authorities must agree on 2.4.0-beta.2,
`.release/next-version` must be consumed and active documents must resolve
exactly. Require clean final-candidate Evidence, focused independent review of
the materialization delta and exact-head CI before Ready. Materialization itself
establishes no gate success. Josh's Ready action authorizes the protected
exact-head merge/publication contract; deployment remains separate.

The installed target remains Core 2026.9.3 / ha-mcp 8.5.0 with the existing
signed 19-profile authority. No Core update or registry transition is included.
Supervisor 2026.09.1 is the exact reviewed provider contract; reconcile a different
observed installed version before the dependent live read.

## Later installed acceptance

Obtain separate deployment/live-read authorization. Bind the running image to
publication evidence and verify expected source/version, actual Supervisor version
and installed role. Reconcile any Supervisor revision difference before testing.
Check Core and upstream identity/authority, storage/audit health, native/delegated
read continuity, fresh public catalog with 79 tools and approval-panel rendering.

Perform one bounded log read and optionally one older window. Verify retained
text, source/provider attribution, redaction, limits, partial coverage, unknown
retention and no fallback. Verify existing System Log and script-dependency read
continuity. No device action, plan, approval, restart or configuration write is
required. Finish with no-probe settlement; distinguish historical error counters
from new failures. Preserve existing acceptance evidence.

No persisted format migration is introduced. Source recovery is a reviewed
revert. Installed recovery requires the prior accepted image and restoration of
its manifest permissions; binary downgrade alone does not remove the Supervisor
role. Do not restore stale execution state to satisfy acceptance.
