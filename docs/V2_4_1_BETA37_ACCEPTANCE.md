# Engineering 2.4.1-beta.37 acceptance

This contract covers truthful stored execution outcomes (HAMCP-181 PR2),
plan-level verification and rollback availability (HAMCP-214), and the
HAMCP-193 dashboard governed-path tests. It follows 2.4.1-beta.36 and
includes it. The release activates no new capability: the signed registry
stays at sequence 12 and every dormant profile stays
`pending_signed_registry_admission`.

See also:
- Release notes:
  [`V2_4_1_BETA37_RELEASE_NOTES.md`](V2_4_1_BETA37_RELEASE_NOTES.md).
- Design: [`HAMCP_181_TRUTHFUL_OUTCOMES_DESIGN.md`](HAMCP_181_TRUTHFUL_OUTCOMES_DESIGN.md)
  (revision 4; owner decisions section 8; implementation note section 10).
- The beta.36 contract this release keeps:
  [`V2_4_1_BETA36_ACCEPTANCE.md`](V2_4_1_BETA36_ACCEPTANCE.md).

**Release target.** `2.4.1-beta.37` is staged by one owner-authorized run of
`scripts/promote_next_release.py --apply` on a base that carries
2.4.1-beta.36. The run consumes the declaration.

The contract separates four stages:
1. source and offline acceptance;
2. release CI;
3. publication;
4. installed Engineering acceptance.

It authorizes none of the later stages.

## 1. Source and offline acceptance

Bind the following:
- Josh's HAMCP-181 PR2 brief and decisions OD-1 (T2), OD-2 (F-A, no F3
  persisted-format change), OD-5, OD-6(ii), OD-7, OD-10, OD-11 (as narrowed
  in revision 4), OD-14 and OD-15;
- `2.4.1-beta.37` materialized in all three version authorities, with exact
  acceptance and release-note resolution from `scripts/codex-context.py` and
  no staged declaration left over;
- the PR head SHA;
- the passing PR `validate` run for that exact head;
- the Evidence tier log named in the PR.

The review needs a security section covering:
- **No new reachability.** No new write, read, endpoint, provider, fallback,
  tool or service call. PR2 changes what is recorded and displayed about a
  call, never whether a call is made.
- **No blind redispatch.** An intent is never discarded and an operation is
  never redispatched. A (b) task keeps its intent; a duplicate apply of a
  terminal plan reuses its terminal authority and refuses.
- **Not-sent proofs are positive only.** A (b) outcome requires the shared
  executor's no-send declaration, a `call_not_sent` attribute at a reviewed
  pre-write raise site, a WebSocket authorization failure raised before the
  frame is written, or the upstream fan second-authority refusal. Each proof
  is set before any send-capable await. `HomeAssistantUnavailableError`, a
  lost response, a registry error result, and the backup and lifecycle
  gateways are never proof. The attribute is never set where a send may
  have happened: not at the helper update command
  (`governance/resources.py:890` at base `587266d2`), and not at the shared
  entity-metadata not-applied raise (`governance/entity_metadata.py:605` at
  base), which also covers a sent `unauthorized` result (P3, r-proven).
- **No unproven no-mutation claim (OD-14).** `changed_by_engineering: false`
  and `sent_rejected_no_mutation` only on P1 or P3. A stored no-dispatch
  outcome that the child authority does not reproduce projects as an
  evidence conflict (c), never the stronger claim.
- **One projection.** Every reader (`get_execution_task`,
  `list_execution_tasks`, `get_change_plan`, `list_change_plans`, audit,
  compact receipts, fan and power reads) derives dispatch facts from the
  shared projection.
- **Health is display only.** `core_admission` reads the local admitted
  generation through `core_runtime.route_status`; it makes no Core call and
  never decides admission.

Preserve the following:
- stable v1.1.2;
- persisted formats (no F3 record change; task JSON gains only new
  `terminal_outcome` values and the `provider_call_started_at` map inside the
  existing `verification_summary`);
- every descriptor and input schema; the catalog is **96 tools, 97 with
  the optional YAML reader admitted**;
- probe fingerprints, the 17 signed Core capability profiles, the
  owner-signed Core registry (sequence 12), the signed ha-mcp registry and
  the delete profile's contract fingerprint
  `sha256:5688af5d141b97ca33eadf251ecfbb8f3b1f833024b0ee1acea4973f558f887d`,
  recomputed at base and head;
- the HOLD and the support window;
- add-on permissions, dependencies and every workflow.

