# HAMCP-195 gate probe (design evidence)

`probe_gate.py` is a design-validation script for
[`docs/HAMCP_195_GATED_HELPER_TOGGLE_DESIGN.md`](../../HAMCP_195_GATED_HELPER_TOGGLE_DESIGN.md).
It is not runtime code and not a test. It builds the shipped HAMCP-209
dependency index over fixture Home Assistant data. Then it computes the shipped
`helper-dependency-risk-v13` binding for each `input_boolean` and applies the
two candidate gates from the design's §4:

- `strict`: the gate passes only when the binding is what governed policy
  would classify as standard (complete, exact, consequence `none`);
- `owner`: the gate passes when evidence is complete and exact, no consumer
  is `unknown` or `safety_critical`, and every `direct` effect target is a
  literal entity that is not a HAMCP-069B OD-4 protected object.

Nothing contacts Home Assistant. No credential is read.

## Inputs

| Input | Provenance |
| --- | --- |
| `tests/fixtures/hamcp213_pr001/op{1,2,4}_after.yaml` | Anonymized copies of the owner's real PR001 configurations (see that directory's `PROVENANCE.md`). |
| Synthetic consumers `automation.synthetic_*` | Written in the probe. Each one exercises one consequence class. |
| `tests/fixtures/dependency/hamcp089_beta51_label_target_scope_replay_v1.json` | Sanitized Beta 51 production replay (HAMCP-089), used unchanged through the same test harness classes. |

The synthetic Home Assistant reports the newest compiled supported release
(`supported_home_assistant_versions()[-1]`), so that version admission is not
what decides the gate.

## Scenarios

| Scenario | Automations |
| --- | --- |
| `pr001_house` | op1, op2, op4 and every synthetic consumer, including a dynamic-selector template |
| `op4_is_number_as_test` | The same, but op4's `is_number(opened_at)` global call is rewritten as `(opened_at is number)`. This is for cause isolation only. |
| `no_opaque_consumers` | op1, op2 and the synthetic consumers, without op4 and without the dynamic-selector template |
| `beta51_production_replay` | The sanitized production replay. `target_standard` is the standard control; `entity_001` is the other helper in the capture. |

## Reproduce

Run this from the repository root, using an environment built from
`hass_mcp_engineering_beta/requirements.lock` and `tests/requirements.lock`
with `--require-hashes`:

    PYTHONPATH=tests python docs/evidence/hamcp195/probe_gate.py > probe_gate.out

Optional arguments: `--scenario NAME` runs one scenario, and
`--dump input_boolean.X` prints that helper's full binding.

Two consecutive runs on base `91ab868d10924caeaa268e005d5cee5b318cab7c`
produced byte-identical output.

| File | sha256 |
| --- | --- |
| `probe_gate.py` | recorded in the PR description |
| `probe_gate.out` | recorded in the PR description |
