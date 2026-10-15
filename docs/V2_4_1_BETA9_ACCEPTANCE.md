# Engineering 2.4.1-beta.9 acceptance

This contract covers HAMCP-183, which is OD-15 of the
[HAMCP-181 design](HAMCP_181_TRUTHFUL_OUTCOMES_DESIGN.md). A sent governed
entity-registry metadata update answered with an error result is no longer
recorded as confirmed non-mutation. The release builds on beta.8 (HAMCP-177).
The contract separates four stages:
- source and disposable acceptance;
- release CI;
- publication;
- installed Engineering acceptance.

This document authorizes none of the later stages.

## HAMCP-182 publication gate

This fix gates HAMCP-182 publication. HAMCP-182 is the owner-signed Core
registry update that selects `core.entity_registry_metadata_operation` and
makes `create_entity_metadata_plan` available in production. By Josh's
OD-15 decision of 2026-10-14, the following must wait until HAMCP-183 is
merged and deployed with section 3 below passing:
- HAMCP-182's Ready action;
- its merge;
- its registry publication.

HAMCP-182's container receipts must come from a full-tier `main` CI run
after this merge. That way, the receipts bind the runtime tree that will use
the profile. Receipts bound to an earlier runtime tree, such as CI #1086
(beta.7) or any beta.8 run, do not cover this release. HAMCP-182 preparation,
pre-signing review and signing may proceed earlier. This document does not
authorize any of them.

## 1. Source, review and CI

Bind the following:
- Josh's OD-15 direction of 2026-10-14:
  - only an authorization refusal (`unauthorized` or `forbidden`) to a sent
    update stays confirmed non-mutation;
  - every other error result is uncertain and settled by registry readback;
  - no redispatch;
- the task's protected-path authorization:
  - `governance/entity_metadata.py`;
  - `f3_configuration/adapter.py`;
  - tests;
  - the beta.9 release metadata;
- the base `main` (2.4.1-beta.8), and every later main merge into the branch;
- the final reviewed commit and tree.

Earlier review does not cover changed code.

Materialize 2.4.1-beta.9 in all three version authorities through the existing
staging tool. Require exact acceptance and release-note resolution, with no
staged declaration left over. Complete Full/Evidence on clean source and the
bounded independent review. The review needs a security section covering:
- the P3 boundary, which is the only post-send error that may claim
  non-mutation;
- the absence of any new dispatch, redispatch or write path;
- the readback mapping and its restriction to the error-result dispatch path;
- recovery from persisted dispatch diagnostics;
- rollback eligibility for each resolution.

Require exact-head CI before release acceptance. Pending or unavailable jobs
are not passing evidence.

Preserve the following:
- stable v1.1.2;
- historical persisted fixtures and durable formats;
- task states and terminal outcome strings (new ones are HAMCP-181 PR2);
- locks, holds and uncertainty;
- production journals;
- probe fingerprints;
- the Core capability profiles;
- every owner-signed Core registry entry and the signed ha-mcp registry;
- the 8.6.0 HOLD and the support window;
- dashboard write admission, typed power and fan, and the read gateway;
- dependency locks, build inputs, pinned runners and workflow permissions.

The public catalog stays at **87 tools (59 native, 28 delegated)**, and every
beta.8 descriptor is unchanged.

Offline source evidence must prove each of the following. It uses the real
WebSocket client against a disposable loopback peer that speaks Core's
`config/entity_registry/*` message shapes, together with the shipped gateway
and the real governance and F3 runtime.

**Useful success: the probe.** Each non-authorization Core error code
(`unknown_error`, `invalid_format`, `home_assistant_error`, `not_found`) is
returned after the registry entry was assigned. The following must hold:
- The plan is verified applied (`succeeded_verified`) by a registry readback
  taken after the update.
- The dispatch is recorded as uncertain, with `provider_error_after_send` in
  the audit next to `exact_readback_verified`.
- Same-ID replay reuses the task without a second update.
- Rollback is offered, and the governed reverse plan restores the prior name
  and icon.

**Truthful readback mapping.**

| Readback | Outcome | Rollback |
| --- | --- | --- |
| Planned state | `succeeded_verified` | Offered |
| Prior state | `verification_mismatch` (`failed_post_dispatch`) | Refused |
| Any other state | `manual_review_required` (`readback_matches_neither_plan_nor_prior`) | Refused |
| Unavailable | `observing`, then `manual_review_required` at the evidence deadline | Refused |

No path records `provider_rejected_mutation` or
`provider_confirmed_no_mutation`, and none redispatches.

**Recovery.** The shipped adapter's `recover` and `verify` keep this
mapping when they receive only the persisted dispatch diagnostics.
Lost-response diagnostics keep the earlier mapping, in which a third state
is `verification_mismatch`.

**Refusal preserved.**
- `unauthorized` and `forbidden` stay `dispatch_failed_confirmed`
  (`provider_permission_denied`) with no readback.
- Pre-send refusals keep their outcomes with no dispatch:
  - a stale label reference is `preflight_rejected`;
  - the area and name rule is refused at planning
    (`entity_metadata_area_requires_own_name`).
- Gateway classification is unchanged for pre-command authentication
  failure, lost response and the closed payload.

**Mutation checks.** Each of the following fails a test:
- restoring the blanket confirmed-non-mutation mapping;
- treating P3 as uncertain;
- recording the error result as confirmed in the adapter;
- mapping a third state to `verification_mismatch`;
- dropping the persisted diagnostics during recovery;
- always reporting the prior state.

The existing 069A-1 entity-metadata suites stay green.

## 2. Publication and recovery preparation

Publication needs Josh's applicable Ready authorization and the protected merge
handoff. Verify the following with the shipped verifiers:
- the merged source;
- v2.4.1-beta.9;
- the immutable version and commit images;
- provenance and SBOM.

Never invent future image identities. Retain the known-good beta.8 and beta.7
artifacts and options for recovery. Nothing new is persisted, so downgrade to
beta.8 needs no migration. A downgraded server records an entity-metadata error
result as confirmed non-mutation again, so do not publish HAMCP-182 while
beta.8 is installed.

## 3. Installed Engineering

1. **Installed-image binding.** Separately authorize installation of the
   published Engineering artifact. Bind the running add-on to:
   - the published image index digest;
   - the v2.4.1-beta.9 tag;
   - the protected `main` merge commit.

   Record the add-on version and image reference that Home Assistant
   reports, and match them to the published index. A source version alone
   does not prove the installed image. Do not use the HAMCP-182 gate above
   until this binding is recorded.
2. Compare all 87 public descriptors, Core signed selection, storage and audit
   health, F3 readiness, upstream admission and zero fallback. Until HAMCP-182
   is published, `core.entity_registry_metadata_operation` stays withheld, and
   `create_entity_metadata_plan` fails closed without any Home Assistant
   request.
3. Retain beta.8 installed obligations unchanged, including the dashboard and
   power canaries when Josh chooses to repeat them.

The error-result path cannot be induced safely on a live registry, so its
evidence is the offline source evidence of section 1. Any first live entity
metadata change happens only after HAMCP-182 is published, on a target that
Josh separately approves.

No push, CI trigger, signing, publication, installation or live action is
authorized by source completion.
