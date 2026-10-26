# HAMCP-155 governed deletion of automations and scripts — design

Status: **design gate passed 2026-10-23 (no blocker). Part 2 builds the
capability inactive.** This document does not authorize deployment, live Home
Assistant access, registry signing, a signed-profile change or a release.

Base: `origin/main` `855da17832066b18913cc35541b9413238626d88`
(Engineering `2.4.1-beta.32`). Core authority: exact tag `2026.10.1`
(`abe1fdd7`); `homeassistant/components/config` is byte-identical to
`2026.10.0`.

## 0. Owner decisions (Josh, 2026-10-23)

- Deletion ships in the sequence-13 signing batch with HAMCP-069B and
  HAMCP-165. The batch is signed on Core 10.x as soon as it is built
  (about 2026-10-26 to 2026-10-27).
- Build it **inactive**:
  - a new sibling profile;
  - `pending_signed_registry_admission` until a signed registry admits it;
  - the service-layer admission check runs first.
- No change to any signed profile. No unsigned admission.

## 1. Gate item 1: HAMCP-181 PR2 is not a prerequisite

The register lists HAMCP-181 PR2 (truthful stored outcomes) as a
prerequisite. The code does not support that dependency.

- **Recovery never redispatches.** A child whose send is uncertain is marked
  `possibly_dispatched=True`. Blind redispatch is refused
  (`BlindRedispatchProhibited`), and recovery reads back only.

  A delete that may have been sent is therefore resolved by readback:
  - absent means verified;
  - present means not applied, or unresolved.

  It is never sent twice. Nothing in that path reads the dispatch strings
  that 181 PR2 corrects.

- **Rollback eligibility keys on verified outcomes, not dispatch strings.**
  `_plan_fresh_rollback` requires all three:
  - `record.normalized_outcome == "succeeded_verified"`;
  - one dispatch;
  - a matching resulting-state fingerprint.

- **The one inexact case errs on the safe side.** A call refused after intent
  but before it is sent is stored as send-uncertain
  (`provider_dispatch_occurred: true`, `failed_post_dispatch`). For a
  destructive operation, overstating that something may have been sent is the
  safe direction. No path reports a false success or a false "not sent".

- **A Core rejection is reported truthfully today.** Core answers DELETE of a
  missing item with HTTP 400 "Resource not found" before any write (§2.1).
  The P1 rule in `dispatch_facts.py` (automation and script target types)
  maps that confirmed rejection to `sent_rejected_no_mutation`, which is
  correct for DELETE.

Interim disclosures the delete family carried until 181 PR2, and their status
from 2.4.1-beta.37:

- (a) **Resolved where the no-send is proven.** A delete stopped after the
  intent is committed, with a proven no-send, now reads
  `failed_after_intent_not_dispatched`. The proof is the shared F3 executor's
  no-send declaration for a failure after the intent and before the provider
  call. It shows phase `intent_recorded_not_sent`, `provider_dispatch_occurred:
  false` and `dispatched_at: null`, the same on every surface. A Core 4xx
  answer to the DELETE stays P1, `sent_rejected_no_mutation`, and is never
  shown as "may have been sent". **Residual:** a pre-request raise inside
  `governance/configuration_delete.py` (outside the PR2 file scope) still
  stays send-uncertain (c), which is the safe direction for a delete.
- (b) **Resolved (HAMCP-214).** A `succeeded_verified` delete shows plan
  `verification.status: verified`. Its desired and actual fingerprints are
  both the absence fingerprint, and `post_apply_fingerprint` equals the
  readback. `execution_task.verification_summary` stays the per-task
  authority, and the plan copies it.
- (c) **Unchanged.** Recovery from a failed or partial delete is a new governed
  plan. The recreate rollback is offered only for children that are
  `succeeded_verified`. An intent is never discarded and a delete is never
  redispatched.

Tests: `tests/test_hamcp181_terminal_outcome_t2.py` (`DeleteAgreementTests`
a/b/c and `RejectedDeleteTests`).

