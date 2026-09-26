# Bounded script-call dependency analysis

## Decision and responsibility

On 2026-09-26 Josh approved the audit-driven roadmap and this first increment:
read-only automation → script → entity and script → script paths in
`entity_dependency_analysis`. This supersedes the earlier helper/administration
next-step selection; it does not change execution authority. Engineering owns the
semantic graph; existing direct automation/registry reads and the admitted
`ha_config_get_script` provider supply evidence. Provider selection remains inside
the single Engineering endpoint. The client explains limits and chooses follow-up
inspection. Deployment and installed acceptance remain separate decisions.

This increment does **not** close all of HAFA-G001. Complete script inventory,
scene/helper/group/template/dashboard adapters and general all-family traversal
remain future work. The alternative was lifecycle analysis first; reconsider the
order if a concrete household lifecycle incident becomes more urgent.

## Useful behavior

The existing input schema is unchanged. `include_indirect=true` adds invocation
paths to the existing group/template traversal. For script paths, `max_depth`
counts reference edges, strictly 1–3. For example:

```text
automation.nightly → script.outer → script.renamed → light.fixture
```

A request for `light.fixture`, `source_types:["automation"]`,
`include_indirect:true`, `max_depth:3` can return `automation.nightly` with depth
3 and three ordered evidence IDs. Filtering reported sources does not discard
script intermediates. Evidence runs from the target's containing script outward
to the caller; the returned source identity and config path identify that caller.
The page-local `path_evidence` table resolves each path ID to its canonical source,
current entity, target, configuration path and supplying provider. A state trigger
or condition watching a script does not inherit its output effects.
These are static potential references, not proof that conditions pass, calls
succeed or a script has executed. Branch conditions are not evaluated.

Supported invocations are literal `action`/`service`/`service_template` direct
`script.<storage_key>` calls, and `script.turn_on` with explicit literal script
entity IDs in `target`, `data` or legacy `data_template`. Direct services resolve
through the unambiguous registry storage-key/current-entity mapping; replacing the
prefix or assuming a renamed entity retains its service name is not allowed.
Exact explicit missing entity targets remain references with an identity gap.
An unresolvable direct service name remains a gap, not a guessed entity edge.

The walker visits action roots and sequence, choose/default, if/then/else,
repeat and parallel bodies. Disabled actions are skipped; unresolved enabled
values are gaps. Prose, variables, notification payloads and state observations
are not invocation edges. `script.turn_off`/`reload` are not calls. Toggle,
generic `homeassistant.turn_on`/`toggle`, dynamic service/target expressions,
area/device/label/floor selectors, ambiguous selectors and unexpanded blueprints
remain explicit gaps. Templates are never compiled, rendered or executed.

## Coverage, bounds and truthful output

The script inventory remains partial. A zero result cannot prove global absence.
The additive `script_call_graph` output carries considered/completeness state,
provider names, the existing script provider policy, fallback status, limitations,
gap categories and traversal bounds. Coverage-dependent automation-only queries
remain partial even when their direct automation inventory is complete. Script
coverage and provider-policy details are retained when a script is only an
intermediate. The graph is built from one published index generation; the
underlying HA reads are not an atomic snapshot.

Per configuration, action scanning stops at 10,000 nodes or depth 32; explicit
target parsing also has a shared 10,000-item budget. Invocation
edges are limited to 10,000 across the diagnostic partition. Reverse traversal
allows at most 5,000 edge visits and 500 indirect results, retaining distinct
paths within those bounds. Cycles, maximum depth and processing/result bounds
produce explicit categories. Pagination handles retained findings only; a cursor
cannot recover paths omitted by a processing bound. Existing source selection,
read concurrency, deadlines, dynamic-reference and response-size limits remain.

No new reads or dispatch routes are introduced. Existing automation configurations
are reused; script reads retain the reviewed gateway, sanitization and same-session
admission. Optional-source failure preserves readable neighbors. Call evidence and
gaps participate in the diagnostic fingerprint and query cursor. Provider
retirement withholds dependent cached evidence and invalidates its cursor; a later
normal rebuild can recover. The helper/impact/integrity shared snapshot, obligation
ledger, locks, plan identity and execution bindings are unchanged. There is no
script execution or fallback.

## Exact source evidence

Home Assistant Core **2026.9.3**, commit
`6de5eb18cd4502f94af44cfff3a02250d88716ed`, supplies these semantics:

- [Script component](https://github.com/home-assistant/core/blob/6de5eb18cd4502f94af44cfff3a02250d88716ed/homeassistant/components/script/__init__.py):
  `turn_on_service` resolves selected entities; `ScriptEntity.async_added_to_hass`
  registers a direct service using `unique_id`, independently of entity renaming.
- [Script helper](https://github.com/home-assistant/core/blob/6de5eb18cd4502f94af44cfff3a02250d88716ed/homeassistant/helpers/script.py):
  action/branch execution and service-call preparation are separate from arbitrary
  payload dictionaries.
- [Script tests](https://github.com/home-assistant/core/blob/6de5eb18cd4502f94af44cfff3a02250d88716ed/tests/components/script/test_init.py):
  direct and entity-targeted script services and validation behavior.

The [source receipt](evidence/script-call-source-review.json) records downloaded
file hashes. Existing [reader evidence](evidence/script-dependency-source-review.json)
binds ha-mcp **8.5.0** at `311d6dc273fb4e9a5b8cde0de15f69472a64fe44`.
These references establish reviewed semantics, not new runtime version pins,
compatibility authority, integration-test success or deployment.

## Acceptance and recovery

Offline tests must prove renamed direct calls, explicit targets, nested chains,
source filtering, distinct paths, cycles/depth/work bounds, false-positive controls,
partial reads, redaction, cache/cursor behavior, provider retirement and unchanged
helper/impact evidence. Full/Evidence and a separately tasked independent review
are required before delivery readiness.

The existing disposable `real_ha_contract_tests.py` script scenario now also
stores a direct call to a subsequently renamed script and an explicit `turn_on`
caller. It checks actual Core service registration and two-/three-edge diagnostic
paths through the admitted reader. It never executes the scripts, and retains its
read capture, fault isolation and cleanup. Source tests do not substitute for an
executed exact Core 2026.9.3 / ha-mcp 8.5.0 lane; report that lane separately.

After a separately authorized release/deployment, acceptance uses one known
read-only call chain with exact configuration identity, confirms provider and
coverage metadata, repeats once to verify cache continuity, and checks settlement.
No device cycle, script execution or repeat beta.3 dashboard canary is needed for
this diagnostic feature. A reviewed source revert recovers the previous diagnostic
behavior; no persistent record migration or execution rollback is introduced.
