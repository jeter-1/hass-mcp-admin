# RC2dev15 pre-deployment acceptance contract

Version: `2.0.0-rc2-dev15`
Status: development-candidate verification procedure only; not published,
deployed, or accepted

This document does not declare a release, record an acceptance pass, authorize
publication, authorize deployment, or authorize access to a live Home Assistant
system. Run any deployed step only after separate authorization tied to an
exact candidate identity and exact environment.

## Repository and invariant gates

1. Review the complete diff from the exact base and confirm stable v1.1.2 is
   unchanged.
2. Confirm no public MCP schema, governed configuration-write lifecycle,
   arbitrary upstream dispatch, raw writer, write fallback, preference write,
   screenshot path, release workflow, container operation, or deployment path
   changed.
3. Confirm the static Engineering catalog remains 41 tools and the reviewed
   automatic-read inventory remains 26 tools.
4. Confirm every dynamic route is fixed-name, automatic-read only, bound to one
   admitted catalog generation, and has no direct Home Assistant fallback.
5. Confirm `ha_search` still treats upstream `partial: true` and missing or
   malformed completeness evidence as partial.
6. Run focused contract, registration, quarantine, reconciliation, dashboard,
   negative-reachability, health, and documentation tests.
7. Run the complete unittest, compilation, metadata, YAML, dependency,
   secret-scan, PowerShell, protected-path, whitespace, and Evidence gates from
   a clean committed head.
8. Run the pinned exact-image read-gateway job and retain the immutable ha-mcp
   7.14.1, 78-tool, 26-read, 67-total baseline. Require the job to wait for
   `/ready` HTTP 200 and always retain its bounded success or failure result;
   it must not treat an arbitrary nonzero HTTP response as readiness.

## Disposable compatibility scenarios

Use fakes or an isolated disposable ha-mcp/Home Assistant environment. Do not
use the household instance.

1. Present the exact reviewed generic profile for 7.14.1 with all 26 reviewed
   contracts identical. Require all 26 reads, total tool count 67, exact
   release/profile status, and zero fallback.
2. Change only the self-advertised version to 7.14.2, then repeat with an
   unknown major and an unreviewed downgrade. Keep every advertised contract
   identical. Require all generic reads to fail closed before exposure or
   dispatch. Require dashboard admission to fail unless an exact built-in or
   verified signed attestation exists for that exact observed release.
   Self-advertised equality must never authorize any case.
3. Under the exact reviewed 7.14.1 profile, change one read input schema.
   Require 25 reads, total tool count 66, only
   that tool quarantined, exact expected/observed fingerprints, and an
   unaffected read still callable.
4. Independently add, remove, or change one read's safety-annotation field,
   change any byte or code point in its bounded full runtime description
   (including a later behavioral paragraph), and add, remove, invalidate, or
   change its declared output schema. Each case must quarantine only the
   affected tool and must never publish the remote metadata. Confirm omitted
   optional annotation fields are not silently normalized to invented defaults.
5. Remove one reviewed read. Require it to disappear immediately after the
   successful probe while the other 25 remain.
6. Add new reads and writes. Require every new tool and every write/mixed/action
   classification to remain unavailable without reducing the unchanged 26
   reads.
7. Add malformed or duplicate unreviewed descriptors while leaving a selected
   reviewed target exact and unique. Require the anomalies to remain
   unexposed, appear only as bounded/redacted reconciliation evidence, and not
   prevent the selected target's same-session dispatch.
   Add noncanonical numeric or Unicode data to an unreviewed descriptor and
   require only the diagnostic whole-catalog fingerprint to become unknown.
8. Remove or change only `ha_config_get_dashboard`. Require all matching generic
   reads to remain available while dashboard calls fail before upstream tool
   dispatch.
9. Change an unrelated generic read while leaving an exactly attested dashboard
   release and its dashboard contract exact. Require the dashboard wrappers and
   every matching generic read to remain available.
10. Supply an exact revoked dashboard attestation with a contract matching an
    older entry. Require rejection with no older-attestation or
    compatible-variant fallback.
11. Supply no exact dashboard attestation but an identical self-advertised
    contract. Require rejection before dashboard dispatch. Then supply a valid
    exact signed attestation for that release and require admission only after
    every compiled contract check also succeeds.
12. Enable the signed dashboard registry, accept an exact revocation, then
    advance beyond registry and cache hard expiry and restart from the cache.
    Require the exact release to remain denied with no compatible-variant
    fallback until valid higher-sequence registry data arrives.

