# Engineering 2.2.0-rc.9 release notes

RC9 corrects RC8-CI-1: a compatible exact Core release admitted through reviewed
signed data could still be refused by governed helper planning with
`home_assistant_version_unsupported`. RC8's successful capability-count check
did not establish the dependency consumer contract. Its failed Core 2026.9.2
runtime lane and unpublished source remain historical evidence.

The correction starts at canonical RC8 source
`b3d014dbd3ecbb8baef2ef247f722e90870bd3a3` and is committed separately as
`e62fe651cff839c35af8af9373f718468fa364a0`. RC9 release preparation is a separate
commit. Stable remains 1.1.2; published RC7 and all earlier artifacts are intact.

## Exact semantic authority through dependency evidence

A consumed, manager-owned dependency build now obtains in-memory evidence of
its current Core version and selected compiled probe, template and dependency
contracts. The selected probe binds the actual compiled semantic registry.
Collection carries that evidence into its snapshot; publication, cached reuse
and helper planning check its currency. Missing applicability, expiry,
revocation, retirement, changed contracts or a mismatched observed version do
not admit an unknown release. No version is added to the compiled allowlist,
substituted for another version or admitted by prefix or caller boolean.

For uncompiled releases, stable semantic identity is part of the helper evidence
fingerprint. Operational generations and source epochs remain separate so an
identical legitimate post-lock refresh preserves approval material. Existing
compiled pairings and their valid approvals retain their contracts. The evidence
does not retain a finished build's commit or grant dispatch authority. Per-read
authorization, freshness, source fences, exact target/state, authenticated plan
approval, final Core checks, one-dispatch ownership and readback remain required.

Engineering remains the unified Nabu Casa endpoint, selecting admitted internal
ha-mcp or native providers. The healthy exact ha-mcp 8.4.3 pairing still requires
17 Core capabilities and exposes 51 static plus 25 delegated tools, 76 total;
`ha_get_operation_status` remains held. Public schemas, registration, routes,
provider policy, permissions, persistence formats, dependencies and workflows
are unchanged. Dependency evidence retains 600/3600-second TTLs and its
300-second cooperative build deadline; these are not remote-abort guarantees.

## Validation and remaining gates

The desired-behavior production-consumer regression fails on unchanged RC8.
Synthetic tests exercise signed 2026.9.2 and another exact uncompiled version,
compiled compatibility, actual transport authorization, helper planning,
authenticated approval, one verified dispatch and duplicate refusal. Refusal,
retirement/expiry races, post-lock refresh, cancellation and resource cleanup
remain covered. The disposable runtime lane now composes the production Core
and dependency runtimes for helper execution; its original success, failure,
uncertain-response reconciliation and cleanup assertions remain in force.

These are source/synthetic observations, not installed-system acceptance.
Require clean final-head Evidence against the canonical RC8 base and complete
exact-head CI, including architecture builds and the actual digest-pinned Core
2026.9.2/ha-mcp lane and the scenarios previously unreached after helper failure.
Keep run/attempt, checkout/merge-parent and artifact evidence distinct. Prepare
one independent review of the correction, authority boundary and release delta;
implementation self-review does not establish an independent verdict.

Follow [RC9 acceptance](V2_2_0_RC9_ACCEPTANCE.md) and the
[Core registry runbook](CORE_RELEASE_REGISTRY.md). Installation alone does not
establish production Core 2026.9.2 authority: opt-in activation, the separate Core
trust key and reviewed signed production data remain separately authorized.
Publication, installed-image binding, a fresh complete public catalog, useful
behavior, natural dependency refresh and resource settlement each need evidence.
Prior PASS results retain their original release, pairing and observation time.

Leave the candidate draft for Josh's Ready decision. No publication, production
signing, deployment, live test, Core update or canary is authorized by these notes.
Recovery must preserve compatible Core, configuration/database state and usable
backups. Returning to RC8 restores the known helper-planning defect; returning
to RC7 removes data-driven Core admission. Neither is a complete recovery plan.
