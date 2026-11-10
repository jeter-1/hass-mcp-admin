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
| Header toggle and card toggle action: valve toggles through its own domain; group through `homeassistant` | `common/entity/turn-on-off-entities.ts:16-19`; `common/entity/turn-on-off-entity.ts:11` |
| The group row renders `ha-entity-toggle`, which sends a group to `homeassistant.<toggle action>` | `entity-rows/hui-group-entity-row.ts:65-67`; `src/components/entity/ha-entity-toggle.ts:142-144`, call at `152-154` |
| Entity-filter renders `{type: "entities", entities: [], ...card}` | `cards/hui-entity-filter-card.ts:123-127` |
| Group members are the runtime `entity_id` state attribute | `entity-rows/hui-group-entity-row.ts:29`, `65`; Core `homeassistant/components/group/entity.py:290-295` |
| `homeassistant.turn_on/off` expands groups and skips members whose domain lacks that service (see the chain below) | Core `homeassistant/components/homeassistant/services.py:76-78`, `89-92`, `107-109` |
| Toggle-group computes the domain from `entities[0]`, takes that domain's toggle action and calls that one service with every listed entity | `cards/hui-toggle-group-card.ts:130-141` (domain `:133`, service `:135`, call `:137-139`); `src/common/entity/get_toggle_action.ts:16-62` (special actions; valve `58-61`), `66-72` (`turn_on`/`turn_off` otherwise) |
| `buttons` header/footer chips merge `{tap_action: toggle, hold_action: more-info, ...chip}` with no domain filter (scene excepted) | `header-footer/hui-buttons-header-footer.ts:32-48` |
| Strategies: registry, tag, generate, `{...base, ...generated}` expansion | `strategies/get-strategy.ts:41-81`, `104-119`, `156-199`, `319-374` |
| `iframe` and `map` strategies generate one control-free card from config alone | `strategies/iframe/iframe-view-strategy.ts:17-30`; `strategies/map/map-view-strategy.ts:16-31` |

## Implementation map (`risk.py` on the PR head)

- Tables: `valve` added to `_ENTITY_ROW_CONTROL_DOMAINS` (line 79) and the
  `valve-entity` typed row (line 97); `_DOMAINS_TOGGLE` (105-116),
  `_DEFAULT_TOGGLE_HIGH_DOMAINS` (121), group-row, header, group-control,
  mixed toggle-group and strategy reasons (129-146).
- `_row_control` (482-538): button row default (509-523), group rows
  (514-515, 525-526).
- `_default_toggle_control` (541-555): the group and valve defaults of the
  button card, tile icon and picture-glance items.
- `_default_control_findings` (588-1031): buttons-row buttons (670-728), the
  header toggle (730-789), button card and tile icon defaults (791-825),
  picture-glance items (826-863), header/footer `buttons` chips (865-906),
  toggle-group (908-958: mixed-domain check 922-934, single-domain list
  935-958).
- `_strategy_findings` (1075-1158), called first by `_scan` (1161-1163).

## What is classified

| Shape | Finding | Reason code (new values) |
|---|---|---|
| Valve on every default control shape: entities rows (default, `valve-entity`, `toggle-entity`, falsy type, conditional, entity-filter), button and buttons rows, header/footer buttons, single-domain toggle-group, area-controls entity, button card, tile icon, picture-glance item, header toggle | `high_consequence_action` | `entity_row_valve_control`, `button_row_default_toggle_targets_high_consequence_domain`, `buttons_row_default_toggle_targets_high_consequence_domain`, `button_card_default_toggle_targets_high_consequence_domain`, `tile_icon_default_toggle_targets_high_consequence_domain`, `picture_glance_default_toggle_targets_high_consequence_domain`, `header_toggle_targets_high_consequence_domain`; existing codes for the other shapes |
| Group row in an entities card (default, `group-entity`, `toggle-entity`, button, buttons, conditional) | `unknown_action_semantics` (disclosed) | `group_row_members_not_established_at_plan_time` |
| Group default control on the button card, tile icon, picture-glance item (string or object, no truthy `force_dialog`), entry of a toggle-group that lists only groups, header/footer `buttons` chip | `unknown_action_semantics` (disclosed) | `group_control_members_not_established_at_plan_time` |
| Toggle-group whose entities do not all share the first entity's domain: one finding at the card's `entities` list | `unknown_action_semantics` (disclosed) | `toggle_group_mixed_domains_receive_first_entity_service` |
| Header toggle over group entities | `unknown_action_semantics` (disclosed) | `header_toggle_group_members_not_established_at_plan_time` |
| Header toggle over lock/cover/alarm/valve rows | `high_consequence_action` | `header_toggle_targets_high_consequence_domain` |
| Header toggle over other `DOMAINS_TOGGLE` rows | `toggle` | `header_toggle_toggle_control` |
| `strategy` on the dashboard, a view or a section, except object `iframe`/`map` | `unknown_action_semantics` (disclosed) | `strategy_generated_controls_cannot_be_analyzed` |

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
default, so an explicit `toggle` on a group stays category `toggle` (reason
code `frontend_toggle_action`, elevated review) on cards as on rows, and
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

