# HAMCP-212 offline evidence

Synthetic model only, not production. No live Home Assistant was used.

## Parity

`parity.py` builds the dependency index from the HAMCP-209 automation fixtures
(`tests/hamcp209_fixtures.py`) plus the HAMCP-212 script fixtures
(`tests/hamcp212_fixtures.py`) with the provider of the tree under test. Two
fixtures are used:

- `representative`: plain, templated, dynamic, calling, blueprint-instance,
  nested-blueprint caller, unavailable and categorized scripts;
- `generated_113_5`: 113 automations and 5 scripts.

It writes, per fixture:

- the HAMCP-209 automation index view;
- the DependencyIndex snapshot fingerprint;
- the script view (findings, dynamic references, call findings, call gaps and
  coverage minus timing, policy and provider);
- the `entity_dependency_analysis` output for each fixture target.

The analysis `index` block (timing, generation, query fingerprint), the script
provider attribution and the script policy change by design. They are written
under `attribution` and are not compared.

```sh
# Python 3.12 venv built from the hashed locks; base tree = git archive f69bf8c hass_mcp_engineering_beta
python docs/evidence/hamcp212/parity.py --tree <base tree> --output base-f69bf8c.json
python docs/evidence/hamcp212/parity.py --tree . --output head.json
python docs/evidence/hamcp212/parity.py --compare base-f69bf8c.json head.json
```

The comparison is **IDENTICAL**: the analysis, automation index, script view and
fingerprint all match for both fixtures. Delegated script reads went from 5 to 0
(`generated_113_5`) and from 8 to 3 (`representative`). The three remaining
reads are the counted fallbacks `blueprint_instance`,
`blueprint_reference_nested` and `loaded_entity_unavailable`. `parity.json`
summarizes both trees.

## Measurement

`measure.py` runs one index build against the same model. Each delegated stored
script read costs 1.86 s, the beta.23 live average (5 reads, 9.3 s cumulative).
Each WebSocket command costs 0.5 ms.

- The serialized model (`--serialize-delegated`) reproduces the live wall time:
  the live build took 8.8 s and the model's base 9.8 s.
- The concurrent model, with the base's 8 workers fully parallel, is the
  optimistic bound.

```sh
python docs/evidence/hamcp212/measure.py --tree <base tree> --label base-serialized --serialize-delegated --output base-serialized.json
python docs/evidence/hamcp212/measure.py --tree . --label head-serialized --serialize-delegated --output head-serialized.json
python docs/evidence/hamcp212/measure.py --tree <base tree> --label base-concurrent --output base-concurrent.json
python docs/evidence/hamcp212/measure.py --tree . --label head-concurrent --output head-concurrent.json
```

| Model | Before (beta.24) | After (HAMCP-212) |
| --- | --- | --- |
| Serialized delegated reads | 9800.2 ms, 5 delegated reads, 226 batch commands | 505.5 ms, 0 delegated reads, 236 batch commands |
| Concurrent delegated reads | 2348.7 ms | 488.5 ms |

Both runs used the same host, back to back. After the change, script coverage
reports provider `direct_ha_api`, `script_config_source=loaded_websocket` and
source counts `loaded_websocket: 5, stored_delegated: 0`. `measurement.json`
holds the combined results.

## Mutations

`../hamcp212-mutations/run_mutations.py` applies each mutation to a disposable
copy outside the repository and runs `tests.test_hamcp212_loaded_script_config`.
All 12 mutations were killed, none by an import or syntax error (`results.json`).