## 2. Gate item 2: Core contract

### 2.1 The only write

`DELETE /api/config/automation/config/<id>` or
`DELETE /api/config/script/config/<object_id>`, with no body. In
`components/config/view.py` `EditKeyBasedConfigView.delete`, Core runs these
steps under `mutation_lock`:

1. It reads the YAML file. If the key is absent, it returns
   **400 "Resource not found"** and writes nothing.
2. Otherwise:
   - it removes the entry with `_delete_value`;
   - for automations, the id-based view pops only the **first** entry with a
     matching `id`;
   - for scripts, it does `data.pop(key)`.
3. It writes the file atomically.
4. It schedules `post_write_hook(ACTION_DELETE, key)` as a fire-and-forget
   task.
5. It returns `{"result": "ok"}`.

Only UI-managed items (in `automations.yaml` or `scripts.yaml`) are
deletable. A YAML-package or `!include`-only item has no config GET and is
refused at planning.

### 2.2 Side effects (no reload)

For `ACTION_DELETE`, the automation and script hooks **do not reload**.

1. Each hook resolves `ent_reg.async_get_entity_id(domain, domain, key)`.
   If there is no registry entry, it returns, and the loaded entity keeps
   running until the next reload.
2. Otherwise it calls `ent_reg.async_remove(entity_id)`. The registry
   `remove` event makes the entity call `async_remove()`. The entity stops,
   which ends any current run, and it leaves the state machine.

Consequences disclosed on every plan:

- The automation stops triggering, or the script stops being callable.
- Any current run is stopped.
- References from other automations, scripts and dashboards break (§3.2).
- Core keeps a `DeletedRegistryEntry` for **30 days**
  (`ORPHANED_ENTITY_KEEP_SECONDS`). It holds the entity_id, name, icon, area,
  labels, categories, aliases, disabled/hidden state and options, including
  assistant exposure.

### 2.3 Planning reads and exact readback

Planning and preflight:

- GET the config. It must exist (200) and is captured as the exact pre-image.
- Read the registry entry for `(domain, domain, key)`. It must exist. Without
  it, Core would not unload the entity, and the runtime effect could not be
  verified, so the item is **refused**.

Verification is proof of absence, polled within a bound because the hook is
fire-and-forget:

- the config GET returns **404**, which the gateway maps to `None`;
- the registry has no entry for `(domain, domain, key)`;
- the state machine has no state for the captured entity_id.

Truthfulness rules:

- An item that was already absent at preflight is a stale-state refusal. It
  is never a verified delete by Engineering, and never eligible for rollback.
- If the GET still returns 200 after a 200 DELETE (for example, a duplicate
  automation id), verification reports a readback mismatch.
- If the registry entry or the state is still present after the bound, the
  child is reported as `config_removed_runtime_unverified`, not success.

### 2.4 Rollback (HAMCP-135 model)

`rollback_change` on a delete plan creates a **new, separately approved plan**
and never runs on its own. For each `succeeded_verified` delete child whose
target is still absent (fingerprint of `None`), that plan has one governed
**create** of the same `resource_type` and `target_id`.

- `proposed_config` is a deep copy of the captured pre-image.
- It runs under the already-admitted create identities.
- Other children are listed as excluded.
- When the create is verified, the config matches the pre-image exactly.
- Core restores the registry metadata from the `DeletedRegistryEntry`,
  including the original entity_id if it is still free. This holds only if
  the recreate happens within 30 days and the deleted entry was not purged.
  It is disclosed, not promised.

## 3. Gate item 3: policy

### 3.1 Protected effects (HAMCP-213)

Today `physical_effects.compare_physical_effects` returns `NONE_COMPARISON`
for any action other than create or update. That would fail open for delete.

The change:

- `delete` is added to the supported actions. `before` is the pre-image and
  `after` is absent.
- The reason is `configuration_deleted`. Every protected effect becomes a
  `removed` row with verdict `changed`, or `unresolved` when it cannot be
  resolved.
