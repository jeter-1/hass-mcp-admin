# HAMCP-165 governed label create, edit and assignment — design

Status: **owner decisions recorded 2026-10-23 (§0). The implementation ships
INACTIVE and switches on only with the sequence-13 registry signing (seq-13
batch = HAMCP-069B + HAMCP-165 + HAMCP-155).** This document does not
authorize deployment, live Home Assistant access, registry creation or
signing, or a release.

## 0. Owner decisions (Josh, 2026-10-23)

- **Seq-13 batch:** 069B, 165 and 155 are signed together on Core 2026.10.x
  once the batch is built. This change leaves the 165 profile ready to sign:
  compiled, fingerprinted, with its evidence lane defined.
- **Scope:** label CREATE and EDIT (`name`, `color`, `icon`, `description`)
  and entity assignment through the existing signed HAMCP-069A path only.
  Device and area label assignment come later. Deletion is out of scope
  (HAMCP-155): `config/label_registry/delete` is never sent.
- **Inactive:** a NEW sibling Core profile `core.label_registry_operation`;
  no signed profile is modified. While the profile is absent from the signed
  registry, every entry point refuses with `pending_signed_registry_admission`.
  Admission is checked FIRST at the service layer, before validation reads:
  zero provider reads, nothing stored. The lowest builder
  (`_create_configuration_plan`, including generic `create_configuration_plan`
  routing) checks it again first and at the persist boundary (review1 M1).
  F3 re-checks it at dispatch on every path (first apply, a retained approved
  plan, resume, recovery and private read-only reconciliation, whose
  verification of a held observation stays held): without a Core runtime the child refuses
  `core_authority_unavailable`, and with a Core that does not admit the
  profile it refuses `pending_signed_registry_admission`, with zero Home
  Assistant commands (review1 H1; the same gate covers 069B's two resource
  types). There is no unsigned local admission and no registry is created or
  signed.
- **069B fix (separate commit):** 069B's two unsigned profiles now name
  `websocket_entity_registry_list` and `websocket_expose_new_entities_get`,
  the reads their gateway sends (HAMCP-222 OD-5).
