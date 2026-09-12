# Engineering 2.2.0-rc.8 release notes

RC8 prepares the independently reviewed Core-update-continuity implementation
from `3bc8084a8a6cf8f7fe512484522a95ce8107b449` (tree
`cadb16a2953a741a60945824ab03e4edc8459363`), based on published RC7
`2a41530e0b3310dd549811002723be238bfe5315`. Stable remains 1.1.2.

## Compatible Core updates through reviewed data

Engineering can admit a compatible exact Core release without rebuilding when
independently reviewed, signed data references the existing compiled capability
and probe contracts. Unknown or changed contracts need further review and may
require implementation and a new release. Successful probes never approve
compatibility data themselves.

Core registry support is opt-in: `ha_core_release_registry_enabled` defaults to
false and `ha_core_release_registry_public_key` is empty. It uses an independent
Core Ed25519 trust key, registry identity and cache, signature/identity/contract
validation, expiry, authenticated checkpoints and retained denials. A verified
denial overrides compiled or signed positive authority. Use-time checks retire
expired, revoked or changed authority while preserving target, approval,
execution ownership, per-read authorization and recovery boundaries.

Both independent review findings are resolved: publication retains the exact
checked selection token so an expiry/authority change cannot be latched onto
older positive decisions; signing compaction retains denial coverage without
counting redundant signatures as separate required sources. Genuine capacity
exhaustion still refuses. No retention, expiry or byte bound was increased.
The offline preparation/signing tooling and
[Core registry runbook](CORE_RELEASE_REGISTRY.md) keep data preparation, independent
review, owner signing and publication distinct.

RC8 alone does **not** authorize production Core 2026.9.2. Owner trust setup,
activation and exact signed compatibility-data publication remain separate.
The new digest-pinned Core 2026.9.2/ha-mcp 8.4.3 disposable CI lane uses ephemeral
test authority; its success cannot substitute for production approval.
Older compiled exact Core pairings, including 2026.9.0 and 2026.9.1, remain intact.

Engineering remains the unified Nabu Casa MCP endpoint with internal provider
selection. Healthy reviewed ha-mcp 8.4.3 exposes 51 static plus 25 delegated tools,
76 total, under all 17 Core capabilities. Operation status stays held. No new
public tool/schema, forwarding/fallback route, action family or approval bypass
is introduced. The feature adds Core registry configuration/cache integration
and the pinned CI lane; this release-preparation delta changes no runtime,
workflow, dependency, persistence or provider behavior. Prior RC7 reporting and
earlier recovery/compatibility corrections remain preserved. Dependency builds
retain their 300-second cooperative deadline and 600/3600-second evidence TTLs.

## Evidence and acceptance

The focused implementation review reported no actionable findings and ran 40
passing tests, including the original expiry and compaction reproductions.
Implementation-head Evidence ran 3,461 tests, 20 skipped, zero failures/errors;
its sole failed step was the intentionally unchanged RC7 version declaration.
These historical results do not establish final RC8 CI, publication or live
acceptance. Materialize RC8 in this same PR, pass final-head Evidence against RC7,
obtain exact-head CI including architecture builds and the actual .2 lane, and
prepare bounded review of the release-only delta.

Follow [RC8 acceptance](V2_2_0_RC8_ACCEPTANCE.md) for separately authorized exact
runtime/build and installed-image binding, fresh complete public catalog,
useful reads, authority and resource reconciliation, natural dependency refresh,
and installed-system continuity. Prior PASS evidence retains its original
pairing and timestamp. Signature, expiry, revocation and fault tests belong in
disposable environments; no household state or incident chronology is asserted.

Governed canaries, restoration, notifications, backups, restarts and Core updates
remain separately authorized and verified operations. Do not reuse a failed
plan/approval or blindly repeat an uncertain mutation. Published RC7 stays
immutable; reverting to it removes this Core data-admission feature and is not
a complete Core/configuration/database recovery plan. Local release-preparation
recovery preserves the reviewed implementation commits. Josh retains Ready;
these notes authorize no publication, production key/data operation, deployment,
Core update, live test or stable promotion.
