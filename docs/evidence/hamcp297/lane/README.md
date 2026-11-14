# HAMCP-297 G5: device configuration entity source binding

This directory binds `core.device_configuration_entity_operation` to the exact
Core source it relies on. It closes no gate by itself, creates no seq-18
candidate and signs nothing.

## What is bound

- `ci/core-contract-read-files.json` profile
  `core.device_configuration_entity_operation`: the 42 ruled RELIED Core files
  (HAMCP-297 G5 Part 1 classification, owner ruling 2026-11-13). Eight of them
  were new to the manifest, which grew from 527 to 535 paths.
- `core-device-configuration-source-hashes.json`: the SHA-256 of each of those
  42 files in the exact Core 2026.11.0 archive (source commit
  `74cf5a49c63ed8d4d87717844bf14f79c28c9cea`, archive SHA-256
  `85f3c42c9f77aa45e01faff4471007beadf5cb50c4324f496a239139eec70ff1`).
- `docs/evidence/hamcp315/core-sources/2026.10.3.json` and `2026.11.0.json`
  gained the new paths present at each tag; `manifest-compatibility.json`
  records `homeassistant/components/switch/entity.py` as absent at 2026.10.3.

`scripts/core_device_configuration_local.py` reads its own profile entry and
the committed record before the journal is parsed. It fails closed with
`device_configuration_manifest_missing`,
`device_configuration_source_provenance_mismatch`,
`device_configuration_manifest_mismatch` or
`device_configuration_source_hash_mismatch`. Its receipt records
`manifest_sha256`, `source_hashes_sha256` and `profile_paths`.

## Regeneration

Every record is generator output; none is hand-written. From the repository
root, with the two exact archives staged under `.artifacts/inputs/`:

```
python -I docs/evidence/hamcp297/lane/generate_source_records.py \
  --archive 2026.10.3=.artifacts/inputs/core-2026.10.3.tar.gz \
  --archive 2026.11.0=.artifacts/inputs/core-2026.11.0.tar.gz --check
```

The generator reuses `docs/evidence/hamcp290/lane/generate_source_records.py`
unchanged and adds only this directory's record. `--check` reproduces every
committed record byte for byte.

The committed lane receipt `docs/evidence/hamcp366/device_configuration_entity.json`
is the R8 re-run on the signing commit
`d84d8d9e69c4fff8481b018182a6b43f088aac7d` (architect ruling 2026-11-13, about
22:15 CST): PASS, cleanup clean, manifest
`073fa5ad6adc954940a5678d5b064795d2a45b2728e76998fc4b79309e8cd9a1`,
`source_hashes_sha256`
`0cb62ae83f6ab5c9913e908ebfcd1e022973f0186f8a943793a07eab1278d4f7`, 42
`profile_paths`. Receipt SHA-256
`710a6aa172ded0dd168eef8b9784456081db0f3f888985ba17a742bc1071f5dc`.
