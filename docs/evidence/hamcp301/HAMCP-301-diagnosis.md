# HAMCP-301: unreferenced helpers plan as elevated (Part 1 diagnosis and options)

Status: **diagnosis and options for architect review. No runtime change.**
The option choice is **pending Josh**. Nothing in this document is a Josh
decision except the two decisions the contract relays (§0).

Source paths are relative to `hass_mcp_engineering_beta/ha_mcp_engineering/`
unless they start with `tests/` or `docs/`. Every claim is labelled:

- **V**: verified by reading source at the base revision, or by running the
  offline reproduction in §1.4.
- **I**: inference. It is not proven by source and is not an observation of
  the live system.

No live Home Assistant or deployed MCP endpoint was accessed. Live facts come
only from the evidence the contract relays (rc.1, build `38b29a3f`, plan
`fe096e85`, 2026-11-04).

## 0. Baseline and authority

| Item | Value |
| --- | --- |
| Base (`origin/main`, fetched 2026-11-04) | `38b29a3f0f2f3ebf8c944213ca6be5965bf11fab`, the merge of PR #359 (HAMCP-302, 2.4.1-rc.1) |
| Head at the time of writing | the same commit, plus this document only |
| Branch | `agent/HAMCP-301-diagnosis` |
| Engineering version | `2.4.1-rc.1` (`config.yaml`, `version.py`, `validate_addon_metadata.py`) |
| Active acceptance | `docs/V2_4_1_RC1_ACCEPTANCE.md`, resolution `exact` |
| Staged release | none |
| Tool counts | 80 static + 28 delegated = 108 (reviewed ha-mcp 8.6.0) |
| Instructions loaded | root `AGENTS.md`, `hass_mcp_engineering_beta/AGENTS.md`, `tests/AGENTS.md` |
| Helper bindings in scope | input_boolean `helper-dependency-risk-v14` (`governance/input_boolean_dependency.py:103`) and input_select v2 (`governance/input_select_dependency.py:276`). Both use `relevance_model = "target_scoped_evidence"`. |

Authority, as relayed by the contract:

- **[DECISION] Josh, 2026-11-04** (architect chat): run HAMCP-301 and
  HAMCP-306 in parallel as the first 2.4.2 work.
- **[DECISION] Josh, 2026-11-04**: the merge-order and Evidence rule for
  release PRs.

The objective comes from HAMCP-274: an ordinary helper value change should be
low-friction when the evidence supports it.

### 0.1 Summary

`input_boolean.engineering_acceptance` is elevated for **three independent
reasons**. Each of them alone is enough to keep the plan elevated:

1. **A false group hold.** One state record that the expand snapshot rejects
   marks the whole state inventory incomplete. Every helper is then "possibly
   in a group" (§1).
2. **214 target-capable opaque obligations.** These come from template
   constructs whose candidate set the analyzer cannot close. They attach 36
   automations to every helper (§2).
3. **Three automations that hit analysis bounds.** Each produces a coverage
   failure, so `coverage_complete` is false for every helper (§3).
   `automation.front_door_lock_bedtime` (safety-critical) is one of them.

The third reason decides the outcome under the hard constraint. While
`front_door_lock_bedtime` is only partly analysed, no option may classify any
unreferenced helper as no-consequence. That includes `engineering_acceptance`.
Fixing (1) and (2) is necessary but not sufficient. `engineering_acceptance`
can reach standard only if that one automation's analysis is completed and
excludes the helper (§5).

The live evidence relayed so far does not contain three facts that the options
depend on (§8):

- the reason-code histogram of the 214;
- the exact bound each of the three automations hit;
- the identity of the rejected state record.

## 1. Group membership (Q1)

### 1.1 Which inventory is incomplete

It is the **state inventory** of the expand snapshot, not the entity registry
(V):

- `dependency/provider.py:833-873` reads states through REST `/api/states`. It
  reads the entity registry through WS `config/entity_registry/list`. Both go
  to `_build_expand_snapshot_evidence` (`dependency/provider.py:2085`).
- `_build_expand_snapshot_evidence` sets `state_inventory_complete = False`
  for the whole snapshot when any one of these holds:

  | Condition | Lines |
  | --- | --- |
  | more than `MAX_EXPAND_SNAPSHOT_ENTITIES` (10,000) states; the bounded list is then emptied | `dependency/provider.py:2103-2104`, constant `dependency/extraction.py:95` |
  | a state record that is not a dict | `dependency/provider.py:2116-2118` |
  | a non-str `entity_id`, an `entity_id` failing `valid_entity_id`, or non-dict `attributes` | `dependency/provider.py:2121-2127` |
  | a duplicate `entity_id` | `dependency/provider.py:2128-2131` |

- `_group_membership_hold` (`governance/helper_dependency.py:1061-1158`)
  turns that flag into `group_membership_state_inventory_incomplete` at lines
  1085-1086. The flag does not depend on the target, so **every** helper is
  held. A hold makes `evidence_complete` false (lines 1435-1437), sets the
  consequence to `unknown` (lines 1438-1439) and adds the warning "Group
  membership keeps this plan elevated" (lines 2530-2560).
- The registry checks (`dependency/provider.py:2137-2172`) set a different
  flag, `source_inventory_complete`. That flag is not what produced the live
  reason code.

### 1.2 Registry size, a paging cap, or a missing entity class?

