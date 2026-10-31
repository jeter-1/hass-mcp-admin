| scale | variant | runs | startup ms med/min | first call ms med/min | first-call status | warm ms med/min (n) | warm validation_wait | warm projection | warm assembly | warm overlay | warm snapshot | load1 range |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| x1 | after_plain | 2 | 16217 / 15522 | 5442 / 5441 | complete | 698 / 636 (10) | 1 / 1 | 14 / 13 | 1 / 1 | 469 / 416 | 185 / 178 | 9.1-9.7 |
| x1 | after_sv | 2 | 16177 / 15998 | 3010 / 3008 | pending | 762 / 677 (10) | 1 / 1 | 16 / 14 | 1 / 1 | 503 / 447 | 199 / 185 | 9.4-9.7 |
| x1 | before_plain | 2 | 16719 / 16411 | 5484 / 5406 | complete | 2451 / 2346 (10) | 1 / 1 | 567 / 551 | 782 / 755 | 444 / 412 | 555 / 497 | 9.7-9.8 |
| x2 | after_plain | 2 | 25639 / 25526 | 8809 / 8729 | complete | 1262 / 1118 (10) | 2 / 2 | 25 / 23 | 2 / 2 | 868 / 718 | 317 / 311 | 4.6-4.8 |
| x2 | after_sv | 2 | 25523 / 25403 | 3007 / 3006 | pending | 1219 / 1206 (10) | 2 / 2 | 24 / 23 | 2 / 2 | 838 / 833 | 306 / 290 | 4.2-4.7 |
| x2 | before_plain | 2 | 26093 / 26090 | 8697 / 8695 | complete | 4145 / 3962 (10) | 3 / 2 | 916 / 894 | 1314 / 1263 | 833 / 697 | 994 / 902 | 4.2-4.8 |
| x4cap | after_plain | 2 | 30637 / 29995 | 10324 / 10000 | complete | 1398 / 1231 (10) | 3 / 2 | 29 / 27 | 3 / 2 | 955 / 789 | 364 / 353 | 2.9-4.7 |
| x4cap | after_sv | 2 | 30913 / 30393 | 3007 / 3007 | pending | 1481 / 1376 (10) | 3 / 3 | 29 / 28 | 3 / 3 | 1004 / 879 | 379 / 372 | 3.6-4.5 |
| x4cap | before_plain | 2 | 31276 / 30987 | 10326 / 10290 | complete | 4980 / 4829 (10) | 3 / 3 | 1126 / 1090 | 1591 / 1568 | 1002 / 962 | 1175 / 1055 | 3.8-4.6 |

