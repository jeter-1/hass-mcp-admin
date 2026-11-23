# Engineering 2.4.2-beta.11 release notes

**Status: in progress.** Engineering 2.4.2-beta.11 is the 2.9.0 build. It
builds on 2.4.2-beta.10 (HAMCP-401, #452). Its content lands through item pull
requests into the `release/2.9.0` integration branch, and the final release
pull request completes these notes.

The architect's task of 2026-11-18 08:2x CST names 2.4.2-beta.11 for the
2.9.0 release path. 2.9.0 is a cleanup release: it keeps ha-mcp 9.2.0 only
and retires the code, tests, workflows and documents that served older
releases.

Acceptance: [`V2_4_2_BETA11_ACCEPTANCE.md`](V2_4_2_BETA11_ACCEPTANCE.md).
Per-release records: [release index](RELEASES.md).

## Current state

The version is materialized at the base of `release/2.9.0` (`main` at
6449c7b3). Every item has landed; `release/2.9.0` is at 6200efbe (#479).
Nothing is published or deployed.

## Client-visible changes

- **Only ha-mcp 9.2.0 is admitted** (HAMCP-437 I2a-D, #479, merged as
  6200efbe). The reviewed registry keeps one row, 9.2.0 (`default_version`
  9.2.0); the rows for 7.14.1 through 9.1.2 and their packaged policies are
  deleted. An older release is refused through the existing unknown-version
  path, before dispatch (`upstream_version_mismatch`; lifecycle reports
  `operational_contract_mismatch` and backup `backup_provider_unavailable`).
  No new refusal path is added. Backup on 9.2.0 stays held
  (`provider_held`, HAMCP-381). Keep the ha-mcp add-on's auto-update off.
- **Lifecycle health records descriptor drift** (HAMCP-436, E022, #453,
  merged as 1069f18e). Lifecycle and backup require exact contracts only for
  the tools each operation dispatches and the reviewed versioned add-on
  tools. The reviewed tool-name set stays a whole-catalog gate. Descriptor
  drift on any other reviewed tool is recorded under
  `operational_administration.lifecycle_provider.catalog_admission`, not
  rejected. On 9.2.0 that is `ha_config_set_helper`, so the lifecycle
  provider is `available` with `status: accepted_unused_tool_drift`, while
  `catalog_validation.validation_status` still reads
  `rejected_catalog_mismatch`. Drift on a dispatched tool, including
  `ha_manage_backup`, still refuses.
- **Upstream warnings are replaced, counted and reported partial**
  (HAMCP-464, #477, merged as d7e57954; ruling 20261122-0132-01). On
  `ha_get_overview`, `ha_get_state` (single and bulk), `ha_get_todo`,
  `ha_get_zone` and `ha_list_floors_areas`, upstream warning text is never
  passed through. It is replaced by the fixed code
  `upstream_warning_replaced`, counted in `upstream_warnings_replaced`, and
  the read reports `partial` with the notes `UPSTREAM_WARNINGS` and
  `REPLACED_PARTIAL`. A read that carries an upstream warning now returns
  `partial`. No public schema, tool registration or provider routing change.
- **Full-detail overview rows keep `entity_id`** (HAMCP-439, #463, merged as
  d4313049). On 9.2.0, `ha_get_overview` with `detail_level: "full"` returns
  `entity_id` on each `domain_stats.<domain>.entities[]` row. Before, it was
  withheld silently. Standard detail is unchanged.
- **`ha_search` `result_fields` keys are admitted** (HAMCP-441, #464, merged
  as 42c4ddc7). The 9.2.0 keys `area`, `floor`, `labels`, `aliases`,
  `is_group` and `member_entity_ids` arrive whole when requested. Unknown
  keys stay withheld, and upstream's "matched no record keys" warning is
  replaced (`upstream_warning_replaced`).

Clients should refresh their tool list after deploying.

## Cleanup items

None of these states a runtime change, except the sequence-18 record in #467.
Each pull request lists what it retired.

- **I2a-T** (#459): the test suite moves onto ha-mcp 9.2.0.
- **I2b-A0** (#462): the CI and test trust root moves to the owner-signed
  sequence-18 Core registry journal.
- **I2b** (#461, #460, #465): one-slot CI window on Core 2026.11.0 with
  ha-mcp 9.2.0; the rollback slot may be empty (HAMCP-406) and the in-place
  rollback lane is retired (HAMCP-405); three unrequired workflows and the
  pre-window Core bindings, fixtures and sequence-15 CI steps are retired.
- **I2c-1** (#466): retired files that no workflow calls are deleted.
- **I2c-2** (#474, #475, #476): the split test modules and fixtures move onto
  9.2.0; the last readers of the 2.1.0-beta.1 and 2.2.0-rc.1 release docs are
  retired with those docs.
- **I2d** (#467, #471): historical docs are deleted under the keep rule, and
  the sequence-18 signing is recorded.
- **I2f-1** (#468): Evidence may reuse one CI run's unit-shard results for
  the same tree (HAMCP-365); a test-order pin (HAMCP-241).
- **I2f-2** (#473, HAMCP-363): one loopback-and-route check for every
  harness send, and an offline unit suite.

## Core evidence (HAMCP-443)

#469, #472 and #478 rebuild the Core evidence lanes on a shared lane host,
with CI-mode lane groups. The lane-groups job now proves all 19 groups, and
the round-8 known red is retired (#478, merged as 7c9b8ee0). Production Core
updates were held until this. The first scheduled run after 2.9.0 reaches
`main` covers the next Core release, and Josh reviews its candidate before
signing.

## Dashboard lane

`dashboard-disposable` now runs ha-mcp 9.2.0 on the strict-full endpoint, on
Core 2026.9.4 (ruling 20261122-1313-01). The lane's source review was done on
8.5.0. In run 45087231676 the dashboard acceptance passed on Core 2026.9.4,
frontend 20260826.7, with
`ghcr.io/homeassistant-ai/ha-mcp:9.2.0@sha256:9de5bf6c4216142fdffe862900abe72dac8dfe9621dd0b2b050d132372b676ba`.

## Known gaps and follow-ups

These are not fixed in 2.9.0.
- **HAMCP-363:** open for five send destinations and two exempt scripts.
- **HAMCP-468:** the stale 8.5.0 tarball step in `ci.yml` (lines 960-972).
- **The 9.2.0 capture row:** YAML tags, the disabled-tool error, and the
  visibility warning text.
- **The family-admission and capture tooling:** retire or rebaseline. That is
  a ruling for Josh. [JOSH]
- **`docs/READ_COVERAGE_MATRIX.md:1116-1117`** still read "admitted
  7.14.1-8.1.1 and 9.2.0, held 8.2.0-9.1.2".
- **`scripts/codex-context.py:622-627`** falls back to the deleted policy
  file `upstream_tool_policy.json`.

Also observed, outside this release's scope: the CHANGELOG entries for
beta.9 and beta.10 still read "in progress", and
`docs/OPERATIONAL_ADMINISTRATION.md` (lines 160 and 247) still names ha-mcp
7.14.1 or 7.14.2.

## Evidence

Each item's review and CI run are bound in the acceptance document, section
1. **Evidence tier: pending.** It runs once, on the final release head,
after Josh's go.

## Tool catalog

Only CI-observed values are listed. All are from run 45087231676, the #479
run at 2d01a7bf (2026-11-22 18:10 CST, success), the last item head before
6200efbe.

| Lane | Engineering | Delegated | Total | Upstream catalog | Source |
| --- | --- | --- | --- | --- | --- |
| ha-mcp 9.2.0 | not recorded | 27 | not recorded | fingerprint `0f1a2c75a766a111a83053733c6803ca14d60357137f4ecc7bc7978061982147` | artifact `exact-image-admission-cases-91_only-amd64`, `91_parity_read_gateway_acceptance.json` (Engineering 2.4.2-beta.11, `ha_config_get_yaml` exposed) and `91_filesystem_disabled_variant.json`; the arm64 artifact is identical except for the image |

- **9.2.0 strict admission.** `admitted_exact`; 82 advertised, 77 reviewed
  exact; no extras, missing or duplicates; 28 dynamically exposed; selected
  entry `ha-mcp-v9.2.0-9de5bf6c` (`91_strict_admission_positive.json`).
- **The 8.5.0 read-gateway lane is retired** (#461), so CI records no
  8.5.0 values.
- **CI reference (lane value).** The 27 above is the CI lane value. The
  cause of 27 in CI against 28 live is not established. No landed item
  states a tool registration change, so the live expectation is the beta.9
  live baseline: Engineering 86, delegated 28, total 114, with
  `ha_config_get_yaml` admitted.
- CI did not record the Engineering count or connector total; installed
  acceptance section 4 step 6 records them.

## Deploy

See [the acceptance contract](V2_4_2_BETA11_ACCEPTANCE.md), "Deploy". The
operator change is that Engineering admits ha-mcp 9.2.0 only: keep
auto-update off. Before deploy, take a full Home Assistant backup and a
Proxmox snapshot of VM 400 (homeassistant), named for the release, taken by
the host admin with Josh's approval.

## Rollback

See [the acceptance contract](V2_4_2_BETA11_ACCEPTANCE.md), "Rollback". In
short: restore the pre-deploy backup or the VM 400 snapshot, which returns
2.4.2-beta.10 (tag `v2.4.2-beta.10`, commit 6449c7b3) with ha-mcp 9.2.0.
There is no in-place rollback. Export `get_audit_log` before any restore.
No landed item states a persisted-format change.