None of these.

- **Registry size.** Ruled out (V). The registry has 2,567 entries and the
  snapshot bound is 10,000. The state bound counts state records, not
  registry entries. Reaching it would need more than 7,400 state-only
  entities (I: implausible, not observed).
- **Paging cap.** Ruled out (V). `/api/states` is a single unpaged response.
  No paging code exists on this path.
- **Missing entity class.** Ruled out (V). The check is per record and does
  not depend on domain or class.
- **The dashboard `structural_limit` on 2,567 entries is unrelated** (V). It
  comes from a separate reader:
  - `clients/dashboard_analysis.py:161-163` parses with `INVENTORY_NODES =
    100,000` (`dashboard_analysis/contracts.py:12-35`).
  - It raises `structural_limit` at `dashboard_analysis/contracts.py:113-115`
    and `126-133`.
  - The dependency provider does not share that reader. Its registry read is
    at `dependency/provider.py:849`, with bounds of 10,000 records and 64 KiB
    per record.
  - (I) About 39 parse nodes per registry record across 2,567 records exceeds
    100,000 nodes. That is a dashboard-reader defect worth its own register
    row. It is not evidence about this plan.
- **The trigger is one rejected record** (I, strongly supported). The most
  plausible cause is an entity id that Home Assistant accepts but Engineering
  rejects:
  - `valid_entity_id` (`dependency/extraction.py:160-186`) requires at least
    one letter in both the domain and the object id (lines 184-186).
  - Core's `VALID_ENTITY_ID` accepts all-digit object ids. Engineering's own
    port of it is at `recorder_settings/entity_filter.py:26-28`, ported at
    Core 2026.10.1; it must be re-verified at the pinned 2026.10.3 under
    ADR-021. `sensor.1234`, or a numerically named Z-Wave, Zigbee or template
    entity, is valid in Home Assistant and is rejected here.
  - Non-dict records, non-dict attributes and duplicate ids are implausible
    in a real `/api/states` response (I).
- **No test exercises a digit-only id against the snapshot** (V). The nearest
  test, `tests/test_hamcp274_input_select_relevance.py:~753-756`, uses an
  upper-case id (`automation.Uses_Mode`).
- **Unknown:** the exact record. Finding it needs a read-only listing of state
  ids that match Core's regex but fail `valid_entity_id`. This session must
  not perform that observation (§8).

### 1.3 What would make membership resolvable at this size

Size is not the obstacle. Any of the following is sufficient:

- **(a) Per-record attribution in the snapshot** (proposal, Option A). Keep
  the rejected entity ids that are still attributable, with a separate count
  of unattributable rejections.
  - `_group_membership_hold` already scopes the search to `group.*` and the
    target's own domain (`governance/helper_dependency.py:1066-1073`, and
    `1088-1091`). Pinned Core 2026.10.3 has no group platform for
    input_boolean or input_select.
  - A rejected record whose id has a valid domain that is neither `group` nor
    the target's domain therefore cannot be a group that contains the target.
    It cannot make the target's membership unresolved.
  - Unattributable rejections keep the hold: non-dict records and
    non-str ids. So do rejected `group.*` or target-domain ids, and
    duplicates of those.
  - This mirrors the existing target-scoped treatment of malformed registry
    records: `target_scoped_label_disposition`,
    `governance/helper_dependency.py:430-469`.
- **(b) Align the state-record validator with Core's grammar.** This applies
  only to the snapshot, using the regex already ported at
  `recorder_settings/entity_filter.py:26-28`. Changing `valid_entity_id`
  globally would also widen template extraction, and
  `tests/test_beta18_reference_classifier.py:55-92` asserts the stricter
  grammar. Option A therefore prefers (a), with (b) at most as the
  attribution test.
- **(c) An operational fix.** Rename the offending entity in Home Assistant.
  That is a live change, which is Josh's decision and outside this repository.
- **The `explain_target_exclude` lead (UNVERIFIED, assessed only as an
  option).** As described, it is a WebSocket command in Core 2026.11.0b5 that
  explains which entities a *service-call target selector* resolves to or
  excludes.
  - (I) It does not answer template reads (§2) or `group.*` membership, so it
    would not resolve this hold.
  - It is in a beta Core, not the pinned 2026.10.3, and nothing in the repo
    has reviewed it.
  - Under ADR-021 it cannot carry authority until a released Core is pinned
    and its exact source is reviewed.
  - At most it could later replace the label-selector resolution for
    structured `target:` selectors. It is not part of any option below.

### 1.4 Offline reproduction (V)

This uses fixtures shaped like the evidence. The reproduction script was
scratch, kept in `.artifacts/hamcp301/repro_group.py` and not committed.

- **Installation shape:** 2,560 synthetic sensors, `engineering_acceptance`,
  `guest_mode`, and one `group.family` that does not contain the target.
- **Scenarios:** each adds one extra state record to that installation.
- **Calls:** each scenario calls `_build_expand_snapshot_evidence(...)` and
  then `_group_membership_hold(...)`.

