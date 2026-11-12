# HAMCP-371 step S6: selftest and capture profile A against a disposable Core

Local evidence, scriptbox, 2026-11-12 (Central). Produced by
`scripts/core_read_replay_capture_local.py`, each launch through the host heavy
gate. Scope of this step as authorized: one `selftest`, then one
`run --profile A`. Profile B, the YAML read cases and everything that depends
on questions N31 and N32 were not run and are pending Josh.

Nothing here admits a release, binds a provider, changes a reviewed value or
writes to the registry. No protected path was edited.

## Launches

| Launch | Unit | Harness commit | Started - ended (Central) | Seconds | Result | Gate line before launch |
| --- | --- | --- | --- | --- | --- | --- |
| selftest-1 | `hamcp371-s6-selftest-1` | 6f10c4a2 | 00:20:52 - 00:21:20 | 28.4 | PASS | heavy_total=1 evidence=1 mem_avail_gb=10.0 load1=5.08 root_free_gb=48.5 |
| run-a-1 (profile A) | `hamcp371-s6-run-a-1` | 6f10c4a2 | 00:21:48 - 00:25:34 | 225.1 | PASS | heavy_total=1 evidence=1 mem_avail_gb=9.9 load1=5.68 root_free_gb=48.5 |

Two launches, one per command. No gate refusal, no preflight refusal, no
relaunch, no interruption. `launches.jsonl` is the harness launch log and
`gate/` holds the gate receipts.

PASS means what each receipt states under `pass_means`: the lane ran to its
end, every posture check held, nothing stored carries a token and cleanup is
proven. A call the peer answered with an error is a stored result, not a
failure.

One tracked change preceded the first launch and is disclosed here because it
moved the head: commit 6f10c4a2 makes the seed wait, with a bound, for the
automation entity that Core creates in a reload task of its own after it has
answered the configuration write. It was found by reading the Core 2026.11.0
source, not by a failed run. It was tested, checked against the unpolled form,
and committed before any S6 launch. No tracked file changed between the two
launches.

## Posture, as recorded in the receipts

- Images from the local cache by digest, `pull` = `never`. Core 2026.11.0,
  ha-mcp 9.1.2 and ha-mcp 9.2.0 at the digests bound in S4 (`bindings` and
  `upstream_images` in each receipt). Nothing was pulled and nothing was
  fetched from a package index.
- One disposable Core per launch, started by the launch, in the namespace of
  an idle holder container created with `--network none`. No Docker network
  was created and no port was published. The peer joins the same namespace,
  one version at a time.
- The no-egress probe (interfaces `lo` only, 0 IPv4 routes, connect errno 101)
  held from the holder and from Core before the seed, before each capture and
  after each Core restart.
- Tokens: two long-lived access tokens minted in the disposable Core (its
  owner and one ordinary user), held in memory and in the peer's environment
  by name, written to no file, gone with the Core. Whether the production
  token is of the same kind is recorded in the receipt as assumed, not
  established.
- Redaction (22:59 record, item 3): `redaction.setting` is `image defaults` in
  both receipts; the lane passes no redaction option or variable to the peer.
  The production add-on's setting was not read by any agent.
- Cleanup: containers absent, volume absent, 0 Docker networks created, in
  both receipts. Checked again afterwards from the host: no `hamcp371`
  container, volume or network remained.

## Secret scans

1. By the lane, while the tokens existed: exact bytes of each token and of its
   signature segment, 4 needles. selftest-1: 4 files, none removed. run-a-1:
   108 files, none removed. Recorded under `secret_scan` in each receipt.
2. Before this commit, a pattern scan (`comparison/scan.py`: JWT shape, bearer
   header, named token or password values, private-key header, any base64url
   run of 80 or more characters) over both complete run directories, the gate
   receipts, the launch log, the run logs and the comparison output: 122 files,
   0 hits. The scan was shown to fire on three synthetic positives first. Run
   again over this directory before commit.

## What is committed and what is not

