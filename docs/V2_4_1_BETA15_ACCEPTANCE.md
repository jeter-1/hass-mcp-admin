# Engineering 2.4.1-beta.15 acceptance

This contract covers HAMCP-162 Part 2: the optional reviewed delegated read
`ha_config_get_yaml`. It builds on beta.14 (HAMCP-189) and beta.13
(HAMCP-164).

It separates four stages:
1. source and disposable acceptance;
2. release CI;
3. publication;
4. installed Engineering acceptance.

This document authorizes none of the later stages.

## 1. Source, review and CI

Bind the following:
- the approved design `docs/HAMCP_162_YAML_READER_DESIGN.md`;
- the D1 capture evidence in `docs/evidence/hamcp162/` (draft PR #264 head
  `0e3e2264491b8d13b988c3ba9d4d50f4e9b520e5`, merged into the branch), with
  its `MANIFEST.sha256`;
- Josh's 2026-10-16 decisions K1–K11 on HAMCP-162, which govern where the
  evidence and the design differ;
- the original base `main` (2.4.1-beta.13 at `06d5555`), and every later
  `main` merge into the branch, including 2.4.1-beta.14 (HAMCP-189, #266) at
  `bdb5af8`;
- the final reviewed commit and tree.

Earlier review does not cover changed code.

Materialize 2.4.1-beta.15 in all three version authorities through the
existing staging tool. Require exact acceptance and release-note resolution,
with no staged declaration left over. Complete Full/Evidence on clean source
and the bounded independent review.

The review needs a security section covering:
- exposure of `ha_config_get_yaml` only, with `ha_list_files` and
  `ha_read_file` never exposed;
- zero write, reload, restart, deletion, fallback or forwarding
  reachability, and no Engineering service call; the one accepted exception
  is the K1 caller-token bootstrap (first call: the component's
  `POST ha_mcp_tools/get_caller_token`, persisted);
- the fixed upstream argument shape;
- refusal of every non-`configuration.yaml` file and every credential-shaped
  path before route acquisition, Core acquisition and any upstream call;
- the L4/L5 redaction rules A, B, C′, R10 and R11, and the default-off
  reference-marker exemption by registry identity only (H2);
- node truncation before redaction (H2, owner direction superseding design
  sections 4 and 8.1), and the final pass of every client-visible field
  through the pipeline (H1);
- the explicit per-version dispositions with no default;
- the complete-descriptor (R4) binding;
- the generation and dispatch lease (R5), and exclusion from the stock
  order, aggregate and signed selections;
- the explicit Core mapping;
- the fixed failure codes, with no upstream text.

Require exact-head CI before release acceptance. Pending or unavailable jobs
are not passing evidence.

Preserve the following:
- stable v1.1.2;
- historical persisted fixtures and durable formats;
- probe fingerprints, the Core capability profiles, every owner-signed Core
  registry entry and the signed ha-mcp registry;
- the 8.6.0 HOLD and the support window;
- the reviewed policy JSON and release registry JSON;
- dependency locks, build inputs, pinned runners and workflow permissions.

The public catalog grows from 87 to **88 tools (59 native, 29 delegated)**.
Every beta.14 descriptor is unchanged.

### Offline source evidence

Run `tests/test_hamcp162_yaml_reader.py` and
`tests/test_hamcp162_yaml_admission.py` against the exact released tree,
with the D1 fixtures. They must prove each of the following:
- the design §8.1 groups;
- the 438-probe rule model, and the rule-A, B, C′, R10 and R11 counterexamples;
- the D1 replay for ha-mcp 8.4.3 and 8.5.0;
- admission, drift and dispositions;
- lease races in both admission modes;
- stock invariants;
- Core gating on 2026.9.2, 2026.10.0 and 2026.10.1.

Run `docs/evidence/hamcp162-mutations/run_mutations.py`. Every design §8.2
mutation must fail at least one test.

## 2. Publication and recovery preparation

Publication needs Josh's applicable Ready authorization and the protected merge
handoff. Verify the following with the shipped verifiers:
- the merged source;
- v2.4.1-beta.15;
- the immutable version and commit images;
- provenance and SBOM.

Never invent future image identities. Retain the beta.14 and beta.13 artifacts
and the known-good beta.12 and beta.11 artifacts and options for recovery.

No new persisted format is introduced, and beta.14 reads every beta.15
record.

## 3. Installed Engineering

All installed checks below are read-only unless a step names Josh's explicit
authorization. The first `ha_config_get_yaml` call may make the ha-mcp
component fetch and persist an admin caller token (K1, an accepted and
disclosed dependency). Record whether it occurred, but do not inspect or
record the token.

1. **Installed-image binding.** Separately authorize installation of the
   published Engineering artifact. Bind the running add-on to the published
   image index digest, the v2.4.1-beta.15 tag and the protected `main` merge
   commit. A source version alone does not prove the installed image.
2. **Live catalog 88.** Enumerate the live catalog with a fresh protocol
   enumeration:
   - exactly 88 tools (59 native, 29 delegated);
   - `ha_config_get_yaml` present, with the reviewed title, schema,
     read-only annotations and description;
   - `ha_list_files` and `ha_read_file` absent.

   Compare the other 87 descriptors with beta.14. `server_info` must report:
   - `optional_tools` with `ha_config_get_yaml: admitted`;
   - both siblings as `prohibited_withheld` or `optional_absent`;
   - `optional_reviewed_accounting_valid: true`;
   - `optional_sibling_drift: []`.

   Also compare Core signed selection, storage and audit health, F3
   readiness and zero fallback. Production ha-mcp must be 8.5.0, or 8.4.3
   on rollback. On any other version, the tool must be absent and the
   catalog stays at 87.

   The tool requires ha-mcp's filesystem beta sub-flag
   (`HAMCP_ENABLE_FILESYSTEM_TOOLS`, already on in production). With the
   flag on, `upstream_advertised_tool_count` is 80 on 8.5.0 (81 on 8.4.3).
   `reviewed_policy_entry_count` must stay at the stock 77 (78 on 8.4.3).
   The stock `reviewed_tool_accounting_valid` keeps its meaning and reports
   `false` with the flag on, exactly as on beta.14. The three optional names
   are accounted by `optional_reviewed_accounting_valid`, which must be
   `true`. With the flag off, the tool is
   `optional_absent` and the catalog is 87 with no quarantine.
3. **Credential-free read.** Read one credential-free section Josh chooses
   (for example `homeassistant.customize` or `logger`). The result must
   succeed, with:
   - `file: configuration.yaml`, `source: disk`, `found: true`;
   - the K7 YAML 1.2 disclosure in the warnings and limitations;
   - the K5 and D12 limitations.

   Record the redaction count and categories only. Do not record values.
4. **Redaction visible.** Read one credential-bearing section Josh chooses
   (for example the parent of a `!secret` password, such as `mqtt` or
   `recorder`). Credential-shaped values must show
   `[REDACTED:credential_key]`, or a `secret_ref` / `env_var_ref` marker
   holding only a reference name. No secret value and no `!env_var` default
   may appear. Record only the marker shapes and categories.
5. **secrets.yaml refused, zero upstream calls.** Call with
   `file: "secrets.yaml"`. The result must be `yaml_file_not_allowed`
   ("Only configuration.yaml is readable."), with:
   - `metadata.upstream_dispatch_occurred: false`;
   - `timing.upstream_request_count: 0`;
   - no retry.

   Repeat the call with `file: "packages/x.yaml"`, which must also give
   `yaml_file_not_allowed`. Then call with a credential-shaped path (for
   example `mqtt.password`), which must give `yaml_path_sensitive`. Both
   must show the same zero-dispatch facts. The audit records for all three
   calls must show no upstream request. They hold error codes and argument
   field names only.
6. **Power and dashboard canaries unchanged.**
   - Repeat the beta.14 read-only checks: one delegated read, one native read
     and `get_core_log_history`, each exactly as on beta.14.
   - Power, fan, dashboard and entity-metadata capabilities must report the
     same admission as beta.14.
   - With Josh's explicit authorization only, repeat the beta.6 power canary
     and the beta.8 dashboard canary on the entity and test dashboard Josh
     chooses. The outcomes must match beta.14.

   No published tool may return `CoreRequirementUnmapped`. Any occurrence is
   a stop condition.

No push, CI trigger, signing, publication, installation or live action is
authorized by source completion.
