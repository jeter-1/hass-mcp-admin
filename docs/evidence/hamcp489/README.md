# HAMCP-489: Core 2026.11.3 binding review

This is preparer evidence for a draft binding PR. It adds Core 2026.11.3 to the
reviewed lane bindings and to the Core candidate slot of the support window, so
CI boots it. It is not a signed admission, a registry candidate, CI attestation,
or approval to upgrade a live system. Signing and the house update are separate
owner steps.

## Exact source and image

The [provenance fixture](../../../tests/fixtures/core_2026_11_3_lane_provenance.json)
binds stable tag `2026.11.3` to commit
`92da9f154079459b9e547126f9bd9fce726de16a`, tree
`ad03c97cdf4d68df1a71a030dfd4294b5e85b5a4`, and source archive SHA-256
`907b4bcabd8eec3a51ffee8ec1352a34f9482373aa86b890eb7be0c6e627f504`.
The [source record](../hamcp315/core-sources/2026.11.3.json) holds the hash of
every manifest path in that archive.

- OCI index: `sha256:24e541ddae5818bf8eb27898b9ba00d0c344a655aa5097e9e9bd4b19356be847`.
- linux/amd64 manifest: `sha256:fbbd521bd989893b6c350877d17e1eb1126b6a1bfb715c28e6945f1c82ae0093`.
- linux/amd64 config: `sha256:313029fcb3acece69f9f3294eb1344c8e1ade8ab93d3b42b03e368994da1b587` (label `io.hass.version` `2026.11.3`).
- linux/arm64 manifest: `sha256:ee500645d61b5483d0b05eebd4f2de22c390618ec178df0e1114a77d40d6d650` (provenance only; not executed).

## O3 comparison against 2026.11.0

The predecessor is Core 2026.11.0, commit
`74cf5a49c63ed8d4d87717844bf14f79c28c9cea`. Strict full-file O3 comparison of all
535 paths in `ci/core-contract-read-files.json` found:

- 7 modified files;
- 527 byte-identical files;
- `homeassistant/helpers/condition.py` absent in both releases, which O3 counts
  as equal (see the known gap below).

[Source comparison](source-comparison.json) records every hash;
[source judgments](source-judgments.json) assess the 7 differences from pinned
source. The device registry, entity registry, `http/config.py`, `storage.py`
and `input_text/__init__.py` are byte-identical, so the binding keeps the
2026.11.0 semantics: child-device name parts, typed operations, child consumer
ha-mcp 9.2.0 and HTTP store major version 3.
`ci/core-contract-read-files.json` is unchanged; no narrowing is proposed.

Cross-checks:

- The comparison's file rows equal those in the refusal artifact of
  Actions run 45271698629 ("Core evidence schedule", 2026-11-23), which refused
  2026.11.3 with outcome `binding PR required`.
- GitHub's compare of the two commits lists 259 changed files; their overlap
  with the manifest is exactly the same 7 files.
- None of the 32 supplemental files in the HAMCP-280
  [moved-source receipts](../hamcp280/moved-source-receipts.json), which hold the
  moved condition implementation, changed between 2026.11.0 and 2026.11.3.

### Judgment summary

| File | Profiles | Change | Engineering effect |
|---|---|---|---|
| `components/auth/indieauth.py` | automation_baseline_metadata_read | client_id fetch session capped at the header size (#193440) | none |
| `components/input_text/entity.py` | typed_helper_value_operation | `set_value` drops a value that fails `pattern` (`re.match`), warns and returns (#192100) | none in code: Engineering refuses on `fullmatch`, which is stricter; [docs note](../../TYPED_HELPER_CONTROL.md) added |
| `components/marketplace/base.py` | marketplace_read | cache file mode; quieter rate-limit log (#191093, #193439) | none |
| `components/marketplace/repositories/base.py` | marketplace_read | README cache cleared on install/uninstall; fuller restore after a failed refresh (#191129, #193438) | none: Engineering reads none of those fields |
| `const.py` | 4 profiles | `PATCH_VERSION` 0 -> 3 | none |
| `core_config.py` | security_metadata_read | time zone set first in `_async_update` (#193306) | none: write-path ordering |
| `helpers/config_validation.py` | configuration_validation, governed_configuration_operation, typed_helper_value_operation | config-entry-only YAML warning names keys only | none; a protected comment citation goes stale (below) |

The `configuration_definitions/schemas.py:20` comment cites
`config_validation.py:1257-1263`. In 2026.11.3 that text sits at lines
1270-1276, unchanged. The comment is protected runtime and is not edited here.

## Frontend 20261028.4 -> 20261028.8

Core's frontend requirement moves from `home-assistant-frontend==20261028.4`
(frontend commit `a67b691a`) to `20261028.8` (`1ea5f1ab`). This is outside the
O3 manifest. `f3_dashboard/risk.py` mirrors frontend `a67b691a`; three files it
relies on changed:

- `panels/lovelace/common/handle-action.ts`: `navigate` first fires a cancelable
  `hass-navigate` event and navigates unless an embedding page cancels it.
  Still no service call; the risk classification of navigate is unchanged.
- `data/area/area_controls.ts` and
  `panels/lovelace/card-features/hui-area-controls-card-feature.ts`: area
  membership moved out of the entity filter into a new `getAreaEntityIds`,
  which `getAreaControlEntities` now receives.
  - Before: the filter matched when `areas[entry.area_id || device area].area_id`
    equals the card's area.
  - After: it matches when `entry.area_id || device area` equals it
    (`getEntityAreaId`). Card exclusions, `entity_category: "none"`, the
    per-control filters, and the state lookup still apply.
  - The two differ only for an area ID missing from the area registry.
  - `AREA_CONTROL_DOMAINS`, `AREA_CONTROLS_BUTTONS`,
    `controls || DEFAULT_AREA_CONTROLS` and `_normalizeControl` are unchanged.
    `entity_filter.ts` and `get_entity_context.ts` are byte-identical.
  - risk.py classifies an area-controls feature by its control domains and
    binds the card's area into the context; it does not compute area
    membership. Its result is unchanged.

## CI evidence

The support window's Core candidate slot is 2026.11.3, so the PR tier runs the
real-HA contract lane, Alarmo and the automation baseline on the exact
2026.11.3 image beside current 2026.11.0. No local lane receipts are added.

## Publication after merge

The automated Core evidence workflow always compares against the newest signed
predecessor (2026.11.0) and runs O3, so after merge a dispatch for 2026.11.3
still refuses with `binding PR required`. Publication of 2026.11.3 therefore
uses the [manual flow](../../CORE_RELEASE_REGISTRY.md#manual-flow-for-each-publication)
and is a separate owner step.

## Known gap (HAMCP-367)

The manifest still lists `homeassistant/helpers/condition.py`, which Core
removed in 2026.11.0. Being absent in both releases makes it equal under O3,
so the condition implementation is witnessed by content only through the
HAMCP-280 moved-source receipts (unchanged here). Re-pointing the manifest is
HAMCP-367 and is outside this change.
