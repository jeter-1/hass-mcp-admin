# HAMCP-371 S8: 9.2.0 error contract (N31) and the 9.2.0 rows

## N31: the review error probes on a Core with no seeded object

Ruling N31 (a), 2026-11-15 06:40 CST: send the review script's four error
probes (`scripts/review_upstream_read_release.py` `ERROR_PROBES`) to 9.1.2 and
9.2.0 before any object is seeded, and proceed with S8 only if every probe on
both versions reproduces the bound 9.1.2 shapes and the aggregate
`4ed5b1b4c0aaad0036b4625206e96739b40fc83ec1c944754fef1ed667a10437`.

### Run

- One profile A launch, `probes-1`. Harness commit `da6e6b70` (the `probes`
  lane command: the seed's `access` phase only, then the `review_probes`
  group, one peer at a time).
- Launched through the heavy gate as unit `hamcp371-s8-probes-1` at
  2026-11-15 07:05:48 CST. Admission: no other heavy job, 12.5 GB available,
  load 0.25, 60.4 GB root free. Gate receipt: `gate/probes-1.txt`.
- Ran 07:05:49 to 07:06:10 CST (21.3 s). Result `PASS`, cleanup proven.
  Launch log line: `launches.jsonl`.
- Core 2026.11.0, by digest `sha256:c6c1ca99…`. Peers ha-mcp 9.1.2
  (`sha256:381b1e01…`) and 9.2.0, by digest from the local cache, no pull.
  The full digests are in `probes-1/receipt.json`.
- D1 production flags on each peer, checked on the container environment and
  in the raw catalogue.
- Loopback only: no interface but `lo` and no IPv4 route, seen from Core and
  from the holder, before the seed and before each capture.
- Seed: the four access steps only (`onboard`, `ready`,
  `administrator_token`, `ordinary_user_token`), all `ok`. No registry object,
  helper, automation or other object was created, so Core held no automation
  when `missing_automation` was sent.
- 8.5.0 was left out: the lane's versions are 9.1.2 and 9.2.0, and 8.5.0
  would need code beyond the call set (ruling: do not extend the lane for it).

### Result

`compare_probes.py` reads only committed files and uses the review script's
own `error_evidence`. Its output is `comparison.txt` (exit 0).

| Probe | Code | Fingerprint | 9.1.2 | 9.2.0 |
|---|---|---|---|---|
| invalid_search | VALIDATION_FAILED | `fc0f1e8b…` | match | match |
| missing_automation | RESOURCE_NOT_FOUND | `8053f516…` | match | match |
| missing_registry_entity | SERVICE_CALL_FAILED | `3e1148ad…` | match | match |
| missing_state | ENTITY_NOT_FOUND | `8a705d92…` | match | match |
| aggregate | | `4ed5b1b4…` | match | match |

Each match is the whole bound record (`is_error`, `structured_code`,
`shape_fingerprint`), not only the fingerprint.

### missing_automation varies with Core content

`missing_automation`'s shape depends on what Core holds. Its payload carries
`available_automation_ids`, a list of the automations that exist. The shape
projection keeps list items, so one automation changes the fingerprint:

- `probes-1`, no automation: `available_automation_ids: []`, fingerprint
  `8053f516…`, the bound value.
- S6 `run-a-1` (both versions), `run-b-9.1.2-1` and `run-b-9.2.0-2`, seeded
  with the one automation `automation.hamcp371_automation`: fingerprint
  `25357453d3b95ffe379819f2be6eea23bce77a65dcc1d8b8bebc66eab4505126` on every
  version. `comparison.txt` shows these four from the committed S6 call files.

So an error-shape capture reproduces the bound aggregate only on a Core with
no automation. A review-time capture against a Core that has automations gives
a different `missing_automation` fingerprint and a different aggregate, and no
compiled adapter would match it. This affects review-time captures only;
Engineering does not send these probes at runtime (see the S8 report, Q6).

### What is committed and what is not

Committed, each byte-identical to the run directory (checked against the run's
own `SHA256SUMS`): `receipt.json`, `SHA256SUMS`, the seed report, and for each
version the four probe call files, `_index.json` and `_initialize.json`. Also
the gate receipt, the launch log, `compare_probes.py` and `comparison.txt`.

Not committed: the two peer catalogues, the Core log, the two peer logs and
the empty holder log. They stay in the ignored `.artifacts/hamcp371-s8/` and
are listed with their hashes in `probes-1/SHA256SUMS`.

Secret checks before the copy:

1. The lane, while the tokens existed: exact bytes of each token and of its
   signature segment, 4 needles, 19 files, none removed (`secret_scan` in the
   receipt).
2. The leak check passed on every file before it was copied.
3. `s6/comparison/scan.py` over the whole run directory, the gate receipt and
   the launch log: 23 files, 0 hits.
