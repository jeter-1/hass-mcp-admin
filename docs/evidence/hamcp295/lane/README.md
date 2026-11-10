# HAMCP-295 entity rename lane evidence

Local, disposable Home Assistant Core 2026.11.0 evidence for the dormant
profile `core_entity_registry_rename_operation_v1` v1. The adapter is
`compiled-core-entity-registry-rename-operation-v1`, and the contract
fingerprint is
`sha256:0c0a0e68e3628bcff3517d6106f9527609643789df503788af856b42e9e11fb3`.
Nothing here is production authority (`production_authority: false`), and no
sequence signs this profile yet.

## Files

- `core-entity-rename-source-hashes.json`: SHA-256 of the 8 Core files in the
  `core.entity_registry_rename_operation` manifest profile, taken from the
  Core 2026.11.0 source archive (commit
  `74cf5a49c63ed8d4d87717844bf14f79c28c9cea`, archive SHA-256
  `85f3c42c…70ff1`, the reviewed `core_2026_11_0_lane_provenance.json`
  inputs). The file was generated with an inline `python -I` read of the tar
  members; no archive code was executed. Each hash equals the HAMCP-315 O3
  record `docs/evidence/hamcp315/core-sources/2026.11.0.json`.
  `tests/test_core_entity_rename_local.py` asserts both equalities.
- `entity_registry_rename.json`: the receipt of one passing run of
  `scripts/core_entity_rename_local.py --mode local --pull never` at source
  commit `6dcd5b65`. It ran on 2026-11-09 23:32 CST, admitted through the
  host heavy-job gate. The run used:
  - a Core container pinned by digest, with `--memory 2g`;
  - a 127.0.0.1-only bind on port 18125;
  - file-backed automation and script stores.

  The run removed every container, network and configuration directory it
  created.

## What the receipt shows

- The rename goes through the single command `config/entity_registry/update`
  with `new_entity_id`. There is no fallback (`fallback_count: 0`), and Core
  itself does not rewrite dependants (`core_rewrote_dependents: false`).
- Refusals are recorded for:
  - a stale pre-image;
  - an occupied destination, at planning and from Core;
  - a cross-domain rename;
  - a closed-payload violation;
  - an untargeted lease.
- Reference rewrites:
  - the trigger and the action target were rewritten;
  - a template occurrence was not rewritten and was counted (plan count 1).
- Forced dependent failure: the rename was compensated and verified
  (`failed_compensated_verified`). There was one compensation child, and no
  child ran again on re-entry.

## Limitations

- The lane is local only. CI refuses it (`local_only_closed_lane`), and it is
  not part of the Real HA workflow job.
- The forward child classes come from verified Core outcomes in the harness,
  not from persisted F3 records. Record classification is unit-tested in
  `tests/test_hamcp109_dependency_bound_execution.py`.
- State consequences (recorder history migration, helper config-entry follow,
  re-add versus in-place move) are observed, not verified.
- The bridge network allows egress, but the lane does not require it.