```text
clean                            states=2563 state_inventory_complete=True  groups=['group.family'] hold=None
numeric_object_id (sensor.1234)  states=2564 state_inventory_complete=False groups=['group.family'] hold=('group_membership_unresolved', ['group_membership_state_inventory_incomplete'])
attributes_not_dict              states=2564 state_inventory_complete=False groups=['group.family'] hold=(same)
duplicate_state                  states=2564 state_inventory_complete=False groups=['group.family'] hold=(same)
non_dict_record                  states=2564 state_inventory_complete=False groups=['group.family'] hold=(same)

valid_entity_id('sensor.1234') = False
valid_entity_id('switch.2') = False
valid_entity_id('sensor.0x00158d0001') = True
valid_entity_id('light.a_1') = True
```

A list of 10,001 states plus `group.family` yields zero snapshot entities.
Over the bound, no group is visible at all.

Command:

```text
TMPDIR=$PWD/.artifacts/tmp .artifacts/venv/bin/python -I -c "import runpy,sys; sys.path.insert(0,'hass_mcp_engineering_beta'); runpy.run_path('.artifacts/hamcp301/repro_group.py', run_name='__main__')"
```

The venv is Python 3.12, installed from `tests/requirements.lock` with
`--require-hashes`.

## 2. The 214 dynamic references (Q2)

### 2.1 What the number is

- The live `target_relevant_dynamic_reference_count` is the binding's
  `opaque_count` (`governance/helper_dependency.py:1631-1643`). It counts
  obligations whose target projection is `bounded_semantic_opaque`.
- `_obligation_targets_helper` (`governance/helper_dependency.py:652-706`, V)
  projects each obligation in this order:
  1. coverage-failure authority gives `coverage_failure`;
  2. a non-causal structured reference is neutral;
  3. exact inclusion gives `exact_dependency`;
  4. scopes `dependency_neutral`, `closed_finite_candidates` and
     `closed_entity_domains` (where the target domain is not listed) give an
     exclusion;
  5. **anything with scope `target_capable` or `None` is opaque for every
     helper** (lines 700-706).
- The 2,804 `unrelated_dynamic_reference_count` are steps 2 and 4, which
  already work.
- The ledger gives every `bounded_semantic_opaque` obligation the scope
  `target_capable` (V):
  - `dependency/obligation_ledger.py:5238-5262`, `_emit`;
  - `dependency/models.py:~225-248`, `DependencyObligation.__post_init__`.
- Domain closure is already used where it is proven (V):
  - `_consume_entity_value` emits `exact_dependency` with domains, which gives
    scope `closed_entity_domains`, whenever `possible_domains` is non-empty and
    `domain_evidence_complete` (`dependency/obligation_ledger.py:4746-4757`).
  - It reaches the `*_target_opaque` fall-through (lines 4775-4790) only when
    that proof is absent.
  - So the 214 are, **by construction**, the obligations that have neither a
    finite candidate set nor a complete domain set.

### 2.2 Constructs that produce target-capable opaque obligations (V unless marked)

| Class | Construct | Reason code | Why a helper with no static reference stays unresolved | Source |
| --- | --- | --- | --- | --- |
| C1 unknown provenance consumed as an entity | a variable, attribute or concatenation of unknown origin passed to `states()`, `is_state()`, `states[x]` or `state_attr()` | `<reason>_target_opaque` | No candidate set and no domain set. The value could be any entity id, including a helper. | `dependency/obligation_ledger.py:4643-4790` (4777) |
| C2 bare `states` iteration | `states \| selectattr(...)`, `for s in states` | `states_item_entity_access`, `rendered_state_value` | It really reads every domain. Opaque is correct. | `dependency/obligation_ledger.py:4160-4410`, `1432-1460` |
| C3 entity-set producers | `area_entities`, `device_entities`, `floor_entities`, `integration_entities`, `label_entities` (dynamic selector), and their non-entity siblings `areas`, `floors`, `labels`, `area_devices`, `floor_areas`, `label_areas`, `label_devices` | `<name>_entity_set_membership_unavailable` | No membership evidence. Helpers can carry an area, label or device. Only a literal `label_entities` selector is resolved later. | `dependency/obligation_ledger.py:2393-2440` (2405); resolution gate `dependency/extraction.py:1774-1820` (`producers == {"label_entities"}`, 1808) |
| C4 registry-lookup globals | `device_attr`, `area_id`, `label_id`, `floor_id`, `config_entry_attr`, ... | `<name>_registry_lookup_opaque` | The lookup result is unknown at analysis time. | `dependency/obligation_ledger.py:2447-2470` |
| C5 unregistered globals, filters and tests | any callable not in the semantic registry, such as `iif`, `bool`, `slugify`, `regex_*`, `as_datetime`, `as_local`, `strptime`, `timedelta`, `is_number` (V: absent from the registry; see §2.3) | `unknown_global_<n>`, `unknown_filter_<n>`, `unknown_test_<n>` | The callable is unreviewed, so its output provenance is unknown. The obligation is emitted at the call site even when the result never reaches a state read. | `dependency/obligation_ledger.py:2516`, `3210`, `3849` |
| C6 dynamic dispatch | `map`, `select`, `reject`, `selectattr` or `rejectattr` with a dynamic name | `*_dynamic_attribute_dispatch`, `*_dynamic_test_dispatch`, `<n>_dynamic_dispatch_name` | The dispatch target is unknown. These can carry complete domains (`_opaque_from_value`, 5142-5172) but the scope is still `target_capable`. | `dependency/obligation_ledger.py:3372-3525`, `5142-5200` |
| C7 callable provenance | argument unpacking, unreviewed or external callable bindings, mixed provenance | `dynamic_call_argument_unpacking`, `unreviewed_callable_binding`, ... | Provenance is unreviewed. | `dependency/obligation_ledger.py:2157`, `2261`, `2272`, `2285` |
| C8 external or recursive templates | imports, external macros, unavailable or recursive local macros, loop cycles | `external_template_content_unavailable`, `local_macro_body_unavailable_or_recursive`, `loop_cycle_without_candidates` | The content was not analysed. | `dependency/obligation_ledger.py:4792-4835`, `2893`, `2559` |
| C9 trigger context | `trigger.entity_id`, `from_state` or `to_state` where the trigger's entity set is unknown; `wait.trigger` | `trigger_context_entity_opaque`, `wait_trigger_context_opaque`, `unknown_context_attribute` | The trigger's entity set is not closed. | `dependency/obligation_ledger.py:4524`, `4560`, `4576` |
| C10 unknown receivers | attribute or item access on an unknown receiver | `unknown_attribute_receiver`, `unknown_item_receiver`, `dynamic_context_item_access` | Same as C1. | `dependency/obligation_ledger.py:4157`, `4404`, `4410` |
| C11 template parse error | a template that does not parse | `template_parse_error` | There is no AST. | `dependency/obligation_ledger.py:1024-1034` |
| C12 blueprints and inputs | `use_blueprint` source not analysed locally; an unresolved input placeholder | `blueprint_source_unavailable_to_local_analysis`, `blueprint_input_value_unresolved` | The value is unknown. | `dependency/extraction.py:~345-352`, `844-845` |
| C13 unbounded event triggers | dynamic or unfiltered `event` triggers that may be `state_changed` or `call_service` | `dynamic_event_trigger_may_be_state_changed`, `state_changed_event_filter_unbounded`, `call_service_event_filter_unbounded` | The filter is unbounded. | `dependency/extraction.py:~470-700` (657) |
| C14 incomplete expand or label resolution | `expand()` or a literal `label_entities` whose snapshot evidence is incomplete | expand or label resolution retained | Snapshot membership is incomplete. **This class is coupled to §1**: the same incomplete state inventory leaves `expand` opaque (`dependency/extraction.py:1530-1556`, `1749-1753`). | `dependency/extraction.py:1753`, `1949` |

