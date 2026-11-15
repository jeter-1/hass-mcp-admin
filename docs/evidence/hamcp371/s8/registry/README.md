# HAMCP-371 S8: the 9.2.0 registry row, policy and admission core

This directory holds the inputs and the commands that produce the exact
ha-mcp 9.2.0 (`51e3aac7`) registry entry `ha-mcp-v9.2.0-9de5bf6c`, its
policy `upstream_tool_policy_9_2_0.json` and its contract review
`docs/evidence/upstream-read-compatibility/ha-mcp-9.2.0-contract-review.json`.
The admission tests are `tests/test_hamcp371_admission_core.py` and the N29
YAML replay is `tests/test_hamcp371_real_core_replay.py` (`YamlReadN29`).

Local candidate only. Nothing here was signed, published, pushed or deployed,
and no Home Assistant instance was accessed.

## Files

| File | What it is |
| --- | --- |
| `build_candidate_inputs.py` | Builds the three `candidate` inputs, the contract review, and the pass-2 (`finalize`) policy and entry. Each command takes `--check`. |
| `run_candidate.sh` | The exact pass-1 `candidate` command. |
| `base-policy-9.2.0.json` | Pass-1 base: the reviewed 9.1.2 policy with the tokens renamed and the four wrapper tokens withheld. |
| `strict-tools-response-9.2.0.json` | The S4 strict `tools/list` response of the exact standalone image. |
| `review-decisions-9.2.0.json` | One reason per tool, each built from the S4 descriptor diff. |
| `source-files-changed-9.1.2-to-9.2.0.txt` | Upstream source files changed between the tags (GIT_NO_LAZY_FETCH=1). |

## Reproduce

From the repository root:

```
python -I docs/evidence/hamcp371/s8/registry/build_candidate_inputs.py inputs --check
bash docs/evidence/hamcp371/s8/registry/run_candidate.sh PYTHON OUT/pass1
python -I docs/evidence/hamcp371/s8/registry/build_candidate_inputs.py finalize --pass1 OUT/pass1 --workdir OUT/final --check
python -I docs/evidence/hamcp371/s8/registry/build_candidate_inputs.py contract-review --registry hass_mcp_engineering_beta/ha_mcp_engineering/upstream_release_registry.json --check
```

On 2026-11-15 each command reported "up to date". A fresh pass 1 was
byte-identical to the original one: both the policy and the entry.
`scripts/review_upstream_read_release.py validate` passes.

## Method and disclosures

1. **Token rename (Engineering binding decision).** The 9.1.2 adapter tokens
   `ha-mcp-9.1.2-*` become `ha-mcp-9.2.0-*`, 27 in all. Each 9.2.0 token is a
   separate compiled binding: `upstream_reads_9_2.py` for the 23 reads, and
   the four compiled wrappers (blueprint, apps, integrations, health). Each is
   bound to the 9.2.0 source commit and the 9.2.0 wire fingerprints. A 9.1
   token never satisfies a 9.2 adapter, and the reverse holds too (tested).
2. **Two passes, both refusals recorded.** `candidate` writes each tool's
   annotations from the capture, and it builds the runtime fingerprint maps
   over `automatic_read` rows only. The loader, however, requires the maps
   over every read route, including the compiled wrappers. With the wrapper
   tokens present, the loader refused pass 1: first with
   `policy_uncompiled_blueprint_adapter`, then with
   `policy_runtime_description_fingerprints_invalid` (the second attempt's
   output is kept under `.artifacts`). This is the HAMCP-319 method: pass 1
   withholds the four wrapper tokens, and `finalize` restores them along with
   their fingerprints, which are computed from the 9.2.0 capture.
3. **Parity carry: flagged for the architect.** In pass 2, each tool's
   published `description` and the blueprint wrapper's public
   `reviewed_annotations` are taken from the reviewed 9.1.2 policy. They are
   Engineering's 8.5-parity public surface, not upstream metadata, which
   follows the 9.1.0 and 9.1.2 method. Every 9.2.0 descriptor assertion stays
   as `candidate` wrote it from the 9.2.0 capture: the input fingerprint, the
   runtime description, the annotation and output-schema fingerprints, the
   classification and the reason. `finalize` stops if any non-blueprint
   annotation differs from 9.1.2's, and none does (S4 diff). This is a carry
   of a public field, not a 9.2.0 descriptor value copied by analogy. It is
   flagged here so the architect can confirm that reading.
4. **Dispositions.** The dispositions are 9.1.2's, per the S8 ruling.
   Seventeen descriptors changed from 9.1.2 (S4 diff) and 60 are
   byte-identical, the dashboard getter among them. `ha_get_history` refuses
   before dispatch until the S9 module exists. `ha_get_operation_status` stays
   held.
