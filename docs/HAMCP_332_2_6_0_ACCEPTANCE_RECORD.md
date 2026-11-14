# 2.6.0 acceptance record (Engineering 2.4.2-beta.8)

Release 2.6.0 is Engineering 2.4.2-beta.8 (HAMCP-332). This record states
the live acceptance results. The procedure is
[`runbooks/DEPLOY-2.4.2-BETA.7-TO-BETA.8.md`](runbooks/DEPLOY-2.4.2-BETA.7-TO-BETA.8.md)
(Phases 5, 8 and 9); the acceptance criteria are
[`V2_4_2_BETA8_ACCEPTANCE.md`](V2_4_2_BETA8_ACCEPTANCE.md).

## Source and scope

- Source: the architect's acceptance record of about 09:55 CST on
  2026-11-14, relayed by Josh. The results below restate it. This
  document adds no observation of its own; it was written without access
  to Home Assistant or the deployed endpoint.
- Release: tag `v2.4.2-beta.8` at commit `00ab812b`.
- Core release registry: sequence 18, signed on registry commit
  `917bfa2a`, from the candidate on `registry-candidates/2026.11.0-seq18`
  (commit `95763112`). Sequence 18 carries 38 references on Core
  2026.11.0 (32 retained, 6 new).
- Home Assistant Core: 2026.11.0.
- Every mutating step had its own approval from Josh.

## Summary

| Phase | Result |
| --- | --- |
| 5 Refresh clients | Blocked by a Claude client-side connector-loading issue (fresh chats never contact Engineering); C1 is a separate spec gap |
| 8 Activation check | PASS |
| 9 Live acceptance | 8 PASS, 1 PARTIAL (9.4) |
| Post-acceptance live regression | 4/4 PASS |

## Phase 5: refresh clients

Blocked, not failed. A brand-new Claude chat did not load the connector;
the Engineering gateway log shows that chat never contacted Engineering.
This is a Claude client-side connector-loading issue. C1 (the
`server/discover` 400 spec gap, HAMCP-384) is a separate issue; the legacy
fallback works. An existing chat saw the new tools after an app restart.

## Phase 8: activation check

PASS. Sequence 18 was accepted after Josh restarted the add-on at about
13:44 UTC (07:44 CST) on 2026-11-14: 38 compatible, 0 withheld; 114 tools.

## Phase 9: live acceptance

| Step | Result | Detail |
| --- | --- | --- |
| 9.1 Topology | PASS | |
| 9.2 Marketplace | PASS | Josh confirmed HACS 15 |
| 9.3 Security metadata | PASS | Josh confirmed 6 people |
| 9.4 Scene create and undo | PARTIAL | The create ended `succeeded_verified`, with `created_identity` captured. The rollback `a86acbae` ended `failed_pre_dispatch` with `core_authority_changed_since_plan` and could not be re-planned. Josh deleted the scene in the Home Assistant UI |
| 9.5 Helper create and delete | PASS | |
| 9.6 Automation create rollback | PASS | Criterion revised to "enabled but inert": the governed create rejects `initial_state` |
| 9.7 Target-kind refusals | PASS | (a) `device_configuration_not_config_category`; (c) `device_configuration_target_kind_not_admitted`; (b) not run separately, covered by (a)'s config-category check |
| 9.8 Device configuration | PASS | Garage camera plug power-on behavior `previous` → `on` → `previous` via `undo`, both `succeeded_verified`; the main switch never changed |
| 9.9 Final readback | PASS | No pending plan, no non-terminal task, no hold; all test objects absent |

## Post-acceptance live regression

4/4 PASS on 2.4.2-beta.8:

| Tool | Target | Result |
| --- | --- | --- |
| `control_power` | `light.front_guest_room_lamp` | Change and restore, each `succeeded_verified`, original end state |
| `control_helper` | `input_boolean.mcp_beta22_smoke_flag` | Change and restore, each `succeeded_verified`, original end state |
| `control_fan` | `fan.living_room_living_room_fan_ceiling_fan` | Change and restore, each `succeeded_verified`, original end state |
| Governed automation update and revert | `automation.engineering_delete_canary` | `succeeded_verified` both ways; config hash back to `824ba93721964c1b` |

## Rollback gate G6

G6 not proven by tests; safe only under restore-first; never downgrade in
place. G6 is accepted for 2.6 by restore-first as a scoped waiver of the
signing plan's §7.3 proving tests 1 and 2 and of the N4 items, not as a
pass (Josh, 2026-11-14). See the runbook's rollback section.

## Follow-ups (2.7 intake)

- HAMCP-391 (from 9.4): `rollback_change` returns the existing rollback
  plan whatever its state, so a failed rollback cannot be superseded.
- HAMCP-392 (from 9.4): Core authority retired mid-approval with no
  recorded reason.
- HAMCP-393: the `created_identity` outcome and the assigned identifiers
  are not surfaced by `get_change_plan` or `get_execution_task`.
- HAMCP-394: every governed delete is elevated while dependency coverage
  is incomplete.
- HAMCP-395: read tools do not expose `entity_category`;
  device-configuration refusals carry the reason only in
  `details.category`; the meaning of `active_commit_count` is
  undocumented.
