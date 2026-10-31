# HAMCP-097 offline `get_server_health` benchmark

Offline only. The store is synthetic and built with synthetic settings. No
network, live Home Assistant, deployed MCP endpoint or real credential was
used. These numbers are local evidence from one loaded, shared host. They are
not CI evidence and not a production measurement.

## Source identity

- before: a frozen `git clone --shared` of commit
  `398f929f4a0584aff8a2c89bf0fc65fea5729628`, with no runtime diff.
- after: the task worktree at the same HEAD, plus uncommitted edits under
  `hass_mcp_engineering_beta/ha_mcp_engineering`. The
  `git diff HEAD -- hass_mcp_engineering_beta/ha_mcp_engineering` has sha256
  `0ab36c77214da3e692b690dfc166b82458bbfc1d51f4fec0ce85242e22c390ce`. Every
  after run records the same hash in `summary.source` of its raw JSON.
  Those edits were later committed unchanged. The command
  `git diff 398f929f dcd4568bb116e958384f6c7dcf4b463647d03040 --
  hass_mcp_engineering_beta/ha_mcp_engineering` gives the same sha256, as
  does the diff to `8085dc0f`.
- after, actionability reuse (`raw/mutate_*_after_actionable.json` only):
  the worktree at `8085dc0f` plus uncommitted runtime edits whose diff has
  sha256 `ce601c3c6235d54f912b2585179f28fa9c7898208d4a82027489c8c89327045d`.
  They were later committed unchanged as `c575723c`:
  `git diff 8085dc0f c575723c -- hass_mcp_engineering_beta/ha_mcp_engineering`
  gives the same sha256.

The matrix in `summary.md` predates `c575723c`. That commit changes only
how a rebuilt assembly evaluates approval actionability. Warm calls reuse
the assembly and never reach that code, and cold calls evaluate every plan
both ways. So it affects only the after-mutation rows below.

## Hardware

- CPU: Intel i7-10700 @ 2.90GHz, KVM guest, 10 vCPU, 1 thread per core.
  `nproc` is 10.
- Memory: 7.7 GiB RAM and 3.3 GiB swap.
- Python 3.12.14.
- The root disk was 91-92% full.
- The host is shared. `load1` was 9-10 during the x1 runs and 3-5 during
  the x2 and x4cap runs. The `uptime` load average is recorded before and
  after every run in `environment_before` and `environment_after`.

Because the load changed between scales, compare before and after within a
scale. Comparisons across scales are confounded by the load difference.

## Fixture (`tools/build_store.py`)

The fixture is deterministic:
- `uuid4` and `secrets.token_*` are seeded.
- The fake clock starts at 2026-10-01 UTC and advances 7 minutes per plan.

It writes only through the real writers:
- Plans are created through `ChangeGovernanceService`.
- Approval uses the real Ingress approval flow: approve, then
  `issue_external_csrf`, then `decide_external_approval`.
- Apply runs through the composed `F3RuntimeIntegration` with the
  repository's offline fake gateways.

The x1 mix is 501 terminal plans:

| Kind | Plans |
|---|---|
| applied config | 333 |
| rejected | 30 |
| expired | 32 |
| apply-failed | 12 |
| prohibited (legacy `create_plan` with `lock.unlock`) | 14 |
| rollback pairs | 8 |
| backups | 20 |
| reloads | 30 |
| add-on restarts | 6 |
| HA restarts | 6 |
| operational rejected | 1 |
| manual review (ambiguous reload, then a recovery sweep after 901 s) | 1 |

Configuration sizes fall into three buckets: 70% have 1-4 actions, 25% have
4-10, and 5% have 10-24.

Exactly 2 projection failures are made by in-place edits that use the
HAMCP-198 technique:
- `policy_decision.policy_subject_hash` is set to `0`*64 on a rejected plan.
- `approval.policy_decision_hash` is set to `2`*64 on an applied plan.

Fixture summaries are in `raw/build_summary_*.json`:

