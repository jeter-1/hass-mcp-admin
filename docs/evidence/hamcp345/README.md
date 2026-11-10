# HAMCP-345: dashboard risk breadth

Approved outcome (register HAMCP-345, Josh, 2026-11-08): dashboard plans
classify valve controls (for example a water main shutoff) and group rows
whose members include lock, cover, valve or alarm entities as
`high_consequence_action` with elevated review; controls rendered by
dashboard strategies or by `show_header_toggle` are analyzed or the plan
discloses that they cannot be; light/switch default-toggle and display-only
count residuals are documented.

The change is analysis and classification only, in
`hass_mcp_engineering_beta/ha_mcp_engineering/f3_dashboard/risk.py`. It adds
no write, service call, fallback or forwarding path, no output field and no
risk category. `governance/service.py` and `governance/risk.py` are
unchanged.

## Upstream authority (ADR-021)

Frontend `a67b691a451e0b3fc469679b07158f963a61842c` (pinned by Core
2026.11.0) and Core `74cf5a49c63ed8d4d87717844bf14f79c28c9cea`. Frontend
paths are under `src/panels/lovelace/` unless noted.

| Fact | Source |
|---|---|
| `DOMAINS_TOGGLE` = fan, input_boolean, light, switch, group, automation, humidifier, valve | `common/const.ts:102-111` |
| Button row toggles a `DOMAINS_TOGGLE` entity by default; `...config` replaces it | `special-rows/hui-button-row.ts:44-54` |
| Buttons row: same default per button | `special-rows/hui-buttons-row.ts:25-38` |
| Button card default and `...config` spread | `cards/hui-button-card.ts:71-74`, `166-178` |
| Tile icon default is toggle for `DOMAINS_TOGGLE` | `cards/tile/tile-card-helpers.ts:10-25`; spread at `cards/hui-tile-card.ts:123` |
| Picture-glance: `force_dialog` or a non-toggle domain opens the dialog; item `tap_action` overrides | `cards/hui-picture-glance-card.ts:96-105`, `273-277` |
| Valve default row renders a toggle | `entity-rows/hui-valve-entity-row.ts:44-66`; `create-element/create-row-element.ts:53`, `:99` |
| Header toggle shown when `show_header_toggle` is truthy, or absent with a `title` and at least two rows naming a `DOMAINS_TOGGLE` entity | `cards/hui-entities-card.ts:31-52`, `160-165` |
| Header toggle receives every row entity with an `entity` key and toggles those in `DOMAINS_TOGGLE` present in states | `cards/hui-entities-card.ts:242-254`; `components/hui-entities-toggle.ts:19-27`, `61-65` |
| Valve toggles through its own domain; group through `homeassistant` | `common/entity/turn-on-off-entities.ts:16-19`; `common/entity/turn-on-off-entity.ts:11` |
| Entity-filter renders `{type: "entities", entities: [], ...card}` | `cards/hui-entity-filter-card.ts:123-127` |
| Group members are the runtime `entity_id` state attribute | `entity-rows/hui-group-entity-row.ts:29`, `65`; Core `homeassistant/components/group/entity.py:290-295` |
| `homeassistant.turn_on/off` expands groups and skips members whose domain lacks that service (see the chain below) | Core `homeassistant/components/homeassistant/services.py:76-78`, `89-109` |
| Toggle-group calls `<first entity domain>.<toggle action>` with every listed entity | `cards/hui-toggle-group-card.ts:130-139`; `src/common/entity/get_toggle_action.ts:64-71` |
| `buttons` header/footer chips merge `{tap_action: toggle, hold_action: more-info, ...chip}` with no domain filter (scene excepted) | `header-footer/hui-buttons-header-footer.ts:32-48` |
| Strategies: registry, tag, generate, `{...base, ...generated}` expansion | `strategies/get-strategy.ts:41-81`, `104-119`, `156-199`, `319-374` |
| `iframe` and `map` strategies generate one control-free card from config alone | `strategies/iframe/iframe-view-strategy.ts:17-30`; `strategies/map/map-view-strategy.ts:16-31` |

## Implementation map (`risk.py` on the PR head)

- Tables: `valve` added to `_ENTITY_ROW_CONTROL_DOMAINS` (line 79) and the
  `valve-entity` typed row (line 97); `_DOMAINS_TOGGLE` (105-117),
  `_DEFAULT_TOGGLE_HIGH_DOMAINS` (121), group-row, header, group-control and
  strategy reasons (129-141).
