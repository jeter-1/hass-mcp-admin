# HAMCP-371 step S6: selftest and capture profile A against a disposable Core

Local evidence, scriptbox, 2026-11-12 (Central). Produced by
`scripts/core_read_replay_capture_local.py`, each launch through the host heavy
gate. Scope of this step as authorized: one `selftest`, then one
`run --profile A`. Profile B, the YAML read cases and everything that depends
on questions N31 and N32 were not run and are pending Josh.

2026-11-14 addition: one `recapture` launch for R1 and R2 of the 16:15 rulings
(profile A only), recorded under **Recapture for R1 and R2** below.

Nothing here admits a release, binds a provider, changes a reviewed value or
writes to the registry. No protected path was edited.

## Launches

| Launch | Unit | Harness commit | Started - ended (Central) | Seconds | Result | Gate line before launch |
| --- | --- | --- | --- | --- | --- | --- |
| selftest-1 | `hamcp371-s6-selftest-1` | 6f10c4a2 | 00:20:52 - 00:21:20 | 28.4 | PASS | heavy_total=1 evidence=1 mem_avail_gb=10.0 load1=5.08 root_free_gb=48.5 |
| run-a-1 (profile A) | `hamcp371-s6-run-a-1` | 6f10c4a2 | 00:21:48 - 00:25:34 | 225.1 | PASS | heavy_total=1 evidence=1 mem_avail_gb=9.9 load1=5.68 root_free_gb=48.5 |
| recapture-1 (profile A), 2026-11-14 | `hamcp371-s6-recapture-1` | 3bacd06f | 16:20:12 - 16:23:30 | 197.8 | PASS | heavy_total=0 evidence=0 mem_avail_gb=12.4 load1=0.04 root_free_gb=60.4 |

Two launches on 2026-11-12, one per command, and one on 2026-11-14. No gate refusal, no preflight refusal, no
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
  *Corrected 2026-11-14:* "in sorted order" was read from the stored call
  files, which are written with sorted keys. On the wire the pages are in
  Core's catalogue order, not sorted. The recapture attributes the membership
  difference to the Core start, not to the version (see **Recapture for R1
  and R2**).
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
  *2026-11-14:* the recapture reproduces this exact difference across one Core
  restart on each version and shows none between the versions on one Core
  start. It follows the Core start (see **Recapture for R1 and R2**).

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
The 2026-11-14 recapture tried to force it (R1); it was still not observed.

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

## Recapture for R1 and R2 (2026-11-14)

Asked by the 16:15 rulings of 2026-11-14:

- **R1:** force `VALIDATION_INVALID_JSON` with both versions in one run, or
  record it as not observed.
- **R2:** run 9.1.2 and 9.2.0 profile A back to back on one disposable Core
  2026.11.0, after one restart, in the same run. Compare the `ha_get_overview`
  per-domain listing and the `ha_list_services` page order. State whether the
  services comparison is order-sensitive, and whether page order is stable
  across that restart.

### The run

Launch `recapture-1`: harness 3bacd06f (the F1 commit), profile A, one launch,
PASS. Posture as above:

- images from the local cache by digest, nothing pulled;
- the holder runs with `--network none`;
- the egress probe held before every capture and after the restart.

Cleanup is clean. The lane's exact-token scan covered 128 files and removed
none. All 15 seed steps were `ok`.

The design, as the receipt records it under `captures.design`:

1. Seed once.
2. Run the 5 `order` cases on 9.1.2, then on 9.2.0, one peer at a time.
3. Restart Core once.
4. Run the 51 calls of the `reads`, `variants`, `errors`, `transport`, `forced`
   and `order` groups on 9.1.2, then on 9.2.0.

Before the restart, each version made 5 calls with no errors. After it, each
made 51 calls: 7 `isError` and 0 exchange failures.

`comparison/recapture.py` reproduces `comparison/recapture.json` from the run
directory. It reads list and object order from the text item of each result,
because that is the only wire-order copy in a stored call file. The call files
are written with sorted keys, so their `structuredContent` is in storage order.
On every success, the parsed text item equals `structuredContent` as a value.

### R1: `VALIDATION_INVALID_JSON` not observed

The forced case `f-invalid-json` calls `ha_call_service` with `data` set to a
string that is not JSON. The domain and service it names do not exist.

Both versions return the same 660-byte response (sha256 `ea8e326e66f8d61f...`):
an `isError` result with code `VALIDATION_FAILED`, details `value_error` and
the message "`data`: Value error, Invalid JSON at line 1 column 2".