Committed here, each byte-identical to the run directory (checked against the
run's own `SHA256SUMS`): both `receipt.json` files, both `SHA256SUMS`, the seed
plan and seed report of each launch, the four call indexes of run-a-1, the gate
receipts, the launch log, and the comparison.

Not committed: the 94 call files, the two `_initialize.json` files, the Core
log, the two peer logs and the empty holder log. They are run output under the
ignored `.artifacts/hamcp371-s6/` in the task worktree and are listed with
their hashes in `run-a-1/SHA256SUMS` (109 lines, all verified). Promoting
captures with provenance is step S7, which has not started. Until then they
exist only in scratch. They are captured from an upstream image and are data.

## Seed

All 15 seed steps `ok` in both launches. Read back: Core 2026.11.0, time zone
`America/Chicago`, 124 components, 4 areas, 1 floor, 1 label, 1 category,
1 helper, 1 automation (1 run, 1 trace), 1 scene, 1 script, 1 dashboard
resource, 1 calendar event, 1 to-do item, 2 persons, 1 zone, 1 imported
statistics row; 3 devices, 27 registry entities and 28 states in the final
read-back. `out/seed/seed-report.json` in each launch directory has the rest.

## Profile A: per-call comparison of 9.1.2 and 9.2.0

Order within the one run: seed; 9.1.2 capture (45 calls); Core restart; two
9.1.2 calls; 9.2.0 capture (45 calls); Core restart; two 9.2.0 calls. The wire
arguments for both versions were built with the 9.1.2 policy, since the 9.2
adapter does not exist yet, and are identical per call.

**Limit of the comparison.** The 9.2.0 capture ran after one Core restart and
about 100 seconds after the 9.1.2 capture. A difference is attributed below to
the version only where the values say so. No call was repeated on 9.1.2 after
the restart as a control, apart from the two after-restart calls.

On every one of the 47 calls, on both versions: HTTP 200, content type
`text/event-stream`, no JSON-RPC error object, the same `isError` value, a
`structuredContent` object on every success and none on every error, and the
text item equal to the structured content. `initialize` returned protocol
`2025-03-26` and the same capabilities on both; only `serverInfo.version`
differs. No session header was returned on either version
(`session_header_present` false in every index).

31 of 47 payloads are identical, 16 differ. `comparison/comparison.json` holds
the key paths and counts for every call (no captured values other than error
codes and `serverInfo`); `comparison/compare.py` reproduces it from the run
directory.

| Call | Upstream tool | isError | Body bytes 9.1.2 / 9.2.0 | Payload | Key paths only in 9.1.2 / only in 9.2.0 | Shared paths with different values |
| --- | --- | --- | --- | --- | --- | --- |
| p3-unknown-argument | `ha_get_state` | yes | 1054 / 1054 | identical | 0 / 0 | 0 |
| p3-wrong-type | `ha_search` | yes | 594 / 594 | identical | 0 / 0 | 0 |
| p4-absent-automation | `ha_config_get_automation` | yes | 498 / 498 | identical | 0 / 0 | 0 |
| p4-absent-entity | `ha_get_entity` | yes | 562 / 562 | identical | 0 / 0 | 0 |
| p4-absent-state | `ha_get_state` | yes | 693 / 693 | identical | 0 / 0 | 0 |
| r01-automation | `ha_config_get_automation` | no | 3264 / 3264 | identical | 0 / 0 | 0 |
| r02-calendar-events | `ha_config_get_calendar_events` | no | 1196 / 1196 | identical | 0 / 0 | 0 |
| r03-category | `ha_config_get_category` | no | 698 / 698 | identical | 0 / 0 | 0 |
| r04-label | `ha_config_get_label` | no | 620 / 620 | identical | 0 / 0 | 0 |
| r05-scene | `ha_config_get_scene` | no | 566 / 566 | identical | 0 / 0 | 0 |
| r06-script | `ha_config_get_script` | no | 672 / 672 | identical | 0 / 0 | 0 |
| r07-dashboard-resources | `ha_config_list_dashboard_resources` | no | 758 / 758 | identical | 0 / 0 | 0 |
| r08-groups | `ha_config_list_groups` | no | 1316 / 1316 | differs | 0 / 0 | 4 |
| r09-helpers | `ha_config_list_helpers` | no | 804 / 804 | identical | 0 / 0 | 0 |
| r10-template | `ha_eval_template` | no | 532 / 532 | identical | 0 / 0 | 0 |
| r11-automation-traces | `ha_get_automation_traces` | no | 1012 / 1012 | identical | 0 / 0 | 0 |
| r12-blueprints | `ha_manage_blueprints` | no | 1634 / 1634 | identical | 0 / 0 | 0 |
| r13-devices | `ha_get_device` | no | 2010 / 2010 | identical | 0 / 0 | 0 |
| r14-entity | `ha_get_entity` | no | 1308 / 1308 | identical | 0 / 0 | 0 |
| r15-entity-exposure | `ha_get_entity_exposure` | no | 582 / 582 | identical | 0 / 0 | 0 |
| r16-history | `ha_get_history` | no | 2998 / 2998 | identical | 0 / 0 | 0 |
| r17-overview | `ha_get_overview` | no | 2742 / 2818 | differs | 0 / 0 | 3 |
| r18-skill-guide | `ha_get_skill_guide` | no | 48021 / 48021 | differs | 0 / 0 | 1 |
| r19-state | `ha_get_state` | no | 1644 / 1582 | differs | 0 / 0 | 5 |
| r20-system-health | `ha_get_system_health` | no | 2380 / 2380 | differs | 0 / 0 | 2 |
| r21-todo | `ha_get_todo` | no | 790 / 790 | identical | 0 / 0 | 0 |
| r22-zones | `ha_get_zone` | no | 1234 / 1226 | differs | 1 / 0 | 0 |
| r23-floors-areas | `ha_list_floors_areas` | no | 2924 / 2924 | identical | 0 / 0 | 0 |
| r24-services | `ha_list_services` | no | 3466 / 3970 | differs | 15 / 17 | 4 |
| r25-search | `ha_search` | no | 1356 / 1356 | identical | 0 / 0 | 0 |
| r26-integrations | `ha_get_integration` | no | 11834 / 11834 | identical | 0 / 0 | 0 |
| r27-apps | `ha_get_app` | yes | 698 / 698 | identical | 0 / 0 | 0 |
| t-after-idle | `ha_get_entity` | no | 1308 / 1308 | identical | 0 / 0 | 0 |
| t-large-services | `ha_list_services` | no | 36672 / 42952 | differs | 213 / 91 | 142 |
| t-new-session | `ha_get_state` | no | 1644 / 1582 | differs | 0 / 0 | 5 |
| v16-history-offset-free | `ha_get_history` | no | 2028 / 2998 | differs | 0 / 3 | 7 |
| v16-history-relative | `ha_get_history` | no | 3054 / 3054 | differs | 0 / 0 | 4 |
| v16-history-statistics | `ha_get_history` | no | 2280 / 2280 | identical | 0 / 0 | 0 |
| v17-overview-standard | `ha_get_overview` | no | 6062 / 6106 | differs | 1 / 1 | 9 |
| v21-todo-list | `ha_get_todo` | no | 640 / 640 | identical | 0 / 0 | 0 |
| v22-zone-single | `ha_get_zone` | no | 664 / 736 | differs | 0 / 2 | 0 |
| v24-services-domain-full | `ha_list_services` | no | 1950 / 2322 | differs | 0 / 0 | 8 |
| v24-services-domain-summary | `ha_list_services` | no | 1852 / 2224 | differs | 0 / 0 | 8 |
| v24-services-response | `ha_list_services` | no | 4996 / 5634 | differs | 22 / 2 | 21 |
| v25-search-text | `ha_search` | no | 4194 / 4194 | identical | 0 / 0 | 0 |
| a-entity-after-core-restart | `ha_get_entity` | no | 1308 / 1308 | identical | 0 / 0 | 0 |
| a-floors-areas-after-core-restart | `ha_list_floors_areas` | no | 2924 / 2924 | identical | 0 / 0 | 0 |

### The 16 differing calls

Differences that follow the version:

- `r22-zones`, `v22-zone-single` (`ha_get_zone`). 9.1.2 returns
  `friendly_name` on the `zone.home` row only, and `device_trackers` and
  `persons` on that row only. 9.2.0 returns no `friendly_name` on any row and
  `device_trackers` and `persons` on every row, including the storage zone.
  Item 15 of the 21:36 record ("restore friendly_name on 9.2.0 zone rows from
  name") addresses this; on 9.1.2 the storage zone row had no `friendly_name`
  either.
- `v24-services-response`, `t-large-services`, `v24-services-domain-full`,
  `v24-services-domain-summary`, `r24-services` (`ha_list_services`). In full
  detail 9.1.2 returns `example`, `required` and `type` on every field
  (`example` is null on 14 of the 44 fields in the 50-service page). 9.2.0
  returns no `type` on any field, and `required` and `example` on some fields
  only (26 and 25 of 39 in its 50-service page). 9.2.0 adds a service-level
  `response` key, seen as `{"optional": false}` on `calendar.get_events` and
  on `recorder.get_statistics`; 9.1.2 has none. Every service `description`
  is empty on 9.1.2 and none is on 9.2.0, and `name` values differ (for
  example "Reload" against "Reload input booleans"). Items 13 and 15 of the
  21:36 record address the `response` key and `type`.
  Not attributed within these calls: the pages do not hold the same services.
  The page of 50 shares 45 names (five `media_player` services on 9.1.2,
  five `scene` services on 9.2.0), and the page of five in summary mode
  shares two. Both pages are in sorted order and both report 247 services in
  62 domains. Whether page membership follows the version or the Core
  restart was not determined.
- `v16-history-offset-free` (`ha_get_history`, start and end without an
  offset). 9.1.2 reads the values as UTC (period echoed with `+00:00`) and
  returns 0 states. 9.2.0 reads them as Core local time (period echoed with
  `-06:00`) and returns 4 states; its payload equals the 9.2.0 payload of the
  explicit-offset call `r16-history`. This is the behaviour items 5 and 6 of
  the 21:36 record refuse on 9.2.0. `r16-history` and
  `v16-history-statistics` are identical across versions.
- `r18-skill-guide` (`ha_get_skill_guide`). One line of 165 differs: an
  acknowledgment key whose suffix differs by version. Same length otherwise.

Differences that follow the clock or the Core restart, not shown to follow the
version:

- `v16-history-relative`: only the echoed period, which is relative to the
  time of the call.
- `r19-state`, `t-new-session` (`ha_get_state`), `r08-groups`: context id and
  the three timestamps; `context.user_id` is set before the restart and null
  after it. The 9.2.0 timestamps are the time of the Core restart.
- `r20-system-health`: the recorder run start and the estimated database size.
- `r17-overview`: the one repair issue of the `http` domain has a different
  `issue_id` and creation time after the restart.

Not attributed:

- `v17-overview-standard` (`ha_get_overview`). Which entity is listed for a
  domain and which domain is marked truncated differ (`group` lists its entity
  on 9.1.2 and `scene` does not; the reverse on 9.2.0; a different zone is
  listed; the fifth most common domain differs). Counts per domain are equal.
  The repair-issue difference of `r17-overview` appears here as well.

### Transport and socket observations (both versions alike)

- Largest body: `r18-skill-guide`, 48021 bytes. `t-large-services`: 36672
  bytes on 9.1.2, 42952 on 9.2.0.
- `t-after-idle`: one call on the same session after a 75 second wait,
  answered normally. `t-new-session`: a second session, answered normally.
- After each Core restart the next two calls succeeded with no exchange
  failure.
- Peer log, each version: 5 lines "WebSocket connected and authenticated
  successfully" to `ws://127.0.0.1:8123/api/websocket` and 4 lines "WebSocket
  disconnected". The fuller socket record (P9) belongs to step S12 and was not
  written here.

## Error shapes observed (for question N31; observation only)

Nothing was written to the registry and no source for the registry capture's
error shapes was chosen. What a real Core gave through each peer:

All six error results are identical between 9.1.2 and 9.2.0, payload for
payload. Each is an HTTP 200 JSON-RPC result with `isError` true, a JSON text
item, no `structuredContent`, and a body of the form
`{"success": false, "error": {"code": "<string>", ...}}`. No JSON-RPC error
object (no -32602, no -32603) was returned on any call.

| Call | Tool | Forced by | `error.code` | `error` keys | Other top-level keys |
| --- | --- | --- | --- | --- | --- |
| p3-unknown-argument | `ha_get_state` | an argument the tool does not declare | `VALIDATION_FAILED` | code, details, message, suggestion | valid_parameters |
| p3-wrong-type | `ha_search` | an argument of the wrong type | `VALIDATION_FAILED` | code, details, message, suggestion, suggestions | none |
| p4-absent-automation | `ha_config_get_automation` | an identifier that was not seeded | `RESOURCE_NOT_FOUND` | code, message, suggestion | automation_id, available_automation_ids |
| p4-absent-entity | `ha_get_entity` | an entity that was not seeded | `SERVICE_CALL_FAILED` | code, message, suggestion, suggestions | entity_id |
| p4-absent-state | `ha_get_state` | an entity that was not seeded | `ENTITY_NOT_FOUND` | code, details, message, suggestion, suggestions | entity_id |
| r27-apps | `ha_get_app` | the disposable Core has no Supervisor | `SERVICE_CALL_FAILED` | code, message, suggestion, suggestions | endpoint, operation, timeout_seconds |

This agrees with item 4 of the 23:11 record for the validation case
(`VALIDATION_FAILED` as a string code inside an `isError` result on both
versions). `VALIDATION_INVALID_JSON` was not forced and was not observed.

## Disclosures

- Not exercised in this step, as each receipt lists under `not_built`: the
  profile B overlay (refused at preflight until N32 is answered), the
  Supervisor facade, and the token-revoked form of the failing read (P4); only
  the unknown-identifier forms of P4 ran. P8 needs profile B and did not run.
- `ha_get_app` is an error on both versions because nothing stands in for a
  Supervisor. It is the sixth counted error, not a lane failure.
- Core's onboarding step starts configuration flows of its own. The Core
  therefore held config entries the seed did not create (among them
  `radio_browser`, `analytics` and `marketplace`), and the setup of those
  three failed on every Core start in the namespace without a route. Core log
  totals: selftest-1 6 ERROR and 4 WARNING lines; run-a-1 11 ERROR and 7
  WARNING lines (three starts), which include one `system_health` "Error
  fetching info" line per capture. The egress probes held throughout. The Core
  log is not committed.
- Each peer log has two ERROR lines, both the expected upstream side of a
  forced or Supervisor-less call (`supervisor/api` unknown command; entity not
  found).
- A failed transient unit record from S4 (`hamcp371-s4-selftest.service`)
  remains on the host and was left alone.