| store | plans (std / elev / prohib) | tasks (manual review) | events | plan bytes median / p90 / max | on disk |
|---|---|---|---|---|---|
| x1 | 501 (396/91/14) | 424 (1) | 2,973 | 17,451 / 27,314 / 52,785 | 24.6 MB |
| x2 | 1001 (788/185/28) | 847 (1) | 5,941 | 17,924 / 26,416 / 53,240 | 43.7 MB |
| x4cap | 1226 (955/234/37) | 1024 (0) | 7,172 | 17,828 / 27,475 / 46,257 | 51.0 MB |

Each store has 2 projection failures and no nonterminal plans or tasks.

### x4 could not be built with the real writers

The x4 build stopped after the 1,024th F3 task. The 1,025th apply raised
`GovernanceError: Execution-task storage is unavailable`. The bound is
`MAX_F3_PUBLIC_TASKS = 1_024`, checked by `_bounded_paths("*.manifest.json",
MAX_F3_PUBLIC_TASKS - 1)` (`f3_runtime/repository.py` lines 40 and 606 at
398f929f).

The partial store, `x4cap`, holds 1,226 plans (2.45 times x1) and 1,024
tasks. The same two tamper edits were applied to it afterwards. It has no
manual-review task, because no further task can be written.

## Bench (`tools/bench_health.py`, `tools/run_matrix.sh`)

How each run works:
- Each run copies the store into a fresh temporary directory.
- It composes the service the way production does:
  `GovernanceRuntime().configure(...)` with the upstream singletons
  configured from synthetic settings.
- `startup_construction_ms` covers construction, which includes cleanup,
  recovery and the initial cache build. The startup recovery pass is not run.
- Retention is 365 days, so records with fake dates are kept.

Interleaving: each round runs `before_plain`, then `after_sv`, then
`after_plain`. There are 2 rounds per scale and 5 warm calls per run, so
each variant has n=10 warm samples.

Cold call, by variant:
- `plain`: the first call after construction.
- `after_sv`: `runtime.start_historical_validation()`, then the first call
  immediately, then wait for completion, then one more call.

Each row records:
- `wall_ms` and `phase_elapsed_ms`;
- the deserialization counters from
  `plan_store_scaling.hot_paths.governance_health`;
- the health cache hit and rebuild counters;
- `incremental_health_index` and `historical_validation`.

## Results (ms, median / min; full table and per-call lines in `summary.md`)

| scale | variant | first call | warm (n=10) | warm projection | warm assembly | warm overlay | warm snapshot |
|---|---|---|---|---|---|---|---|
| x1 | before_plain | 5484 / 5406 | 2451 / 2346 | 567 | 782 | 444 | 555 |
| x1 | after_plain | 5442 / 5441 | 698 / 636 | 14 | 1 | 469 | 185 |
| x1 | after_sv | 3010 / 3008 (pending) | 762 / 677 | 16 | 1 | 503 | 199 |
| x2 | before_plain | 8697 / 8695 | 4145 / 3962 | 916 | 1314 | 833 | 994 |
| x2 | after_plain | 8809 / 8729 | 1262 / 1118 | 25 | 2 | 868 | 317 |
| x2 | after_sv | 3007 / 3006 (pending) | 1219 / 1206 | 24 | 2 | 838 | 306 |
| x4cap | before_plain | 10326 / 10290 | 4980 / 4829 | 1126 | 1591 | 1002 | 1175 |
| x4cap | after_plain | 10324 / 10000 | 1398 / 1231 | 29 | 3 | 955 | 364 |
| x4cap | after_sv | 3007 / 3007 (pending) | 1481 / 1376 | 29 | 3 | 1004 | 379 |

Notes on the table:
- In the after_sv rows, the first call returned `status: pending` with
  `pending_reason: historical_validation_in_progress` at the 3000 ms wait
  budget.
- Background validation took, from launch to completion:
  - x1: 5246 and 4745 ms
  - x2: 7537 and 7749 ms
  - x4cap: 9509 and 9034 ms
- Construction took about 16 s at x1, 26 s at x2 and 31 s at x4cap. Before and
  after were within about 3% of each other.

Deserialization per call, from the hot_paths terminal plan and task
counters:

- before: every call deserializes 501/847 records at x1, 1001/1693 at x2 and
  1226/2047 at x4cap. The rebuild counter increments on every call.
