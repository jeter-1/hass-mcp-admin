# Engineering 2.4.1-beta.31 release notes

Engineering 2.4.1-beta.31 ships the governed entity enable/disable and voice
assistant exposure family (HAMCP-069B) **inactive**. This release activates
nothing new on a deployed server.

- New tool `create_entity_availability_exposure_plan` proposes one operation
  (`disable`, `enable`, `expose` or `unexpose`) over 1-8 exact registered
  entities.
- Its two resource types, `entity_registry_availability` and
  `entity_assistant_exposure`, need two new Core capability profiles. The
  signed registry (sequence 12) does not reference them, so every call is
  refused with `CoreCapabilityUnavailable` and reason code
  `pending_signed_registry_admission`. No plan is stored and nothing is
  dispatched (`plan_created: false`, `provider_dispatch_occurred: false`).
- `list_capabilities` and server health show both resource types as
  `pending_signed_registry_admission`.

Activation needs a separate, reviewed, signed registry sequence 13 that
references the two profiles (owner decision OD-2c (i)). The live canaries in
the acceptance document wait for that sequence.

Design and owner decisions:
[`HAMCP_069B_ENABLE_EXPOSE_DESIGN.md`](HAMCP_069B_ENABLE_EXPOSE_DESIGN.md).
Acceptance: [`V2_4_1_BETA31_ACCEPTANCE.md`](V2_4_1_BETA31_ACCEPTANCE.md).

beta.31 follows 2.4.1-beta.30 (HAMCP-131) and includes it. It adds no
dependency change; both hashed locks are beta.30's.

The catalog grows from 92 to **93** tools (94 with the optional YAML reader
admitted): 65 static plus 28 delegated. Nothing else changes: no existing tool,
input schema, description, signed registry entry or fingerprint (the 069A
entity-metadata profile is byte-identical), HOLD, support window, workflow,
add-on permission or stable-v1 file.

## What the family does once admitted

Inputs:
- `operation`: `disable`, `enable`, `expose` or `unexpose`;
- `entity_ids`: 1-8 exact registered entity ids (OD-7 as amended: the shared
  8-operation bound);
- `assistant`: required for `expose` and `unexpose`, refused otherwise. It is
  `conversation` (Assist) or `cloud.google_assistant`. Alexa is refused
  (OD-3);
- `expiration_minutes`: 5-1440, default 120.

There is no free-text field.

Each entity is one child operation with exactly one closed Home Assistant
write:

| Resource type | Write | Readback |
| --- | --- | --- |
| `entity_registry_availability` | `config/entity_registry/update` with exactly `entity_id` and `disabled_by` (`"user"` or `null`) | `config/entity_registry/get`, `disabled_by` |
| `entity_assistant_exposure` | `homeassistant/expose_entity` with one assistant, one entity and `should_expose` | `config/entity_registry/get`, `options[assistant].should_expose` |

The exposure write returns an empty result, so only the readback decides.

Reads are limited to these commands:
- `config/entity_registry/get`;
- `config/entity_registry/list`;
- `config_entries/get_single`;
- `homeassistant/expose_new_entities/get`.

The gateway never sends:
- `cloud/status`;
- `homeassistant/expose_entity/list`;
- `homeassistant/expose_new_entities/set`;
- either cloud entity-update command.

Refused before any plan is stored:
- unregistered (state-only) entities;
- devices and config entries;
- Core-internal disables;
- already-applied states;
- an `enable` plan that spans more than one config entry.

Approval:
- Protected entities need the existing elevated acknowledgement, with a
  plain-language consequence statement (OD-4, OD-5, OD-9). Protected means the
  existing protected-object floor, protected cover classes, unresolved cover
  classes, sirens, safety binary sensors and entities that feed
  safety-critical automations.
- `unexpose` is always standard.
- External administrator approval and `apply_change_plan` are required.

Execution:
- Children run in order, non-atomically, and stop on the first failure. A
  partial plan ends `manual_review_required`.
- A changed registry entry is refused as stale before the write.
- A readback that differs from the approved entry fails verification.
- One side effect is tolerated: Core's Alexa handler may add a non-target
  `cloud.alexa` options key that was absent. It is reported with observation
  code `tolerated_new_exposure_key_cloud_alexa`.
- Rollback is an inverse plan (OD-8).

## Security

- The new write paths cannot be reached on a deployed server until a signed
  sequence 13 admits their profiles. A tampered profile fingerprint keeps the
  profile withheld.
- The gateway accepts only its closed command vocabulary. A static check
  proves `cloud/status` is never constructed.
- The tool has no delegated provider and no direct fallback.

## Rollback

Reinstall the 2.4.1-beta.30 artifact. No stored format changes: while the
family is unadmitted it stores no plan or child record of its types.