- `_row_control` (477-534): button row default (504-518), group rows
  (509-510, 520-521).
- `_default_toggle_control` (536-550): the group and valve defaults of the
  button card, tile icon and picture-glance items.
- `_default_control_findings` (583-1002): buttons-row buttons (665-724), the
  header toggle (725-785), button card and tile icon defaults (786-820),
  picture-glance items (821-859), header/footer `buttons` chips (860-902),
  toggle-group (903-930).
- `_strategy_findings` (1046-1129), called first by `_scan` (1132-1134).

## What is classified

| Shape | Finding | Reason code (new values) |
|---|---|---|
| Valve on every default control shape: entities rows (default, `valve-entity`, `toggle-entity`, falsy type, conditional, entity-filter), button and buttons rows, header/footer buttons, toggle-group, area-controls entity, button card, tile icon, picture-glance item, header toggle | `high_consequence_action` | `entity_row_valve_control`, `button_row_default_toggle_targets_high_consequence_domain`, `buttons_row_default_toggle_targets_high_consequence_domain`, `button_card_default_toggle_targets_high_consequence_domain`, `tile_icon_default_toggle_targets_high_consequence_domain`, `picture_glance_default_toggle_targets_high_consequence_domain`, `header_toggle_targets_high_consequence_domain`; existing codes for the other shapes |
| Group row in an entities card (default, `group-entity`, `toggle-entity`, button, buttons, conditional) | `unknown` (disclosed) | `group_row_members_not_established_at_plan_time` |
| Group default control on the button card, tile icon, picture-glance item (string or object, no truthy `force_dialog`), toggle-group entry, header/footer `buttons` chip | `unknown` (disclosed) | `group_control_members_not_established_at_plan_time` |
| Header toggle over group entities | `unknown` (disclosed) | `header_toggle_group_members_not_established_at_plan_time` |
| Header toggle over lock/cover/alarm/valve rows | `high_consequence_action` | `header_toggle_targets_high_consequence_domain` |
| Header toggle over other `DOMAINS_TOGGLE` rows | `toggle_action` | `header_toggle_toggle_control` |
| `strategy` on the dashboard, a view or a section, except object `iframe`/`map` | `unknown` (disclosed) | `strategy_generated_controls_cannot_be_analyzed` |

A strategy whose `type` is not a string (for example an object or a list)
is never a built-in strategy and is disclosed like any other. The
base-vs-head planning check found that the first PR head (57454d31) raised
on such values, which planning reported as
`dashboard_provider_identity_unavailable`. The correction test is
`StrategyTests.test_malformed_strategy_types_disclose_without_crashing`.

An explicit action key (`tap_action`, `icon_tap_action`, picture-glance
`force_dialog`) replaces the default and is classified from that action, as
before. Base keys beside a strategy are still scanned.

Group rows are disclosed rather than classified high-consequence: group
membership is runtime state and is not in the dashboard configuration, and
the brief requires disclosure when membership cannot be established at plan
time. Disclosure yields `manual_review_required`, which is stricter than the
elevated review the outcome names.

Owner decisions (Josh, in session, 2026-11-10), quoted:

1. Group rows: "Keep as built (manual_review_required, members unknown). In
   the PR and the review request, cite the Core source (file:line) for the
   claim that group on/off skips lock, cover, valve and alarm members, marked
   verified or inferred. The reviewer checks it."
2. Non-row shapes: "Apply the same rule as group rows: any group entity on any
   card type (button, tile, picture-glance, toggle-group, header/footer
   buttons) gets manual_review_required (members unknown), through the shared
   domain classification if that is where it lives."

Decision 2 is implemented by `_default_toggle_control` and the group branches
of the header/footer chip and toggle-group scans. "The same rule as group
rows" is applied with the row precedent: an explicit action replaces the
default, so an explicit `toggle` on a group stays `toggle_action`
(`frontend_toggle_action`, elevated review) on cards as on rows, and
display-only group shapes (entity card, glance, history graph, graph footer)
stay unclassified
(`GroupNonRowShapeTests.test_explicit_group_toggle_keeps_the_row_precedent`,
`test_group_entities_outside_these_shapes_are_not_classified`).

### Core citation: group on/off skips lock, cover, valve and alarm members

