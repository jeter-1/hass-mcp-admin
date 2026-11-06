# Engineering 2.4.2-beta.4 acceptance

This contract covers HAMCP-310: a condition step in the middle of an action
sequence is a causal helper read. It follows 2.4.2-beta.3 (HAMCP-311, #366).

See also the release notes:
[`V2_4_2_BETA4_RELEASE_NOTES.md`](V2_4_2_BETA4_RELEASE_NOTES.md).

**Release target.** `2.4.2-beta.4`, following 2.4.2-beta.3. The release
transition accepts only `2.4.2-beta.3 -> 2.4.2-beta.4` from the current base.

**Merge-order rule.** This change competes with the 2.4.2 Lows bundle. If
another release PR is ordered ahead of this one at Codex Ready, this branch:
1. merges `main` with a merge commit;
2. restages once to the next free beta;
3. runs final Evidence once, when it is next in line.

The release is materialized in all three version authorities, with no staged
declaration left. Nothing is published or deployed.

The contract has four stages:
1. source and offline acceptance;
2. release CI;
3. publication;
4. installed read-only acceptance.

It authorizes none of the later stages.

## 1. Source and offline acceptance

Bind:
- **The direction.** Josh, 2026-11-05, in the architect chat, relayed in the
  HAMCP-310 contract: use the free lane for HAMCP-310, because
  `control_helper`'s safety-critical warning relies on exact dependency
  evidence. Severity Medium. The fix may only make classification stricter.
- **The cause**, with file and line at the base `468d0278`:
  - `dependency/extraction.py:3309-3310`: `_relation_for` returns
    `action_target` for every key under `action`, `actions`, `sequence`,
    `parallel`, `then`, `else`, `choose` and `repeat`. A condition step's
    `entity_id` at `$.actions[N].entity_id` therefore had relation
    `action_target`;
  - `governance/helper_dependency.py:78-79` and
    `f3_configuration/locks.py:23-25` treat `action_target` as non-causal
    for structured references. A helper read only by such a step projected
    `proven_dependency_neutral`, received no F3 helper lock, and could not
    make `control_helper` warn.
- **The Home Assistant authority.** Core's `ACTIONS_MAP` (line 2125) and
  `determine_script_action` (line 2152) in
  `homeassistant/helpers/config_validation.py` at 2026.11.0 (`74cf5a49`),
  identical at 2026.7.2 (`f9122fb2`) and 2026.9.1 (`fc034572`), map a step
  carrying `condition`, `and`, `or` or `not` to the check-condition action.
  A failing check-condition step stops the rest of its sequence. The same
  file's `_SCRIPT_CHOOSE_SCHEMA`, `_SCRIPT_IF_SCHEMA`,
  `_SCRIPT_REPEAT_SCHEMA`, `_SCRIPT_SEQUENCE_SCHEMA` and
  `_SCRIPT_PARALLEL_SCHEMA` define which step members are action sequences;
  service `data` is accepted as a template-complex mapping, not as an
  action sequence (`SERVICE_SCHEMA`).
- **The non-goals:**
  - no change to the obligation ledger, the policy classes, the semantic
    registry or any other relation;
  - literal action targets (`target`) and service data (`data`) stay
    non-causal, including structure-shaped keys inside a literal payload;
  - script references outside a condition step keep `script_reference`;
  - other gating constructs are reported for follow-up, not changed (see
    the release notes);
  - no tool, schema, route, provider, profile or persisted-format change.
- `2.4.2-beta.4` materialized before Ready, with exact document resolution.
- The PR head SHA, its `validate` run and the Evidence tier log named in the
  PR.

The review needs a security section covering:
- **Stricter only.** Only a step in an executable action position, tracked
  explicitly by every extraction walker (automation `actions`/`action`,
  script `sequence`, and a step's `sequence`, `then`, `else`, `default`,
  `parallel`, or choose/repeat `sequence`), that carries a condition key is
  reclassified, and only from a step relation (`action_target` or, in
  scripts, `script_reference`) to the existing condition family
  (`condition`, `choose_condition`, `repeat_condition`). Every consumer
  treats the new relation as causal, so the change can add dependency
  evidence, consumers, locks and warnings, never remove them. Literal
  service `data`, `data_template` and `target` subtrees are never an action
  position, so their `service_target`, `action_data` and `action_target`
  relations and their neutral projection are unchanged, whatever keys the
  payload carries.
- **No new reachability.** No new tool, route, dispatch, write or fallback.
  `control_helper` can ask for a confirmation it did not ask for before; it
  dispatches nothing new.
- **Fail-closed identity change.** The relation is part of each structured
  obligation's `evidence_id` and `expression_fingerprint`. For automations
  and scripts with a condition step in a sequence, those values change, so
  a binding captured on 2.4.2-beta.3 no longer matches and is refreshed or
  refused. No stored format changes. Literal payload references keep their
  identities.

Required offline evidence is in
`tests/test_hamcp310_sequence_condition_causal.py`:
- **Extraction**, for findings and obligations: a condition step in top-level
  `actions`, legacy `action`, a choose sequence and default, if `then` and
  `else`, a parallel branch and a nested sequence in parallel, a repeat
  sequence, a nested `sequence` step, the `and`/`or`/`not` shorthand forms
  (list and mapping), a deep choose/if/parallel nesting and a script `if`
  branch all get a condition-family relation, and their obligations are
  `exact_dependency`;
- **Script sequences** (round 1, Codex L1), for findings and obligations:
  plain, nested `sequence`, shorthand `and`/`or`/`not`, parallel, if
  `then`, choose and repeat condition steps get exactly `condition`,
  `choose_condition` or `repeat_condition`, and their evidence IDs move off
  the `script_reference` form. Unrelated script references keep
  `script_reference`;
- **Unchanged positions:** top-level `conditions` (`condition`), choose
  `conditions` (`choose_condition`), `if` (`if_condition`), repeat `while`
  (`repeat_condition`) and a nested `condition: and` keep their relations;
- **Literal targets stay non-causal:** `target.entity_id` (`service_target`),
  `data` that carries condition-shaped keys (`action_data`), a legacy
  step-level `entity_id` on a service step and a device action entity
  (`action_target`), and a condition-keyed mapping inside `target`
  (`service_target`). Each projects `proven_dependency_neutral`;
- **Literal payloads with structure-shaped keys** (round 1, Codex M1): a
  condition-shaped mapping under `sequence`, `then`, `else`, `parallel`,
  `actions`, `repeat` (list and options form), `default`, `choose` options
  or a shorthand `and`, placed directly in, or one `data` level below,
  `data`, `data_template`, `service_data` or `target`, keeps its previous
  relation and projects `proven_dependency_neutral` (80 cases). Template
  reads inside such a payload stay `exact_dependency`. A blueprint input
  used there resolves to `action_target`, not `condition`;
- **Blueprints:** a blueprint input used by a sequence condition step
  resolves to the `condition` role; an input used as a lock target stays
  `service_target`;
- **Projection over a full synthetic index** (guest-mode-shaped: a
  `person` trigger, a `condition: state` step on `input_boolean.guest_mode`,
  then `lock.unlock`): the binding has `exact_dependency_obligation_count` 1,
  `physical_consequence` `safety_critical`, the projected obligation
  `condition` / `exact_dependency`, and one `referenced` safety-critical
  consumer. A helper used only as a literal target and in service data
  has no exact obligation, no consumer and no safety-critical consequence.
  A helper read only inside the 80 structure-shaped literal payloads, beside
  a `lock.unlock`, has no exact or relevant obligation, no consumer and no
  causal source;
- **F3 locks:** every placement above receives the exact helper lock, as does
  an `input_select` in an if-branch condition step. Literal targets, data
  and a legacy step-level `entity_id` receive no helper lock and no
  unconstrained guard, and neither do the 80 structure-shaped literal
  payloads. A genuine nested shorthand condition step beside such a payload
  takes the exact lock for its own helper only;
- **`control_helper`**, over the shipped evidence reader and a real index:
  turning on a helper read only by a mid-sequence condition step returns
  `confirmation_required` with `no_dispatch`, no task and no write, and
  names the consumer. The acknowledged retry succeeds with one write. A
  literal-target helper changes without a confirmation;
- **Regression:** the beta37, beta38, beta47, beta48, beta49, beta50 and
  beta54 helper-dependency suites, `test_typed_helper_value` (including the
  existing guest-mode confirmation), the entity and script dependency
  analysis, the fast index, dependency build authority and the F3 lock
  suites pass with no changed test;
- **Mutation testing** in a copied tree, counting only assertion failures as
  kills:
  - reverting condition steps to `action_target`;
  - reclassifying only top-level steps, which misses nested sequences;
  - reclassifying any condition-keyed mapping, which makes a literal target
    causal;
  - making `service_target` causal in the projection, and separately in the
    F3 locks;
  - making `action_data` causal;
  - round 1: treating a service `data` subtree as an action position;
    treating a `target` subtree as one; dropping the script-sequence
    (`script_reference`) handling;
- the complete repository suite in the Evidence tier.

## 2. Release CI

The exact materialized head's `validate` check passes, including the
release transition.

## 3. Publication

Publication happens only through Josh's Ready action and the repository's
head-bound workflow.

## 4. Installed read-only acceptance (separately authorized, after deploy)

This stage runs only after Josh separately authorizes deploying this
release. It is the architect's, read-only, and changes no helper.

1. **Identity.** `server_info` and `get_server_health` report
   `2.4.2-beta.4`, the merged release SHA as build SHA and `dirty: false`.
2. **The defect.** The architect looks for a real helper that is read by a
   condition step inside an action sequence (not in top-level `conditions`)
   in Josh's automations, using read-only configuration and
   `entity_dependency_analysis` reads. If one exists:
   - its dependency evidence lists the automation with relation `condition`,
     `choose_condition` or `repeat_condition` at that step's path, as
     `exact_dependency`;
   - the automation appears as a `referenced` consumer for that helper.

   If none exists, record that no mid-sequence condition step reading a
   helper was found. That is not a failure.
3. **Literal targets.** A helper that an automation only targets with a
   service call is still not listed as a causal consumer for that
   automation.
4. **Tool catalog.** A fresh `tools/list` shows the same tool names and
   descriptors as 2.4.2-beta.3.

PASS needs every applicable check. If a step fails, record the response in
HAMCP-310. Whether to roll back is Josh's decision.