`trigger.context` and `trigger.for` are already neutral
(`dependency/obligation_ledger.py:4440`, `4456`). They are not in the 214.

**Unknown:** the distribution of the 214 across C1-C14, and per automation.
The live evidence relayed has only totals. The option results in §6 are
therefore given per class.

### 2.3 Which classes the semantics registry alone proves cannot read input_boolean or input_select

Strict answer: **none of the classes now in the 214** (V).

- The pinned registry (`dependency/semantic_registry.py:20-34`,
  `dependency/template_semantic_registry.json`, digest `857e344b…`) carries
  only six category strings per callable:
  - `attribute_item_access`
  - `dependency_neutral`
  - `dynamic_filter_test_dispatch`
  - `entity_set_producer`
  - `provenance_preserving`
  - `state_entity_access`
- It carries no domain data.
- The analyzer already applies every proof the registry can give before it
  emits an obligation: `dependency_neutral` and `provenance_preserving`
  callables, at `dependency/obligation_ledger.py:2509-2514` and similar. An
  obligation that remains target-capable is one the registry could not
  discharge.

What would be provable with a **reviewed registry extension**. Each needs
review of the exact pinned Core source under ADR-021 and a re-pin of
`EXPECTED_SEMANTIC_REGISTRY_SHA256`:

- **C5, pure Core template functions.** Examples: `iif`, `bool`, `slugify`,
  `regex_match`, `regex_replace`, `regex_findall`, `is_number`,
  `as_datetime`, `as_local`, `strptime`, `timedelta`, `md5` and `sha*`.
  - (I) They read no state, so they can be registered as `dependency_neutral`.
    Where an operand flows to the output (`iif`, `bool`, the regex
    functions), they can be registered as `provenance_preserving`. That is the
    existing treatment of `default` and `first`.
  - This proves no read *by the callable*. If its output later reaches
    `states()`, value tracking still decides (C1).
  - This is the only class the registry mechanism can discharge without
    guessing. Its size in the live 214 is unknown.
- **C3, the non-entity producers.** These are `areas`, `floors`, `labels`,
  `area_devices`, `floor_areas`, `label_areas` and `label_devices`.
  - (I, needs Core source confirmation) They return area, floor, label or
    device ids, never entity ids.
  - A new category, or `provenance_preserving` with "no entity candidates",
    would let an obligation that uses only these become neutral through the
    existing `_no_entity_candidates` path
    (`dependency/obligation_ledger.py:4747-4763`).
  - When their output feeds `area_entities` or a similar producer, the
    downstream producer stays opaque.

What the registry **cannot** prove, from any extension:

- **C3 entity producers.** `area_entities`, `device_entities`,
  `floor_entities`, `integration_entities` and dynamic `label_entities` need
  membership evidence. Area, device and floor membership is stored per entity
  (`dependency/provider.py:895-915`) but is not wired to obligation
  resolution. The area, device and floor registries are fetched only for
  effect display (`dependency/provider.py:250-252`).
  - (I) A literal `integration_entities('<x>')` with `<x>` not in
    `{input_boolean, input_select}` could be excluded by the helper's
    registry platform. Core's implementation also matches config-entry
    titles, so this needs source review. It is not a registry fact.
