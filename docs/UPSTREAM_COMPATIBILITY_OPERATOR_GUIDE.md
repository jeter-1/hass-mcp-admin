# Upstream compatibility operator guide

Use this guide to interpret a changed `homeassistant-ai/ha-mcp` release without
turning a version string or catalog difference into new authority. The active
runtime decision is
[`ADR-006`](architecture/ADR-006-CONTRACT-LEVEL-UPSTREAM-COMPATIBILITY.md).

## Runtime compatibility check

1. Verify `observed_upstream_server_name`,
   `observed_upstream_server_version`, `observed_protocol_version`, and
   `observed_identity_status`. The endpoint must still identify as `ha-mcp`,
   provide bounded version evidence, and negotiate the supported MCP protocol.
   Identity, malformed-version, or protocol failure is global and must not be
   worked around with a schema match.
2. Read `get_server_health.upstream_read_gateway`. Treat
   `version_status=observed_contract_only` as evidence, not failure. Require
   the separately reported `compatibility_status`.
3. Compare reviewed automatic-read, exact-matched, missing, quarantined, and
   unreviewed counts. The reviewed accounting must satisfy:

   ```text
   exact matched + missing + quarantined = reviewed automatic reads
   ```

4. Confirm every quarantine entry contains only a bounded tool name, stable
   input-schema, description-semantics, annotation, or output-contract reason,
   and expected/observed fingerprints. Do not request raw schemas,
   descriptions, registry content, or endpoint material through health or logs.
5. Confirm missing and quarantined tools have no route while other exact
   matches remain available. New or newly visible tools, including apparent
   reads and writes, must remain unavailable.
6. Inspect `upstream_dashboard` independently. Dashboard compatibility must not
   be inferred from generic-read status, and dashboard failure must not remove
   generic reads.
7. Require zero generic writes, arbitrary forwarding, direct-HA fallback, and
   provider fallback.

With 41 static tools, all 26 reviewed reads produce 67 registered tools. One
missing or quarantined read produces 66. Additional blocked or unreviewed tools
do not increase the registered count.

Interpret `compatibility_status` as follows:

- `exact`: all reviewed reads match at the reviewed evidence version;
- `compatible`: all reviewed reads match at another observed version;
- `partial`: at least one matches and at least one is missing or quarantined;
- `incompatible`: a stable catalog was evaluated with zero matches; and
- `reconciling`: a selected target matched at changed bounded version
  evidence, but aggregate compatibility at that version is not yet known; and
- `unavailable`: no valid catalog identity is currently available.

## Call-time contract check

Admission is not the final dispatch check. In the same MCP session that would
perform `tools/call`, Engineering first obtains `tools/list`, confirms the
bounded server identity and protocol, requires the selected upstream target
exactly once, and compares that target's complete reviewed contract with the
currently registered route. Missing, duplicate, or changed target evidence
stops before `tools/call` and removes or quarantines only that route. Other
matching reviewed routes remain registered.

A different but valid bounded upstream version does not by itself stop the
call. If the selected target is still exact, Engineering retains the admitted
routes, records the new version as `observed_contract_only`, performs the one
bounded dispatch, reports `compatibility_status=reconciling` and
`admission_status=compatibility_reprobe_pending`, and triggers compatibility
reprobe. These are last-admitted routes, not a claim that all 26 were already
verified at the newly observed version. A malformed version,
different server name, or unsupported protocol remains a global
pre-dispatch failure.

The call-time check is target-local. Unrelated new, changed, malformed, or
duplicate unreviewed descriptors cannot authorize a route and do not block an
exact selected target. Periodic or event-triggered catalog reconciliation
records them only as bounded, redacted unreviewed anomalies. It never exposes
them or infers policy from their contents.

## Recovery versus incompatibility

Use the fast bounded retry state only for endpoint startup and transient
transport availability. A discovered missing or incompatible contract uses the
separate slow compatibility-reprobe state. Do not restart-loop Engineering or
reduce the compatibility delay merely to make a stable upstream difference
look transient.