- after_plain:
  - Cold call: the same counts as before, with `last_run` reporting the
    assembly rebuilt and projection misses equal to the plan count.
  - Warm calls: 0/0, with the assembly reused and projection hits equal to
    the plan count. The rebuild counter stays at 2.
- after_sv: the pending call has null counters. The first complete call and
  every warm call are 0/0 with `projection_hits` equal to the plan count.
- Mutation at x1 (`raw/mutate_x1_*.json`): 5 terminal plans were rewritten
  with `ChangePlanRepository.save`.
  - before: the next call deserialized 501/847, as every call does.
  - after: the next call deserialized 5 plan and 423 task records, with
    `projection_invalidations` 5, `projection_misses` 5, `projection_hits`
    496 and the assembly rebuilt. It took 1270 ms, against 570-648 ms warm.
    Assembly was 611 ms of that: re-evaluating approval actionability read
    a task record for every plan.
  - after, actionability reuse (`c575723c`): 5 plan and 3 task records,
    `actionable_hits` 494 and `actionable_misses` 5. It took 714 ms
    (assembly 113 ms), against 503-583 ms warm. `load1` was 2.2-2.4, against
    4.4 for the earlier run.
- Mutation at x4cap, actionability reuse (`c575723c`): 5 plan and 4 task
  records, `actionable_hits` 1219, `actionable_misses` 5. It took 1739 ms
  (assembly 287, overlay 974, snapshot 353), against 1236-1377 ms warm.
  This is above the 1.5 s warm SLO. The overlay and snapshot are paid on
  every call and still scale with history (see Limitations in the PR), and
  the remaining assembly work is linear in plan count.

## Profiles (`profiles/`, x1, cProfile across all threads, top 40 cumulative)

| profile | total | top entries by cumulative seconds |
|---|---|---|
| before cold | 14.93 s | `_validate_health_records` 9.39; `persistence_safety_errors` 8.50; `sanitization.rescan_stored_plan` 7.66; `sanitize` 7.65; `_sanitize_string` 3.91; `policy_snapshot_matches` 2.34; `_build_health_summary` 1.87; `compare_physical_effects` 1.84 |
| before warm | 5.78 s | `async_health_summary` 5.69; `policy_snapshot_matches` 2.42; `_build_health_summary` 2.14; `_is_effectively_prohibited_plan` 1.94; `_approval_is_actionable` 1.93; `compare_operation` 1.91; `_require_projection_policy_snapshot` 1.65; `physical_effects._analyze` 1.40 |
| after cold | 14.77 s | `_validate_health_records` 9.52; `sanitize` 7.76; `_async_health_scan` 5.19; `policy_snapshot_matches` 2.07 |
| after warm | 1.10 s | `_async_health_scan` 1.02; `f3_runtime.async_health` 0.67; F3 `repository.collect_health` 0.50; `health_scan.check` 0.28; `_read_source` and `pathlib.stat` 0.25; `storage.collect_health` 0.16; `health_scan.stamp` 0.16; `task_storage.collect_health` 0.12 |

## Deviations from the requested fixture shape

- Events are 2,973 at x1, against a target of about 2,422.
- There are no legacy plans without a policy snapshot. The current writers
  cannot produce them.
- The projection failures come from tampering in place, not from organic
  writes.
- Prohibited plans use the legacy `create_plan` path and keep status
  `awaiting_approval`. A plan created with `create_configuration_plan` that
  touches more than 32 protected objects classifies as prohibited but keeps
  `risk.apply_allowed=True`. Health then counts it as a projection failure
  (`approval_sequence_inconsistent`), so that path was not used.
- x4 is replaced by x4cap, because of the F3 bound described above.

## Reproduce

Copy `tools/` to `.artifacts/h097/` and clone the base into
`.artifacts/h097/base`. Then run from the worktree root with
`TMPDIR=$PWD/.artifacts/tmp`:

```sh
build_store.py --scale N --out .artifacts/h097/store_xN
run_matrix.sh N 2
summarize_results.py
```

The raw JSON has the worktree path replaced by `<worktree>`.
