# HAMCP-195 risk-gated fast toggles for input_boolean helpers — design

Status: **Part 1 design. Josh's decisions in §11 are pending.** This document
authorizes nothing. It does not authorize implementation, a release,
deployment, live Home Assistant access, registry signing, or a change to
existing approval behavior. Part 2 starts only after Josh records decisions in
a new §0.

Base: `main` at `91ab868d10924caeaa268e005d5cee5b318cab7c` (Engineering
`2.4.1-beta.32`, 65 static tools). All file references are relative to
`hass_mcp_engineering_beta/ha_mcp_engineering/` unless the path starts at the
repository root.

## 1. Scope and goal

**Owner goal (HAMCP-195 row).** Everyday helpers such as guest mode, cleaner
mode or vacation mode should switch quickly, without the plan, approve and
apply round trip, when they are proven safe. Everything else keeps the
governed path (`create_helper_state_plan`, HAMCP-089).

**Recorded owner rule.** The fast path is allowed only when dependency
analysis shows no high-consequence consumers **and** coverage is complete.
Otherwise it fails closed.

**In scope:**

- `input_boolean` only;
- `turn_on` and `turn_off` on one exact, existing entity.

**Out of scope:**

- other helper types;
- creating, deleting or reconfiguring helpers;
- reloads;
- any change to the governed path;
- new permissions or add-on configuration.

## 2. Consumer analysis today (what the index can prove)

### 2.1 The existing helper risk binding

`governance/helper_dependency.py:1275` `build_helper_dependency_risk_binding`
(model `helper-dependency-risk-v13`, `:25`) is the reviewed answer to "who
consumes this helper, and what can they do?". The governed path already uses
it to choose between standard, elevated and prohibited
(`governance/policy.py:1042-1172`). It provides the following.

**Consumers.** It reverse-looks-up automation and blueprint findings in the
HAMCP-209/212 index snapshot.

- Causal relations are triggers, conditions and templates.
- `action_data`, `action_target` and `service_target` are non-causal: writing
  the helper is not consuming it.
- At most `MAX_RELEVANT_AUTOMATIONS = 50` relevant consumers are kept (`:52`);
  more than that blocks execution.

**Consequence.** Each consumer gets a downstream profile from
`governance/risk.py:1531` `automation_action_consequence_profile`. Precedence
is safety_critical > unknown-if-unresolved > direct > unknown-if-incomplete
> none.

| Class | Meaning |
| --- | --- |
| `safety_critical` | lock, alarm, valve, garage cover (id), water control, unlock/disarm, sensitive blueprint input |
| `direct` | Literal service on alarm_control_panel, climate, cover, fan, light, lock, switch, valve, water_heater |
| `unknown` | Any script, scene or automation call; generic `homeassistant.*`; unrecognized domain (`media_player`, `tts`, `input_boolean.*`, …); unreviewed notify; templated service or target |
| `none` | Static notify, reviewed Companion UI controls, or no effect |

**Evidence quality.** These fields describe how complete the evidence is:

- `coverage_complete`;
- `consequence_evidence_complete`;
- `semantic_precision` (`exact`, `bounded_opaque` or `coverage_failure`);
- `execution_eligible`;
- `execution_block_reason_codes`, which covers index not fresh, payload
  truncated, and Home Assistant version unavailable, unreadable or
  unsupported.

A target-capable opaque template **anywhere** in the house makes the evidence
incomplete for every helper that template could select (§3.2, §6).

**Identity.**

- `dependency_index_generation`, `_fingerprint`, `_source_epoch`;
- `evidence_fingerprint`;
- `dependency_lock_projection`, which the F3 lock calculator turns into the
  exact lock graph.

### 2.2 What the binding does not see

