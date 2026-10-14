# HAMCP-179: entity-metadata registry-write container evidence lane

This record defines the CI-only evidence that the dormant
`core.entity_registry_metadata_operation` contract holds on each support-window
Core slot. It is the follow-up named in
[HAMCP-161 §4](../hamcp161/core-registry-hamcp161.md#4-c-entity-metadata-selection-stopped)
and the input to a later, separate owner-signed `extend-capabilities` session
(HAMCP-069A-1).

This lane does not sign anything or change the signed registry or journal. It
does not enable `create_entity_metadata_plan` or change Engineering runtime
source, release metadata or versions. It never contacts live Home Assistant.

## Exact contract under test

The values below are read from current source on every run, not copied.

| Binding | Value at this branch's base |
| --- | --- |
| Capability | `core.entity_registry_metadata_operation` |
| Profile | `core_entity_registry_metadata_operation_v1`, version 1 |
| Adapter | `compiled-core-entity-registry-metadata-operation-v1` |
| Contract fingerprint | `sha256:cc09630efbc0e1e6d0a587696406f6502c62fe730a767bc0a8cfc96a9988f767` |
| Engineering runtime tree (`HEAD:hass_mcp_engineering_beta/ha_mcp_engineering`) | `be28da94a9f9c63dde79aebe7c36a4ecdb65753f` |

The receipt carries the commit and runtime tree it ran from. A runtime change
changes the tree, and the offline verifier then refuses the older receipt.

## Support window and tiers (HAMCP-159 slot-driven)

| Slot | Core | Image index digest | Tier |
| --- | --- | --- | --- |
| current | 2026.10.1 | `sha256:130241f28d01fa80dfa3b44f7d89781c188d8642ecab9024f079f19f9f82baa0` | PR and full |
| rollback | 2026.10.0 | `sha256:1b64d38f38d922bf9d59336451fd6453e1d614f934456af4ee3d2a51061be3a4` | full only |
| candidate | none (`null`) | none | no job |

Core 2026.9.4 is outside the window and receives no lane.

The lane runs inside the existing Real HA job's Alarmo-slot path
(`matrix.alarmo`), after the automation-baseline contract on the same
disposable instance. It is not a new job, matrix or trigger. Bindings come only
from `ci/support-window.yaml` together with `scripts/core_lane_bindings.py`.

- Rotating Cores already in the window edits only the window.
- A Core with no reviewed binding is refused by `scripts/support_window.py`
  before any matrix row advertises coverage.
- The lane's own guard also refuses any Core that is not an exact window slot,
  a wrong lane, a wrong image, or a non-loopback host, before any client is
  built.

## Scenario matrix

The lane uses two ephemeral Ed25519-signed CI registries:

- a predecessor with the current 21 references; and
- an extension with this reference appended (22).

The production signed registry is never used as authority.

| Scenario | Proof retained in the receipt |
| --- | --- |
| Predecessor admission | Capability not acquired (`authority_unavailable`) with 0 HA requests and 0 updates. A control capability on the same runtime is acquired, so the refusal is specific. |
| `closed_field_update` | Planner → approve → apply. Writes all 7 managed fields (name, icon, area, labels, category, aliases including `null`, hidden_by). Exact readback, 1 dispatch, 1 update, unmanaged registry fields preserved, `succeeded_verified`. |
| `restore` | Governed return to the preflight value with exact readback. |
| `no_change` | Status `no_change`, no plan, no dispatch, 0 updates. |
| `not_found` | `entity_not_found`, no plan, no dispatch, 0 updates (raw `get` also returns `not_found`). |
| `area_requires_own_name` | On kitchen_sink `switch.hub_switch` (device-bound, no own name), the planner refuses area-without-name and clear-name-with-area. Raw Core returns `invalid_info` for both and accepts name+area. The entity is restored exactly. |
| `unknown_label` | The planner refuses (`entity_metadata_reference_not_found`) with 0 updates. A raw write of `[known, unknown]` reads back `[known]`: Core silently drops it, which is why planner pre-validation is required. |
| `alias_normalization` | The planner refuses (`entity_metadata_invalid_value`) with 0 commands. Raw `[" Lamp ", "", null]` reads back `["Lamp", null]`. |
| `response_lost_applied` | The response is lost after send. Readback reconciles the task to `succeeded_verified` with 1 dispatch. Replay reuses the result with 0 further updates. |
| `response_lost_unapplied` | The request is lost before send. Readback is unchanged: `failed_post_dispatch` / `verification_mismatch`. Replay is refused (`duplicate_apply_attempt`) with 0 updates, so it is never redispatched. |
| No label creation | Engineering command types are a subset of `{entity_registry get/update, label/area/category list}`. `config/label_registry/create` is refused by the verifier. |

The predecessor/extension F3 runtime health must end with zero issued leases,
active commits and fallbacks.

Every synthetic area, label, category and `input_boolean` is created and deleted
within the run, and their absence is verified (`fixture_cleanup: true`). The
helper name is made unique per run because Core restores a deleted entity's
registry metadata when the same `unique_id` returns.

## Receipt format and artifacts

`scripts/entity_metadata_contract_acceptance.py` writes the receipt as
`json.dumps(sort_keys=True, indent=2) + "\n"`. It records:

- `core`, `support_slot`, `core_image` and `image_index_digest`;
- `image_receipt_sha256`, the hash of the job's verified image receipt;
- `engineering_source` (`commit`, `runtime_tree`);
- the five reference keys plus `added_capability`;
- `predecessor_capabilities_sha256`, `authority_transition: [21, 22]` and `production_authority: false`;
- `predecessor_admission` and `engineering_command_types`;
- `synthetic_labels`, the ordered `scenarios`, and `fixture_cleanup`.

The same receipt is also nested as `entity_metadata` in the existing required
Alarmo result.

`alarmo_inspection_contract_acceptance.py --verify-cleanup` re-verifies the
nested receipt offline against the window and current source. It requires the
standalone artifact to be byte-identical, then records
`required_entity_metadata.receipt_sha256` in the cleanup receipt.

Each slot uploads the artifact
`entity-metadata-core-<version>-<slot>-amd64`, retained for 14 days. It contains
`entity-metadata-<lane>.json` and `alarmo-cleanup-<lane>.json`, and the upload
step fails if either file is missing.

## Use by the signing session (HAMCP-069A-1)

The prep tool's `core-capability-extension-evidence-v1`
([CORE_RELEASE_REGISTRY.md §Explicit capability extension](../../CORE_RELEASE_REGISTRY.md#explicit-capability-extension))
accepts only closed fields. Free text is not accepted, so receipts bind only by
digest in `review_artifacts_sha256` (1–32 distinct lowercase hex SHA-256 values).

For each Core version being extended, the session should:

1. Take the receipts from a CI run on `main` whose head matches the reviewed
   runtime tree:
   - 2026.10.1 from the PR or full tier;
   - 2026.10.0 from the full-tier dispatch Josh runs after merge.
2. Run `verify_result` (or `--verify-cleanup`) offline at that head.
3. Put into that version's `review_artifacts_sha256`:
   - the SHA-256 of the exact `entity-metadata-<lane>.json` bytes; and
   - the `alarmo-cleanup-<lane>.json` bytes, alongside other review artifacts.
4. Use `added_capabilities = [<the five reference keys above>]`, appended after
   the existing ordered prefix.

The prep tool needs no change. Precedent:
[core-automation-baseline-registry-sequence5.md](../core-automation-baseline-registry-sequence5.md).

## Disclosed limitations

- **Synthetic provider identity.** A plain Core container has no Supervisor, so
  F3 task declarations bind a fixed synthetic provider identity
  (`provider_identity: synthetic_disposable_lane`). This proves the registry
  contract, not installed Supervisor lineage.
- **kitchen_sink coupling.** The area/name rule needs a device-bound entity
  without its own name, so the lane uses kitchen_sink `switch.hub_switch`. The
  Real HA Start step loads kitchen_sink for the 2026.9/2026.10 lanes. A future
  Core that changes that entity fails the lane's preconditions, which is
  reported and never adjusted.
- **Raw controls.** The production WS client discards Home Assistant error codes.
  The raw Core-behavior controls therefore use a lane-owned loopback client that
  is never part of Engineering dispatch and is excluded from
  `engineering_command_types`.
- **Offline tests are not execution.** `tests/test_entity_metadata_disposable.py`
  verifies synthetic receipts and verifier mutations only. Execution evidence is
  the CI artifact.

## Local scratch run (not evidence for signing)

A local disposable container of Core 2026.10.0 at the exact rollback digest
(loopback only, no live HA) ran the lane:

- all 9 scenarios passed;
- the lane took about 5 s;
- the scratch receipt SHA-256 was
  `cfc089d22df0af9b9bdb4ba9f2739bc9d7a01ec42bdbee4466f44fc4dbff80e3`.

The container and its disposable token were removed. Core 2026.10.1 was not run
locally; CI runs it.