Connection and timeout failures use the capped fast cadence. A transient
404/session-not-ready classification receives a bounded 600-second startup
grace, matching the full-host reboot gate, and then falls back to the slow
cadence if it remains unresolved.

The fast lane reports `retry_count`, `next_retry_delay_seconds`, and
`reconciliation_status`. The slow lane reports
`compatibility_reprobe_interval_seconds`,
`last_compatibility_reprobe_at`, `next_compatibility_reprobe_at`, and
`compatibility_reprobe_status`. Do not interpret one lane's counters or
timestamps as activity by the other.

An admitted exact subset remains usable while slow reprobe waits. Clients that
cache `tools/list` must re-list or reconnect after the subset changes; the
server does not claim `tools/list_changed` delivery.

Equivalent same-session calls that overlap a probe do not starve it: a bounded
admission-relevant token can prove that the reviewed outcomes are unchanged.
A different token keeps the probe stale. Health exposes
`stale_reprobe_retry_armed`; only the first consecutive stale mismatch receives
an immediate retry, and continued churn returns to the slow cadence.

Dev15 protects same-session verification and dispatch with one generic-read
dispatch barrier. Generic delegated calls are therefore serialized, and each
adds a bounded paginated `tools/list` before `tools/call`. Characterize
`ha_search` latency and concurrent delegated-read throughput for the candidate;
do not assume the earlier one-call latency profile is unchanged.

## Controlled upstream update sequence

Do not let a household or production updater install an unreviewed `ha-mcp`
release merely because a new version is available. Use this sequence:

1. Detect the upstream release and resolve its exact immutable source and image
   identity.
2. Exercise that exact target in an isolated disposable environment. Compare
   every reviewed generic-read contract, run the semantic fixtures, and
   evaluate the dashboard wrapper contract independently.
3. Publish any separately reviewed compatibility evidence that the current
   mechanism supports. Dev15 supports existing dashboard attestations; generic
   signed-registry authority is deferred to Dev16.
4. Upgrade `ha-mcp` only after the target's compiled live contracts or
   applicable signed evidence are known to pass, and retain the exact prior
   image as the rollback target.
5. Let Engineering reconcile the live catalog without restarting it.
6. Verify the observed identity, generic matched/missing/quarantined counts,
   delegated tool count, dashboard status, and zero-write/fallback invariants.
7. Roll back `ha-mcp` if the required subset is not compatible or the result
   differs from the disposable review.

An unattended update gate may proceed only when the target already has the
applicable reviewed signed evidence, or when a bounded pre-upgrade check proves
that its live contract matches the compiled reviewed family. Version presence
alone is insufficient. Dev15 does not provide a generic registry publisher or
automatic release-review pipeline, so operators must not describe those
Dev16/Dev17 capabilities as active.

## Dashboard compatible-contract path

The dashboard provider still allows only `ha_config_get_dashboard` through the
two fixed non-screenshot Engineering operations. Evaluate its exact compiled
input, safety, output/hash, and runtime contract.

- If an exact-version built-in or verified signed attestation exists, it is
  authoritative. Revocation or fingerprint mismatch blocks that release.
  Never substitute an older release entry or the unattested path.
- If no exact-version entry exists, an exact compiled-family match may report
  `admitted_compatible_contract`. This status makes no source, image,
  provenance, or release-attestation claim. If the optional signed registry is
  enabled, its currently usable state must also establish that no exact entry
  applies.
- A semantic mismatch remains unavailable regardless of version or signed
  data. Descriptive-only changes may remain informational only when every
  dispatch-relevant projection is exact.

## Optional dashboard attestation setup

1. Generate an Ed25519 seed and public key in a protected administrative
   environment. Do not use a shell command that echoes the seed.
2. Create the GitHub environment `upstream-attestation-signing` with required
   reviewers and no untrusted branch access.
3. Add environment secrets:
   `UPSTREAM_TRUST_REGISTRY_SIGNING_KEY` (base64 raw 32-byte seed),
   `UPSTREAM_TRUST_REGISTRY_PUBLIC_KEY` (base64 raw public key), and a bounded
   `UPSTREAM_TRUST_REGISTRY_KEY_ID`.
