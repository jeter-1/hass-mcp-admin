# Engineering 2.4.2-beta.4 release notes

Engineering 2.4.2-beta.4 (HAMCP-310) treats a condition step in the middle
of an action sequence as a causal helper read. It builds on 2.4.2-beta.3
(HAMCP-311, #366), and its runtime is otherwise identical to it.

Acceptance: [`V2_4_2_BETA4_ACCEPTANCE.md`](V2_4_2_BETA4_ACCEPTANCE.md).
Per-release records: [release index](RELEASES.md).

## The defect

Home Assistant runs a step that carries `condition`, `and`, `or` or `not` as
a check-condition action: if it fails, the rest of the sequence does not
run. In this automation, `input_boolean.guest_mode` decides whether the door
unlocks:

```yaml
triggers:
  - trigger: state
    entity_id: person.resident
    to: home
actions:
  - condition: state
    entity_id: input_boolean.guest_mode
    state: "on"
  - action: lock.unlock
    target:
      entity_id: lock.front_door
```

The dependency extractor labelled every entity under an action path as
`action_target`, the relation for an entity an action acts on. The helper
projection and the F3 configuration locks treat that relation as non-causal,
so a helper read only by such a step was reported as
`proven_dependency_neutral`:
- the automation was not listed as a consumer of the helper;
- an F3 plan that changed the automation took no lock on the helper;
- `control_helper` could not warn before a change to that helper, because its
  safety-critical warning needs exact dependency evidence.

Top-level `conditions`, choose `conditions`, `if` and repeat `while`/`until`
were already causal. A helper read in one of those places, as well as in a
sequence step, was handled correctly.

## The change

Home Assistant Core decides a step's type from its keys (`ACTIONS_MAP` and
`determine_script_action` in `homeassistant/helpers/config_validation.py`,
verified identical at 2026.7.2 `f9122fb`, 2026.9.1 `fc03457` and 2026.11.0
`74cf5a49`, lines 2125 and 2152).

A step in an executable action position that carries `condition`, `and`,
`or` or `not` now gives its entity reads the same relation as a condition in
that position: `condition`, `choose_condition` inside a choose option, or
`repeat_condition` inside a repeat. The extractor tracks the action position
explicitly while it walks the configuration. A position is executable only
when it is reached through action structure:
- automation `actions` and the legacy `action` key, and a script's
  `sequence`;
- inside a step: `sequence`, `then`, `else`, `default` and `parallel`, and
  the `sequence` of a `choose` option or of a `repeat`.

This holds at any depth, including:
- top-level actions and the legacy `action` key;
- choose option sequences and `default`;
- if `then` and `else`;
- `parallel` branches and nested `sequence` steps;
- repeat sequences;
- script sequences, plain, nested and in the shorthand `and`/`or`/`not`
  forms, the same as automations;
- blueprint inputs used in such a step.

For such a helper:
- the dependency evidence is `exact_dependency`;
- the automation is a `referenced` consumer, with its physical consequence;
- an F3 plan touching the automation takes the exact helper lock;
- `control_helper` returns `confirmation_required` before changing it when
  that consumer is safety-critical.

Nothing else is reclassified. A helper named as a service call's `target`,
in its `data` (or `data_template`), as a legacy step-level `entity_id` on a
service call or as a device action entity is still the thing the action
acts on, and keeps its relation (`service_target`, `action_data` or
`action_target`) and its `proven_dependency_neutral` projection. That holds
even when the literal payload contains keys named `sequence`, `then`,
`else`, `default`, `parallel`, `actions`, `repeat` or `choose`: a payload is
data passed to the service, not an action sequence Home Assistant runs.
Template reads inside such a payload stay `exact_dependency`, as before.
Script entity references outside a condition step keep `script_reference`.
The change can only add evidence, consumers, locks and warnings.

The relation is part of each structured obligation's evidence ID and
expression fingerprint, so those values change for the affected references:
- an automation condition step in a sequence (previously `action_target`);
- a script condition step: previously `script_reference` at the top of the
  sequence, in nested `sequence` steps and in choose, `default` and repeat
  sequences, which was already `exact_dependency`, and `action_target` in
  `then`, `else` and directly in `parallel`, which was not.

A plan or binding captured on 2.4.2-beta.3 that covers such an automation or
script no longer matches and is refreshed or refused, as for any changed
dependency evidence.

## Other gating constructs (not changed)

These were checked offline at the base and are recorded for follow-up:

| Construct | Current classification | Assessment |
| --- | --- | --- |
| `wait_for_trigger` with an entity trigger | Own `wait_for_trigger` relation, causal | No gap. |
| repeat `while` / `until` | `repeat_condition`, causal | No gap. |
| `if` / `stop` inside an `if` | `if_condition`, causal | No gap. |
| `condition: template` step | `condition`, template obligation | No gap. |
| `wait_template`, a template shorthand step, `variables`, a templated `enabled`, a templated target | Labelled `action_target` (`service_target` for a templated target), but template obligations are `exact_dependency` | Causal in both the projection and the locks: only structured references are neutralized. The label is misleading in disclosed evidence. Low, cosmetic. |
| An entity in `above`/`below` (numeric_state) or `zone` (zone condition) | Not extracted, in any position | Real gap: such a helper read is invisible to the dependency index, including in top-level conditions. Follow-up candidate, Medium. |
| A step carrying both `delay` and `condition` | Classified as a condition | Home Assistant runs it as a delay. Stricter only; no action. |

## Unchanged by design

- The obligation ledger, policy classes, semantic registry and every other
  relation.
- Literal `target` and `data` relations, including structure-shaped keys
  inside a literal payload.
- Script references outside a condition step (`script_reference`).
- Tool and input schemas, routes, providers, signed profiles, contract
  fingerprints, persisted formats and add-on options.

Static tools stay at 81. The advertised catalog stays at 109, or 110 with
the optional YAML reader.

## Known limitations

- The gating constructs above are unchanged; entity references in
  numeric_state `above`/`below` and zone `zone` remain unextracted.
- The known issues carried by 2.4.2-beta.3 are not addressed here, apart
  from this defect.

## Rollback

Reinstall 2.4.2-beta.3. It has the same storage, plan and task formats.
Helpers read only by a mid-sequence condition step are again reported as
dependency-neutral, and plans captured on 2.4.2-beta.4 for affected
automations are refreshed or refused. An image rollback does not restore
household configuration or physical state.