### Offline proof

No test reaches a live system. Every transport is a synthetic fake that
enforces the real clients' Core-authority check
(`tests/core_authority_fakes.py`).

- `tests/test_hamcp181_terminal_outcome_t2.py`: every public surface agrees
  with `get_execution_task` for (a), (b), (c) and r-proven, including the
  delete family.
- `tests/test_hamcp181_proven_no_call_producers.py`: each proof site, the
  WebSocket client, end-to-end chains, the b5/b8 declarations and the
  backup/lifecycle exclusions.
- `tests/test_hamcp214_plan_verification_rollback_projection.py`: plan
  verification, `post_apply_fingerprint` and rollback availability per
  family (configuration, configuration rejection, label, entity metadata,
  availability/exposure, configuration delete).
- `tests/test_hamcp181_rollback_unchanged_forward.py`: OD-11.
- `tests/test_hamcp181_health_core_admission.py`: stub and TEST-signed
  registry.
- `tests/test_hamcp181_entity_registry_error_unproven.py`: a registry error
  is never no-mutation, with hash-pinned Core 2026.10.0 and 2026.10.1
  excerpts and one record written by the exact beta.8 writer.
- `tests/test_hamcp181_beta8_downgrade.py`: the exact beta.8 reader
  (`7370ad25`, tag object `4913bfd6`) over thirteen shapes written by the
  PR2 candidate writer. Three `expectedFailure` tests keep the governed-plan
  limitation below visible.
- `tests/test_hamcp181_architecture_guard.py` and
  `tests/test_dashboard_update_mvp.py` (HAMCP-193).
- Mutations: `docs/evidence/hamcp181-pr2-mutations/`. `results.json` records
  each mutant and the tests that killed it.

### Downgrade rule (design section 4.3)

- **Supported:** downgrade to exactly 2.4.1-beta.8 for fan, power and
  execution-task records, under terminal-only quiescence (every task, F3
  child, fan and power record terminal; no unexpired claim). Beta.8 loads
  them with zero quarantine and zero provider calls.
- **Not supported:** downgrade to beta.8 while any governed change plan
  exists. Beta.8 quarantines every plan written since HAMCP-213
  (2.4.1-beta.29): its reader rejects the `called_scripts` operation field
  and policy version `f2-v3`, so `get_change_plan` answers
  `change_plan_not_found`. This is pre-existing and needs Josh's decision
  (a fan/power/task-only floor, a newer floor, or a compatibility change).
- Never clear F3 journals, task files or locks. No migration rewrites a
  historical record (OD-10).

## 2. Release CI and publication

Require exact-head CI before release acceptance. Pending or unavailable jobs
are not passing evidence.

Publication needs Josh's Ready authorization and the protected merge handoff.
Verify the merged source, the release tag, the immutable version and commit
images, provenance and SBOM with the shipped verifiers. Never invent future
image identities. Retain the beta.36 artifacts and the known-good earlier
artifacts for recovery.

No stored format changes. Rollback is reinstalling the beta.36 artifact.

## 3. Installed Engineering

Separately authorize installation. Checks 1 to 3 are read-only; checks 4 and
5 are owner-approved live canaries.

1. **Installed-image binding.** Bind the running add-on to the published
   image index digest, the release tag and the protected `main` merge commit.
2. **Catalog.** `tools/list` shows 96 tools (97 with the YAML reader
   admitted).
3. **Inactive.** `list_capabilities` and server health still show the four
   dormant providers' `core_admission` as their pending reasons under
   sequence 12.
4. **Power and fan canary surface consistency.** With Josh's approval, run
   one ordinary `control_power` and one `control_fan` action on the usual
   canary entities. For each, `get_execution_task`, `list_execution_tasks`,
   the fan/power task read and the audit entry must agree on
   `dispatch_phase`, `provider_dispatch_occurred`, `dispatched_at` (basis
   `provider_call_start_time`, not the intent commit time) and the terminal
   outcome.
5. **Plan-level verification on a configuration apply.** With Josh's
   approval, apply one single-operation configuration plan on a test
   automation. After `succeeded_verified`, `get_change_plan` must show
   `verification.status: verified`, `post_apply_fingerprint` equal to the
   operation's actual fingerprint, the task's `verification_summary` as its
   source, and a rollback status of `available_pending_current_state_check`.
   No rollback is applied by this check.

## 4. Out of scope

Signing sequence 13, the HAMCP-155 deletion canary and any downgrade are not
authorized by this document.