- **CI:** only the 165 lane is defined (and 069B's lane, which did not exist).
  No existing lane changes behaviour.

## 1. Core authority (exact source, Core 2026.10.0 and 2026.10.1)

The two releases are identical for every file cited.

| Item | Source |
|---|---|
| WebSocket commands, `@require_admin` | `components/config/label_registry.py` list 53–67, create 70–98, delete 101–120, update 123–152 |
| Response entry | `_entry_dict` 155–166: `color`, `created_at`, `description`, `icon`, `label_id`, `name`, `modified_at` |
| Colour schema | lines 13–40: `vol.Any(cv.color_hex, vol.In(THEME_COLORS))` |
| Create / update | `helpers/label_registry.py` `async_create` 135–166, `async_update` 168–224 |
| Name index, id generation | `helpers/normalized_name_base_registry.py` |

Facts the design depends on:

1. `config/label_registry/create` takes `name` (required), `color`, `icon`,
   `description` (each optional, nullable). A normalized-name duplicate
   raises `ValueError`, returned as `invalid_info`.
2. `label_id = generate_id_from_name(name)`: `slugify(name)`; if that id is
   taken, `_2`, `_3`, … are tried. The generated id is returned in the
   response and never chosen by the caller.
3. `normalize_name(name) = name.casefold().replace(" ", "")`.
4. `config/label_registry/update` takes `label_id` plus any of the four
   fields. An unknown id raises `KeyError` → `unknown_error`. A rename to a
   normalized name held by another label raises `ValueError`. A case-only
   rename of the same label is allowed. An update that changes nothing
   returns the old entry and fires no event.
5. `cv.icon` accepts any string containing `:`. `cv.color_hex` is
   `^#[0-9A-F]{6}$`, case-insensitive. Theme colours are exactly: `primary`,
   `accent`, `disabled`, `red`, `pink`, `purple`, `deep-purple`, `indigo`,
   `blue`, `light-blue`, `cyan`, `teal`, `green`, `light-green`, `lime`,
   `yellow`, `amber`, `orange`, `deep-orange`, `brown`, `light-grey`, `grey`,
   `dark-grey`, `blue-grey`, `black`, `white`.

## 2. Tool and input

One new governance tool, `create_label_plan`, is a sibling of
`create_entity_metadata_plan`:

```json
{
  "operations": [
    {"operation": "create", "name": "Holiday", "color": "red",
     "icon": "mdi:pine-tree", "description": "Seasonal lighting",
     "entity_ids": ["light.porch"]},
    {"operation": "update", "label_id": "outdoor", "color": "#00AA55",
     "entity_ids": ["switch.fountain"]}
  ],
  "expiration_minutes": 30
}
```

- `operations`: 1–8 items. Every label operation must change something.
  An update with no field that differs from the current entry is refused
  `label_no_change`.
- `entity_ids` is optional per operation. Each entity named is assigned that
  label through the signed 069A `entity_registry_metadata` path
  (`add_labels`).
- Apply, approval, status and rollback use the existing generic plan tools.
  No new apply or approval path exists.

## 3. Validation

| Field | Rule | Refusal |
|---|---|---|
| `name` | `^[A-Za-z0-9](?:[A-Za-z0-9 _-]{0,48}[A-Za-z0-9])?$` (1–50 chars, ASCII, no leading or trailing separator) | `invalid_label_name` |
| `color` | `^#[0-9A-Fa-f]{6}$`, one of the 26 theme tokens, or `null` | `invalid_label_color` |
| `icon` | `^[a-z0-9-]+:[a-z0-9-]+$` (the 069A `ICON_PATTERN`, stricter than `cv.icon`) or `null` | `invalid_label_icon` |
| `description` | 1–255 characters, no control characters, or `null` | `invalid_label_description` |
| `label_id` (update) | `^[a-z0-9][a-z0-9_]{0,63}$` and present in the registry | `invalid_label_id` / `label_not_found` |

Name uniqueness is enforced against the live registry read at planning time,
and again by the simulated namespace of earlier children in the same plan:

- create with a normalized name already present → `label_already_exists`
  (the existing `label_id` is returned so the caller can plan an assignment
  instead);
- rename to a normalized name held by another label → `label_name_in_use`;
- two operations in one plan that target the same label, or produce the
  same normalized name → `duplicate_label_request`.

### 3.1 `label_id` derivation

With the restricted name pattern, Core's `slugify` reduces to lowercasing and
replacing each run of space, `_` or `-` with one `_`. The predicted id is that
exact slug. **The reviewed 069A §6 `label_id_collision` refusal is kept:** if
the exact slug is already a `label_id` (for example, a label renamed away from
its original name), the create is refused rather than predicting a `_2`
suffix. Core therefore generates exactly the predicted id, because:

- the plan binds a namespace fingerprint (§4), so Core's namespace at
  dispatch equals the planned one; and
- the slug is free in that namespace, so `generate_id_from_name` returns it
  on the first try.

If the create response carries a different `label_id`, the outcome is
`provider_completed_unexpectedly`: indeterminate with orphan risk, never
verified.

## 4. Exact targets, baselines and stale state

- Resource type `label_registry_entry`; resource id = the `label_id`
  (predicted for create, exact for update). Provider contract
  `direct-ha-exact-label-registry-v1`.
- Baseline: the update child binds the full current entry (normalized
  `label_id`, `name`, `color`, `icon`, `description`; timestamps excluded) as
  its target fingerprint. The create child binds absence.
- Every label child also binds a **namespace fingerprint**: a hash of the
  sorted `(label_id, normalized_name)` pairs of the whole registry, simulated
  as of just before that child (earlier children in the plan applied).
- Stale-state refusal at dispatch. Children are preflighted one at a time,
  immediately before their own dispatch. Preflight re-lists the registry and
  refuses before any write when:
  - the target entry changed (`stale_target_state`);
  - a create target already exists (`target_already_exists`);
  - the namespace fingerprint changed (`label_namespace_changed`).
- The final registry read (the namespace check) judges the exact target too
  (review1 M3). If the target's normalized entry differs from its bound
  baseline (any of `name`, `color`, `icon`, `description`, or the target was
  deleted), or a create target now exists, the child refuses
  `stale_target_state` and the drifted entry is kept, never overwritten.
- Entity children keep the 069A preflight unchanged. Their reference
  bindings for in-plan labels use the label's FINAL id and name, which is what
  the registry holds once the earlier label children have succeeded.

## 5. Dispatch

Each label child sends at most one write; recovery is read-only and never
redispatches.

- create: `{"type": "config/label_registry/create", "name", "color", "icon",
  "description"}` (all four fields, with explicit `null`s);
- update: `{"type": "config/label_registry/update", "label_id", …}` with only
  the changed fields.

`config/label_registry/delete` is never constructed. Entity children send
the signed 069A `config/entity_registry/update` with the merged `labels` list
only.

Error mapping:

- authorization refused before send → not dispatched (confirmed);
- a Core error result after send → indeterminate (`provider_error_after_send`);
- a transport failure after send → indeterminate.

## 6. Readback and truthful results

After each write, the label registry is listed again and the entry is
compared exactly (normalized fields). The result is:

- **verified** only on an exact match;
- `verification_mismatch` on any difference, including a missing entry or a
  different generated id;
- manual review when the dispatch outcome is unknown.

The plan stops on the first failure. Results report every child: succeeded,
failed, or not attempted. **The plan is not atomic:** a label created by an
earlier child persists when a later child fails, and this is disclosed in the
plan and the result. Every child writes the existing plan, approval, apply and
verification audit records.

## 7. Policy

- Label children are classified `standard_admin`, risk low, physical effect
  none. Labels are registry metadata and have no device effect.
- Entity assignment children keep their 069A classification unchanged. The
  plan's approval class is the highest child class, so an assignment to a
  protected entity remains elevated exactly as in 069A.
- Standard external approval with exact plan-hash binding. No self-approval:
  the planning caller cannot approve, as for every governed plan.
- A multi-operation plan containing entity metadata children is admitted only
  when it is a complete label plan:
  - label children first, then entity children;
  - at most 8 children;
  - every entity child depends on the label children it references;
  - every target is unique.

  Otherwise the plan is refused `label_registry_execution_contract_incomplete`.

## 8. Bounds

- At most 8 children per plan (label children plus merged per-entity
  children).
- Registry reads are bounded by `MAX_REGISTRY_LIST_ITEMS` (2000). A larger
  registry is refused as unbounded.
- Lock graph: each label child takes `label_registry:<label_id>` and the
  exclusive `label_namespace:registry` lock (reason
  `label_namespace_mutation`). This replaces 069A §6's planned
  `label_registry:create`, which could collide with a label whose id is
  `create`. Like every configuration child, each label child also takes the
  shared `reload:label_registry_entry` lock. A label write needs no reload;
  the lock keeps the uniform configuration lock graph (the 069A-1 V6
  precedent). The entity children's lock graph is the unchanged 069A graph.

## 9. Rollback

Rollback of a plan containing a `label_registry_entry` child is refused
`label_plan_rollback_not_supported`. Undoing a create would need deletion
(HAMCP-155). A corrective edit is a new plan.

## 10. Supersessions of HAMCP-069A §6

069A §6 sketched label creation. This design supersedes it as follows:

- `description` is supported (owner decision, 2026-10-23);
- colours are the exact 26 Core theme tokens or hex, not a loose token
  pattern;
- label update and rename are added;
- the namespace lock key is `label_namespace:registry`.

The `label_id_collision` refusal and the exact-slug prediction are retained.

## 11. Core profile and evidence lane

```text
core.label_registry_operation
  class            label_registry_operation
  profile          compiled-core-label-registry-operation-v1
  required checks  websocket_label_registry_list,
                   websocket_label_registry_create_single,
                   websocket_label_registry_update_single,
                   label_registry_readback_exact
  boundary         direct_ha_api
  predispatch      core_identity, capability_contract, target_fingerprint,
                   session_fingerprint, generation
```

The signed fingerprint is reported in the pull request and pinned by tests.
Entity children route to the signed `core.entity_registry_metadata_operation`
profile. A plan becomes executable only when both profiles are admitted.

Evidence lane: the `label_registry` lane in
`scripts/seq13_contract_acceptance.py` (with 069B's `availability_exposure`
lane beside it). It runs against the disposable CI Home Assistant on each
Alarmo-slot window Core, under two CI-only ephemeral authorities:

- the predecessor holds the 22 shipped signed profiles. It must refuse at the
  route and at the service (`pending_signed_registry_admission`, 0 Home
  Assistant requests);
- the extension appends the label profile (authority transition 22 → 23).

It creates a label, edits it, and creates a second label and assigns it to a
run-unique helper in one ordered plan, verifying each readback. It also proves
`label_already_exists`, `label_name_in_use`, invalid colour and icon (0 Home
Assistant commands), and a stale-state refusal that keeps a raw drift made
between plan and apply. Only the lane's own raw client deletes fixtures,
followed by an independent absence readback. The governed paths never send
delete.

The lane is defined, not wired. `ci.yml` sets
`REAL_HA_SEQ13_LANES: dormant` on the `real-ha-contract-tests` job, and the
lane's two steps (stage paths, upload evidence) run only when that value is
`enabled`. The seq-13 batch evidence run flips that one value. No existing
lane changes behaviour.

## 12. Live canary (after seq-13 signing only)

Defined in the release acceptance document:

1. create test label `HAMCP Canary`;
2. edit its colour and description;
3. assign it to one harmless entity;
4. verify each readback.

Removal waits for HAMCP-155, or is done manually by Josh.
