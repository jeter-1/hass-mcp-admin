# HAMCP-371: capabilities lost moving the add-on from ha-mcp 9.1.2 to 9.2.0

Plan list B item 21. This lists what a client could do through Engineering on
ha-mcp 9.1.2 and cannot do on 9.2.0, as the HAMCP-371 branch implements it.
Each line cites the test or receipt that pins it. It is a local candidate
record: nothing here was observed on the production add-on, and no Home
Assistant instance was accessed.

Paths: `P` = `hass_mcp_engineering_beta/ha_mcp_engineering`.

## 1. Unbound write providers

**None.** The four list B providers (power, fan, helper value, dashboard
setter) are bound on 9.2.0 by their release rows; see `sb/README.md`.

- Power, fan, helper value: `tests/test_hamcp371_write_providers_9_2.py`
  (`ReleaseRows.test_920_catalog_binds_each_provider`,
  `Public920.test_920_peer_succeeds_verified_with_one_exact_write`,
  `Power920Tests`, `Helper920Tests`).
- Dashboard: `tests/test_hamcp371_admission_core.py` `DashboardAdmissionTests`.

## 2. Reads held on 9.2.0

**None beyond 9.1.2.** `P/upstream_tool_policy_9_2_0.json` and
`upstream_tool_policy_9_1_2.json` have the same classification for every tool
(23 `automatic_read`, 14 `mixed_or_requires_wrapper`, 1 `held_for_canary`,
2 `unsupported`, 1 `prohibited`, 4 `physical_or_high_risk_action`,
32 `persistent_write`; compared tool by tool). Both have 27 automatic reads
(`tests/test_upstream_release_registry.py` `expected_automatic_reads`). The
provider dispositions are equal: backup and lifecycle stay held on both
(`tests/test_hamcp184_provider_dispositions_fail_closed.py`
`REVIEWED_AFTER_HAMCP184`; `test_hamcp371_admission_core.py`
`test_provider_dispositions_equal_912`).

## 3. Keys passed on 9.1.2 and withheld on 9.2.0

The 9.1 projections pass these names on 9.1.2. On 9.2.0, E1 (13:25 ruling)
withdrew them because no 9.2.0 receipt carries them; they are withheld and
counted in `fields_withheld` (S10 README, sections E1 and E1-R). The E1-R item
1 decision to keep every withdrawal is an agent choice, disclosed there.

| Read | Key | 9.2.0 behaviour | Pinned by |
| --- | --- | --- | --- |
| `ha_config_list_groups` | `all` | withheld and counted; Engineering's recomputed `unavailable_fields` reports `all` | contract row; `test_hamcp371_key_sets.py` `RowsEqualTheContract.test_rows` |
| `ha_config_list_groups` | `unavailable_fields` (upstream) | withheld and counted; Engineering recomputes its own | same |
| `ha_eval_template` | `unavailable_fields` (upstream) | withheld and counted; Engineering recomputes its own | same |
| `ha_get_entity`, `ha_get_entity_exposure` | `warnings`, `partial` | withheld and counted; no longer marks the read partial | same |
| `ha_get_todo`, `ha_get_zone`, `ha_list_floors_areas` | `partial` | withheld and counted | same |
| `ha_get_todo`, `ha_get_zone`, `ha_list_floors_areas`, `ha_get_overview`, `ha_get_state` | `warnings` | HAMCP-464 (ruling 0132-01): admitted with no reviewed text; each upstream text is replaced with the fixed code and counted in `upstream_warnings_replaced`, and the read reports partial with `UPSTREAM_WARNINGS` | same; `test_hamcp464_warnings_are_replaced_not_withheld`; `test_hamcp319_parity_entities.py` `EntitiesUpstreamWarnings92Tests` |
| `ha_list_services` | `partial` | withheld and counted | same |
| `ha_get_overview` | `ha_mcp_update.python_required`, `ha_mcp_update.upgrade_command` | dropped by policy and counted in `fields_dropped_by_policy` (ruling 2d) | `test_hamcp371_overview_search_9_2.py` (S11 README) |

Only the HAMCP-464 `warnings` row has direct behaviour tests; the others are
pinned through the contract by `test_rows` and the generator `--check`.

Not losses, recorded for completeness:

- Keys new in 9.2.0 and withheld pending review (they never passed on 9.1.2):
  `ha_list_services` `filter`; `read_only`, `secret`, `later_forms` on
  `ha_config_list_helpers` and `ha_get_integration`
  (`test_hamcp371_real_core_replay.py` `test_unreviewed_9_2_0_keys_stay_withheld`).
- Keys upstream removed in 9.2.0: `ha_list_services` `type`, `ha_get_zone`
  `friendly_name` (`test_recorded_removals`). Upstream no longer sends them.
- `ha_get_system_health`: the 18 names the shipped rebuild drops are dropped
  as on 9.1.2; 9.2.0 adds only their count in `fields_withheld`
  (`test_hamcp371_platform_9_2.py` `test_r20_counts_the_names_the_rebuild_drops`).
- `ha_get_blueprint`: on 9.2.0 a top-level key under `data` outside the
  reviewed 9.2.0 set is refused (D5-B); on 9.1.2 it passes. Every key in the
  9.2.0 receipts and in the source-review get-mode set passes
  (`test_hamcp371_blueprint_9_2.py` `test_list_keys_equal_every_9_2_0_receipt`,
  `test_any_other_key_under_data_is_refused`). No observed key is lost; an
  unobserved upstream key would now refuse the read instead of passing.

## 4. Request forms accepted on 9.1.2 and refused on 9.2.0

`ha_get_history` `start_time` and `end_time` (S9). On 9.1.2 these are
forwarded and upstream reads them as UTC. On 9.2.0 they are refused before
dispatch with `invalid_request`, `details.failure_category`
`history_time_offset_required`, because 9.2.0 reads them in Home Assistant
local time:

- an ISO date and time with no offset;
- a date-only value;
- other forms `datetime.fromisoformat` accepts but the reviewed pattern does not
  (`+0000`, a space separator, the basic format, 7 fraction digits).

Forwarding on 9.1.2 is tested for offset-free values only (S9 README); how
9.1.2 handled the other forms in the last bullet is not separately tested
here, so they are listed as refused on 9.2.0, not as proven 9.1.2 successes.

Pinned by `tests/test_hamcp371_history_offsets.py`
(`test_offset_free_and_date_only_times_need_an_offset`,
`test_offset_free_time_refuses_before_dispatch_with_the_fixed_category`) and
`tests/test_hamcp371_admission_core.py`
`test_920_offset_free_history_refuses_with_no_upstream_call`. Receipt:
`v16-history-offset-free` echoes `-06:00` on 9.2.0 and `+00:00` on 9.1.2
(S9 README). Offset-qualified and relative (`24h`) forms still work.

## 5. To be filled in by Josh

| Item | Value |
| --- | --- |
| Production add-on redaction setting at upgrade time | _(unfilled; Josh fills it in; no agent reads the production add-on)_ |
| Capture runs' setting, for comparison | image defaults: the lane passed no redaction option or variable to the peer (`redaction.setting` `image defaults` in the S6 receipts; S6 README, 22:59 record item 3) |

If the production setting differs from the image default, the 9.2.0 receipts
and key sets above may not match what the production add-on returns.