| Path | Current state | Evidence |
| --- | --- | --- |
| A consumer automation calls a script | The profile becomes `unknown`. The callee is **not followed**. | `governance/risk.py:1531` |
| A script reads the helper directly (condition or template) | **Invisible to the binding.** Scripts are scanned into a separate diagnostic partition that only `entity_dependency_analysis` reads. | `dependency/script_sources.py:256`, `dependency/provider.py:1815-1834`, `dependency/service.py:109-120`; `helper_dependency.py` never mentions scripts |
| Script coverage | At best `partial`: package-defined scripts are not enumerable, and dynamic calls are unknown | `dependency/script_sources.py:30-33, 486-497` |
| Template sensors, groups, scenes, dashboards as consumers | Coverage hard-coded to `unavailable` | `dependency/provider.py:1789-1797` |
| Indirect chains (helper → template sensor → automation) | Traversal code exists, but no provider emits group or template findings | `dependency/service.py:273-314` |
| Protected objects by device class | `protected_object_reason` is id-only (`governance/physical_effects.py:250`). The HAMCP-069B OD-4 union (`governance/entity_availability_exposure_planning.py:189` `object_protection`) adds device class garage/gate/door covers, sirens and safety sensors, but only for an entity being planned, not for a downstream effect target. | |
| Called-script physical effects | `compare_physical_effects` (`governance/physical_effects.py:1293`) follows callees to depth 8, but only for HAMCP-213 configuration changes with a called-script snapshot | `:199`, `:1453` |
| Consumers outside Home Assistant's own config | Integration-owned consumers (alarm, generic_thermostat, bayesian), Node-RED, AppDaemon, external WebSocket subscribers | Not readable by any reviewed surface |

## 3. Coverage gaps and proposed treatment

### 3.1 Gap table

| # | Gap | Can it cause a physical effect when the helper flips? | Proposal |
| --- | --- | --- | --- |
| G1 | Index stale, fenced, truncated, version unsupported | Unknown | **Blocks.** These are the binding's existing execution blockers. |
| G2 | Target-capable opaque template anywhere that could select this helper | Unknown | **Blocks.** This is inherent in `coverage_complete`. See the §6 finding: today this blocks many or all helpers. |
| G3 | Consumer automation calls a script | Yes, through the callee | **Blocks** under OD-B (i), or the callee is followed under OD-B (ii) |
| G4 | A script reads the helper directly | Only when something calls the script. Automation callers are already consumers (G3), so the gap is human-, dashboard- or voice-triggered scripts. | Proposal OD-C |
| G5 | Script coverage structurally partial (package scripts) | As G4 | **Disclosed, not blocking** (OD-C) |
| G6 | Indirect one hop (helper → template/group/derived entity → automation) | Yes | Proposal OD-D |
| G7 | Scenes | No. A scene *writes* states; it does not react to the helper. | **Not blocking.** Disclosed. |
| G8 | Dashboards | No. Conditional cards only show or hide UI; a human still presses the button. | **Not blocking.** Disclosed. |
| G9 | Integration-owned or external consumers (Node-RED, AppDaemon, alarm/thermostat integrations, Bayesian) | Yes | Cannot be read. **Disclosed in every receipt.** Mitigated by the owner exclusion label (OD-E). |

### 3.2 Why G2 matters (finding)

The binding treats an unreviewed template call whose target could be any
`input_boolean` as a coverage failure for every `input_boolean`. This is
correct fail-closed behavior: the analyzer cannot prove which helper the
template reads. The probe (§6) shows that the owner's own anonymized evening
garage alert (`tests/fixtures/hamcp213_pr001/op4_after.yaml`, condition 2)
calls `is_number(...)` as a global function. That callable is absent from the
compiled semantic registry (`dependency/obligation_ledger.py:2255-2280`,
`unknown_callable_binding`). With that automation present, **no helper in the
house qualifies under either gate.** Rewriting only that call as the Jinja test
`opened_at is number` removes the coverage failure (scenario
`op4_is_number_as_test`). The remaining `bounded_opaque` precision comes from
the deliberately dynamic synthetic consumer.

Whether op4's text is live today is unknown: it was the owner's proposed
HAMCP-213 configuration, and this task reads nothing live. If it is live, the
fast path is inert until one of these happens:

- `is_number` is reviewed into the semantic registry. This is a runtime
  semantic change with its own review; it is not part of this task.
- The owner rewrites that template.

## 4. The fast-path rule (candidate)

The fast path runs only when **every** rule below holds on a **fenced** binding
recomputed after the locks are held (§5.2). Anything else is refused.

**R1, evidence.**

- The model is `helper-dependency-risk-v13`.
- `execution_contract_complete` and `execution_eligible` are true, and
  `execution_block_reason_codes` is empty.
- `coverage_complete` and `consequence_evidence_complete` are true.
- `semantic_precision == "exact"`.
- The index is fresh (soft TTL 600 s; a fenced rebuild is forced).
- It is not truncated, and the version is admitted.

**R2, consequence threshold (OD-A).**

