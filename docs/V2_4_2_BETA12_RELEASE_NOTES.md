# Engineering 2.4.2-beta.12 release notes

**Status: in progress.** Engineering 2.4.2-beta.12 is the 2.9.0.1 hotfix. It
builds on 2.4.2-beta.11 (2.9.0, #481). Its content lands through item pull
requests into the `release/2.9.0.1` integration branch. These notes are
complete on that branch except for what is recorded after release: the
Evidence tier result, release CI and publication.

The architect's block of 2026-11-23 (HAMCP-470 hotfix) names 2.4.2-beta.12
for the 2.9.0.1 path. The hotfix is narrow: the lifecycle add-on identity
read that governed apply makes on ha-mcp 9.2.0.

Acceptance: [`V2_4_2_BETA12_ACCEPTANCE.md`](V2_4_2_BETA12_ACCEPTANCE.md).
Per-release records: [release index](RELEASES.md).

## Current state

The version is materialized at the base of `release/2.9.0.1` (`main` at
23c5c429). The one item, HAMCP-470, has landed (#483, merged as 71520cba),
and so have these notes' Items and the acceptance sections 4 and 5 (#484).
A final docs pull request completes the tool catalog, Deploy and Rollback,
so the Evidence tier runs on a head that nothing changes afterwards. Nothing is
published or deployed.

## Items

Every item has landed in `release/2.9.0.1` and is listed by its pull request.

- **HAMCP-470** (#483, reviewed head e044b9f7, merged as 71520cba).
  Lifecycle add-on identity read on ha-mcp 9.2.0.
  - **The defect.** At apply, F3 prepare reads the ha-mcp add-on's identity
    before dispatch: the installed inventory, then the detail, through
    `ha_get_app`. On beta.11 the `STRUCTURED_V1` decoder required
    `content[0].text` to parse as strict JSON within 250,000 bytes and to
    equal `structuredContent` exactly. With ha-mcp `redact_secrets` on, the
    text and the structured object are scrubbed separately, so the text can
    stop parsing or carry a duplicate member. The detail is also close to
    the old bound. On the house, the first governed lifecycle apply (an
    automation reload) was refused before dispatch, and the refusal surfaced
    as `internal_server_error`.
  - **Text comparison.** The comparison bound is now derived from the
    1,000,000-byte exchange bound: 333,333 bytes, since one exchange holds
    the text twice. Within the bound, a text that parses cleanly is still
    compared exactly, and a divergence still refuses. Otherwise the read is
    decided from `structuredContent` alone, under every existing structured
    check, and recorded as `skipped` with its failure class. It is never
    recorded as `verified_equal`.
  - **Redaction-renamed keys.** A key renamed `<key>#N` by upstream's
    scrub is never an identity key or an envelope key; such a record is
    refused. The refusal's diagnostic path is the fixed
    `/structuredContent/<field>#N`, with a literal `N`: the observed key is
    never copied into health.
  - **Exponent overflow.** A number that overflows to infinity (for
    example `1e999`) is classified `nonfinite`, as `NaN` and `Infinity`
    tokens are, so it is never compared as a clean text.
  - **Diagnostic.** `operational_administration.lifecycle_provider.lifecycle_addon_text_comparison`
    in `get_server_health`: one value-free entry per read, `inventory` and
    `detail`, holding `text_comparison`, `text_failure_class` and
    `text_bytes_bucket` from fixed enums. It holds no sizes, keys or values.
  - **Error code.** An unreadable add-on identity at apply is now refused
    before dispatch with `operational_contract_mismatch`
    (`failure_stage: pre_dispatch`, no dispatch, no fallback,
    `required_action: refresh_provider_evidence_and_replan`), at both F3
    callers, instead of `internal_server_error`.
  - No new write, dispatch path or fallback. No public input schema, tool
    registration or persisted format change.
  - **Review.** Cursor r1 (`grok-4.7-xhigh-fast`, the recorded reviewer):
    READY, no findings. Codex security review (`gpt-6-astra`, high): r1
    CHANGES_REQUIRED with two Medium findings, the renamed-key diagnostic
    path and exponent overflow; both were corrected in e044b9f7, and r2
    found both resolved with no new finding. Codex r2's remaining gap, its
    read-only sandbox's inability to run tests or mutations, was closed by
    the architect's own run in a separate clone: 50 tests OK; mutations
    M18, M19 and M20 each fail, and the restored tree is OK.
  - **Follow-up, not in this release.** HAMCP-471 (Backlog): the same
    exponent-overflow class in the other strict JSON decoders.

The public tool schemas and registration are unchanged, so clients need no
tool refresh for this release.

## Evidence

Each pull request's review and CI run are bound in the acceptance document,
section 1. **Evidence tier: pending.** It runs once, on the final release
head, after Josh's go. Its result, release CI and publication are recorded
after release by a small docs pull request to `main`, as #482 did for
2.4.2-beta.11.

## Tool catalog

Only CI-observed values are listed. All are from run 45282990469, the #483
run at e044b9f7 (2026-11-23, 16:47 to 17:02 CST, success), the last item
head before 71520cba.

| Lane | Engineering | Delegated | Total | Upstream catalog | Source |
| --- | --- | --- | --- | --- | --- |
| ha-mcp 9.2.0 | not recorded | 27 | not recorded | fingerprint `0f1a2c75a766a111a83053733c6803ca14d60357137f4ecc7bc7978061982147` | artifact `exact-image-admission-cases-91_only-amd64`, `91_parity_read_gateway_acceptance.json` (Engineering 2.4.2-beta.12, `ha_config_get_yaml` exposed) and `91_filesystem_disabled_variant.json`; the arm64 artifact is identical |

- **9.2.0 strict admission.** `admitted_exact`; 82 advertised, 77 reviewed
  exact; no extras, missing or duplicates; 28 dynamically exposed; selected
  entry `ha-mcp-v9.2.0-9de5bf6c` (`91_strict_admission_positive.json`).
- These are the beta.11 CI values: delegated count and fingerprint are
  unchanged. No landed item states a tool registration change.
- **CI reference (lane value).** The 27 above is the CI lane value; the
  cause of 27 in CI against 28 live is not established. The live
  expectation stays the beta.9 live baseline: Engineering 86, delegated 28,
  total 114, with `ha_config_get_yaml` admitted.
- CI did not record the Engineering count or connector total. Beta.12
  installed acceptance takes no fresh `tools/list` (Josh's short read
  check, section 4 step 3), so this release records no live catalog.

## Deploy

See [the acceptance contract](V2_4_2_BETA12_ACCEPTANCE.md), "Deploy". The
operator change from beta.11 is none: Engineering still admits ha-mcp 9.2.0
only, so keep auto-update off. Before deploy, export the audit, take a full
Home Assistant backup and a Proxmox snapshot of VM 400 (homeassistant),
named for the release, taken by the host admin with Josh's approval.

## Rollback

See [the acceptance contract](V2_4_2_BETA12_ACCEPTANCE.md), "Rollback". In
short: restore the pre-deploy backup or the VM 400 snapshot, which returns
2.4.2-beta.11 (tag `v2.4.2-beta.11`, commit 23c5c429, image digest
`sha256:e5e46d52a6db1bc4e02e207603d21dab57c7e6a03cc1030d03d9193199a294fd`)
with ha-mcp 9.2.0, and the HAMCP-470 defect. The restore also reverts every
Home Assistant change made since the backup. There is no in-place rollback.
Export `get_audit_log` before any restore. No landed item states a
persisted-format change.
