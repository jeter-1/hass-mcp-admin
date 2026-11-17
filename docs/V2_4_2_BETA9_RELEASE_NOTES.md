# Engineering 2.4.2-beta.9 release notes

**Status: source complete; not published or deployed.** Engineering
2.4.2-beta.9 is the 2.7.0 build. It builds on 2.4.2-beta.8 (HAMCP-332,
#438). Its content landed through item pull requests into the
`release/2.7.0` integration branch.

Josh decided on 2026-11-14 (relayed by the architect) that HAMCP-371 ships as
2.4.2-beta.9.

Acceptance: [`V2_4_2_BETA9_ACCEPTANCE.md`](V2_4_2_BETA9_ACCEPTANCE.md).
Per-release records: [release index](RELEASES.md).

## Current state

The version is materialized at the base of `release/2.7.0` (`main` at
e741f2ed). HAMCP-371 landed through #443 (merge ffe8c156). Nothing is
published or deployed.

## Items

- **HAMCP-371** (#443, head 067e443d, merged as ffe8c156). ha-mcp 9.2.0
  (upstream 51e3aac7) is admitted as a reviewed delegated provider, with the
  HAMCP-374 per-read real-output replay.
  - The 9.2.0 server is admitted only with component 9.2.0; they deploy as
    a pair.
  - The registry row admits the dashboard and read gateway; lifecycle and
    backup stay held.
  - The 9.2.0 reads are bound to closed key sets. Unknown blueprint
    top-level keys are refused; unknown platform-read keys are withheld and
    counted.
  - History on 9.2.0: ha-mcp 9.2.0 resolves offset-free times in Home
    Assistant local time, where 9.1 used UTC. Engineering therefore forwards
    `start_time` and `end_time` only when they are a full date and time with
    an explicit offset or `Z`, or a relative form (`30min`, `24h`, `7d`,
    `2w`, `1m`). Any other parseable value, date-only included, is refused
    before dispatch as `invalid_request` (`history_time_offset_required`),
    with a message naming the required format. 8.5.0 behaviour is unchanged.
  - Blueprint: "unknown top-level data keys are refused" applies on 9.2.0
    only. On 8.5.0 they still pass through (HAMCP-402) until the upgrade.
  - `ha_search` has exactly two 9.2.0 descriptors, legacy and unified. Under
    unified, `config_time_budget` is forwarded unchanged with the one fixed
    warning `search_budget_legacy_path_only`.
  - The existing governed dashboard, power, fan and helper-value writes each
    gain an exact 9.2.0 contract row. No new write or service name.
  - CI's single admission candidate moves from 9.1.2 to 9.2.0. After this
    release no CI admission lane exercises 9.1.2; it stays admitted in the
    registry.

## Evidence

Bound in [the acceptance contract](V2_4_2_BETA9_ACCEPTANCE.md), section 1:
- #443, reviewed head 067e443d, merge ffe8c156 (the same tree);
- Cursor independent review: APPROVE, one Low finding (F1), accepted by the
  owner;
- the architect's independent review: passed, no findings against HAMCP-371;
- local Full and Evidence tiers at 067e443d: rc 0, 15,158 run, 32 skipped,
  0 failures;
- CI 43960418854 on #443: success, 29 of 29 jobs;
- the three archives in `~/archives/`, with their sha256.

The Evidence tier did not run on the final release head; it changes only
these two Markdown files on top of ffe8c156.

## Tool catalog

Only CI-observed values are listed. All are from run 43960418854.

| Lane | Engineering | Delegated | Total | Upstream catalog | Source |
| --- | --- | --- | --- | --- | --- |
| ha-mcp 8.5.0 | 86 | 27 | 113 | 77 tools, fingerprint `0d7ecdb7f58b9b3f92fe05e0f3afbd213da6d5841263219d8c12c3dea1d77845` | job 133626243207 `exact-image-read-gateway`, artifact `exact-image-read-gateway-8.5.0`, `exact-image-read-gateway-result.json` |
| ha-mcp 9.2.0 | not recorded | 27 | not recorded | fingerprint `0f1a2c75a766a111a83053733c6803ca14d60357137f4ecc7bc7978061982147` | job 133626243432 `Exact-image admission 91_only amd64`, artifact `exact-image-admission-cases-91_only-amd64`, `91_parity_read_gateway_acceptance.json` and `91_filesystem_disabled_variant.json`; also job 133626243233 `Exact-image admission side_by_side arm64`, `sbs_stage_b2_91_strict_full_useful.json` |

- **9.2.0 fingerprint and the optional YAML reader.** The 9.2.0 delegated
  count (27) and fingerprint are the same with the optional
  `ha_config_get_yaml` reader exposed (`91_parity_read_gateway_acceptance`)
  and without it (`91_filesystem_disabled_variant`). The fingerprint does
  not change with it.
- **9.2.0 strict admission.** 82 advertised, 77 reviewed exact; no extras,
  missing or duplicates (job 133626243432,
  `91_strict_admission_positive.json`).
- **CI did not record the 9.2.0 Engineering count or connector total; live
  acceptance step 7 records them.**

Clients should refresh their tool list after deploying.

## Rollback

See [the acceptance contract](V2_4_2_BETA9_ACCEPTANCE.md), "Rollback". In
short:
- **Engineering.** Recovery is 2.4.2-beta.8, by restoring the full Home
  Assistant backup taken right before deploy. Export `get_audit_log` before
  any restore. In-place downgrade from beta.9 to beta.8 is not supported and
  is unproven. CI's rollback image is 2.4.2-beta.7 and is not the recovery
  target (HAMCP-405).
- **ha-mcp pair.** Restore the pre-update add-on backup, redownload
  component 2.2.1 in HACS, and restart Core. Restore the configuration
  folder only if the HACS redownload fails.
