# RC2dev15 development notes

Version: `2.0.0-rc2-dev15`
Status: development candidate; not published, deployed, or accepted

Repository version metadata advertises this local candidate identity. This
document does not publish, deploy, or accept it. It records the bounded
development scope that must be independently reviewed and validated before any
separate release decision.

## Contract-level delegated-read admission

Dev15 replaces all-or-nothing generic read admission with a layered decision.
Explicit reviewed release/profile authority is required first. The compiled
generic-read profile currently authorizes exactly `ha-mcp` 7.14.1; an
unrecognized patch, minor, major, prerelease, or downgrade remains unavailable
even when its self-advertised contracts appear identical. Live upstream
metadata is observation and cannot authorize itself.

After that release/profile prerequisite succeeds, Engineering evaluates every
automatic read independently. It checks the exact tool name and input schema,
the exact domain-separated fingerprint of the complete bounded runtime
description, the exact runtime safety-annotation presence/value fingerprint,
the declared output contract, the automatic-read security classification, the
compiled behavior adapter, and the supported MCP protocol. The 26 per-tool
description fingerprints and 26 per-tool annotation fingerprints were
captured from the pinned image's real `tools/list` after the exact
stock-catalog fingerprint matched. Engineering-owned descriptions and
annotations remain the only model-facing policy. Remote descriptions, schemas,
or annotations never expand authority.

The safety-annotation comparison uses a separate exact, domain-separated
fingerprint of the runtime presence/value projection. Optional upstream hints
that are absent remain absent in that evidence; Engineering does not convert
absence into an invented default. After admission, the client-visible tool
still receives the stricter Engineering-owned four-boolean annotation policy.

The pinned runtime declares `{"additionalProperties": true, "type": "object"}`
as the output schema for every reviewed read. Engineering stores an exact
per-tool fingerprint of that wire schema and rechecks it before dispatch.
That generic declaration does not authorize new behavior: the bounded opaque
adapter and the explicit `ha_search` partial-data rule remain binary-owned.

Unchanged reviewed reads remain exposed. A changed read is quarantined with a
bounded reason and fingerprints. A missing read is removed. New tools remain
unreviewed and unavailable. Mixed, write, physical/action, prohibited, and
unsupported classifications remain blocked. There is no arbitrary dispatch,
raw write, or direct Home Assistant fallback.

The admitted set is replaced as one generation after a successful catalog
probe. In the same MCP session used for a delegated call, Engineering re-lists
the catalog and requires the current selected target exactly once with its
complete reviewed contract before `tools/call`. Missing, duplicate, or changed
target evidence retires only that route before dispatch.

The current server name, exact reviewed release/profile, and protocol must all
remain authorized. If call-time evidence reports another version, Engineering
does not dispatch on the self-advertised match. It records bounded evidence,
fails closed into `blocked_incompatible_upstream`, and waits for the slow
periodic reconciliation instead of triggering the fast retry lane. Unrelated
new, changed, malformed, or duplicate unreviewed descriptors remain blocked and
are reconciled as bounded anomalies. Transient connection and timeout failures
retain the last-known-good set while fast startup recovery continues. Stable
compatibility differences use the separate slow reprobe lane instead of
consuming the fast retry loop.

An admission-relevant token covers only the validated upstream identity,
protocol, version evidence, and sorted reviewed-read outcomes. It is used only
to decide whether an overlapping newer same-session observation proves a
background discovery equivalent; it never admits a tool. Equivalent busy
traffic therefore cannot starve publication, while a different token still
discards the stale discovery. One immediate stale retry is permitted; continued
churn returns to the slow cadence instead of forming a reprobe loop.

Transient endpoint/session-not-ready evidence uses a bounded 600-second
startup-ordering grace before moving to the slow cadence, preserving the
RC2dev13 full-host-reboot recovery window. The bounded `/ready` endpoint reports
only `ready`, `initial_reconciliation_required`,
`initial_reconciliation_complete`, and status
`ready|initial_reconciliation_pending`. When upstream is configured,
authenticated MCP traffic receives HTTP 503 until
`reconcile_until_initialized` returns its first stable or terminal result.
This prevents a schema-caching client from retaining a transient 41-tool
catalog before the initial result is known. Startup failure diagnostics remain
bounded and secret-free.

Every generic delegated call adds a bounded same-session `tools/list`, but
network I/O is not held behind one global dispatch lock. Calls acquire an
immutable current-generation route snapshot under a short lease, then perform
validation and dispatch concurrently. A route retired before pre-dispatch
validation cannot call upstream. A call already committed after successful
validation may finish, but its completion cannot republish or revive a retired
generation. Candidate validation must re-characterize `ha_search` latency and
concurrent delegated-read throughput.

Health reports the bounded `observed_upstream_server_name`,
`observed_upstream_server_version`, `observed_protocol_version`, and
`observed_identity_status` separately from aggregate compatibility.
Generic delegated-call audit records include the bounded same-session upstream
version evidence and accepted/rejected identity status, never raw catalog or
credential material. Whole-catalog fingerprinting is diagnostic only and
becomes unknown when noncanonical unreviewed data cannot be fingerprinted.

## Independent dashboard contract family

The fixed dashboard wrappers no longer gate unrelated generic reads. Their
compiled `ha_mcp_dashboard_read_v2` family still requires the exact bounded
argument shapes, suppressed screenshots, unreachable preference writes,
reviewed safety annotations, output/hash behavior, server identity, and MCP
protocol.

Dashboard admission requires an exact built-in or verified signed attestation
for the observed release before the compiled family is evaluated. Revocation,
missing exact authority, or contract mismatch rejects with no fallback to
older evidence or to a self-advertised compatible variant. An expired cached
exact entry remains deny-only until valid higher-sequence data supersedes it,
so expiry cannot revive a revoked or mismatched release.

## Follow-on milestones

Dev15 uses the exact compiled generic profile and exact dashboard attestations.
Dev16 is the planned extension of signed, monotonic, expiring, revocable
release/profile authority to the generic read contracts, including
cache/expiry, rollback/replay protection, revocation, and runtime refresh. That
is the milestone intended to admit a reviewed newer release without rebuilding
Engineering. Dev17 is the planned upstream-release pipeline for isolated
catalog capture, catalog and annotation diffing, semantic fixtures, dashboard
checks, zero-write verification, compatibility reports, and reviewed
registry-entry pull requests. Neither a generic signed-registry update path nor
that automated pipeline is implemented or implied by this candidate.

The exact-image CI gate now waits for bounded catalog readiness and retains a
bounded result artifact on success or failure. No workflow permission,
publication behavior, stable-v1 source, public MCP schema, governed
configuration-write behavior, release declaration, Dockerfile, image metadata,
credential, image, tag, deployment operation, or live Home Assistant system is
changed.