- **C2.** It genuinely reads all domains.
- **C1, C6-C13.** They need value-tracking or configuration-analysis proof.
- **C14.** It is resolved by fixing §1.

A narrower analyzer-level candidate exists for **C6 only** (I). Dispatch
obligations already carry `possible_domains` when `domain_evidence_complete`
(`dependency/obligation_ledger.py:5169-5173`). Projecting them as
`closed_entity_domains` would mirror what `_consume_entity_value` already
does for state reads at lines 4746-4757. This is analyzer semantics, not
registry semantics. The comment at
`governance/helper_dependency.py:700-705` deliberately refuses to promote
"finite hints" into proof. Any change must first prove that
`domain_evidence_complete` is sound on every dispatch path (33 sites set or
propagate it).

## 3. The three bounded automations and the three failed items (Q3)

### 3.1 They are the same three (V)

- `automation_failed_items = max(failed + overflow, len(coverage-failure
  sources))` (`dependency/provider.py:1687-1696`).
- The status is set at `dependency/provider.py:1702-1706`.
- The warning "N automation configuration(s) exceeded dependency-analysis
  coverage bounds." comes from the same set (`dependency/provider.py:1759-1769`).
- `change_impact_analysis` relays it (`impact/service.py:269-276`).

So the 3 failed items, the 3 bounded sources (`front_door_lock_bedtime`,
`close_garage_both_in_bed` and `garage_open_30m_away_auto_close_fallback`) and
the 3 automations "exceeding coverage bounds" are one set. In the binding they
produce:

- `automation_inventory_incomplete`
  (`governance/helper_dependency.py:1271-1276`);
- `obligation_coverage_failure` (lines 1307-1308).

Either one makes `coverage_complete` false (line 1425).

### 3.2 What the bounds are (V)

| Kind | Bounds | Source |
| --- | --- | --- |
| Cost and DoS: per template | source 65,536 chars; AST 4,096 nodes; 16,384 work units; depth 64; 256 bindings; 512 scopes; 128 macro captures; 32 external references; 8,192 abstract value units, value depth 8 | `dependency/obligation_ledger.py:28-42`; an `_AnalysisLimit` emits one terminal `coverage_failure` and stops (1055-1074) |
| Cost and DoS: per document | 2,000 obligations; 10,000 nodes; depth 64 | `dependency/extraction.py:92-99` |
| Cost and DoS: blueprint | 10,000 nodes; depth 64 | `dependency/provider.py:125-126` |
| Cost and DoS: inventory | 1,000 automation sources | `dependency/provider.py:62` |
| Evidence size, coupled to correctness | 128 candidates per obligation (`MAX_TEMPLATE_CANDIDATES`); 256 obligations per template; 32 context ids; 128 context entity ids and variables | `dependency/obligation_ledger.py:33`, `5262-5285`; `dependency/extraction.py:88-101` |
| Evidence bytes | 128 bytes per value; 8 KiB exact aggregate; 2 KiB selectors; 1 KiB domains | `dependency/models.py:50-55`; loss gives `coverage_failure` (316-325) |
| Event selectors | 128 values | `dependency/extraction.py:~470-700` |

**Unknown:** which bound each of the three hit. The live `bounded_sources`
entries name it for each automation (`dependency/bounded_sources.py:73-137`,
`153-199`), but the relayed evidence does not include them.

### 3.3 Is raising them, or making them evidence-based, safe?

- **Raising a cost bound.** It is safe only with measured cost evidence. These
  bounds keep a hostile or pathological configuration within
  `BUILD_TIMEOUT_SECONDS = 300` (`dependency/authority.py:19`). Raising one
  increases worst-case CPU and memory for every installation. It changes
  protected runtime code. It does not change what is proven: a bigger
  template can still hit the new bound.
- **Raising an evidence-size bound** (`MAX_TEMPLATE_CANDIDATES`, the byte
  caps). This is safe only if every structure sized from the constant is
  re-reviewed. The candidate cap is shared by `MAX_EXPAND_MEMBERS_PER_ENTITY`
  (`dependency/extraction.py:96`) and by label-union limits.
- **Making the bound evidence-based.** This is possible for **one sub-case
  only** (I):
  - An obligation that overflows at `_emit` with a fully enumerated exact
    list has a complete candidate set before it is clipped
    (`dependency/obligation_ledger.py:5262-5271`). The domain set derived
    from that list is itself complete and small. Emitting
    `closed_entity_domains` instead of `coverage_failure` would exclude a
    helper whose domain is not in that set.
  - It does **not** apply to `entity_candidate_limit_exceeded` (4705-4720),
    where the value was clipped upstream and the list is not complete.
  - It does not apply to any `_AnalysisLimit`, where analysis stopped part
    way and the unvisited remainder is unknown (1060-1073).
- **No existing path proves that a stopped analysis does not read a helper**
  (V). Attribution (`_attributable_coverage_sources`,
  `governance/helper_dependency.py:911-952`) narrows *which automations* are
  relevant. It does not make coverage complete.