- **A1 strict:** `physical_consequence == "none"`. This is exactly the set the
  governed path already classifies as standard.
- **A2 owner-literal:** no consumer is `unknown` or `safety_critical`. Every
  `direct` consumer's effect targets are literal entity ids, and none is
  protected under the OD-4 union. The union is evaluated on the **effect
  target** using the registry `device_class`: lock, alarm, valve, siren;
  cover classes garage, gate and door (an unresolved cover class counts as
  protected); safety sensors; water terms.

**R3, called scripts (OD-B).**

- (i) Any consumer that calls a script refuses. This is already true because
  the profile is `unknown`.
- (ii) Callees are followed with `compare_physical_effects` over a
  called-script snapshot (depth ≤ 8). Any protected or unresolved callee
  refuses.

**R4, scripts reading the helper (OD-C).** Read the script diagnostic
partition and refuse if any of these holds:

- it is `unavailable` or bounded;
- a script causally references the helper and that script has a protected or
  unresolved effect.

Structural `partial` coverage (package scripts) is disclosed, not blocking.

**R5, indirect one hop (OD-D).** For each consumer of any protected entity, or
of any entity with an `unknown` or `safety_critical` profile, refuse if any of
its causal trigger or condition sources is a derived entity that could carry
this helper's state. Derived means the registry platform is in a closed set
(`template`, `group`, `bayesian`, `threshold`, `min_max`, `trend`, `derivative`,
`statistics`, `filter`, `utility_meter`, `switch_as_x`) or the entity is not
registered.

- Groups can be cleared by their `/states` `entity_id` membership.
- Template-derived entities cannot be cleared from the reviewed reads, so they
  refuse.

Indexing template definitions would be a new read surface and a separate task.

**R6, exclusion (OD-E).** A helper carrying the owner-applied Home Assistant
label `hamcp_protected` always refuses, whatever the evidence says.

**R7, scope (OD-F).** `turn_on` and `turn_off` only, with no `toggle`, so the
requested end state is explicit and idempotent.

**Refusal** (rule (b) of the brief):

- no dispatch;
- the response states `executed: false`, `decision: refused`, the exact reason
  codes, and a pointer:
  `governed_alternative: {"tool": "create_helper_state_plan", "entity_id": …, "state": …}`;
- no plan is created automatically and nothing is silently downgraded;
- the refusal is audited.

## 5. Mechanics (proposed `control_input_boolean`)

The tool is named for the domain. "helper" would collide with the Core family
`core.typed_helper_operation`.

### 5.1 Schema

| Field | Type |
| --- | --- |
| `entity_id` | `^input_boolean\.[a-z0-9_]+$` |
| `action` | `Literal["turn_on", "turn_off"]` |
| `operation_id` | `^[0-9]{10}-[a-f0-9]{32}$`; same freshness window as control_power: 30 s in the future to 300 s old |
| `check_only` | `bool = False`; returns the gate decision with no locks, intent or dispatch, for acceptance and for listing eligibility |

Outputs pass through the strict sanitizer, as control_power's do.

### 5.2 Flow

1. **Replay guard.**
   - Namespace `ordinary-input-boolean-v1`, using the same store bounds as
     `power/service.py:27` (4096 records, 16 active).
   - A known `operation_id` with the same arguments returns the read-only
     reconcile of the original operation.
   - Different arguments are refused (`rebound`); expired or full stores are
     refused.
2. **Unfenced binding** via `HelperDependencyRiskService.assess(refresh=True)`,
   then the R6 label check and existence (the exact target must exist).
3. **Locks.** Use the governed lock graph from the F3 calculator for the
   binding (`f3/operational_locks.py:245-300`): the helper EXCLUSIVE; the
   `reload:input_boolean`, `reload:automation` and helper-dependency fences
   SHARED; downstream automation locks SHARED.
   - Acquire with zero wait (`LockTiming(30, 10, 0)`, as control_power does).
   - Contention is refused as `lock_conflict`, naming the blocking tasks.
4. **Fenced binding** (`fenced=True`) after the locks are held. Re-evaluate
   R1-R6.
   - The fenced `dependency_lock_projection` must be a subset of the held
     locks; otherwise refuse with `dependency_lock_drift`, with no
     re-acquisition loop.
5. **State preflight** through `HelperStateGateway.read_state`. If the helper
   is already in the desired state, return a verified no-op without dispatch.