Status: **verified** by reading Core `74cf5a49c63ed8d4d87717844bf14f79c28c9cea`
and frontend `a67b691a451e0b3fc469679b07158f963a61842c`, for built-in
service registration. Not verified: a custom integration that registers
`turn_on`/`turn_off` for one of these domains at runtime (inferred not to
exist in a default installation; it would make that member controllable).

1. The frontend turns a group on or off through `homeassistant`:
   `src/common/entity/turn-on-off-entity.ts:11` (`serviceDomain = "homeassistant"`
   for `group`), with `turn_on`/`turn_off` from
   `src/common/entity/get_toggle_action.ts:64-71`.
2. `homeassistant.turn_on/off` extracts targets with the default
   `expand_group=True`: `homeassistant/components/homeassistant/services.py:76-78`;
   `homeassistant/helpers/target.py:217-241`, `303-307`
   (`group.expand_entity_ids`).
3. Members are grouped by domain and a domain without that service is
   skipped: `homeassistant/components/homeassistant/services.py:89-109`
   (`if not hass.services.has_service(domain, service.service): ... continue`
   at 107-109).
4. None of the four domains registers `turn_on` or `turn_off`:
   `homeassistant/components/lock/services.py:20-28` (unlock, lock, open);
   `cover/services.py:21-91` (open, close, set position, stop, toggle and the
   tilt variants); `valve/services.py:20-50` (open, close, set position, stop,
   toggle); `alarm_control_panel/services.py:28-64` (disarm, arm modes,
   trigger).

So the default group toggle skips those members, and the actual consequence
depends on the runtime group platform and members. A toggle-group entry is
sent to `<first entity domain>.<toggle action>` instead
(`hui-toggle-group-card.ts:130-139`); the group integration registers no
`turn_on` (`homeassistant/components/group/services.py:189-216`), so its
effect on a group entry is runtime-dependent and is disclosed the same way.

## Review level changes

Only introduced or changed findings drive the disposition (`analyze_dashboard_risk`).

| Newly analyzed shape, when introduced or changed | Base disposition | HEAD disposition | Plan risk / policy class |
|---|---|---|---|
| Valve control, header toggle over high-consequence or toggle domains | `standard_review` | `elevated_manual_review` | `high` / `elevated_admin` |
| Group row, header toggle over groups, strategy | `standard_review` | `manual_review_required` | `high` / `elevated_admin` |
| Group default control on the button card, tile icon, picture-glance item, toggle-group or header/footer `buttons` chip | `standard_review` | `manual_review_required` | `high` / `elevated_admin` |

The governance mapping is unchanged (`governance/service.py`): any non-standard
disposition already maps to `high` and `elevated_admin`, and the plan stays
approvable and applicable after approval. An unrelated edit to a dashboard
that already holds one of these shapes produces no changed finding and keeps
its classification.

## Public output

Additive only: new `reason_code` string values on existing finding objects,
and more findings in `findings` and `evidence_sha256`. No field, category or
disposition value is added or removed.

Compatibility: every finding, changed or not, counts toward
`MAX_RISK_EVIDENCE_FINDINGS` (256, `f3_dashboard/constants.py:71`), and changed
findings toward `MAX_RISK_FINDINGS` (64). A dashboard near those bounds that
planned on the base could now be refused with the existing bounded-analysis
error. `base_vs_head_planning.py` checks the corpus for this.

## Documented residuals (unchanged by design)

- Light, switch, fan and the other non-high `DOMAINS_TOGGLE` defaults on the
  button card, button and buttons rows, tile icon and picture-glance items
  are not classified (`DocumentedResidualTests.test_light_and_switch_defaults_on_button_shapes_are_unclassified`).
- A nested action object on a high-consequence card adds a separate
  display-only finding; counts can exceed the number of controls
  (`test_display_only_counts`: a valve tile with an explicit toggle yields
  three findings and a high-consequence count of two).
- Strategy-generated cards are disclosed, not analyzed.

## Evidence

- `tests/test_hamcp345_dashboard_risk_breadth.py`: positive, negative,
  identity, residual and plan-level tests.
- `run_mutations.py` writes `mutation-results.json`. It records the base
  regressions, the correction regressions (strategy types on 57454d31, the
  group non-row shapes on d220dfd9), mutants killed by assertion, and an
  unmutated control.
- `base_vs_head_planning.py` writes `base-vs-head-results.json`.

## Rollback

Revert the PR. Classification returns to the base rules; stored plans keep
their recorded risk evidence, and no persisted format changes.
