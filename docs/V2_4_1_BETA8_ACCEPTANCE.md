# Engineering 2.4.1-beta.8 acceptance

This contract covers HAMCP-177: dependency-scoped admission for governed
dashboard writes, on top of beta.7 (the HAMCP-129 platform reads). It separates
four stages: source and disposable acceptance, release CI, publication, and
installed Engineering acceptance. This document authorizes none of the later
stages.

## 1. Source, review and CI

Bind the following:
- Josh's direction of 2026-10-13: admit dashboard writes the same way as power
  and fan, only against the exact upstream tool contracts that the dashboard
  write path uses, with extra upstream tools granting nothing;
- the task's protected-path authorization for the Engineering runtime and the
  beta.8 release metadata;
- the base `main` (2.4.1-beta.7), and every later main merge into the branch;
- the final reviewed commit and tree.

Earlier review does not cover changed code.

Materialize 2.4.1-beta.8 in all three version authorities through the existing
staging tool. Require exact acceptance and release-note resolution, with no
staged declaration left over. Complete Full/Evidence on clean source and the
bounded independent review. The review needs a security section covering:
- the dependency set and its derivation from source;
- per-component contract exactness, identity, protocol, provider disposition,
  holds and revocation;
- Core authority and same-session revalidation;
- zero exposure or dispatch of unrequired advertised tools;
- the `tool_call_attempted` marker, which is the only path that may claim no
  dispatch after setter admission.

Require exact-head CI before release acceptance. Pending or unavailable jobs
are not passing evidence.

Preserve the following:
- stable v1.1.2;
- historical persisted fixtures and durable formats;
- the dashboard authority evidence hash;
- locks, holds and uncertainty;
- production journals;
- probe fingerprints;
- the Core capability profiles;
- every owner-signed Core registry entry and the signed ha-mcp registry;
- the 8.6.0 HOLD and the support window;
- the read gateway, backup and lifecycle holds, and typed power and fan;
- dependency locks, build inputs, pinned runners and workflow permissions.

The public catalog stays at **87 tools (59 native, 28 delegated)**, and every
beta.7 descriptor is unchanged.

Offline source evidence must prove each of the following through real MCP SDK
client sessions against a synthetic ha-mcp peer, the shipped provider and the
real governance and F3 runtime, at 8.5.0 and 8.6.0.

**Dependency set.** The write path names exactly `ha_config_get_dashboard`,
`ha_get_skill_guide` and `ha_config_set_dashboard`, and admission uses exactly
that set.

**Useful success.**
- A governed dashboard write is planned, approved, applied and verified by
  readback, with exactly one setter call. This holds with the stock 77-tool
  catalog and with the 80-tool catalog that adds `ha_config_get_yaml`,
  `ha_list_files` and `ha_read_file`.
- The provider authority is identical for both catalogs.
- The extra tools are never called, and the provider refuses to dispatch them.
- The whole-catalog validator still refuses the 80-tool catalog.

**Refusal.** Each case refuses with no setter call:
- input schema, description, annotations, output schema or title drift in
  each required tool;
- a missing or duplicated required tool;
- version or protocol change;
- a held dashboard disposition, or a revoked release.

Drift in an unrequired tool grants and takes nothing. Each case is refused at
planning as non-retryable `provider_unavailable`
(`dashboard_provider_not_admitted`), and after planning as
`failed_pre_dispatch`. Setter drift between the guide and the setter is
`dispatch_failed_confirmed` after the saved intent (see Core authority
below for the outcome). Extras appearing or vanishing between sessions
still succeed.

**Core authority.** Core retirement before the guide is pre-dispatch. Core
retirement during the guide, after setter admission, is
`dispatch_failed_confirmed`: the task failed after the saved-intent boundary
(`failed_post_dispatch`); no setter message was sent; no change was made; it
is never redispatched. The execution evidence records `setter_call_not_sent`
and `provider_confirmed_no_mutation`, and the apply result, task read,
same-ID replay refusal and audit show `provider_dispatch_occurred` false,
`dispatch_performed` false, `setter_call_not_sent` true,
`changed_by_engineering` false and `intent_recorded` true (stored counts
qualified by `attempt_counts_basis`); a sent or possibly sent setter never
carries `setter_call_not_sent`. The truthful pre-send phase is HAMCP-181.

