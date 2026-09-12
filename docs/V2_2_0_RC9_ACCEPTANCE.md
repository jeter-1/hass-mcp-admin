# Engineering 2.2.0-rc.9 acceptance

RC9 corrects RC8-CI-1 against canonical source
`b3d014dbd3ecbb8baef2ef247f722e90870bd3a3`, tree
`bcc00e8ec987acc0ea96b0f8983d10a212d36b89`. Implementation commit:
`e62fe651cff839c35af8af9373f718468fa364a0`. Stable remains 1.1.2.
RC8's failed runtime-lane evidence is preserved; that source is not published
by this task. Prior release documents and published artifacts remain immutable.

## Release and validation authority

Author `.release/next-version` as `2.2.0-rc.9` with its final newline. Require
exact staged resolution of these RC9 documents, validate the RC8-to-RC9
transition, inspect `scripts/promote_next_release.py` without `--apply`, then
materialize with `--apply` under release-preparation authority. Require all three
authoritative declarations at RC9, consumed staging and exact active resolution
of this acceptance document. Keep implementation and release commits separate.

Record the complete canonical-RC8-to-final-RC9 comparison, the correction and
any separately committed validation-fixture adjustments, and each release-only
delta. Full discovery exposed two prewarm test doubles lacking semantic evidence;
their positive scheduling/invalidation cases now use the real Core runtime,
preserving their assertions and adding resource-settlement checks. Run focused tests
and final clean-head Evidence against the canonical RC8 base with exact changed
protected-path declarations. Require all applicable steps, including metadata,
to pass; record commands, interpreter, counts, skips, failures and base/head/tree.
Preserve failed attempts separately rather than changing baselines or gate rules.

Require complete exact-head CI: validation, architecture builds, existing
supported disposable pairings and the actual pinned Core 2026.9.2/ha-mcp 8.4.3
lane. Capability admission alone is insufficient. That lane must complete the
legitimate helper plan, authenticated approval, verified forward/reverse actions,
duplicate refusal, uncertain-response readback and subsequent contracts/cleanup
that RC8's failure prevented reaching. Record run/attempt IDs, all job conclusions,
tested revisions and artifacts. Bind synthetic merge parents and tree to the
candidate and protected base. Unavailable/skipped checks remain explicit.

Prepare one independent review handoff covering correction and release scope,
authority currency, compiled compatibility, evidence and negative reachability.
Self-review remains separate. Leave OPEN/DRAFT without auto-merge; Josh controls
Ready. Publication, production trust/data, deployment and live actions remain
separately authorized operations.

## Semantic continuity contract

Engineering uses one public Nabu Casa endpoint and selects internal admitted
ha-mcp or native providers. Data may select only exact existing compiled Core
capability and probe contracts. Unknown contracts still need review and possibly
code changes. The [Core registry contract](CORE_RELEASE_REGISTRY.md) retains its
opt-in configuration, independent Ed25519 key, signature and identity checks,
expiry, bounded history, retained denials and authority retirement. A denial
overrides positive authority, including compiled authority. Installation alone
does not authorize production Core 2026.9.2.

Each shared dependency build consumes its own exact Core authority before provider
interaction. Its in-memory semantic evidence binds the observed version, compiled
probe identity and template/dependency contracts to the actual semantic registry.
The same active authority is revalidated before subsequent reads. Publication,
cached reuse and helper admission refuse retired, expired, revoked, missing or
mismatched applicability. Cached evidence cannot revive a retired generation or
acquire replacement authority. Finished builds release/finish exactly once;
semantic evidence grants no transport or mutation permission.

Unknown versions without reviewed applicability remain unsupported. Compiled
version behavior and valid existing approval material are preserved. For a
reviewed uncompiled version, evidence fingerprints bind stable semantic identity;
generation and source epoch remain operational provenance. Identical post-lock
refresh may preserve an approval; changed version/semantics or target state must
not. Source fences, soft/hard freshness, external approval, final Core authority,
ownership, durable intent and authoritative verification remain independent
requirements. A disclosed unknown consequence does not itself remove an exact,
owner-approved execution contract.

