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
   7.14.1, 78-tool, 26-read, 67-total baseline.

## Disposable compatibility scenarios

Use fakes or an isolated disposable ha-mcp/Home Assistant environment. Do not
use the household instance.

1. Change only the observed version from 7.14.1 to 7.14.2. Keep all reviewed
   contracts identical. Require all 26 reads and the dashboard wrappers to
   remain available, total tool count 67, version status to record the changed
   evidence, and zero fallback.
2. Repeat with an unknown major version and exact contracts. Require the same
   contract admission; no major-version range may expand authority.
3. Change one read input schema. Require 25 reads, total tool count 66, only
   that tool quarantined, exact expected/observed fingerprints, and an
   unaffected read still callable.
4. Independently change one read's safety annotations, relevant description
   semantics, and declared output contract. Each case must quarantine only the
   affected tool and must never publish the remote metadata.
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
9. Change an unrelated generic read while leaving the dashboard contract
   exact. Require the dashboard wrappers and every matching generic read to
   remain available.
10. Supply an exact revoked dashboard attestation with a contract matching an
    older entry. Require rejection with no compatible-contract fallback.
11. Supply no exact dashboard attestation but an identical nonrevoked reviewed
    variant. Require `admitted_compatible_contract`, the observed version, and
    no borrowed release/source/image provenance.
12. Enable the signed dashboard registry, accept an exact revocation, then
    advance beyond registry and cache hard expiry and restart from the cache.
    Require the exact release to remain denied and compatible-family fallback
    to remain unavailable until valid higher-sequence registry data arrives.

## Reconciliation and failure gates

1. Start Engineering before ha-mcp. Require the 41 native tools to become
   available first, capped fast retry for connection/timeout startup failures,
   a bounded 600-second endpoint/session-not-ready grace matching the full-host
   reboot gate, and automatic admission when ha-mcp becomes ready.
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
   one call dispatch, all admitted routes retained, bounded observed identity
   fields updated, `version_status=observed_contract_only`,
   `compatibility_status=reconciling`,
   `admission_status=compatibility_reprobe_pending`, and a slow reprobe trigger
   without an aggregate compatibility claim or fallback.
8. Change the server name, use a malformed version, or change the protocol.
   Require all delegated routes to fail closed and no call dispatch.
9. Make the selected target missing, duplicate, or contract-incompatible after
   admission. Require zero `tools/call`, only that route removed or
   quarantined, and every unrelated exact route retained.
10. Retain a stale tool object across a generation replacement. Require its call
   to fail before network dispatch.
11. Confirm health reports reviewed and observed versions separately;
    `observed_upstream_server_name`, `observed_upstream_server_version`,
    `observed_protocol_version`, and `observed_identity_status`; aggregate
    compatibility; matched/quarantined/missing/unreviewed accounting; bounded
    quarantine reasons and fingerprints; separate call/discovery failures; and
    separate fast-retry versus slow-reprobe state without remote prose, schemas,
    endpoints, or credentials.
12. Re-characterize bounded `ha_search` latency and concurrent delegated-read
    throughput with the same-session `tools/list` check and serialized Dev15
    dispatch barrier. Record the deliberate tradeoff; do not reuse the
    pre-Dev15 one-call performance result.
13. Confirm each generic delegated-call audit record contains the bounded
    same-session upstream version evidence and identity status, and contains no
    raw catalog, schema, description, endpoint, credential, or argument value.

Acceptance passes only when routine upstream version movement produces
contract-level reconciliation rather than server-wide capability loss while
every changed, new, write, mixed, or otherwise unreviewed capability remains
unreachable. Any widened write path, borrowed attestation claim, hidden
fallback, unbounded diagnostic, stale-generation dispatch, completeness
misreporting, or manual Engineering restart fails the procedure.
