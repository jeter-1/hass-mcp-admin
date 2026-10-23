# Engineering 2.4.1-beta.33 release notes

Engineering 2.4.1-beta.33 ships governed label create, edit and entity
assignment (HAMCP-165) **inactive**, and corrects the read vocabulary of the
two dormant HAMCP-069B profiles. This release activates nothing new on a
deployed server. Both switch on with the sequence-13 registry signing
(seq-13 batch = HAMCP-069B + HAMCP-165 + HAMCP-155).

beta.33 follows 2.4.1-beta.32 (HAMCP-220) and includes it. It adds no
dependency change; both hashed locks are beta.32's.

- New tool `create_label_plan` proposes 1-8 ordered label operations:
  `create` or `update` of a label's `name`, `color`, `icon` and
  `description`, each optionally assigning the label to exact registered
  entities.
- Its resource type, `label_registry_entry`, needs the new Core capability
  profile `core.label_registry_operation`. The signed registry (sequence 12)
  does not reference it, so every call is refused with
  `CoreCapabilityUnavailable` and reason code
  `pending_signed_registry_admission`. No plan is stored and nothing is
  dispatched (`plan_created: false`, `provider_dispatch_occurred: false`).
  The governance service enforces the same check itself, first, before any
  Home Assistant read, so no entry point bypasses it.
- `list_capabilities` and server health show `label_registry_entry` as
  `pending_signed_registry_admission`.
- **HAMCP-069B profile fix.** The two unsigned 069B profiles
  (`core.entity_registry_availability_operation` and
  `core.entity_assistant_exposure_operation`) now name
  `websocket_entity_registry_list` and `websocket_expose_new_entities_get`,
  the reads their gateway actually sends (HAMCP-222 OD-5). Their
  fingerprints change; both profiles are still unsigned and dormant, so no
  behavior changes.

New fingerprints (pinned by tests):

| Profile | Fingerprint |
| --- | --- |
| `core.label_registry_operation` (new) | `sha256:b2f0dd11ff243aa2559c27738d962785e37455d37b836cb5a6997aeabb26813c` |
| 069B availability (corrected) | `sha256:ae292fbc16ad82c8d8347b7e2eb1c3a7e8c7644d95783f5bf2ae9f9cc63a76b2` |
| 069B exposure (corrected) | `sha256:474c8ccaaae83c12f56a2848995ff089097936568a2ad5dc8d25ce855d51dbbd` |

The signed 069A `core.entity_registry_metadata_operation` profile is
byte-identical.

Activation needs the separate, reviewed, owner-signed registry sequence 13
that references these profiles. The live canaries in the acceptance document
wait for that sequence.

Design and owner decisions:
[`HAMCP_165_LABELS_DESIGN.md`](HAMCP_165_LABELS_DESIGN.md).
Acceptance: [`V2_4_1_BETA33_ACCEPTANCE.md`](V2_4_1_BETA33_ACCEPTANCE.md).

The catalog grows from 93 to **94** tools (95 with the optional YAML reader
admitted): 66 static plus 28 delegated. Nothing else changes: no existing
tool, input schema, description, signed registry entry or signed profile
fingerprint, HOLD, support window, add-on permission or stable-v1 file.

## What the family does once admitted

Inputs (`operations`, 1-8 items, plus `expiration_minutes`, 5-1440, default
120):

| Field | Rule |
| --- | --- |
| `operation` | `create` or `update` |
| `label_id` | `update` only: an existing id, `^[a-z0-9][a-z0-9_]{0,63}$` |
| `name` | required for `create`; 1-50 ASCII characters, letters, digits, space, `_`, `-`, no leading or trailing separator |
| `color` | `#RRGGBB`, one of Core's 26 theme colours, or `null` |
| `icon` | `prefix:name` (lowercase letters, digits, `-`), or `null` |
| `description` | 1-255 characters without control characters, or `null` |
| `entity_ids` | optional exact registered entities to assign the label to |

Refused before any plan is stored:
- a duplicate normalized name (`label_already_exists`, with the holder's
  `label_id`) or a rename onto another label's name (`label_name_in_use`);
- a predicted `label_id` already taken (`label_id_collision`; Core's `_2`
  suffix is never predicted);
- two operations on the same label or producing the same name
  (`duplicate_label_request`);
- an unknown label (`label_not_found`) or an update that changes nothing
  (`label_no_change`);
- an invalid name, colour, icon or description, before any Home Assistant
  read;
- more than 8 children.

Each label child sends exactly one closed write:
- `config/label_registry/create` with `name`, `color`, `icon` and
  `description`;
- `config/label_registry/update` with `label_id` and only the changed
  fields.

The readback is `config/label_registry/list`, compared exactly.
`config/label_registry/delete` is never constructed (deletion is HAMCP-155).
Entity assignment children use the signed 069A
`config/entity_registry/update` path unchanged.

Approval and execution:
- Label children are standard (registry metadata, no device effect). An
  assignment keeps its 069A classification, so a protected entity still needs
  elevated approval.
- External administrator approval with exact plan-hash binding and
  `apply_change_plan` are required. The planning caller cannot approve.
- Children run in order, non-atomically, and stop on the first failure. A
  label created by an earlier child persists when a later child fails; the
  plan and the result say so.
- Immediately before each write, a changed entry (`stale_target_state`), a
  create target that now exists (`target_already_exists`) or any change to
  the label namespace (`label_namespace_changed`) is refused.
- A readback that differs, including a different generated id, is never
  verified.
- Rollback is refused (`label_plan_rollback_not_supported`); a corrective
  edit is a new plan.

## Security

- The new write paths cannot be reached on a deployed server until a signed
  sequence 13 admits the profile. A tampered profile fingerprint keeps the
  profile withheld.
- The gateway accepts only its closed command vocabulary; delete is refused
  before any I/O.
- The tool has no delegated provider and no direct fallback.
- `ci.yml` defines the seq-13 evidence lanes for 165 and 069B but keeps them
  dormant (`REAL_HA_SEQ13_LANES: dormant`). No existing CI step changes.

## Rollback

Reinstall the 2.4.1-beta.32 artifact. No stored format changes: while the
family is unadmitted it stores no plan or child record of its type.