6. **Durable intent, then at-most-once dispatch** through
   `SharedOperationExecutor`, using `HelperStateGateway.set_state`. This is
   WebSocket `call_service input_boolean.turn_on|turn_off` on the exact target,
   the same permitted command as HAMCP-089.
   - `DISPATCH_INDETERMINATE` is never retried.
7. **Verification.**
   - Inline polls (1, 1, 2, 2, 2, 2 s) within a 10 s budget, then
     `observing`.
   - A 30 s sweep, and a final read at the 180 s deadline.
   - A contrary observed state (for example an automation flips it back) ends
     in manual review with a conflict hold. This is the same lifecycle and
     `_get_execution_task` integration as control_power.

### 5.3 Receipt and audit

The receipt has control_power's fields: `task_id`, `operation_id`,
`authorization`, `provider`, `contract`, `fallback: none`, `state`,
`terminal`, `verification`, `redispatch_prohibited` and `observation_status`.
It adds a `gate` object:

- `decision`, `rule` (`A1` or `A2`), `gate_model`
  (`hamcp195-fast-helper-gate-v1`);
- `binding_model`, `evidence_fingerprint`;
- `dependency_index_generation`, `dependency_index_fingerprint`,
  `dependency_index_source_epoch`;
- `fenced: true`;
- `consumers`: for each, the automation, its consequence, reason codes, and
  the protection verdict for each effect target;
- `scripts_checked`, with the coverage status;
- `derived_sources_checked`;
- `disclosures` (G5, G7, G8, G9);
- `reason_codes`.

`consequence_coverage` is `indexed_complete_unindexed_disclosed`, never
`complete`.

Audit events are `typed_input_boolean_<state>`, plus
`typed_input_boolean_refused` carrying the reason codes and the index identity.

### 5.4 Provider, authority and Core profile (OD-G)

**G1 (recommended).** Reuse the reviewed HAMCP-089 adapter and F3 identity.

- Provider `direct_home_assistant_state`, contract
  `direct-ha-exact-input-boolean-v1`.
- The internally prepared operation keeps the identity
  `("set_exact_input_boolean_state", "set_input_boolean_state")`.
- `ha_core_readmission/routes.py:589-625` `f3_requirements` therefore maps it
  to `core.f3_mutation_verification` and `core.typed_helper_operation`, both
  already signed in sequence 12 for 2026.10.x.
- The new tool gets a `CoreIndependence` route entry like `control_power`'s
  (`routes.py:339`): it acquires that exact authority before dispatch.
- The profile's contract (`ha_core_readmission/profiles.py:335-356`) is
  satisfied unchanged:
  - target `exact_existing_typed_helper`;
  - readback `authoritative_exact_state`;
  - predispatch core identity, capability contract, target and session
    fingerprints, generation.
- **What changes is only the Engineering authorization source.** It moves from
  an exact-hash external approval to an authenticated connector call that
  passes the gate.

**Caveat (explicit decision).** The profile's `provider_boundary` is named
`existing_governed_helper_adapter`.

- If Josh or the reviewer reads "governed" as "plan-approved only", then G1
  changes the profile's meaning.