5. **Dashboard quarantined (07:40/08:05 ruling).** The 9.2.0 dashboard
   disposition is `admitted`, and its attestation is `quarantined` with null
   fields. Tests assert that the 9.2.0 dashboard provider refuses
   (`upstream_version_mismatch`) and that 9.1.2 stays reviewed. SB replaces
   this with the attestation from the capture. S13 checks that the row and
   the attestation equal 9.1.2's before SR.
6. **Release page and provenance.** The v9.2.0 release page listed no assets
   (read 2026-11-15). Only the official OCI images are admitted: the
   standalone index `9de5bf6c…` (amd64 `349a3ec6…`, arm64 `5799ff6f…`) and
   the App images (amd64 manifest `928c55d6…9244`, arm64 manifest
   `de36201e…`). Per Q7-b, the arm64 App image is pinned by digest only, and
   only amd64 was run.
7. **`--artifact-evidence` is not usable for 9.x.** `candidate`'s artifact
   evidence check requires the 8.1.0 misversioned release-asset set, so
   pass 1 omits the flag and `finalize` binds the 9.2.0 contract review
   instead.
8. **Strict-full evidence.** The amd64 App image produced byte-identical
   catalogues to the standalone image in both filesystem profiles: 77 tools
   off (profiles-1) and 82 tools on (profiles-2). The strict full-contract
   and raw catalogue fingerprints are checked against both the release and
   the strict response.
9. **YAML capture binding (N29).** `upstream_yaml_reader._CAPTURES["9.2.0"]`
   binds the profiles-2 standalone filesystem-on capture (`8a4d3770…`). The
   9.2.0 read contract is the 9.1 contract object. SY records that
   `tools_yaml_read.py` is byte-identical at `51e3aac7` and that the imported
   filesystem functions are unchanged by AST hash.
10. **`ha_write_file` descriptor drift.** Three filesystem siblings are
    byte-identical to 9.1.2. The `ha_write_file` descriptor changed (runtime
    binding `bff12e49…`). All four stay prohibited and never route; the
    binding only accounts for them and reports drift.
11. **Error contract (N31, 06:40 ruling).** The 9.2.0 adapter is
    `ha_mcp_error_contract_9_2_0_v1`, with upstream versions `{"9.2.0"}` and
    evidence class `real_core_capture`. Its probes equal 9.1.2's. The note
    that `missing_automation`'s shape varies with Core content (run
    `25357453`) is in `../README.md`.
12. **Gateway helper.** `upstream_read_gateway._reads(entry)` returns
    `upstream_reads_9_2` for an entry that satisfies the 9.2 adapter and
    `upstream_reads_9_1` otherwise. The nine existing call sites go through
    it. An entry that names both adapters raises
    `read_adapter_release_ambiguous`. The "refusal for anything else" is
    built from three layers:
    - The policy loader refuses crossed or stale tokens and the 9.1.2
      fingerprint under a 9.2 token (`policy_uncompiled_read_adapter`).
    - The helper raises on ambiguity.
    - Non-adapter entries pass through unchanged, as they did before S8.
13. **S8 interim projections.** The 9.2.0 reads use the compiled 9.1
    projections until S10 closes the 9.2.0 key sets. `RealCoreReplay920`
    therefore fails by design until S10: an injected unlisted key is not
    withheld (`KeyError 'fields_withheld'`). The groups-projection warning
    text still says "ha-mcp 9.1.0", which S10 addresses.
14. **Q1 switch outcomes (designed behaviour; amended Q1).**
    - (a) Both switches off: `admitted_exact`, reviewed version 9.2.0,
      registry status `compiled_reviewed_release_registry`. This is the
      positive path, and it passes.
    - (b) `ha_mcp_release_registry_enabled=True` with no trust anchor:
      `configure()` raises `RegistryValidationError("registry_trust_anchor_invalid")`
      before any connection.
    - (c) `ha_mcp_release_registry_enabled=True` with the fixture anchor and a
      current, empty signed journal (sequence 1, no 9.2.0 row):
      `admitted_exact`, with authority source `compiled_exact`. The registry is
      enabled at sequence 1 and current, all 27/27 delegated reads are exposed,
      and no upstream calls are made. The signed snapshot redacts versions.
    - (d) `upstream_trust_registry_enabled=True`: `admitted_exact`, and the
      read gateway is unaffected. The claim that this switch gates only the
      dashboard provider is an inference from `upstream_dashboard.py`.
15. **Test note.** With the signed registry enabled, the gateway keeps a
    persistent transport session. Tests close it
    (`await gateway._transport.aclose()`), otherwise `asyncio.run` teardown
    hangs.

## Version-enumerating test appends (condition (a))

- `tests/test_hamcp162_yaml_admission.py`: 9.2.0 is appended to the skip set
  and the admitted set.
- `tests/test_hamcp351_ha_mcp_912_admission.py`: 9.2.0 is appended to the
  used-tool admission releases.

No other 9.1 test changed.