4. Put only the public key into the Engineering Beta add-on option
   `upstream_trust_registry_public_key`, then explicitly enable
   `upstream_trust_registry_enabled` in a later deployment window. Do not repeat
   the key or endpoint in tickets/logs.

## Review a dashboard release for provenance

1. Open GitHub Actions on `main` and select
   **Prepare ha-mcp compatibility attestation**.
2. Enter only the exact stable version, such as `7.14.2`.
3. Approve the protected environment after confirming the upstream release is
   intentional.
4. The workflow resolves the exact official tag/source and GHCR image, verifies
   image/source ancestry and allowed metadata-only packaging delta, records
   amd64/arm64 platform digests and provenance, starts the exact image by digest
   against disposable Home Assistant, and extracts the actual MCP contract.
5. The workflow invokes only dashboard inventory and exact config reads with
   `include_screenshot=false`; it verifies the dashboard hash and records zero
   write dispatches.
6. Semantic normalization must match the compiled family. Descriptive changes
   may normalize away; argument, annotation, output/hash, protocol or unknown
   semantic changes fail. Do not create an attestation to bypass an
   incompatibility.
7. The workflow signs a new entry and opens a draft PR containing only registry,
   signature, bounded evidence and generated index files.

## Review the data PR

Confirm exact version, source commit/tag, official immutable image/index/platform
digests, image revision/created time, provenance/SBOM result, runtime identity,
ordinary catalog fingerprint, all four normalized fingerprints, fixed argument
shapes, dual-hash evidence and zero write dispatches. Confirm the sequence
increased once and no prior entry was silently replaced.

Reject the PR if the release adds a required argument, changes a type/default,
loosens `additionalProperties`, changes safety annotations, adds an output schema,
changes the dashboard return/hash contract, changes the protocol, or cannot prove
the exact source/image relationship. Do not whitelist a fingerprint to bypass a
compiled-family rejection.

The PR must not change runtime code, workflows, Dockerfiles, dependencies,
Engineering versions, public schemas, tool allowlists, capability metadata, or
governance. Merge remains a separate human action. This dashboard evidence
workflow does not attest the 26 generic read contracts and cannot expand them.

## Revocation and recovery

Normal attestation creation cannot replace or re-add an existing/revoked release.
Revocation requires a separately reviewed higher-sequence data change signed by
the protected key. After merge, verify runtime health reports the new sequence
and `rejected_revoked_attestation` before relying on it.

On refresh/signature/expiry failure, do not delete the cache or disable security
checks to restore access. A valid exact-version entry remains authoritative,
including its revocation state. An expired cached exact entry remains deny-only
until valid higher-sequence registry data supersedes it. When the registry is
enabled, an unavailable or hard-expired registry blocks the compiled
`admitted_compatible_contract` path; it must not revive older binary contract
evidence. Compare the bounded failure category, repository registry/signature
files, protected key ID/public key, sequence and expiry. Never paste registry
private key material into debug output.

## Deferred generic release support

Dev15 does not generalize the dashboard registry to generic reads.

- Dev16 may define signed, data-only provenance and revocation evidence for
  binary-owned generic read contract families, including cache, expiry,
  rollback/replay protection, revocation, and runtime refresh. It must not
  change classifications, routes, arguments, or fallback.
- Dev17 may automate immutable source/image resolution, disposable runtime
  extraction, catalog and annotation diffing, semantic fixture and dashboard
  testing, zero-write verification, compatibility reports, and draft evidence
  updates.

Until those milestones are separately reviewed, the 7.14.1 generic policy is
the committed contract baseline and observed compatible versions are reported
without signed generic release provenance.

## Deferred registry administration writes

The upstream 7.14.1 `ha_set_entity` and `ha_set_device` contracts are retained as
non-runtime design evidence only. They are destructive and cannot be activated by
a signed registry entry. A future governed registry-administration milestone must
separately design proposal, external approval, stale-state, apply, verification,
rollback and audit semantics before either operation can enter Engineering.