- In that case **Part 2 must stop**: a new or amended Core profile requires
  signing (the brief's LIMITS).

**G2.** A new ha-mcp-delegated typed family, like power and fan, needs a new
compiled profile plus an owner-signed registry entry. That is a STOP
condition, so it is not proposed.

**Security-architecture note (AGENTS.md).** G1 creates a **newly reachable
write path**: an existing provider reached from connector authority without
approval. Part 2 therefore needs:

- explicit scope (this task);
- negative reachability tests: no other domain, no other service, no
  `toggle`, no fallback, no plan side effects;
- a security section in the reviewer brief.

### 5.5 Registration touchpoints (Part 2)

- `tools/registry.py`: static tool count 65 → 66. This count is pinned in
  about 30 tests.
- `capabilities.py`: category `ordinary_runtime_action`.
- `routing.py`: telemetry binding.
- `ha_core_readmission/routes.py`: route entry.
- `application.py`: hooks.
- `tools/governance.py`: `_get_execution_task`.
- `approval_web.py`: `TYPED_HOLD_KINDS`.
- MCP server instructions text: name the fast path and its refusal pointer.

Core runtime profile counts are unchanged under G1 (typed 2, runtime 24).

## 6. Live-shaped evidence (offline)

Live Home Assistant was not read. The owner's real helper inventory is **not
in the repository**: the repository is public, and its fixtures are
anonymized. The list below is everything the repository can support. Probe:
`docs/evidence/hamcp195/probe_gate.py`, output
`docs/evidence/hamcp195/probe_gate.out`, provenance in its README.

### 6.1 Helpers named in repository evidence

| Helper | Source | Expected outcome | Why |
| --- | --- | --- | --- |
| `input_boolean.mcp_f2_standard_admin_test_flag` (replayed as `target_standard`) | Beta 38/51 acceptance, sanitized Beta 51 replay | **Qualifies** (A1 and A2) | 0 consumers, complete and exact, consequence none |
| `input_boolean.guest_mode` | Beta 52 acceptance | **Refused** | Seven safety-critical downstream profiles |
| `visitor_mode`, `service_visit_mode` (anonymized PR001) | HAMCP-213 PR001 | **Refused** | Guard the departure garage close (`cover.close_cover` on a garage cover: safety_critical). Op1 also has an unknown consumer. |
| Cleaner mode, vacation mode | none | **Unknown** | No repository evidence. A `check_only` call after release would answer it. |

### 6.2 Probe results (shipped analyzer, fixture data)

| Helper (consumer) | `pr001_house` | `no_opaque_consumers`: A1 | `no_opaque_consumers`: A2 |
| --- | --- | --- | --- |
| `synthetic_quiet_notify` (static notify) | refused: G2 | **qualifies** | **qualifies** |
| `synthetic_unused` (no consumers) | refused: G2 | **qualifies** | **qualifies** |
| `synthetic_opaque_mode` (only the dynamic template) | refused: G2 | **qualifies** | **qualifies** |
| `synthetic_lights_mode` (`light.turn_on` literal) | refused: G2 | refused: direct | **qualifies** |
| `synthetic_blind_mode` (cover, device class blind) | refused: G2 | refused: direct | **qualifies** |
| `synthetic_gate_mode` (cover, class gate, id without "garage") | refused: G2 | refused: direct | **refused**: protected target (class gate) |
| `synthetic_script_mode` (`script.turn_on`) | refused | refused: unknown | refused: unknown (G3) |
| `synthetic_lock_mode` (`lock.unlock`) | refused | refused: safety_critical | refused: safety_critical |
| `visitor_mode`, `service_visit_mode` | refused | refused | refused: garage close |
| Beta 51 `target_standard` | — | **qualifies** | **qualifies** |
| Beta 51 `entity_001` | — | refused: unknown, coverage | refused |

Two points from the probe:

- The binding classifies the class-gate cover as merely `direct`, because its
  id does not contain "garage". Under A2 the gate must therefore evaluate OD-4
  on the effect target with the registry device class. Using the binding's
  consequence alone would let a gate cover through.
- `synthetic_opaque_mode` qualifies only when the dynamic template is
  **absent**. With it present, every helper is refused (G2).

## 7. Part 2 test and mutation plan (after decisions)

**Tests:**

- qualifying helper → fast path, inline verified;
- each of these is refused with exact reason codes, no dispatch, and the
  governed pointer:
  - a direct protected consumer;
  - a protected target by device class only;
  - a called script (per OD-B);
  - a script reader (OD-C);
  - a one-hop template or group chain (OD-D);
  - stale, truncated, partial, version-unsupported or opaque index (G1, G2);
  - the exclusion label;
- opaque template consumer → refused;
- a gate that holds on the unfenced read but fails on the fenced read →
  refused (TOCTOU), and the lock projection drift case;
- idempotency: replay with the same arguments gives the reconcile with no
  second dispatch; replay with different arguments gives `rebound`;
- lock contention gives `lock_conflict` naming the governed plan's task;
- inline verification versus `observing` versus deadline and manual review;
- already-desired gives a verified no-op;
- the receipt records the gate evidence (index identity, consumers checked);
- no silent downgrade: no plan is created and no other route or provider is
  called on refusal;
- negative reachability: non-`input_boolean` domains, `toggle`, and services
  other than turn_on/turn_off are rejected at the schema; there is no
  fallback;
- `check_only` never acquires a lock, writes intent or dispatches.

**Mutations** (memory-capped runs):

- the gate skipped;
- a protected consumer ignored;
- the device-class check removed;
- a stale or partial index accepted;
- the unfenced binding used for the decision;
- the replay guard removed;
- a refusal that dispatches;
- refusal without the pointer.

## 8. Release and canary (after decisions)

**Release.** The next free beta at materialization: `main` is `beta.32` and
open PR #294 claims `beta.32`, so this is likely `beta.34` or later. It is
materialized with `scripts/promote_next_release.py --apply` under the
owner-authorized release files, with BETA acceptance, CHANGELOG, README,
TOOL_AUDIT and SECURITY updates.

**Acceptance canary (live, separately authorized).**

1. `check_only` on the Josh-confirmed harmless helper. The candidate is
   `input_boolean.mcp_f2_standard_admin_test_flag`.
2. on → off with verified receipts.
3. A refused attempt on a helper that drives something protected (candidate:
   `guest_mode`), proving no dispatch and the governed pointer.

The canary is blocked if the G2 finding holds live, and the acceptance
document says so.

## 9. Findings against the brief's assumptions

1. **The motivating helpers likely do not qualify.** Guest and visitor modes
   guard garage and lock automations, so refusing them is correct. Vacation
   and cleaner mode cannot be judged offline.
2. **A single unreviewed template call can disable the fast path house-wide**
   (§3.2). This is the governed standard classification's behavior too, not
   a new strictness.
3. **"Including called scripts at bounded depth" is not what the binding
   does.** It refuses script calls as `unknown`. Following callees is new work
   (OD-B (ii)).
4. **The index has no indirect chains.** Template, group, scene and dashboard
   coverage is `unavailable`. The one-hop rule (OD-D) has to be a
   conservative reverse check, not a traversal.
5. **Scripts that read a helper are invisible to the binding.** They live in
   a separate partition (OD-C).
6. **The OD-4 union is target-only today.** A2 must apply it to downstream
   effect targets with registry device classes (§6.2).
7. **Core signing.** No new profile is needed under G1, provided the
   `existing_governed_helper_adapter` boundary may serve gate-authorized
   calls. If it may not, this is the brief's STOP condition.

## 10. What stays unchanged

- `create_helper_state_plan` and its policy, locks and approval behavior.
- The stable v1 tree.
- Workflows, `config.yaml`, permissions and the signed registry.

## 11. Owner decisions requested

| ID | Decision | Options | Recommendation |
| --- | --- | --- | --- |
| OD-A | Consequence threshold | A1 strict (none only); A2 allow literal non-protected `direct` (lights, fans, climate, non-protected covers) | **A2**: parity with control_power. Unknown and safety_critical are always refused. |
| OD-B | Consumer automations that call scripts | (i) refuse; (ii) follow callees (depth ≤ 8, fail closed) | **(i) for v1**, with (ii) as a follow-up |
| OD-C | Scripts that read the helper | Refuse if a referencing script is protected or unresolved, or if the partition is unavailable or bounded; structural partial disclosed | **As proposed** |
| OD-D | Indirect one hop | Conservative reverse check (groups clearable by membership, templates refuse); or refuse whenever any derived entity feeds a protected or unknown automation | **Conservative reverse check** |
| OD-E | Always-excluded helpers | Owner label `hamcp_protected` (always refused); name heuristics | **Label only** |
| OD-F | Actions | turn_on/turn_off; plus toggle | **turn_on/turn_off only** |
| OD-G | Provider and Core authority | G1 reuse the signed HAMCP-089 adapter and F3 identity with gate authorization; G2 new typed family (signing, STOP) | **G1**, if Josh confirms the `existing_governed_helper_adapter` boundary may serve gate-authorized calls. Otherwise STOP. |
| OD-H | §3.2 house-wide opaque finding | Accept (the fast path may be inert until resolved); separately review `is_number` into the semantic registry; owner rewrites the template | **Accept and open a separate task** for the registry review |
| OD-I | `check_only` mode | include; omit | **Include** (acceptance and eligibility listing, with no extra tool) |
| OD-J | Canary helpers | Confirm the harmless helper and the refused helper | `mcp_f2_standard_admin_test_flag` / `guest_mode` |

## 12. Non-actions in Part 1

None of the following happened in Part 1:

- runtime, test, schema, tool-registration, provider-routing, workflow,
  release, version, `config.yaml`, permission or stable-v1 change;
- live Home Assistant or deployed endpoint access;
- reading credentials;
- signing or creating a registry;
- merge, Ready, publication or deployment;
- reviewer sessions.

The probe is offline design evidence and is not collected by the test suite.