**Truthful outcomes.**
- Grouped and direct SDK admission refusals keep the typed, non-retryable
  `reviewed_contract_mismatch` category.
- A Core refusal is `connection_failed` without dispatch.
- Transport loss and timeouts after the call stay dispatched.
- Cancellation propagates. A cancelled setter leaves the saved dispatch intent
  `possibly_dispatched` for recovery.

**Existing semantics with 80 tools.** The following are unchanged:
- stale-state refusal before dispatch;
- concurrent duplicate suppression;
- exact restore through a second governed plan;
- lost-response readback;
- structured setter rejection;
- one telemetry exchange per session.

**Mutation checks.** Each of the following fails a test:
- reintroducing the whole-catalog check;
- generic planning-error mapping;
- counting a Core refusal as a dispatch;
- dropping the marker from grouped errors;
- mapping admission refusals to a generic transport error;
- removing the disposition check.

## 2. Publication and recovery preparation

Publication needs Josh's applicable Ready authorization and the protected merge
handoff. Verify the following with the shipped verifiers:
- the merged source;
- v2.4.1-beta.8;
- the immutable version and commit images;
- provenance and SBOM.

Never invent future image identities. Retain the known-good beta.7 and beta.6
artifacts and options for recovery. Nothing new is persisted, so downgrade to
beta.7 needs no migration. Before a downgrade, disable ha-mcp's filesystem
tools, because beta.7 refuses dashboard writes while they are advertised.

## 3. Installed Engineering

1. **Installed-image binding.** Separately authorize installation of the
   published Engineering artifact. Bind the running add-on to the published
   image index digest, the v2.4.1-beta.8 tag and the protected `main` merge
   commit. A source version alone does not prove the installed image.
2. Compare all 87 public descriptors, Core signed selection, storage and audit
   health, F3 readiness, upstream admission and zero fallback.
3. **Stock-catalog check.** Keep ha-mcp's filesystem tools disabled. Record the
   `server_info` upstream catalog count, unreviewed count, reviewed-accounting
   validity and catalog fingerprint.
4. **Dashboard canary.** Run this on a test dashboard Josh chooses, with Josh's
   explicit authorization of each write:
   - Record the dashboard's configuration hash and content.
   - Write: plan a bounded change (for example, the title), approve it and
     apply it. The task must report exactly one setter dispatch and end
     `succeeded_verified`.
   - Readback: `get_dashboard_config` must show exactly the planned change.
   - Rollback: dashboard rollback is not a governed capability, so restore the
     recorded configuration through a second governed plan. That plan must end
     `succeeded_verified`, and readback must match the recorded configuration
     hash.
   - `get_execution_task` must resolve each task. Replaying an apply must not
     dispatch again.
5. **Owner step: expanded catalog.** Only after steps 1-4 pass, Josh re-enables
   ha-mcp's filesystem tools (`ha_config_get_yaml`, `ha_list_files`,
   `ha_read_file`) as a separate owner decision. Then:
   - Record `server_info` again. The catalog count must rise by the enabled
     tools, and Engineering must still expose 87 tools, none of them a
     filesystem tool.
   - Repeat the full dashboard canary of step 4 on the same test dashboard.
   - Repeat the beta.6 power canary on an entity Josh chooses, with Josh's
     explicit authorization of each physical action:
     - Run `turn_off`, then `turn_on`, or restore the recorded pre-canary
       state. Each step must report exactly one provider attempt and
       `succeeded_verified` by readback.
     - Leave the entity in its recorded pre-canary state.

   If any repeated canary fails, disable the filesystem tools again and report.
   Do not retry with other tools enabled.

This release does not add the refusal warning log (HAMCP-172) or change the
signed-registry read retry telemetry.

No push, CI trigger, signing, publication, installation or live action is
authorized by source completion.