Prove these boundaries in disposable tests with ephemeral keys: reviewed 2026.9.2
and another uncompiled exact release, compiled controls, unsigned refusal,
missing semantic capability, changed compiled registry, observed-version mismatch,
expiry/revocation and authority changes during collection, publication, cached
use, planning and final pre-dispatch await. Require stale-plan refusal, post-lock
source fences, one verified dispatch, no redispatch, cancellation/shutdown cleanup,
successful unrelated authority and zero fallback. Keep faults out of production.

No public schema/tool, route, provider, permission, persistence-format, dependency,
workflow or stable-v1 change is part of this correction. The healthy reviewed
ha-mcp 8.4.3 pairing requires all 17 Core capabilities, 51 static tools and 25
delegated reads (76 total); operation status remains held. Earlier reporting,
recovery, device, transport and held-read corrections remain intact. Build timeout
stays 300 seconds cooperatively; dependency TTLs stay 600/3600 seconds. Neither
deadline nor cancellation establishes remote abort or exactly-once remote action.

## Separately authorized installed-build acceptance

Keep source, CI/build, publication, installed-image, catalog and live evidence
separate. Prior PASS evidence retains its original release/pairing/time. Keep
household details, connection secrets and private operator receipts local.

1. Read actual Engineering product version, clean source/build identity and Core,
   Supervisor, HAOS and ha-mcp versions. Bind the running architecture/container
   and image configuration digest through its platform manifest to the published
   RC9 index. Labels/version alone are insufficient installed-image evidence.
2. Capture one fresh public MCP session, preserving raw initialize, initialized
   notification and all tools/list pages with zero tools/call in enumeration.
   Verify every descriptor/schema, unique name and cursor against exact RC9
   source/dependencies; bracket with Engineering identity/health. Require the
   expected 76 tools only with the fully admitted reviewed pairing. Unexplained
   losses fail; unavailable capture facilities leave an evidence gap.
3. Verify exact provider admission, all 17 Core capabilities, REST/WebSocket
   identity agreement, dashboard authority, healthy audit/storage and F3. Existing
   compiled pairings remain usable without activating the signed registry. For
   an enabled installation, bind current verified signed data and owner-approved
   trust identity; do not activate/change trust merely to perform read-only tests.
4. Exercise bounded entity/device/effective-area and unrelated reads, service
   discovery, automation configuration and naturally retained traces, configuration
   validation, templates and complete dashboard rereads. Preserve actual provider,
   completeness, uncertainty, canonical errors and zero fallback.
5. Verify natural dependency replacement after an ordinary initiating request
   returns: record generation/build/fingerprint/expiry, age beyond soft expiry
   below hard expiry, make one `refresh_index=false` query, then monitor health
   without dependency queries that initiate retries. Require fresh replacement
   under current Core semantics and settled resources. An unchanged fingerprint
   is acceptable; do not force refresh, change TTLs or manufacture source changes.
6. Reconcile identity, authority, active executions, leases, commits, locks, holds
   and historical failure counters. End with an unrelated useful read. Require
   no unexplained retained work or fallback.

Governed helper/dashboard canaries require exact fixtures, fresh state, consequence
disclosure, bounded scope authorization and each plan's authenticated approval.
Apply once, inspect the task/child, independently read back and verify restoration.
Never reuse a failed plan/approval, blindly retry uncertain dispatch or recreate
an already-restored condition. Dashboard saves remain non-atomic against outside
editors. Held reads, notification/navigation, backup, restart and Core-update tests
remain separate. Data-driven installed continuity requires an exact target, signed
data/trust identity, backup/recovery and its own update authorization.

Report PASS, FAIL, BLOCKED or NOT RUN separately for source, CI, installed image,
raw catalog, reads, authority/semantic continuity, natural refresh, each canary
and settlement. Stop dependent mutations on identity drift, lost authority,
fallback, uncertain dispatch, failed restoration or unexplained resource retention.

## Recovery

Local release-preparation recovery reverts its separate commit while retaining the
correction and evidence. Returning to RC8 restores this known planning defect;
RC7 lacks data-driven Core admission. Installed recovery must account for compatible
Core, configuration/database state, usable backups and access that survives connector
failure. Do not delete denial history or disable controls to restore authority.