- The unlock/disarm floor is scanned on the pre-image.

The existing `_safety_critical_comparison_policy` then requires
`ELEVATED_ADMIN` and the safety-critical acknowledgement. The consequence
statement reads: "Deleting <kind> <id> removes these protected effects: …".
`_exact_configuration_write` gains an exact delete clause, so deletion is
never treated as an ordinary write.

### 3.2 Impact disclosure (dependents)

The entity is the captured entity_id (`automation.*` or `script.*`). The 069B
predicates `dependency_evidence_gaps` and `_consumer_evidence` run against the
fenced dependency index:

- automations, blueprints and scripts that reference the entity are listed;
- `UNAVAILABLE_COVERAGE` (dashboards, groups, scenes, templates) is stated as
  unavailable, never as "no consumers".

Every evidence gap becomes `dependency_evidence_incomplete:<gap>`. Following
the 069B rule that unresolved is protected, it raises the plan to elevated
with "impact could not be fully determined". A consumer that is
safety-critical or unresolved does the same.

`consumer_evidence_fingerprint` is bound into the plan and rechecked at
preflight. A change is a stale-state refusal.

### 3.3 Limits and execution

- One to eight deletions per plan.
- Ordered and non-atomic; stops on the first failure. Unattempted children
  are marked as such.
- Locks: the existing per-resource lock, the same as create and update.
- Exact-hash external approval through the Ingress panel only. There is no
  self-approval path.
- Rollback is available as in §2.4.

## 4. Shape (inactive)

| Item | Value |
| --- | --- |
| Tool | `create_configuration_delete_plan` (sibling); input `deletions: [{resource_type: automation\|script, target_id}]` (1–8), `reason`, `expiration_minutes` |
| Core profile | `core.configuration_delete_operation` (runtime-only; `CORE_RUNTIME_CAPABILITY_PROFILES`, not `CORE_CAPABILITY_PROFILES`) |
| F3 identities | `delete_automation_configuration`, `delete_script_configuration` → the new profile only |
| Adapter | `compiled-core-configuration-delete-operation-v1`, `code_owned_closed_contract` |
| Admission | `core.route_status((profile,))` in the service before any read; rechecked at `_before_persist`; router pending details; catalog and health `core_admission: pending_signed_registry_admission` |
| Generic planner | `create_configuration_plan` keeps refusing `delete` |
| Legacy | `delete_automation` stays prohibited; upstream `ha_config_remove_*` stay unrouted |

A delete never reaches the REST client except through the new identity. Only
the new gateway method sends DELETE. The generic `write` keeps raising for any
action other than create or update.

## 5. Tests and mutations (Part 2)

Tests:

- Refused while unadmitted: zero Home Assistant reads and nothing stored.
- Test-signed registry, end to end: plan → approve → delete → readback proves
  absence → rollback plan → approve → recreate matches the pre-image exactly.
- A protected effect → elevated plus acknowledgement.
- Dependents and unavailable coverage are disclosed.
- Stale state is refused (changed pre-image, item already absent, consumer
  fingerprint changed).
- A rejected (400) delete, an uncertain delete, and a partial plan (stop on
  first failure) are each reported truthfully.
- No unplanned writes.
- No self-approval.

Mutations (`docs/evidence/hamcp155-mutations/`):

1. the admission check is removed;
2. the readback is skipped;
3. a protected effect is treated as ordinary;
4. the rollback pre-image capture is skipped.

## 6. Release

The next free beta at materialization. Beta.33 is staged by both #294 and
#303; this PR takes the next free beta and discloses the collision. It
"activates nothing new".

Post-signing canary: on one test automation,
1. delete it under an approved plan and read back that it is absent;
2. roll it back and read back that the recreate matches exactly.

Sequence 13 must reference `core.configuration_delete_operation` for this to
run.

The CI container lane for HAMCP-155 is a follow-up, because `ci.yml` changes
are not authorized for this task.
