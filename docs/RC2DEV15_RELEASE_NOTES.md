# RC2dev15 development notes

Version: `2.0.0-rc2-dev15`
Status: development candidate; not published, deployed, or accepted

Repository version metadata advertises this local candidate identity. This
document does not publish, deploy, or accept it. It records the bounded
development scope that must be independently reviewed and validated before any
separate release decision.

## Contract-level delegated-read admission

Dev15 replaces exact-version-only, all-or-nothing generic read admission with a
binary-reviewed per-tool contract decision. The observed upstream version is
recorded as evidence, but an unknown version does not by itself disable an
otherwise identical reviewed contract.

For every automatic read, Engineering checks the exact tool name and input
schema, normalized relevant description semantics, the reviewed MCP safety
annotations, the declared output contract, the automatic-read security
classification, the compiled behavior adapter, and the supported MCP protocol.
Engineering-owned descriptions and annotations remain the only model-facing
policy. Remote prose, schemas, or annotations never expand authority.

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

The current server name and protocol must remain exact. A different valid
bounded version does not stop an otherwise exact target call or remove the
admitted set; Engineering records it as observed evidence and triggers slow
compatibility reprobe while aggregate health reports `reconciling` and
`compatibility_reprobe_pending`. That state retains the last admitted routes
without claiming the complete catalog was verified at the newly observed
version. Unrelated new, changed, malformed, or duplicate
unreviewed descriptors remain blocked and are reconciled as bounded anomalies
without preventing that exact target call. Transient connection and timeout
failures retain the last-known-good set while fast startup recovery continues.
Stable compatibility differences use the separate slow reprobe lane instead of
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
RC2dev13 full-host-reboot recovery window. Same-session catalog verification
and dispatch are serialized in Dev15, and every generic delegated call adds a
bounded `tools/list`; candidate validation must therefore re-characterize
`ha_search` latency and concurrent delegated-read throughput.

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

An exact release attestation, when present, remains authoritative: revocation
or contract mismatch rejects with no fallback to older evidence. When no exact
release entry exists, an identical nonrevoked reviewed contract variant may be
admitted as `admitted_compatible_contract`. That path records the observed
version but does not claim that an older source commit, image, or release
attestation covers it. If the optional signed dashboard registry is enabled,
the compatible-family path additionally requires a currently usable registry.
An expired cached exact entry remains deny-only until valid higher-sequence
data supersedes it, so expiry cannot revive a revoked or mismatched release.

## Follow-on milestones

Dev15 uses the committed binary policy and the existing dashboard registry.
Dev16 is the planned extension of signed, monotonic, expiring, revocable
compatibility data to the generic read contracts, including cache/expiry,
rollback/replay protection, revocation, and runtime refresh. Dev17 is the
planned upstream-release pipeline for isolated catalog capture, catalog and
annotation diffing, semantic fixtures, dashboard checks, zero-write
verification, compatibility reports, and reviewed registry-entry pull
requests. Neither a generic signed-registry update path nor that automated
pipeline is implemented or implied by this candidate.

No stable-v1 source, public MCP schema, governed configuration-write behavior,
workflow YAML, release declaration, container behavior, credential, image,
tag, publication, deployment, or live Home Assistant system is changed by
these notes.