## Reconciliation and failure gates

1. Start Engineering before ha-mcp with upstream configured. Require `/health`
   to remain a liveness response while `/ready` returns HTTP 503 with bounded
   `ready=false`, `initial_reconciliation_required=true`,
   `initial_reconciliation_complete=false`, and
   `status=initial_reconciliation_pending`. Authenticated MCP traffic must also
   return 503, so no schema-caching client can retain the transient 41-tool
   catalog. Require capped fast retry for connection/timeout startup failures,
   a bounded 600-second endpoint/session-not-ready grace matching the full-host
   reboot gate, and automatic transition to `/ready` HTTP 200 with
   `ready=true`, `initial_reconciliation_complete=true`, and `status=ready`
   only after `reconcile_until_initialized` returns its first stable or
   terminal result. For the exact 7.14.1 fixture, the first accepted
   `tools/list` must contain all 67 tools.
2. After admission, block a slow reprobe. Require every last-known-good route to
   remain present during the probe; do not publish a temporary 41-tool catalog.
3. Return a stable partial catalog. Require the exact matching subset and a
   stable degraded state without 30-second compatibility retries.
4. Restore the full catalog on a later slow reprobe. Require automatic recovery
   to all 67 tools without an Engineering restart.
5. Overlap a stable background discovery with sustained exact same-version
   delegated calls. Require the equivalent admission-relevant catalog token to
   permit publication, with no starvation or immediate reprobe loop. Then
   overlap two genuinely different catalog observations: require at most one
   immediate stale retry and slow cadence thereafter until a stable token can
   publish.
6. For a delegated call, require same-session `tools/list` and exact
   selected-target contract verification immediately before `tools/call`.
7. Change the upstream version between catalog admission and a delegated call
   while leaving the server name, protocol, and selected target exact. Require
   zero `tools/call`, bounded observed identity evidence,
   `blocked_incompatible_upstream`, no fast-reprobe trigger, and reconsideration
   only on the slow periodic cadence. The self-advertised matching target must
   not authorize the unreviewed release or produce an aggregate compatibility
   claim.
8. Change the server name, use a malformed or syntactically valid but
   unreviewed version, or change the protocol. Require delegated dispatch to
   fail closed.
9. Make the selected target missing, duplicate, or contract-incompatible after
   admission. Require zero `tools/call`, only that route removed or
   quarantined, and every unrelated exact route retained.
10. Retain a stale tool object across a generation replacement. Require its
    route lease to fail before network dispatch. Also pause a call after its
    route is acquired and prove another exact delegated read and reconciliation
    can proceed without waiting for that call. If a call has already committed
    after successful pre-dispatch validation, its completion may return to that
    caller but cannot republish or revive the retired generation.
11. Confirm health reports reviewed and observed versions separately;
    `observed_upstream_server_name`, `observed_upstream_server_version`,
    `observed_protocol_version`, and `observed_identity_status`; aggregate
    compatibility; matched/quarantined/missing/unreviewed accounting; bounded
    quarantine reasons and fingerprints; separate call/discovery failures; and
    separate fast-retry versus slow-reprobe state without remote prose, schemas,
    endpoints, or credentials.
12. Re-characterize bounded `ha_search` latency and concurrent delegated-read
    throughput with the same-session `tools/list` check and immutable route
    snapshots. Prove network I/O is concurrent and no global dispatch lock
    blocks an unrelated read or reconciliation; do not reuse the pre-Dev15
    one-call performance result.
13. Confirm each generic delegated-call audit record contains the bounded
    same-session upstream version evidence and identity status, and contains no
    raw catalog, schema, description, endpoint, credential, or argument value.
14. Confirm the pinned exact-image job first matches the reviewed 78-tool
    stock-catalog fingerprint, then independently matches all 26
    domain-separated full-runtime-description fingerprints, all 26 exact
    runtime safety-annotation presence/value fingerprints, and all 26 exact
    runtime output-schema fingerprints before accepting the 67-tool
    Engineering catalog.

Acceptance passes only when an exact reviewed release/profile is established
before contract-level reconciliation preserves unaffected reads, while every
unreviewed release and every changed, new, write, mixed, or otherwise
unreviewed capability remains unreachable. Any self-authorized release,
widened write path, borrowed attestation claim, hidden fallback, unbounded
diagnostic, stale-generation revival, completeness misreporting, or manual
Engineering restart fails the procedure.