`VALIDATION_INVALID_JSON` was not observed on either version. It is recorded
as not observed, and the case was not re-run.

From reading the source (an inference, not a run), argument coercion rejects
the value before the tool body runs. The `VALIDATION_INVALID_JSON` sites are
in the control, write and settings paths, and in the coercion of the proxy's
`arguments` string.

### R2: the differences follow the Core start, not the version

On the same Core start, 9.1.2 and 9.2.0 are equal on everything R2 names:

- **`ha_get_overview`:** the per-domain listing, domain order,
  `most_common_domains` and pagination, in `o-overview-standard`,
  `r17-overview` and `v17-overview-standard`.
- **`ha_list_services`:** every page is in the same order with the same
  `total_count` (247). That covers `o-services-first-five`,
  `o-services-default-page`, `o-services-page-1` (200 services),
  `o-services-page-2` (47), `r24-services`, `t-large-services` and the three
  `v24-*` calls.

This holds before the restart (the 5 `order` cases) and after it (all 12
listing calls). The catalogue order hash over pages 1 and 2 is the same on both
versions: `1abd5ad1...` before the restart and `c0211b37...` after it.

Across the one restart, the same calls change, and alike on both versions.

**Overview.** Per-domain counts are equal, but the listing changes:

- `group` lists its entity before the restart and is truncated after it;
  `scene` does the reverse.
- A different zone is listed.
- The fifth entry of `most_common_domains` changes from `group` to `scene`.
- The domain order changes.

This is the 2026-11-12 difference between 9.1.2 and 9.2.0 in
`v17-overview-standard`, so that difference follows the Core start.

**Services.** The same 247 services in 62 domains, in a different order:

- 50 of the 62 domain positions moved.
- The page of five keeps 2 of its 5 names.
- The 50-service page keeps all 50 names, in another order.
- Pages 1 and 2 change membership.

This accounts for the 2026-11-12 difference in page membership.

**Is the `ha_list_services` comparison order-sensitive?** Yes. On the wire,
every page is an exact slice of Core's catalogue order:

- The page of five and the page of 50 are the first 5 and the first 50
  services of pages 1 and 2 joined.
- Each domain forms one contiguous run.
- The domain order is not alphabetical, and within a domain the services are
  not all in name order.

So page membership and order are valid only within one Core start. Only the
full set of 247 services is order-insensitive.

This matches the upstream source at both tags (a reading, not a run).
`tools_services.py` slices `list(services.keys())` by `offset` and `limit` and
sorts only the `domains` list: lines 499-505 at 51e3aac7, and 452-458 at
bab615df.

**Is page order stable across that restart?** No, on either version.

### Per call after the restart

37 of the 51 payloads are identical across the versions, and every call sent
the same wire arguments. 14 payloads differ.

13 of the 14 follow the version, as recorded for 2026-11-12:

- the content of the eight services pages: `name`, `description` and
  `response`, plus `fields` in full detail;
- `r22-zones` and `v22-zone-single`;
- `v16-history-offset-free`;
- `r18-skill-guide`.

The fourteenth, `v16-history-relative`, follows the clock.

Six calls that differed on 2026-11-12 are identical when both versions run on
one Core start: `r08-groups`, `r17-overview`, `r19-state`, `t-new-session`,
`r20-system-health` and `v17-overview-standard`. This confirms the 2026-11-12
attribution of those calls to the clock or the Core restart.

### Recapture disclosures

**Committed.** Each of these is byte-identical to the run directory:

- `recapture-1/receipt.json` and its `SHA256SUMS` (129 lines, all verified);
- the seed plan and the seed report;
- the four call indexes.

The gate receipt, the launch log and the comparison are committed too. The
launch log's first two lines are unchanged.

**Not committed.** The 112 call files, the four `_initialize.json` files and
the logs stay in ignored scratch until S7.

**The gate receipt.** `gate/recapture-1.txt` differs from the gate's own
receipt only in trailing spaces on three lines. They were stripped so that no
new trailing-whitespace line enters the tree:

- the gate's own receipt: sha256 `734ea162...`;
- the committed copy: sha256 `de85d2f5...`.

`gate/run-a-1.txt` and `gate/selftest-1.txt` are unchanged. (They were
unchanged at this recapture. Later, F3 commit e9bfa4db stripped their trailing
spaces; neither is bound by a SHA256SUMS file.)

**Pattern scan.** Before the commit, `comparison/scan.py` ran over the full run
directory, the gate receipt, the launch log, the comparison output and the
staged copy: 145 files, 0 hits.