1. The frontend turns a group on or off through `homeassistant`. The group
   row mounts `ha-entity-toggle`
   (`src/panels/lovelace/entity-rows/hui-group-entity-row.ts:65-67`), which
   sets `serviceDomain` to `homeassistant` for `group` and takes the service
   from `getToggleAction` (`src/components/entity/ha-entity-toggle.ts:142-144`,
   call at `152-154`). The card `toggle` action and the header toggle do the
   same (`src/panels/lovelace/common/entity/turn-on-off-entity.ts:11`, `:13`;
   `src/panels/lovelace/common/entity/turn-on-off-entities.ts:16-19`).
   `getToggleAction` returns `turn_on`/`turn_off` for `group`
   (`src/common/entity/get_toggle_action.ts:66-72`; the special actions at
   `16-62` have no `group` entry).
2. `homeassistant.turn_on/off` extracts targets without passing
   `expand_group` (`homeassistant/components/homeassistant/services.py:76-78`),
   so the default `expand_group: bool = True` applies
   (`homeassistant/helpers/target.py:217`, default at `:220`) and groups are
   expanded at `target.py:307-310` (`group.expand_entity_ids`).
3. Members are grouped by domain
   (`homeassistant/components/homeassistant/services.py:89-92`). The
   `homeassistant` domain itself is skipped (`99-105`), and a domain without
   that service is skipped (`107-109`:
   `if not hass.services.has_service(domain, service.service): ... continue`).
4. None of the four domains registers `turn_on` or `turn_off`:
   `homeassistant/components/lock/services.py:20-31` (unlock, lock, open);
   `cover/services.py:21-95` (open, close, set position, stop, toggle and the
   tilt variants); `valve/services.py:20-54` (open, close, set position, stop,
   toggle); `alarm_control_panel/services.py:28-68` (disarm, arm modes,
   trigger).

So the default group toggle skips those members, and the actual consequence
depends on the runtime group platform and members.

A toggle-group card does not go through `homeassistant`. It sends
`<domain of entities[0]>.<that domain's toggle action>` with every listed
entity (`src/panels/lovelace/cards/hui-toggle-group-card.ts:130-141`). For a
list of groups that is `group.turn_on`/`group.turn_off`; the group
integration registers `reload`, `set` and `remove` and no `turn_on`
(`homeassistant/components/group/services.py:189-223`), so the effect on a
group entry is not established at plan time and is disclosed the same way.

### Toggle-group: one service from the first entity

Review finding (Medium, at 14386e65): the toggle-group scan classified each
listed entity by its own domain, but the card sends one service taken from
`entities[0]` (`hui-toggle-group-card.ts:133`, `:135`, `:137-139`). A valve
listed after a light was reported as a valve action the card does not send.

Owner ruling (Josh, 2026-11-10, relayed by the orchestrator), quoted:

> M: classify a toggle-group by what the card sends: one service from
> entities[0] for the whole list. If any entity's domain differs from
> entities[0]'s, the group is manual_review_required. No shape may get a
> lower disposition than on base. Add tests for a valve that is not
> entities[0] and for mixed domains.

As implemented (`risk.py:908-958`):

- The domain of an entry is the text before the first dot of a string entry.
  An entry that is not a string, or has no dot or an empty domain, has no
  domain.
- Every entry has the domain of `entities[0]`: each entry receives its own
  domain's service and is classified by that domain, as before (lock, cover,
  alarm and valve `high_consequence_action`; light and switch `toggle`;
  group disclosed; other domains unclassified). A first-entity valve is
  therefore still `high_consequence_action`.
- Any entry's domain differs from that of `entities[0]`: one
  `unknown_action_semantics` finding at the card's `entities` list, reason
  code `toggle_group_mixed_domains_receive_first_entity_service`,
  `entity_domain` set to the first entity's domain and bound to the whole
  list. There is no per-entry finding, so a valve or lock in a later position
  is not reported as a valve or lock action. The disposition is
  `manual_review_required`.

Consequences of reading the ruling literally, for review:

- The rule compares domains over the whole list and does not depend on which
  domains they are. A mixed list of domains that are otherwise unclassified
  (fan with input_boolean, sensor with binary_sensor), or a control domain
  beside a malformed entry, requires manual review. On the base these were
  `standard_review` or `elevated_manual_review`.
- For a mixed list the single disclosure replaces the per-entry `toggle` and
  `high_consequence_action` findings of the base, so those entries no longer
  add to `high_consequence_count`. The disposition they selected was
  `elevated_manual_review`; the disclosure selects `manual_review_required`.