- **Owner-side alternative** (live change, Josh's decision). Restructure the
  affected automation so its templates fit the existing bounds. This needs no
  code change and no bound change, and the analysis becomes complete by its
  existing rules.

## 4. `entity_registry_malformed_relevant_record` (Q4)

### 4.1 Record classes (V)

These come from `_build_label_membership_evidence`,
`dependency/provider.py:2461-2985`. Each class marks a label selector
incomplete and adds the code.

| # | Record class | Selectors made incomplete | Attributable to an entity? | Lines |
| --- | --- | --- | --- | --- |
| 1 | registry item that is not a dict | all | no | 2726-2734 |
| 2 | unreadable `labels`: not a list, more than 64, or a label that is not a str, is empty, or is over 255 chars | all | yes, if `entity_id` is a valid str | 2746-2775 |
| 3 | readable labels, but a missing or invalid `entity_id` | selectors whose label is in the record | no | 2776-2797 |
| 4 | canonical-bytes failure (over 64 KiB, over 4,096 nodes, depth over 64, non-str keys, lone surrogates, non-finite floats) with a valid `entity_id` | selectors whose label is in the record | yes | 2807-2830; helpers 2286-2405 |

### 4.2 Why the record is "relevant"

"Relevant" means relevant to a **literal `label_entities` selector** used by
some automation template. The selector's resolved label id appears in the
record's labels. Classes 1 and 2 are treated as relevant to every selector
(V).

### 4.3 Whether it elevates the plan

The code is **disclosure**: it goes into
`consequence_uncertainty_reason_codes`
(`governance/helper_dependency.py:1474-1484`). It is not a coverage reason.
It elevates only indirectly:

- `target_scoped_label_disposition` (lines 430-469) returns `unresolved` in
  two cases:
  - the bad record is unattributable, or invalid as in class 3;
  - the record is the target itself.
- The label obligation then stays opaque and counts in the 214, through C14.
- When the bad record is attributable to *another* entity, the selector is
  decided for the target and the code is dropped
  (`_selector_authority_diagnostics`, lines 472-590). The tests confirm both
  cases:
  - `tests/test_hamcp274_input_select_relevance.py:129-130` and `342-350`:
    an unrelated bad record stays standard.
  - `tests/test_hamcp274_input_select_relevance.py:~507-517`: a related one
    goes elevated.

**Live implication** (I): because the code reached the live plan, at least
one relevant selector has an unattributable class 1 or class 3 record, or a
malformed record for `engineering_acceptance` itself. Class 2 or 4 for a
different entity would have been excluded.

**Unknown:** which class it is and which selector. Finding out needs a
read-only registry observation.

## 5. Options (Q5)

**The ceiling for any option.** Standard (`STANDARD_ADMIN`, `RiskDelta.LOW`)
needs all of the following
(`governance/helper_dependency.py:1425-1473`, `2353-2466`;
`governance/policy.py:1750-1762`):

- an execution contract with no blocking reason;
- `coverage_complete`;
- `opaque_count == 0`;
- no group hold;
- observed consequence `none`;
- zero relevant consumers.

**The hard constraint applies to every option.** Any helper that a
`safety_critical` automation might read through an unresolved template must
never be classified as no-consequence. `front_door_lock_bedtime` is
safety-critical. It is both a coverage-failure source and an opaque consumer
of every helper. So **no option may move any unreferenced helper to standard
while that automation's analysis is bounded or opaque**.

**What friction exists today, from source (V).** For an *executable* helper
plan (`execution_contract_complete`), `ELEVATED_ADMIN` and `STANDARD_ADMIN`
both require only `plan_approval`:

- `governance/policy.py:2205-2246`, through the `exact_owner_authoritative`
  shortcut;
- `tests/test_beta54_owner_authoritative_execution.py:1697`, where the
  acknowledgements are `["plan_approval"]` even for the safety-critical
  `guest_mode`.

The remaining friction is the class and risk label (elevated, high, unknown
consequence), the warnings, and the coarse `reload:automation` fence
(`governance/helper_dependency.py:1452-1469`). Opaque consumers that cannot be
locked individually add that fence.

(I) What Josh experiences as friction on the live system is not observed
here.

### Option A: target-scoped group-membership attribution

- **Change**
  - `_build_expand_snapshot_evidence` keeps the rejected state ids that are
    still attributable, with a separate count of unattributable rejections.
    Attributable means the id parses under Core's grammar, as ported in
    `recorder_settings/entity_filter.py:26-28` and re-verified at 2026.10.3.
  - `_group_membership_hold` reports `group_membership_state_inventory_incomplete`
    only in these cases:
    - an unattributable rejection exists;
    - a rejected id is in domain `group` or in the target's domain;
    - the state list exceeds the bound.
  - No change to `valid_entity_id`, the ledger or policy.
- **Becomes lower.** Nothing changes class by itself. The false
  `group_membership_unresolved` code, its warning and its fence contribution
  go away for every helper that is in no group.
  - (I) C14 `expand` obligations over `group.*` would also resolve, if the
    same per-record attribution were applied to `expand`
    (`dependency/extraction.py:1530-1556`). That is a scope choice for Josh.
- **Stays elevated**
  - `engineering_acceptance`: it still has opaque obligations and incomplete
    coverage.
  - Every helper that is really in a group, through
    `group_membership_unattributed`.
- **Safety argument**
  - The hold's own premise (`governance/helper_dependency.py:1066-1073`) is
    that only `group.*` or a group-sourced entity of the target's domain can
    contain an input_boolean or input_select. A record outside those domains
    cannot be such a container.
  - Every record that cannot be attributed, and every record inside those
    domains, keeps the hold. That is monotonic and fails closed.
  - Over-bound and non-dict cases are unchanged.
  - The narrowing is proven per record. It does not rest on an absence of
    observation.

### Option B: A, plus reviewed narrowing of the opaque classes

- **Change**
  - Option A.
  - **B1:** register the C5 pure Core template functions after an exact
    2026.10.3 source review. Use `dependency_neutral` or
    `provenance_preserving`, and re-pin the digest.
  - **B2:** separate the non-entity producers from the entity producers in
    C3.
  - **B3** (optional, the highest-risk part): project C6 dispatch obligations
    that carry complete domain evidence as `closed_entity_domains`. This
    first needs the soundness proof described in §2.3.
  - C3 entity producers, C2, C1, C7-C13 and coverage failures are unchanged.
- **Becomes lower**
  - Automations whose *only* target-capable obligations are in C5, B2's part
    of C3, C6 (with B3) or C14 drop out of the 36 for helpers they provably
    do not read.
  - A helper whose *remaining* relevant consumers are all excluded, and that
    no coverage failure touches, would reach standard.
  - At this installation no helper does, because of §3.
- **Stays elevated**
  - Every helper while any coverage failure remains. That includes
    `engineering_acceptance`.
  - Helpers with real or opaque consumers in C1, C2, C3 entity producers, or
    C7-C13.
- **Safety argument**
  - Each narrowing comes from reviewed exact-version Core source (B1, B2) or
    from the analyzer's existing domain proof (B3). None comes from the
    absence of a candidate.
  - Exact inclusion stays monotonic (`governance/helper_dependency.py:675-682`).
  - Opaque consumers still contribute at most `unknown` and are never `none`.
  - The coverage-failure path is untouched, so `front_door_lock_bedtime`
    keeps every helper elevated, as the hard constraint requires.

### Option C: B, plus evidence-based completion of the bounded automations

- **Change**
  - Option B.
  - For each of the three bounded automations, depending on the bound it hit
    (unknown, §8):
    - **C-i:** if the bound is an `_emit` candidate overflow with a complete
      exact list, convert the overflow into `closed_entity_domains` (§3.3).
    - **C-ii:** if the bound is a cost bound, raise it only with measured
      cost evidence, or leave it unchanged.
    - **C-iii** (owner-side, live, Josh's decision): restructure the
      automation to fit the existing bounds.
- **Becomes lower.** `engineering_acceptance`, and other helpers with no
  static reference, reach **standard/low** only if all of these hold:
  - all three bounded automations complete and exclude the helper;
  - every remaining opaque obligation is excluded by A and B;
  - the group hold is clear;
  - the `entity_registry_malformed_relevant_record` selector (§4) resolves.
- **Stays elevated**
  - Any helper with any residual opaque or bounded consumer.
  - Every helper while `front_door_lock_bedtime` stays bounded or opaque.
- **Safety argument**
  - C-i uses a candidate set that was complete before it was clipped.
  - C-ii does not change proof, only reach.
  - C-iii uses the existing rules unchanged.
  - No path treats "stopped" as "excluded".

### Rejected option: classify a helper with unresolved coverage or opaque consumers as standard

This would make every helper low-friction immediately. It **violates the hard
constraint**, because `front_door_lock_bedtime` might read any helper. It also
contradicts `governance/helper_dependency.py:700-705`. It is listed only to
record why it is excluded.

## 6. Expected live result per option (Q6)

Live baseline: 36 opaque consumers. Of these, 35 are `unknown` and 1 is
`safety_critical` (`front_door_lock_bedtime`). `engineering_acceptance` is
`elevated_admin`, high, with physical consequence `unknown`.

| | Option A | Option B | Option C |
| --- | --- | --- | --- |
| Group code for unreferenced helpers | cleared, if the rejected record is attributable and outside `group` and the target domain (I) | cleared, as in A | cleared, as in A |
| Which of the 36 remain | all 36 (A changes no obligation; C14 group-`expand` obligations would drop only with the optional `expand` extension) | the 3 bounded automations always remain; of the other 33, those with any C1, C2, C3-entity or C7-C13 obligation remain; **exact list unknown** until the reason histogram is observed | none, only if all 3 bounded automations complete and exclude the helper and the residual opaque set from B is empty; otherwise the residual remains, including `front_door_lock_bedtime` if it stays bounded |
| `engineering_acceptance` | `elevated_admin`, high, `unknown` | `elevated_admin`, high, `unknown` | `standard_admin`, low, *if* every C precondition holds; otherwise `elevated_admin`, high, `unknown` |
| `input_boolean.guest_mode` | `elevated_admin`, `safety_critical`; unchanged | unchanged | unchanged |

Why `guest_mode` is unchanged (V):

- It has 7 exact obligations whose downstream actions are `lock.unlock`.
- `tests/test_beta48_helper_target_relevance.py:365`,
  `tests/test_beta54_owner_authoritative_execution.py:1697`.
- Exact inclusion is evaluated before any scope projection
  (`governance/helper_dependency.py:675-682`), and no option touches exact
  obligations or action-profile classification (`governance/risk.py:92-94`,
  `1654-1672`).
- Each option's PR must keep these tests unchanged and add a guest-mode
  negative case.

`action_profile_semantic_incomplete` (`governance/helper_dependency.py:900-901`;
`governance/risk.py:1654-1672`) is unaffected by every option. It marks
transitive, generic or notification actions in the consumers' downstream
profiles. It explains why 35 consumers show `unknown` rather than a definite
class. It does not block standard once a helper has no relevant consumers.

## 7. PR shape and Evidence/mutation scope (Q7)

All runtime options touch protected paths: `governance/` and `dependency/`
under `hass_mcp_engineering_beta/ha_mcp_engineering/`. Each must be named in
the Part 2 contract and declared through `-AuthorizedProtectedPath`. Each is a
2.4.2 release PR under the merge-order rule, with one final Evidence run when
it is next in line.

| Option | PR shape | Protected files | Tests | Mutation scope |
| --- | --- | --- | --- | --- |
| A | one release PR | `dependency/provider.py` (`_build_expand_snapshot_evidence`), `governance/helper_dependency.py` (`_group_membership_hold`); optionally `dependency/extraction.py` for `expand` | extend `tests/test_hamcp285_group_membership_hold.py` and `tests/test_hamcp274_input_select_relevance.py` with: digit-only id outside the domain (released); a rejected `group.*` id (held); a rejected target-domain id (held); non-dict, non-str or over-bound (held); guest_mode unchanged | the two functions |
| B | split: **PR 1** = A; **PR 2** = registry extension (B1, B2) with its Core-source evidence file and digest re-pin; **PR 3**, only if Josh wants B3 = dispatch domain projection | add `dependency/template_semantic_registry.json`, `dependency/semantic_registry.py`, `dependency/obligation_ledger.py` (category dispatch; B3 `_opaque_from_value`) | per newly registered callable: neutral when unused; preserved provenance when fed to `states()`; still opaque when combined with an unknown value; for B3, a negative case per dispatch filter | registry loader, global, filter and test dispatch in the ledger, and the B3 projection |
| C | B's PRs, plus **one PR per bound class actually hit** (§8 fact 2). C-iii needs no PR. | `dependency/obligation_ledger.py` (`_emit` overflow), plus any constant actually raised | overflow with complete list gives domain closure that excludes the helper; clipped list stays `coverage_failure`; `_AnalysisLimit` stays `coverage_failure`; `front_door_lock_bedtime`-shaped fixture stays elevated while bounded | `_emit` overflow branch and `_normalize_coverage_failure_authority` interplay |

Part 1 itself changes only `docs/evidence/hamcp301/`. It has no version change
and no Evidence run.

## 8. Recommendation and decisions needed from Josh (Q8)

### Recommendation

1. **Do Option A first, as one small 2.4.2 PR.**
   - It removes a false reason code that applies to every helper.
   - It is proven per record, and it is a prerequisite for anything else.
   - Expectation: it will **not** change `engineering_acceptance`'s class by
     itself.
2. **Before choosing between B and C, obtain three live facts.** These need a
   read-only observation that this session must not perform:
   1. the reason-code histogram of the 214 target-capable obligations, by
      automation (from `entity_dependency_analysis` or the plan's
      consumer detail);
   2. the `bounded_sources` `reason_code` and bound for each of the three
      bounded automations;
   3. the state id that `_build_expand_snapshot_evidence` rejects, and the
      class of the malformed label-relevant registry record (§4).
3. **Then:**
   - Take B1 and B2 if C5 or C3-non-entity is a material share of the 214.
   - Address `front_door_lock_bedtime` through C-i or C-iii. Under the hard
     constraint, this one automation decides whether any unreferenced helper
     can ever be standard at this installation.

### Decisions pending Josh

Each item below is **pending Josh**. None has been answered in this session.

1. Which option or options to implement, and in what order.
2. Whether to authorize the read-only live observation for the three facts in
   recommendation 2, and who performs it.
3. Whether the offending state entity, the malformed registry record, or the
   three bounded automations should be fixed operationally in Home Assistant
   (C-iii). That is a live change.
4. Whether Option A should also apply per-record attribution to `expand()`
   resolution (C14).
5. Whether B3, which changes analyzer semantics for dispatch domain proof, is
   in scope.
6. Disclosure: whether an opaque consumer that is `safety_critical` should
   surface the plan's physical consequence as "possibly safety-critical"
   instead of `unknown`. Today it is `unknown`, which already satisfies the
   hard constraint.
7. Whether the dashboard registry `structural_limit` (§1.2) gets its own
   register row.

## 9. Limits of this diagnosis

- No live observation was made. The three facts in §8 are **unknown**, not
  inferred.
- Core behaviour statements marked (I) need exact-version source review at
  the pinned 2026.10.3. That includes Core's entity-id grammar at 2026.10.3,
  the non-entity producers, `integration_entities` semantics and the pure
  template functions.
- Three test bodies were inventoried by name only and were not read:
  - `tests/test_beta52_helper_label_target_scope.py`;
  - `tests/test_hamcp285_planner_matrix.py`;
  - `tests/test_beta54_owner_authoritative_execution.py` after line ~1790.
- The C6 domain-evidence soundness claim (§2.3) and the C-i claim (§3.3) are
  inferences that a Part 2 must prove with negative tests before relying on
  them.
