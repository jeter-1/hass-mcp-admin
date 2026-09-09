# Engineering 2.2.0-rc.5 release notes

RC5 prepares the independently reviewed RC4-DR-1 correction. Materialized source
declares `2.2.0-rc.5` in all three authoritative locations and consumes
`.release/next-version`. Stable remains 1.1.2. These notes do not establish
publication, installed-image identity or restored live capabilities.

## Integration identifiers preserve their string-pair contract

RC4 applied the 128-character registry-ID/reference limit to integration
identifier string pairs. A legitimate identifier could therefore withhold three
Core capabilities and five delegated reads despite an exactly admitted ha-mcp
provider. RC5 separates integration identifier validation from registry target
identity validation. It preserves the original text without truncation,
normalization or another arbitrary small length cap.

The global registry-ID/reference limit, connections validation, pair type/arity,
collection and byte bounds, duplicate/parent/cycle checks, capability-local
refusal, per-read authorization and cleanup remain unchanged. This corrects
evidence compatibility for existing capabilities; it adds no operation, schema,
provider contract, admission policy, route, forwarding, fallback or permission.

## Reviewed source and complete-registry evidence

The independent source verdict covers
`bf0312ada55f7611014c6a186ccea72f2faf5c59` against published RC4
`535bfcc05b35c1c35fb31ecb404d46e12e590cbb`, with **356 independently passing
tests**, zero skips/failures/errors. Preserve the two correction files exactly
and review the release-preparation delta separately.

The separately executed complete-registry comparison at approximately
`2026-09-09T20:46:28Z` retrieved one complete fresh response on RC4/Core 2026.9.1.
Unchanged RC4 rejected zero-based record 63, whose integration identifier pair
contained strings of lengths 4 and 149. The exact candidate accepted all 261
records and completed whole-registry checks on an identical copy. Neither
validator modified its input. Collection, identity continuity and cleanup passed;
no raw household registry or identifier values were exported.

The failing predicate is observed. This later response does not identify the
historical response that created authority generation 1. The response had no
child records, so positive live parent/child coverage is not established.
Transient candidate execution did not deploy the correction or publish authority:
the surrounding RC4 readbacks still reported 71 tools and three unavailable
Core capabilities. The household incident is not claimed resolved.

The complete comparison receipt SHA256 is
`8d6d765e3a0019c6f5860ba8a6ba87331b225fd9c4f8fa2bd01df7b7942b958c`.
Preserve the review, failed diagnostic attempts, successful receipt and prior
unchanged-RC4 metadata failures. Final RC5 validation must pass the legitimate
RC4-to-RC5 metadata transition using the published RC4 base and unchanged rules.

## Preserved contracts and remaining acceptance

Exact reviewed Core compatibility, including 2026.9.0/2026.9.1, and internal
ha-mcp 8.4.3 remain unchanged. Engineering remains the unified public Nabu Casa
endpoint and catalog. The healthy pairing requires all 17 Core capabilities,
51 static tools plus 25 delegated reads, 76 total. `ha_get_operation_status`
remains held and absent from ordinary registration.

RC4 display-string semantics and failed-reconciliation pacing remain intact.
Recovery may take the existing 300-second interval plus probe time; this is not
a global request-rate limit. Shared dependency builds retain manager-owned
authority, per-read revalidation, cancellation/shutdown cleanup, source/post-lock
fences and the separate 300-second cooperative build deadline. Soft/hard TTLs
remain 600/3600 seconds. Already-dispatched reads may finish after cancellation;
no remote-abort guarantee is established. Earlier raw nonfinite REST rejection,
capability-local failures/timeouts, bounded authenticated WebSocket replacement,
composite authority and held-read cleanup remain required.

Public schemas, attribution, governance (task schema 1, approval authority 3),
dependencies, workflows, Dockerfiles, permissions and stable-v1 remain unchanged.
Source review, final candidate validation/CI, future publication, installed-image
binding, fresh public catalog and live restoration are separate evidence stages.
Do not transfer earlier CI results to a new head. Record exact final-head
run/attempt IDs, tested commits, synthetic merge parents/tree and artifacts.

The last observed Core is already 2026.9.1. Follow
[RC5 acceptance](V2_2_0_RC5_ACCEPTANCE.md) after separately authorized corrected
deployment: prove installed artifact identity, complete fresh 76-tool catalog,
all 17 Core capabilities and the five restored delegated reads, useful unaffected
reads, automatic refresh after the initiating request returns, fresh replacement
evidence, settled resources and zero fallback. Canaries and restoration retain
their exact authorization, panel approvals and verification; navigation and
restart recovery retain separate requirements. No further Core update or stable
promotion is part of this release preparation.

## Immutable history and recovery

Published RC4 remains immutable. Returning to it restores the observed identifier
defect; RC3 additionally restores display/reconciliation defects and RC2 the
shared-refresh defect. None is a complete recovery procedure for an updated
Core/configuration/database state. Before authorized deployment recovery, bind
compatible artifacts, a verified full backup/off-host copy, recovery access and
restoration consequences. Backup, restart and live changes require separate scope.

Before merge, release preparation can remain unused or be reverted while keeping
the reviewed correction and evidence. Leave the PR draft for Josh. His later
Ready action may authorize controlled merge/publication under repository policy;
this task does not authorize Ready, approval, merge, publication, deployment or
live operations.