- A list in which no entry has a domain (malformed entries only) and the
  empty list are not mixed and stay unclassified, as on the base.
- The review's suggested correction (classify the list by `entities[0]`
  alone) is superseded by the ruling for mixed lists.

No toggle-group shape has a lower disposition than on the base
(`release/2.6.0` at cb9d6783); see `toggle_group_base_vs_head.py` under
Evidence.

## Review level changes

Only introduced or changed findings drive the disposition (`analyze_dashboard_risk`).

| Newly analyzed shape, when introduced or changed | Base disposition | HEAD disposition | Plan risk / policy class |
|---|---|---|---|
| Valve control, header toggle over high-consequence or toggle domains | `standard_review` | `elevated_manual_review` | `high` / `elevated_admin` |
| Group row, header toggle over groups, strategy | `standard_review` | `manual_review_required` | `high` / `elevated_admin` |
| Group default control on the button card, tile icon, picture-glance item, groups-only toggle-group or header/footer `buttons` chip | `standard_review` | `manual_review_required` | `high` / `elevated_admin` |
| Toggle-group of valves only | `standard_review` | `elevated_manual_review` | `high` / `elevated_admin` |
| Toggle-group whose entries do not all share the first entry's domain | `standard_review` (no lock, cover, alarm, light or switch entry) or `elevated_manual_review` | `manual_review_required` | `high` / `elevated_admin` |

The governance mapping is unchanged (`governance/service.py`): any non-standard
disposition already maps to `high` and `elevated_admin`, and the plan stays
approvable and applicable after approval. An unrelated edit to a dashboard
that already holds one of these shapes produces no changed finding and keeps
its classification.

## Public output

Additive only: new `reason_code` string values on existing finding objects
(`toggle_group_mixed_domains_receive_first_entity_service` among them), and
more findings in `findings` and `evidence_sha256`. No field, category or
disposition value is added or removed. One shape reports fewer findings than
on the base: a mixed-domain toggle-group yields one finding at
`<card>/entities` in place of one per lock, cover, alarm, light or switch
entry.

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
  identity, residual and plan-level tests. The toggle-group correction is
  `ToggleGroupSingleServiceTests` (a valve that is not `entities[0]`, mixed
  domains, single-domain lists, first-entity valve, unchanged-control
  identity, the base disposition floor),
  `GroupNonRowShapeTests.test_group_beside_other_toggle_group_domains_is_one_mixed_disclosure`,
  `PlanLevelTests.test_mixed_toggle_group_requires_manual_review` and
  `test_valve_toggle_group_is_high_consequence`, and
  `tests/test_hamcp337_338_dashboard_risk_rows.py`
  `NativeControlShapeTests.test_toggle_group_card_targets_each_entity`.
- `toggle_group_base_vs_head.py` writes
  `toggle-group-base-vs-head-results.json`: the pure analyzer of the release
  base (cb9d6783) and of the correction (909d81aa; `risk.py` is unchanged
  after that commit) over 10990 toggle-group cases. 2101 are a card added to
  an empty view, alone or inside a `vertical-stack` or `conditional` card:
  every entity list of length 0-2 over 21 entries, of length 3 over 6
  entries, the 20 named shapes, and 4 `entities` values that are not a list.
  699 are the same card already stored beside an unrelated edit. 8190 are
  transitions between two lists. Result: 0 cases where HEAD ranks below the
  base and 0 where HEAD raises and the base did not. Of the 2101 added-card
  cases, 1182 move from `elevated_manual_review` and 618 from
  `standard_review` to `manual_review_required`, 27 move from
  `standard_review` to `elevated_manual_review`, and 274 keep their
  disposition; the 699 stored cards stay `standard_review` on both.
- `run_mutations.py` writes `mutation-results.json`. It records the base
  regressions, the correction regressions (strategy types on 57454d31, the
  group non-row shapes on d220dfd9), mutants killed by assertion, and an
  unmutated control.
- `base_vs_head_planning.py` writes `base-vs-head-results.json`.
- `mutation-results.json` and `base-vs-head-results.json` were recorded at
  22299f9c, before the toggle-group correction, and were not regenerated.
  For the correction `run_mutations.py` was updated to the renamed test and
  the re-indented toggle-group block and checked statically (each pattern
  occurs once in `risk.py`; each named test exists); it was not rerun.
  Mutation checks for the correction are run separately and reported in
  the pull request, not in this directory.
- Both runners pin the original release base 4b8db82b. The branch later
  merged `release/2.6.0` at cb9d6783; between those commits `risk.py`,
  `governance/`, `dashboard_analysis/` and `tests/test_dashboard_update_mvp.py`
  are unchanged (`git diff --stat 4b8db82b cb9d6783 -- <those paths>` is
  empty), so the recorded base comparisons still hold for the new base.

## Rollback

Revert the PR. Classification returns to the base rules; stored plans keep
their recorded risk evidence, and no persisted format changes.