Per-call deserialization (plan terminal / task terminal from hot_paths.governance_health; incremental_health_index.last_run):
- x1 after_plain r1 cold: wall=5442.9ms terminal_plan=501 terminal_task=847 cache_hit=0 rebuild=2 index.last_run={"assembly": "rebuilt", "mode": "incremental", "projection_hits": 0, "projection_invalidations": 0, "projection_misses": 501}
- x1 after_plain r1 warm1: wall=657.2ms terminal_plan=0 terminal_task=0 cache_hit=1 rebuild=2 index.last_run={"assembly": "reused", "mode": "incremental", "projection_hits": 501, "projection_invalidations": 0, "projection_misses": 0}
- x1 after_plain r1 warm2: wall=721.9ms terminal_plan=0 terminal_task=0 cache_hit=2 rebuild=2 index.last_run={"assembly": "reused", "mode": "incremental", "projection_hits": 501, "projection_invalidations": 0, "projection_misses": 0}
- x1 after_plain r1 warm3: wall=635.6ms terminal_plan=0 terminal_task=0 cache_hit=3 rebuild=2 index.last_run={"assembly": "reused", "mode": "incremental", "projection_hits": 501, "projection_invalidations": 0, "projection_misses": 0}
- x1 after_plain r1 warm4: wall=735.0ms terminal_plan=0 terminal_task=0 cache_hit=4 rebuild=2 index.last_run={"assembly": "reused", "mode": "incremental", "projection_hits": 501, "projection_invalidations": 0, "projection_misses": 0}
- x1 after_plain r1 warm5: wall=843.5ms terminal_plan=0 terminal_task=0 cache_hit=5 rebuild=2 index.last_run={"assembly": "reused", "mode": "incremental", "projection_hits": 501, "projection_invalidations": 0, "projection_misses": 0}
- x1 after_sv r1 cold_first_after_validation_launch: wall=3012.1ms terminal_plan=None terminal_task=None cache_hit=None rebuild=None index.last_run=None
- x1 after_sv r1 first_after_validation_complete: wall=828.2ms terminal_plan=0 terminal_task=0 cache_hit=1 rebuild=2 index.last_run={"assembly": "reused", "mode": "incremental", "projection_hits": 501, "projection_invalidations": 0, "projection_misses": 0}
- x1 after_sv r1 warm1: wall=748.1ms terminal_plan=0 terminal_task=0 cache_hit=2 rebuild=2 index.last_run={"assembly": "reused", "mode": "incremental", "projection_hits": 501, "projection_invalidations": 0, "projection_misses": 0}
- x1 after_sv r1 warm2: wall=877.8ms terminal_plan=0 terminal_task=0 cache_hit=3 rebuild=2 index.last_run={"assembly": "reused", "mode": "incremental", "projection_hits": 501, "projection_invalidations": 0, "projection_misses": 0}
- x1 after_sv r1 warm3: wall=711.4ms terminal_plan=0 terminal_task=0 cache_hit=4 rebuild=2 index.last_run={"assembly": "reused", "mode": "incremental", "projection_hits": 501, "projection_invalidations": 0, "projection_misses": 0}
- x1 after_sv r1 warm4: wall=859.7ms terminal_plan=0 terminal_task=0 cache_hit=5 rebuild=2 index.last_run={"assembly": "reused", "mode": "incremental", "projection_hits": 501, "projection_invalidations": 0, "projection_misses": 0}
- x1 after_sv r1 warm5: wall=776.9ms terminal_plan=0 terminal_task=0 cache_hit=6 rebuild=2 index.last_run={"assembly": "reused", "mode": "incremental", "projection_hits": 501, "projection_invalidations": 0, "projection_misses": 0}
- x1 before_plain r1 cold: wall=5405.5ms terminal_plan=501 terminal_task=847 cache_hit=0 rebuild=2 index.last_run=None
- x1 before_plain r1 warm1: wall=2709.4ms terminal_plan=501 terminal_task=847 cache_hit=1 rebuild=3 index.last_run=None
- x1 before_plain r1 warm2: wall=2426.5ms terminal_plan=501 terminal_task=847 cache_hit=2 rebuild=4 index.last_run=None
- x1 before_plain r1 warm3: wall=2612.7ms terminal_plan=501 terminal_task=847 cache_hit=3 rebuild=5 index.last_run=None
- x1 before_plain r1 warm4: wall=2456.3ms terminal_plan=501 terminal_task=847 cache_hit=4 rebuild=6 index.last_run=None
- x1 before_plain r1 warm5: wall=2512.1ms terminal_plan=501 terminal_task=847 cache_hit=5 rebuild=7 index.last_run=None
- x2 after_plain r1 cold: wall=8888.5ms terminal_plan=1001 terminal_task=1693 cache_hit=0 rebuild=2 index.last_run={"assembly": "rebuilt", "mode": "incremental", "projection_hits": 0, "projection_invalidations": 0, "projection_misses": 1001}
- x2 after_plain r1 warm1: wall=1300.5ms terminal_plan=0 terminal_task=0 cache_hit=1 rebuild=2 index.last_run={"assembly": "reused", "mode": "incremental", "projection_hits": 1001, "projection_invalidations": 0, "projection_misses": 0}
- x2 after_plain r1 warm2: wall=1291.1ms terminal_plan=0 terminal_task=0 cache_hit=2 rebuild=2 index.last_run={"assembly": "reused", "mode": "incremental", "projection_hits": 1001, "projection_invalidations": 0, "projection_misses": 0}
- x2 after_plain r1 warm3: wall=1232.6ms terminal_plan=0 terminal_task=0 cache_hit=3 rebuild=2 index.last_run={"assembly": "reused", "mode": "incremental", "projection_hits": 1001, "projection_invalidations": 0, "projection_misses": 0}
- x2 after_plain r1 warm4: wall=1264.9ms terminal_plan=0 terminal_task=0 cache_hit=4 rebuild=2 index.last_run={"assembly": "reused", "mode": "incremental", "projection_hits": 1001, "projection_invalidations": 0, "projection_misses": 0}
- x2 after_plain r1 warm5: wall=1139.3ms terminal_plan=0 terminal_task=0 cache_hit=5 rebuild=2 index.last_run={"assembly": "reused", "mode": "incremental", "projection_hits": 1001, "projection_invalidations": 0, "projection_misses": 0}
- x2 after_sv r1 cold_first_after_validation_launch: wall=3006.1ms terminal_plan=None terminal_task=None cache_hit=None rebuild=None index.last_run=None
- x2 after_sv r1 first_after_validation_complete: wall=1207.9ms terminal_plan=0 terminal_task=0 cache_hit=1 rebuild=2 index.last_run={"assembly": "reused", "mode": "incremental", "projection_hits": 1001, "projection_invalidations": 0, "projection_misses": 0}
- x2 after_sv r1 warm1: wall=1206.2ms terminal_plan=0 terminal_task=0 cache_hit=2 rebuild=2 index.last_run={"assembly": "reused", "mode": "incremental", "projection_hits": 1001, "projection_invalidations": 0, "projection_misses": 0}
- x2 after_sv r1 warm2: wall=1219.9ms terminal_plan=0 terminal_task=0 cache_hit=3 rebuild=2 index.last_run={"assembly": "reused", "mode": "incremental", "projection_hits": 1001, "projection_invalidations": 0, "projection_misses": 0}
- x2 after_sv r1 warm3: wall=1263.3ms terminal_plan=0 terminal_task=0 cache_hit=4 rebuild=2 index.last_run={"assembly": "reused", "mode": "incremental", "projection_hits": 1001, "projection_invalidations": 0, "projection_misses": 0}
- x2 after_sv r1 warm4: wall=1214.7ms terminal_plan=0 terminal_task=0 cache_hit=5 rebuild=2 index.last_run={"assembly": "reused", "mode": "incremental", "projection_hits": 1001, "projection_invalidations": 0, "projection_misses": 0}
- x2 after_sv r1 warm5: wall=1217.7ms terminal_plan=0 terminal_task=0 cache_hit=6 rebuild=2 index.last_run={"assembly": "reused", "mode": "incremental", "projection_hits": 1001, "projection_invalidations": 0, "projection_misses": 0}
- x2 before_plain r1 cold: wall=8694.6ms terminal_plan=1001 terminal_task=1693 cache_hit=0 rebuild=2 index.last_run=None
- x2 before_plain r1 warm1: wall=3961.8ms terminal_plan=1001 terminal_task=1693 cache_hit=1 rebuild=3 index.last_run=None
- x2 before_plain r1 warm2: wall=4033.5ms terminal_plan=1001 terminal_task=1693 cache_hit=2 rebuild=4 index.last_run=None
- x2 before_plain r1 warm3: wall=4030.2ms terminal_plan=1001 terminal_task=1693 cache_hit=3 rebuild=5 index.last_run=None
- x2 before_plain r1 warm4: wall=4210.8ms terminal_plan=1001 terminal_task=1693 cache_hit=4 rebuild=6 index.last_run=None
- x2 before_plain r1 warm5: wall=4152.6ms terminal_plan=1001 terminal_task=1693 cache_hit=5 rebuild=7 index.last_run=None
- x4cap after_plain r1 cold: wall=10648.5ms terminal_plan=1226 terminal_task=2047 cache_hit=0 rebuild=2 index.last_run={"assembly": "rebuilt", "mode": "incremental", "projection_hits": 0, "projection_invalidations": 0, "projection_misses": 1226}
- x4cap after_plain r1 warm1: wall=1480.9ms terminal_plan=0 terminal_task=0 cache_hit=1 rebuild=2 index.last_run={"assembly": "reused", "mode": "incremental", "projection_hits": 1226, "projection_invalidations": 0, "projection_misses": 0}
- x4cap after_plain r1 warm2: wall=1504.6ms terminal_plan=0 terminal_task=0 cache_hit=2 rebuild=2 index.last_run={"assembly": "reused", "mode": "incremental", "projection_hits": 1226, "projection_invalidations": 0, "projection_misses": 0}
- x4cap after_plain r1 warm3: wall=1331.1ms terminal_plan=0 terminal_task=0 cache_hit=3 rebuild=2 index.last_run={"assembly": "reused", "mode": "incremental", "projection_hits": 1226, "projection_invalidations": 0, "projection_misses": 0}
- x4cap after_plain r1 warm4: wall=1428.0ms terminal_plan=0 terminal_task=0 cache_hit=4 rebuild=2 index.last_run={"assembly": "reused", "mode": "incremental", "projection_hits": 1226, "projection_invalidations": 0, "projection_misses": 0}
- x4cap after_plain r1 warm5: wall=1419.0ms terminal_plan=0 terminal_task=0 cache_hit=5 rebuild=2 index.last_run={"assembly": "reused", "mode": "incremental", "projection_hits": 1226, "projection_invalidations": 0, "projection_misses": 0}
- x4cap after_sv r1 cold_first_after_validation_launch: wall=3007.3ms terminal_plan=None terminal_task=None cache_hit=None rebuild=None index.last_run=None
- x4cap after_sv r1 first_after_validation_complete: wall=1503.1ms terminal_plan=0 terminal_task=0 cache_hit=1 rebuild=2 index.last_run={"assembly": "reused", "mode": "incremental", "projection_hits": 1226, "projection_invalidations": 0, "projection_misses": 0}
- x4cap after_sv r1 warm1: wall=1463.4ms terminal_plan=0 terminal_task=0 cache_hit=2 rebuild=2 index.last_run={"assembly": "reused", "mode": "incremental", "projection_hits": 1226, "projection_invalidations": 0, "projection_misses": 0}
- x4cap after_sv r1 warm2: wall=1452.3ms terminal_plan=0 terminal_task=0 cache_hit=3 rebuild=2 index.last_run={"assembly": "reused", "mode": "incremental", "projection_hits": 1226, "projection_invalidations": 0, "projection_misses": 0}
- x4cap after_sv r1 warm3: wall=1489.7ms terminal_plan=0 terminal_task=0 cache_hit=4 rebuild=2 index.last_run={"assembly": "reused", "mode": "incremental", "projection_hits": 1226, "projection_invalidations": 0, "projection_misses": 0}
- x4cap after_sv r1 warm4: wall=1395.1ms terminal_plan=0 terminal_task=0 cache_hit=5 rebuild=2 index.last_run={"assembly": "reused", "mode": "incremental", "projection_hits": 1226, "projection_invalidations": 0, "projection_misses": 0}
- x4cap after_sv r1 warm5: wall=1471.4ms terminal_plan=0 terminal_task=0 cache_hit=6 rebuild=2 index.last_run={"assembly": "reused", "mode": "incremental", "projection_hits": 1226, "projection_invalidations": 0, "projection_misses": 0}
- x4cap before_plain r1 cold: wall=10361.4ms terminal_plan=1226 terminal_task=2047 cache_hit=0 rebuild=2 index.last_run=None
- x4cap before_plain r1 warm1: wall=4881.1ms terminal_plan=1226 terminal_task=2047 cache_hit=1 rebuild=3 index.last_run=None
- x4cap before_plain r1 warm2: wall=4989.5ms terminal_plan=1226 terminal_task=2047 cache_hit=2 rebuild=4 index.last_run=None
- x4cap before_plain r1 warm3: wall=5281.6ms terminal_plan=1226 terminal_task=2047 cache_hit=3 rebuild=5 index.last_run=None
- x4cap before_plain r1 warm4: wall=5113.0ms terminal_plan=1226 terminal_task=2047 cache_hit=4 rebuild=6 index.last_run=None
- x4cap before_plain r1 warm5: wall=5356.6ms terminal_plan=1226 terminal_task=2047 cache_hit=5 rebuild=7 index.last_run=None

Startup historical validation (after_sv): launch_to_complete_ms per run, plans_validated:
- x1: 5246.2 ms (501 plans), 4745.3 ms (501 plans)
- x2: 7536.5 ms (1001 plans), 7748.9 ms (1001 plans)
- x4cap: 9509.3 ms (1226 plans), 9033.5 ms (1226 plans)