**Logs.**

- The Core log has 8 ERROR and 6 WARNING lines over its two starts. As on
  2026-11-12, they come from Core's onboarding flows (Core has no route) and
  from `system_health`.
- Each after-restart peer log has three ERROR lines: two for `ha_get_app`
  (there is no Supervisor) and one for the entity that was not seeded. The
  before-restart peer logs have none.

**Not built.** The receipt's `not_built` list is unchanged: profile B, the
Supervisor facade and token revocation.

## Profile B on 9.2.0 (2026-11-14)

Asked by C4 and N32 (16:15 rulings): build profile B within N32 and capture
the 11 component-dependent reads on 9.2.0 with component 9.2.0.

### The run

Launch `run-b-9.2.0-1`: harness e9bfa4db, profile B, version 9.2.0, one
launch, PASS in 124 seconds. The posture is as for profile A. Cleanup is clean.
The lane's exact-token scan covered 65 files with 4 needles and removed none.
All seed steps were `ok`.

**Component.** The component comes from upstream tag `v9.2.0`, commit
`51e3aac788482a081807591cd5d58d07dc4e540b`, read by git plumbing with lazy
fetch off: 108 files, 3,689,155 bytes. The receipt records the tag object, the
root tree, the component tree and the sha256 of every file. It is placed in
`/config/custom_components`. No HACS, no pip and no fetch by Core or by the
lane.

**Requirements (N32).** Core resolved the four requirements of the 9.2.0
manifest as follows:

- `mcp>=1.24.0` from the Core image (2.3.0);
- `ruamel.yaml>=0.18.0` from the N32 wheel 0.19.1;
- `voluptuous-openapi>=0.4.1` from the N32 wheel 0.4.1;
- `ha-mcp==9.2.0` from the pinned 9.2.0 image's package. That container is
  created, never started.

The receipt records the wheel sha256s and sources. Core asked for none of the
other ten requirements. All three overlay packages load from
`/opt/hamcp371-overlay`, which is on `PYTHONPATH` in Core only.

**Package copy.** Both images carry one symbolic link, `settings_ui/CLAUDE.md`
-> `AGENTS.md`. It is recorded under `symbolic_links_not_copied`, and it is
neither copied nor counted. Any other link refuses (0308eb73).

**Phases.** Both phases, after setup and after the Core restart, show:

- `check_exit` 0;
- the entry `ha_mcp_tools` loaded;
- `info` reporting `component_version` 9.2.0;
- no refusal in the Core log.

**Calls.** There are 47 calls before the restart, with 8 `isError` and no
exchange failure, and 2 calls after it. Of the 27 component reads, 22 succeed.
The 5 errors:

- three are the designed error cases: `p3-wrong-type`,
  `p3-unknown-argument` and `p4-absent-state`;
- two are `y-group` and `y-absent-key`. They return `Unknown tool:
  'ha_config_get_yaml'` for both the administrator and the ordinary-user token.

**The YAML read is not registered.** Upstream registers `ha_config_get_yaml`
only when `HAMCP_ENABLE_FILESYSTEM_TOOLS` is true (default False). This holds at
both 9.1.2 and 9.2.0: `tools/tools_yaml_read.py:644-654`,
`config_settings.py:678-680`. The lane passes no feature flag, so the peer runs
on image defaults. The N29 conditions on the YAML read are therefore **not
evaluated** by this run. Setting the flag needs a ruling.

**Committed:**

- `run-b-9.2.0-1/receipt.json` and its `SHA256SUMS` (66 lines; the committed
  files verify);
- the seed plan and the seed report;
- the three call indexes;
- the two profile B phase records.

Also committed: the gate receipt, with trailing spaces stripped from three
lines (the gate's own receipt is sha256 `2c06d218...`, the committed copy
`cf9494d2...`), and the launch log's fourth line.

**Not committed.** The call files, the `_initialize.json` files and the logs
stay in ignored scratch.

**Scans.** Before the copy, `comparison/scan.py` covered 67 files with 0 hits,
and the leak check passed. Over the staged copy, the scan covered 11 files with
0 hits.

**Logs.**

- The Core log has 7 ERROR and 7 WARNING lines over its two starts.
- The peer log has two WARNING lines (`Invalid arguments for tool`), one for
  each designed argument error.
- The ordinary-user peer log has none.

**Not done.** As the receipt records:

- no server entry of the component is created;
- the component 2.2.1 / ha-mcp 8.5.0 control belongs to SR;
- 9.1.2 profile B has not run.
