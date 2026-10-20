# HAMCP-213: Safety-Critical Owner Approval and Physical-Action Equivalence

Status: **design report only, awaiting owner decisions.** This report changes no
runtime code, schema, policy, workflow or release declaration.

- Register: HAMCP-213 (Active). Audit findings: HAAC-E012 (no approval path for
  safety-critical configuration plans) and HAAC-E013 (classifier false
  positives).
- Base: `origin/main` `f69bf8cca4e4d6a1af9c4fdfd95476f62d7c601b`
  (Engineering `2.4.1-beta.24`).
- Live audit observation: Engineering `2.4.1-beta.19`, ha-mcp 8.5.0, Core
  2026.10.1.
- `governance/policy.py` and `governance/risk.py` are byte-identical between tag
  `v2.4.1-beta.19` and the base (`git diff --stat` is empty). The script branch
  of `_configuration_risk` in `governance/service.py` is also unchanged. The
  audited behavior therefore still applies on `main`.

All paths are relative to `hass_mcp_engineering_beta/ha_mcp_engineering/`
unless stated otherwise. Line numbers refer to the base commit.

Nothing in this report came from a live Home Assistant instance or a deployed
MCP endpoint. The E012 analysis rests on an offline probe that runs the shipped
classifier on synthetic configurations:
[`evidence/hamcp213/probe_e012.py`](evidence/hamcp213/probe_e012.py), with
output in [`probe_e012.out`](evidence/hamcp213/probe_e012.out). The probe runs on
CPython 3.12 in a virtual environment installed with `--require-hashes` from
`hass_mcp_engineering_beta/requirements.lock` (sha256 `d466020b…`), located
outside `/tmp`.

D6 uses the owner's real PR001 plan (`7b651ef4…`), supplied offline after the
first draft of this report. The committed fixtures in
[`evidence/hamcp213/pr001/`](evidence/hamcp213/pr001/README.md) are
**anonymized structural transforms** of it: every entity, person, notify
service and free-text string is renamed, and the structure is unchanged. The
README records the provenance and the checks.

> The task prompt was cut off partway through D1 ("…never grantable by the
> assistant; clearly"). D1 below finishes that sentence as "clearly
> distinguished from standard approval". The other sections come from the
> register Outcome for HAMCP-213, and D6 from the owner's PR001 example. If
> the missing text contained other requirements, they still need to be
> supplied.

---

## Summary

1. **E012 has a narrower cause than "safety-critical is always prohibited".**
   - On `main`, an exact update to an existing **automation** that touches the
     garage cover already gets `elevated_admin`, and only one `plan_approval`
     is needed.
   - An exact **script** update with the same content is `prohibited`. The
     reason is the script path, not the garage: the automation-only override in
     `governance/service.py:3469-3484` never applies to scripts, and
     `_exact_existing_automation_update` (`governance/policy.py:144-172`)
     accepts only `resource_type == "automation"`.
   - The policy aggregate takes the strictest class across operations, so one
     script makes the whole plan prohibited.
   - The synthetic probe reproduces the audited outcome for a mixed
     automation+script plan: `prohibited` with `exact_existing_automation_update`,
     `owner_decision_required` and `safety_critical_effect_not_reviewed` (plus
     the generic `supported_configuration_change`).
   - The owner's real plan confirms this (D6). Its third operation is a
     script, and that operation alone is `prohibited`. The real aggregate also
     carries `standard_configuration_policy` from the one operation with no
     garage trigger, so the reason set is a superset of the audit's, not an
     exact match.
2. **The automation path that does work has no safety-specific
   acknowledgement.** "Owner-authoritative exactness" removes the elevated
   acknowledgement (`governance/policy.py:1083-1088`). A plan that changes
   whether a garage door closes is therefore approved with the same single
   click as a description edit. D1 tightens that path while it opens the script
   path.
3. **The risk classifier never compares before and after.** Any change to a
   behavioral field (actions, mode, conditions and so on) re-raises every hazard
   anywhere in the proposed body (`governance/risk.py:1753-1767`). That
   behavior is the shared root cause of the E013 false positives, and the
   physical-action-equivalence comparison in D2 replaces it.
4. **"No external approver configured" is a stale disclosure, not a policy
   reason.** The source string is `no_external_approver_exists`. It is reported
   for every plan with no recorded Ingress decision, prohibited plans included
   (`governance/service.py:244-259`). It says nothing about whether the
   HAMCP-205 administrator approver exists.
5. **On the owner's real plan, the proposed rules give the intended result.**
   Two operations become `none` and two become `equivalent`. The plan needs one
   elevated plan approval and shows the comparison, instead of being
   prohibited (D6). Seven targeted mutations of the same plan all become
   `changed`. Validating the rules on that plan exposed two gaps that need
   owner decisions: steps that keep a run busy after a close (OD-10) and
   notification buttons that lead to a close elsewhere (OD-9).

Recommended direction: replace "prohibited because safety-critical" with three
outcomes driven by the comparison.

- **Equivalent:** one plan approval, with the comparison shown as evidence.
- **Changed:** plan approval plus a separate safety-critical acknowledgement
  that names each object.
- **Prohibited:** only when the execution contract cannot be established.

There is no bypass flag, and the assistant has no way to approve.

---

## Part 1: Inventory

### 1.1 How risk, physical consequence and policy class are computed

**Risk (per operation).**

- `ChangeGovernanceService._configuration_risk` (`governance/service.py:3418-3492`)
  calls `classify_risk(legacy_operation, risk_diff, risk_config)` at `:3468`.
- For scripts it first copies `sequence` into `action`. A `sequence` change is
  relabeled as an `actions` change (`:3443-3462`).
- `classify_risk` (`governance/risk.py:1748-1811`):
  - The diff is reduced to the set of changed top-level field names (`:1753`).
  - Hazards come from `_structured_analysis(proposed)` (`:1754`), which looks
    at the proposed configuration only. `current_config` is never passed in.
  - When any behavioral field changed (`:1756-1762`: triggers, conditions,
    actions, variables, trace_settings, blueprint_usage, mode, maximum_runs)
    and any evidence trigger is in {`high_risk_service`,
    `sensitive_entity_domain`, `sensitive_blueprint_input`,
    `garage_cover_target`, `water_control_target`} (`:1764-1767`), the result
    is `RiskLevel.HIGH` with `apply_allowed=False` (`:1776-1782`).
  - Otherwise it returns MEDIUM or LOW with `apply_allowed=True`
    (`:1786-1811`).
- Only the automation-update branch resets `apply_allowed` to `True`
  (`governance/service.py:3469-3484`; the comment says consequence "does not
  make the exact configuration provider target or payload ambiguous").
  Scripts and creates keep `False`.
- The retry proof (`:5187-5197`) and the rollback inverse (`:5205-5213`) are the
  other two places that set it to `True`.

**Physical consequence and policy class (per operation).**

`configuration_operation_policy` (`governance/policy.py:464-620`):

- Any evidence trigger in `_SAFETY_CRITICAL_TRIGGERS` (`:50-58`:
  `sensitive_entity_domain`, `sensitive_blueprint_input`,
  `garage_cover_target`, `safety_critical_service`, `water_control_target`)
  sets `PhysicalConsequence.SAFETY_CRITICAL` and adds reason
  `safety_critical_effect_not_reviewed` (`:501-509`).
- The ordered rules:
  1. Retained-effect proof (Beta 33: action graph unchanged, only appended
     reviewed conditions) → `elevated_admin` (`:482-497`; theorem at `:302-375`).
  2. Garage inverse or retry-transfer proof markers → `elevated_admin` with
     `owner_decision_required` (`:524-537`).
  3. Consequence uncertain and exact automation update → `elevated_admin`
     (`:538-556`). Consequence uncertain otherwise → `prohibited` (`:557-562`).
  4. **Safety-critical and `exact_automation_update` → `elevated_admin` with
     `exact_existing_automation_update` and `owner_decision_required`
     (`:567-584`).**
  5. **Safety-critical otherwise → `prohibited` (`:585-591`).** This is the
     rule a script update reaches.
  6. Critical delta → `prohibited`. High or direct → `elevated_admin`.
     Everything else → `standard_admin`.
- `_exact_existing_automation_update` (`:144-172`) requires
  `resource_type == "automation"`, `action == "update"`, both configurations
  normalized, a 64-hex fingerprint and proposed hash, `validation_results.valid`
  and `risk.apply_allowed`.

**Aggregation and required actions.**

- `aggregate_policy_classifications` (`:901-928`) takes the strictest class,
  delta and consequence, and the union of reason codes.
- `_evaluate_change_policy_version` (`:1035-1110`) computes
  `exact_owner_authoritative`. For configuration plans that means every
  operation is an exact automation update, or a retry or inverse proof is bound
  (`:1054-1075`).
- When it holds, `elevated_admin` requires only `(plan_approval,)`
  (`:1081-1088`). `prohibited` requires `()` (`:1089`).

**Probe result** (synthetic `cover.synthetic_garage_door`; the warning
automation from the audit, with the push moved before TTS, the text reworded and
mode changed single→restart):

| Case | Risk / `apply_allowed` | Policy |
| --- | --- | --- |
| automation update | high / True | `elevated_admin`, plan approval only |
| same body as a script update | high / **False** | **`prohibited`** |
| automation + script in one plan | — | **`prohibited`**: `exact_existing_automation_update`, `owner_decision_required`, `safety_critical_effect_not_reviewed`, `supported_configuration_change` |
| script whose only change is `mode` | high / False | `prohibited` |
| automation with no cover service, only `logbook.log` `data.entity_id` = the cover, notification text reworded | high / True | `elevated_admin`, **safety_critical** |

The live plan's reason codes are the union of an elevated automation member
(`owner_decision_required`) and a prohibited member
(`safety_critical_effect_not_reviewed`). The spurious "No complete
retry-transformation proof" warning is appended only to **script** operations
with `apply_allowed=False` (`governance/service.py:5198-5204`). Both point to at
least one script update in plan `7b651ef4…`. This is **inferred**: the plan was
not read here, because live access is out of scope. A read-only check of the
plan's `operations[].resource_type` would confirm it.

**"No external approver configured".**

- This exact phrase appears nowhere in the repository. The source value is
  `principal_separation_status().reason == "no_external_approver_exists"`
  (`governance/service.py:235-259`), and it is chosen whenever
  `approval.principal_separation_enforced is None` (`:244`, `:255`).
- That field defaults to `None` (`governance/models.py:243-248`). It is set only
  when an Ingress decision is recorded (`governance/service.py:7400`, `:7515`).
- It is added to plan reads as a disclosure (`:2958-2962`). It is **not** a
  policy reason code: it is absent from `governance/policy.py` and from
  `ChangePolicyDecision.reason_codes`.
- For a prohibited plan, `approve()` refuses before it creates a challenge
  (`:6564-6571`, `PROHIBITED_CHANGE`). The field can therefore never be set,
  and the plan always reports "no external approver".
- **Accuracy: stale.** HAMCP-205 is implemented.
  - `approval_identity.py:63-74` (`ingress_user_id`) and `:126-140`
    (`AdministratorVerifier.verify`, fixed `config/auth/list`) identify the
    caller.
  - The verdict at `:106-115` rejects system-generated and inactive users and
    then requires `is_owner` or membership in `system-admin`.
  - `approval_web.py:435-470` runs the check before every handler. Without a
    verifier it fails closed (`:455-456`), and the verifier is wired at
    `application.py:383-388`.
- The value really means "no Ingress decision recorded yet". For a prohibited
  plan, the accurate statement is "this plan cannot be approved".
  `docs/EXTERNAL_APPROVAL.md:279-281` documents the current meaning.

### 1.2 Approval classes and their mechanics

- **Enums.**
  - `ApprovalPolicyClass` {`standard_admin`, `elevated_admin`, `prohibited`}
    (`governance/models.py:58-61`).
  - `ApprovalActionKind` {`plan_approval`, `elevated_risk_acknowledgement`}
    (`:80-84`).
- **Action record** `ApprovalActionRecord` (`governance/models.py:183-201`):
  - kind, `authority_version` 3, state, `bound_plan_hash`,
    `policy_decision_hash`, `policy_class`, `risk_delta`,
    `physical_consequence`;
  - challenge id, requested and expires timestamps;
  - `granted_at`, `approver_principal`, `consumed_at`, `csrf_digest`,
    `csrf_issued_at`.
  - The elevated acknowledgement records the class, delta, consequence, plan
    hash and decision hash it acknowledged. It does **not** record any object
    identity or effect summary.
- **Binding.**
  - `approve()` (`governance/service.py:6537-6689`) builds the bundle against
    the computed plan hash and decision hash (`:6658-6676`).
  - The challenge for the second action is issued only after the plan approval:
    `token_urlsafe(24)`, TTL `min(now + 60 min, plan.expires_at)`
    (`:7406-7441`).
  - CSRF is a stored sha256 of a single-use nonce. It is compared with
    `hmac.compare_digest` and then cleared (`:7236-7273`, `:7340-7371`).
- **Who can grant.**
  - Only an Ingress request is accepted: the peer must be `172.30.32.2` with a
    valid `X-Ingress-Path` (`approval_web.py:413-422`), and the HAMCP-205
    administrator verdict must pass on every review and decision.
  - The acknowledgement must come from the same principal as the plan approval
    (`governance/service.py:7373-7391`; dispatch recheck `:12255-12260`).
  - This is deliberately not two-person control (ADR-012).
- **Assistant separation is enforced by channel only.**
  - The MCP tool `approve_change_plan` (`tools/governance.py:619-642`) only
    creates an `EXTERNAL_PENDING` challenge.
  - `decide_external_approval` has one caller, `approval_web.py:1049`.
  - Nothing compares the Ingress administrator with the MCP requester
    (`approving_caller_id` is never set). The same human may both request and
    approve, which is the intended single-owner topology.
- **What the reviewer sees.**
  - `_render_review` (`approval_web.py:1696-1866`) shows risk, policy class,
    delta, consequence, reason codes, hashes, changed fields and warnings.
  - For configuration plans it also shows each step's risk reasons and the full
    semantic before/after projection (`:1526-1580`, `:1612-1694`).
  - It shows **no** list of safety-critical objects and **no** effect summary.
- **Revalidation before dispatch.**
  - `_approval_requirement_error` (`governance/service.py:12195-12265`) rechecks
    the policy snapshot, not-prohibited, state, channel, kind, principal, plan
    hash, decision hash and class, expiry, acknowledgement grant, same
    principal and bundle state.
  - The configuration consumption sites are `:11006` and `:11088-11089`.
    F3 rechecks the bundle hash (`f3_runtime/runtime.py:2500-2504`).

### 1.3 How "physical actions" and protected objects are identified; E013 root causes

- **Action discovery.** `_structured_analysis` (`governance/risk.py:637-750`)
  walks the reviewed action positions (`_action_roots` and `_action_nodes`). For
  every node with `service` or `action` it records the service and runs target
  analysis.
- **Target discovery.** `_target_selectors` (`:536-552`) yields `target.*`,
  top-level selectors **and `data.entity_id` for every service** (`:550-552`).
- **Protected objects.** Each `entity_id` target (`:713-738`) is classified as:
  - domain in `SENSITIVE_ENTITY_DOMAINS` → `sensitive_entity_domain`;
  - `domain == "cover" and "garage" in entity_id` → `garage_cover_target`
    (`:719-726`, a substring test);
  - any `WATER_TARGET_TERMS` substring → `water_control_target`.
- **Services.** A service in `SAFETY_CRITICAL_SERVICES` → `safety_critical_service`.
  `_is_high_service` → `high_risk_service`.

| E013 symptom | Root cause | Effect today |
| --- | --- | --- |
| `logbook.log` with `data.entity_id` = the cover is flagged `garage_cover_target` | `_target_selectors` treats `data.entity_id` as an action target for any service (`risk.py:550-552`). The protected-object test never checks whether the service can actuate that entity (`:713-726`). | Can create a safety-critical classification by itself. In the probe, an automation with no cover service becomes `safety_critical`. |
| `notify.mobile_app_*` is flagged `omitted_action_target` | `_target_shape_evidence` emits it for any service node with no selector (`risk.py:565-574`). Notify services route by service name. | Review noise only. No policy rule reads this trigger (`policy.py:50-70`), but it appears in the evidence list the approver sees. |
| "No complete retry-transformation proof" when retry logic is unchanged | `derive_proof` runs on **every** configuration plan (`service.py:5187`). It accepts only 1- or 5-operation compositions (`configuration_eligibility.py:333`). On failure, every script operation with `apply_allowed=False` gets the warning (`service.py:5198-5204`), whether or not a retry transformation was attempted. | Misleading text. It also adds a risk warning to the policy subject hash (`policy.py:961`). |
| "Recipient or environmental-control behavior may change" on an ordering-only change | `risk.py:1792-1793` fires when `actions` changed and **any** notify or climate service exists anywhere in the proposed body. No recipient set is compared. | Moderate-risk reason. It is masked when the plan is already HIGH (`:1776`). |
| (shared) a notification wording change re-raises the unchanged `cover.close_cover` | `classify_risk` has no current-vs-proposed effect comparison (`risk.py:1753-1767`). | Every edit to a garage automation is classified HIGH or safety-critical. |

No tests pin the retry-warning text, the recipient reason or the
`omitted_action_target` trigger outside `governance/`. Those strings can be
corrected without breaking an existing test contract.

### 1.4 Other protected and safety-critical object types

| Object | Configuration-plan classification | Elsewhere |
| --- | --- | --- |
| Lock | Domain is in `SENSITIVE_ENTITY_DOMAINS` (`risk.py:37`) → safety-critical. `lock.unlock` is in `SAFETY_CRITICAL_SERVICES` (`:29-34`) → reason `safety_critical_service_prohibited` (`policy.py:508-509`). `lock.*` is a high-service prefix (`:14-20`). | Helpers: `_HELPER_SAFETY_CRITICAL_DOMAINS` (`risk.py:76-78`). Dashboards: `_HIGH_ENTITY_DOMAINS` (`f3_dashboard/risk.py:19-21`). Entity metadata: alias changes elevated (`entity_metadata_planning.py:62`). |
| Alarm panel | Same as lock. `alarm_control_panel.alarm_disarm` is a safety-critical service. | Same as lock. |
| Valve | Domain is sensitive → safety-critical. `valve.open` and `valve.close` are high exact services (`risk.py:21-28`). | Helpers and dashboards. |
| Water shutoff | Substring match on `water`, `shutoff`, `shut_off`, `main_valve` in any action target id (`risk.py:38`, `:727-733`). This catches any entity id with "water" in it, e.g. `switch.water_feature_lights`. | — |
| Garage cover | Substring `garage` in a `cover.*` id (`risk.py:719`). Misses a garage door whose entity id lacks "garage". | Dashboards: every `cover` is high. Entity metadata: covers with `device_class` in {garage, gate, door} are elevated (`entity_metadata_planning.py:61`, `:325`), so configuration plans and metadata use **inconsistent** rules. |
| Gate or door cover | **Not** safety-critical in configuration plans; `cover.*` services are only medium (`risk.py:36`). | Elevated in entity metadata (device_class). |
| Siren | **Not classified at all.** `siren.*` is in neither the medium prefixes nor the high set. | — |
| Climate or water heater | `climate.*` is medium plus the recipient reason (`risk.py:36`, `:1792`). `water_heater.turn_off` is a high exact service. | Helpers: direct-effect domains. |
| Script, automation or scene calls | `script.*` and `automation.*` are high-service prefixes → `high_risk_service` → **direct**, not safety-critical. Transitive effects are not followed. | Helper analysis has transitive domains (`risk.py:79-81`). |

---

## Part 2: Design proposal

Owner decisions are labeled **OD-n**. Each one has a recommendation. None is
implemented until the owner decides.

### D1. Safety-critical owner acknowledgement

**Mechanism.** Add one new ordered administrator action,
`safety_critical_acknowledgement`. A plan requires it whenever the D2
comparison for any operation is `changed` or `unresolved` (D3). The plan keeps
`policy_class = elevated_admin`, because ADR-012 keeps the class and the
consequence independent. The required actions become
`(plan_approval, safety_critical_acknowledgement)`. This action subsumes
`elevated_risk_acknowledgement` for that plan, because it acknowledges
everything the elevated action does and more. The owner-authoritative exactness
shortcut at `policy.py:1083-1088` **never** removes it.

- **OD-1 (form of the path).**
  - **Recommended:** a new action kind inside `elevated_admin`. The public
    `policy_class` enum stays the same, so clients, historical projections
    (`governance/historical_policy.py`) and notifications keep their meaning;
    only the required-actions list grows.
  - Alternative: a fourth `ApprovalPolicyClass`, e.g. `owner_safety_critical`.
    It is more visible to clients, but it changes a public enum, every
    rank/aggregation table and the historical policy projection.
- **What it binds.** The record extends `ApprovalActionRecord` and uses
  `authority_version` 4 for this kind only. It stores:
  - `bound_plan_hash` and `policy_decision_hash`, as today;
  - `safety_critical_projection_hash`, the stable hash of the D2 comparison
    projection, which is also bound into the policy subject (D8);
  - `acknowledged_objects`, the sorted, bounded list of protected object ids;
  - the existing challenge, CSRF, expiry, `granted_at` and `approver_principal`
    fields.
- **Who.** Only an Ingress request that passes the HAMCP-205 verifier at
  decision time, from the same principal that granted `plan_approval`. TTL is at
  most 60 minutes and never longer than the plan.
  - **OD-2 (approver).**
    - **Recommended:** any verified HA administrator. This matches the task
      text and today's model, and it keeps the decision possible when the owner
      account is not the one in use.
    - Alternative: restrict this action to `is_owner`. The data is already in
      the `config/auth/list` row (`approval_identity.py:106-115`), but a
      household with several administrators could no longer approve these
      changes.
- **What the approver sees.** A separate page, reached only after plan
  approval:
  - Heading "Safety-critical acknowledgement", visually distinct from the
    standard approval (OD-3 covers wording and colour). The text says what
    changes about a physical object, not the risk score.
  - One row per protected object: entity id; the bounded device class or
    reason it is protected (D5); effects before → after (service, data
    summary, position); reachability changes (triggers, conditions, mode, waits
    or abort points before the effect); and a verdict of `changed`, `added`,
    `removed` or `unresolved` with reason codes.
  - **A checkbox for each object.** The server accepts the decision only when
    the submitted object-id set equals the bound set exactly, and the
    `safety_critical_projection_hash` and plan hash match the recomputed values.
  - **OD-3:** **recommended** per-object checkboxes. Alternative: typing the
    entity id. That is stronger against reflexive clicking but awkward on
    mobile; see `docs/BETA35_MOBILE_APPROVAL_NAVIGATION.md`.
- **Never grantable by the assistant.**
  - No MCP tool and no `approve_change_plan` argument can create, advance or
    decide this action.
  - The only decision entry remains `approval_web.py` →
    `decide_external_approval`.
  - Negative tests:
    - `approve_change_plan` calls cannot advance the action;
    - the tool catalog contains no acknowledgement tool;
    - a non-Ingress peer, a non-administrator, lookup failure, a
      different principal, a replayed or missing CSRF value, a mismatched
      object set, a tampered projection, expiry and stale state are all refused.
- **Dispatch.** `_approval_requirement_error` additionally requires:
  - the acknowledgement is granted;
  - its `safety_critical_projection_hash` matches the plan's recomputed
    projection;
  - its `acknowledged_objects` equal the recomputed set.
- **No bypass.** There is no flag, option, environment variable or tool
  argument that removes the acknowledgement. Only a different, recomputed
  comparison result can remove it.
- **Approval notifications.** `governance/approval_notifications.py` says
  "safety-critical acknowledgement required" for these plans.
- **Disclosure fix (OD-4).** Replace `no_external_approver_exists` with
  accurate values: `no_decision_recorded` for pending plans and
  `not_approvable` for prohibited plans. This changes public output, so it
  needs the same explicit schema authorization.
  - **Recommended:** keep the field and add the new values in the same
    protected PR. Record the rename in `EXTERNAL_APPROVAL.md`.

### D2. Physical-action equivalence comparison

Add a deterministic comparison of protected-object effects between the
normalized current configuration and the normalized proposed configuration of
each operation. Its output is bounded, server-owned evidence and never a grant.

1. **Effect extraction** uses closed, reviewed tables only.
   - **Effect actions on a protected object (D5):**
     - `cover.{open_cover, close_cover, stop_cover, toggle, set_cover_position, open_cover_tilt, close_cover_tilt, stop_cover_tilt, toggle_cover_tilt, set_cover_tilt_position}`;
     - `homeassistant.{turn_on, turn_off, toggle}`;
     - `lock.*`, `alarm_control_panel.*`, `valve.*`, `siren.*` (OD-7);
     - device actions whose domain is a protected domain.
   - An **unknown service whose target is a protected object is an effect**
     (conservative).
   - **Reference-only services** (closed table: `logbook.log`, `notify.*`,
     `persistent_notification.*`, `system_log.write`) never make their
     `data.entity_id` an effect target. They are recorded as references for
     display.
   - **Actionable notification buttons** (`data.data.actions[].action` on a
     `notify.*` call, such as a "close garage" button) are not effects of the
     operation that sends them. A button press fires a
     `mobile_app_notification_action` event, and whatever handles that event
     (usually another automation) performs the effect. Added and removed
     button ids are recorded as reference evidence and shown (OD-9).
   - **Transitive calls** (`script.*`, `script.turn_on`, `automation.trigger`,
     `scene.turn_on`) are effects keyed by callee id. A callee updated in the
     same plan gets its own comparison.
2. **Effect record**: (service, the resolved target set limited to protected
   objects, canonical data excluding display-only fields, and the control path
   of enclosing `choose`, `if`, `repeat`, `parallel` and `sequence` positions
   with their predicates).
3. **Reachability context** for each effect:
   - the automation's triggers, top-level conditions, `mode`, `max` and
     `max_exceeded`, and top-level `variables`;
   - the **gating prefix**: the ordered steps in the same run before the effect
     that can delay, block or abort it. These are `delay`, `wait_*`,
     `condition`, `stop`, control predicates, and every service step without
     `continue_on_error: true`, because a failing step aborts the run.
   - the **run-occupancy signature**: every step anywhere in the operation
     except non-aborting reference-only steps, in order. Under `mode: single`,
     `queued` or `restart`, a step that runs **after** an effect still keeps the
     run busy, so it can drop, delay or cancel the effect of a later trigger.
     A wait added after a close is therefore a reachability change even though
     it is not in that close's gating prefix. Leaving non-aborting
     reference-only steps out of this signature is an assumption, so OD-10
     records it as an owner decision.
   - The claim that a failing service call aborts the run, and the exact
     `continue_on_error` semantics, must be verified against pinned Core
     2026.10.1 `helpers/script.py` before implementation (ADR-021). This report
     has not verified it.
   - The gating prefix is recursive. For an effect inside a `choose`, `if` or
     `repeat`, it includes the earlier steps of every enclosing list and the
     predicates of the earlier `choose` options, because those options decide
     whether this branch is reached.
4. **Verdict per operation:**
   - `none`: no protected effect on either side. The protected-object rules do
     not apply.
   - `equivalent`: a bijection exists between current and proposed effect
     records, every reachability context is identical, each gating prefix
     has the same ordered abort-point signature, and the run-occupancy
     signature is identical. A reference-only step may be
     reworded, reordered among other reference-only steps, added or removed
     only if it is not an abort point. An abort point counts as unchanged when
     its service and target are the same and only template-free literal
     `message` or `title` text changed.
   - `changed`: an effect was added or removed, a record differs, or a
     reachability context or abort signature differs. A `mode` change on an
     operation that has protected effects is always `changed`, because
     `restart` can cancel an in-flight wait or close.
   - `unresolved`: a protected effect's service or target is templated or
     dynamic, a blueprint input cannot be resolved, or the structure is outside
     the reviewed grammar.
5. **The equivalence theorem**, stated for review: if the verdict is
   `equivalent`, every run that reaches a protected effect after the edit,
   under the same trigger and state, reached the same effect with the same
   arguments before the edit, and vice versa. Only the template-free text of
   non-effect steps and their position among non-aborting reference steps may
   differ. Like the Beta 33 proof, this does not claim the automation is
   correct, only that its physical effects are unchanged. It does not claim
   identical timing either: a non-aborting push before a close can delay the
   close by as long as that call takes. OD-10 decides whether that latency is
   acceptable for `equivalent`.
6. **Projection.** `physical_effect_comparison` is stored per operation, with a
   row per object (verdict, reason codes, bounded before/after effect
   summaries) and its hash bound into the policy subject (D8). It is shown on
   the review page for every plan with protected effects, including
   `equivalent` ones, as the comparison evidence the register Outcome requires.

7. **Implementation (PR 1, independent-review corrections).** When the
   comparison cannot prove that a protected effect and its reachability are
   unchanged, the verdict is `changed` or `unresolved` and the
   acknowledgement is required; it is never `none` or `equivalent`.
   - **Unresolvable effects (H1).** A call is effect-capable when its service
     is in `cover`, `lock`, `alarm_control_panel`, `valve`, `siren`, `switch`
     or `water_heater`, is `homeassistant.turn_on`, `turn_off` or `toggle`, or
     is an unknown service. It is also effect-capable when it is a device
     action of those domains. If its target is templated, variable-driven, or
     an `area_id`, `device_id`, `floor_id` or `label_id` selector (in `target`
     or `data`), it is an `unresolved` effect. The same holds when its service
     name is templated or uses `service_template`. Registry selectors are
     never resolved, which is stricter than resolving them through the
     registry. A templated service name may be an unlock or disarm, so it
     carries the OD-6(b) floor. `light`, `fan` and `climate` calls and
     transitive callees are not flagged (residual).
   - **Reference steps before an effect (H2).** A reference-only step that
     can run before a protected effect is compared in full: service, every
     data field (nested data included), target, `continue_on_error`,
     `enabled`, and its position relative to conditions, waits and branches.
     Only the display `alias` is ignored. Any difference is `changed`. This
     supersedes the template-free `message`/`title` allowance in item 4 for
     such steps. A reference step after the last effect still compares by
     signature only, so trailing wording stays `equivalent`.
   - **Unlock and disarm floor (H3, OD-6(b)).** Where an unlock or disarm
     effect is or may be present, no shortcut applies: the Beta 33
     retained-effect proof, the garage inverse and the retry transfer are
     skipped, and the per-object acknowledgement is always required. That
     includes an `equivalent` unlock in an otherwise-edited automation. Under
     current policy, the legacy contract-v1 route refuses such a change
     (`prohibited`). Historical `f2-v1` and `f2-v2` recomputation is unchanged.

**Effect on the audit plan:** see D6. The owner's real plan replaced the
earlier inference from the audit description, and the predicted verdicts held.
The review corrections leave them unchanged (op1 `none`, op2 `changed`, op3
`equivalent`, op4 `none`).

### D3. Policy mapping (replaces `policy.py:567-591` for protected effects)

| Comparison result | Execution contract | Class / actions |
| --- | --- | --- |
| `none` | exact | Ordinary rules (standard or elevated by risk). |
| `equivalent` | exact update (automation **or script**) | `elevated_admin`, `plan_approval` only. Reason `safety_critical_effect_equivalent`. Delta from the non-effect changes (at most `moderate`). Consequence stays `safety_critical` (retained), disclosed. |
| `changed`, `added`, `removed` | exact | `elevated_admin`, `plan_approval` + `safety_critical_acknowledgement`. Reason `safety_critical_owner_acknowledgement_required`. |
| `unresolved` | exact write target | `elevated_admin` + `safety_critical_acknowledgement`, with the unresolved objects shown as "effect cannot be determined" (ADR-022: consequence uncertainty is disclosed, not prohibited). |
| any | incomplete (no exact target, fingerprint, validation, provider, normalization, or a retry/inverse proof marker that fails) | `prohibited` (unchanged). |

- **OD-5 (strength of `equivalent`).**
  - **Recommended:** `elevated_admin` with a single plan approval. This is
    today's automation behavior, so the change is minimal, and a classifier
    defect cannot drop a protected plan below the elevated review page.
  - Alternative: `standard_admin`, which is more proportional but depends
    entirely on the theorem.
- **OD-6 (scope).** Should the acknowledgement path also cover the following?
  - (a) **Automation and script creates.** The execution contract is exact: the
    id is new, conflict is checked, and the write is validated and read back.
    Every effect is `added`. **Recommended: yes.**
  - (b) `lock.unlock` and `alarm_control_panel.alarm_disarm`, which ADR-012
    lists by name as prohibited. **Recommended: yes**, with the same
    acknowledgement, because the register Outcome keeps `prohibited` only for
    genuinely unreviewable changes. This supersedes part of ADR-012 and must be
    recorded as an ADR-022 owner decision.
  - Alternative for (b): keep these two services prohibited in this PR and
    decide later.
- **Script parity.** Generalize `_exact_existing_automation_update` to
  automations and scripts, and stop leaving `apply_allowed=False` on script
  updates (`service.py:3469-3484`). Before this lands, verify that the script
  provider's write, readback and F3 configuration conformance are exact for
  updates. This report has not checked it; it is a prerequisite for the
  implementation PR.
- The Beta 33 retained-effect proof (`policy.py:302-375`) becomes a special
  case of `equivalent` plus appended guards. Keep it unchanged in the first PR
  for compatibility, and fold it in later.

### D4. E013 precision fixes

1. **Reference-only services:** `_target_selectors` and protected-object
   classification ignore `data.entity_id` for the reference-only table (D2.1).
   Those ids appear as `reference_target` evidence instead.
2. **`omitted_action_target`** is emitted only for services that act on
   entities; notify-style services routed by name are exempt. Policy does not
   change, because no rule reads it.
3. **Retry warning** is emitted only when the plan is a retry candidate: 1 or 5
   operations, the first a script, and `script_baseline(current)` succeeds.
   Otherwise the comparison verdict explains the script.
4. **Recipient reason** is emitted only when the recipient set changes:
   {(service, target or `data.target`)} for notify and climate calls in current
   versus proposed, ignoring order, text and `continue_on_error`.
5. **The before/after gap:** `classify_risk` receives the current configuration
   and raises HIGH for protected effects only when D2 is not `none` or
   `equivalent`. The existing physical-device and broad-target reasons stay
   whole-body (no change).

### D5. Protected-object identification

- **OD-7.** **Recommended:** keep today's rules as a floor. Never narrow them.
  Add:
  - covers whose entity-registry `device_class` is in {garage, gate, door}, the
    rule entity metadata already uses (`entity_metadata_planning.py:61`);
  - `siren.*`.
- The registry read happens at plan time, its result is bound in the
  projection, and the plan fails closed to `unresolved` when the read is
  unavailable.
- Narrowing the `water` substring (false positives such as
  `switch.water_feature_lights`) is out of scope unless the owner asks for it.

### D6. Worked example: the owner's PR001 plan

**Source.** The four exact updates of plan `7b651ef4…`, from the owner's
before/after configurations (Core 2026.10.1, Engineering 2.4.1-beta.19, read
2026-10-20). Only anonymized structural transforms of them are committed:
[`evidence/hamcp213/pr001/`](evidence/hamcp213/pr001/README.md). That
README gives the provenance, the source fingerprints and the checks that the
structure is unchanged. All names below are the anonymized ones.

**Plan composition.** The first draft inferred this from the audit; the
owner's example confirms it.

| Op | Type | What changes (structure only) |
| --- | --- | --- |
| op1 | automation (open-garage warning) | Adds a `repeat`-`while` loop: a push **without** `continue_on_error` (now a templated message), a logbook entry, a 15-minute `wait_for_trigger` on an acknowledgement button, and an "acknowledged" `if` block (logbook, a clearing push with `continue_on_error`, `stop`). Mode `single` → `restart`. Calls no cover service, before or after. |
| op2 | automation (departure close) | Four `cover.close_cover` sites on `cover.garage_main` (`continue_on_error: true`), each followed by a 30 s `wait_for_trigger` and a verification `choose`. Adds 3 "override" pushes before the close and 4 "close failed" pushes after it, all with `continue_on_error`. The failure pushes carry a `CLOSE_GARAGE` button. Triggers, conditions and `mode: single` are unchanged. |
| op3 | **script** (close attempt) | One close inside a `choose` option, followed by a 30 s `wait_template` and a result `choose`. Only the description changes, plus `tag`, `priority` and `ttl` on three pushes after or outside the close's prefix (all `continue_on_error`). |
| op4 | automation (evening alert) | The push moves first; `media_player.turn_on` and `tts.speak` gain `continue_on_error`. No cover service. |

**Today, on `main`** ([`probe_pr001.out`](evidence/hamcp213/pr001/probe_pr001.out),
the shipped classifier at the base commit, run offline):

| Op | Risk | Policy | Why |
| --- | --- | --- | --- |
| op1 | high | `elevated_admin`, safety-critical | Both `garage_cover_target` hits are `logbook.log` `data.entity_id` (E013). |
| op2 | high | `elevated_admin`, safety-critical | 11 `garage_cover_target` hits: the 4 real close targets and 7 logbook entries. |
| op3 | high, `apply_allowed=False` | **`prohibited`** | Script path (§ Summary 1); 5 hits, 4 of them logbook. |
| op4 | medium | `standard_admin` | Spurious recipient reason; the recipient set is unchanged (E013). |

- The plan aggregate is `prohibited`, safety-critical, with reasons
  `exact_existing_automation_update`, `owner_decision_required`,
  `safety_critical_effect_not_reviewed`, `standard_configuration_policy` and
  `supported_configuration_change`.
- `derive_proof` returns `None` for the four-operation plan, so the shipped
  service attaches the retry warning to the script
  (`governance/service.py:5198-5204`).
- `omitted_action_target` appears on all four operations, from notify calls.

**Under this design** ([`d2_reference_check.out`](evidence/hamcp213/pr001/d2_reference_check.out),
a design-validation prototype of D2, not runtime code):

| Op | Protected effects before → after | D2 verdict (proposed rule / OD-10 conservative) | Expected outcome (D3, D4) |
| --- | --- | --- | --- |
| op1 | 0 → 0 | `none` / `none` | Ordinary rules. Once D4 drops the logbook hits, the mode change is expected to be moderate and `standard_admin`. |
| op2 | 4 → 4 | `equivalent` / **`changed`** | Proposed: `elevated_admin`, plan approval only, `safety_critical_effect_equivalent`. Conservative: plus the acknowledgement for `cover.garage_main`. |
| op3 | 1 → 1 | `equivalent` / `equivalent` | `elevated_admin`, plan approval only (needs script parity, D3). |
| op4 | 0 → 0 | `none` / `none` | `standard_admin`; D4.4 removes the recipient reason. |

- **Expected plan result (proposed rule):** `elevated_admin` with a single plan
  approval. The consequence stays `safety_critical` and is disclosed, and the
  review page shows the op2 and op3 comparisons. Under the conservative OD-10
  rule, the plan also needs one safety-critical acknowledgement for
  `cover.garage_main`. In neither case is the plan prohibited.
- These "expected" outcomes are the design's predictions. Only the D2 verdicts
  were computed, by the prototype; the D3 and D4 mapping is not implemented.

**Mutations.** Each of these edits to the real structure must make the verdict
`changed`. All 7 do, under both rules:

1. an added op2 pre-close push loses `continue_on_error` (a new abort point);
2. a `delay` inserted before an op2 close;
3. a `condition` inserted before an op2 close;
4. a 10-minute `wait_template` appended after an op2 close (run occupancy);
5. op3 `mode: single` → `restart`;
6. the op3 close target changed to another garage cover;
7. a `cover.close_cover` added to op1.

**What the example taught the design.**

- **Run occupancy.** The gating prefix alone would miss mutation 4. Under
  `mode: single`, a long wait after a close blocks the next trigger's close.
  D2.3 adds the run-occupancy signature.
- **Latency before the close.** op2's verdict is the only one that depends on
  whether a non-aborting push placed *before* a close is ignored. OD-10 asks
  the owner to decide.
- **Notification buttons.** op2 adds a `CLOSE_GARAGE` button (op1 already had
  one and adds an acknowledgement button). Pressing it closes the garage
  through a separate automation outside this plan, so the close is not an
  effect of op2. D2.1 discloses the new button as reference evidence; OD-9
  decides whether that is enough.
- **Recursive prefixes.** Three of op2's four closes sit two `choose` levels deep. Their
  prefixes include the earlier steps and option predicates of every enclosing
  list (D2.3).

### D7. What does not change

- Hash binding, the stale-state fingerprint, the F1 one-dispatch rule, F3
  locks, non-atomic stop-on-first-failure, readback verification and rollback
  all stay as they are.
- No new write, service call, provider route or fallback becomes reachable.
  Planning and approval still never trigger an automation action.
- No new client-facing tool is added.

### D8. Compatibility and migration

- `POLICY_VERSION` goes to `f2-v3`, and `COMPATIBLE_POLICY_VERSIONS` keeps
  `f2-v1` and `f2-v2` readable.
- `policy_snapshot_matches` recomputes under the current policy, so a pending
  `f2-v2` plan whose decision changes **fails validation and must be
  recreated**. This is the Beta 33 precedent: terminal and prohibited records
  are never rewritten. Historical projections in `historical_policy.py` gain
  the new action kind as read-only.
- The new action uses `authority_version` 4. Version 3 bundles remain valid
  for plans that do not require it.
- **Implementation (PR 1).** Two read-only boundaries keep admitting an exact
  `f2-v2` snapshot: F3 rollback creation reads an applied source as evidence
  (the inverse is evaluated under `f2-v3`), and reverification reads the
  consumed plan of the task it reverifies. Neither dispatches under the
  historical decision; every other load refuses it.
- Public output changes:
  - the new `ApprovalActionKind` value;
  - new reason codes;
  - the `physical_effect_comparison` projection;
  - the OD-4 disclosure values.
- These are protected surfaces: `governance/`, `approval_web.py`,
  `tools/governance.py` descriptions and capability metadata. The
  implementation task must name them explicitly
  (`-AuthorizedProtectedPath hass_mcp_engineering_beta/ha_mcp_engineering/`).
- **Implementation deviation (PR 1).** The projection hash is not bound into
  the policy subject. The comparison is a deterministic function of the
  operation's normalized before and after configurations, which the plan hash
  already binds, and `policy_snapshot_matches` recomputes the decision from
  them. The acknowledgement record stores `safety_critical_projection_hash`
  and the sorted object set. The grant and every dispatch recompute the
  binding from the immutable plan and refuse a mismatch with
  `safety_critical_acknowledgement_mismatch`. Keeping the policy subject
  unchanged leaves the historical policy projections and hashes for `f2-v1`
  and `f2-v2` records untouched.

### D9. Security section (for the implementation PR)

- **Main risk: a false `equivalent`.** It would remove the safety
  acknowledgement, though not the elevated plan approval under OD-5.
  Mitigations:
  - closed tables;
  - unknown service on a protected target counts as an effect;
  - unknown structure becomes `unresolved`;
  - mutation tests that delete each rule and must fail;
  - property tests that permute only reference-only non-aborting steps and must
    stay `equivalent`, and any change to effects, waits, conditions, triggers,
    mode or abort points must become `changed`.
- **Approval forgery.** Covered by the HAMCP-205 verifier, the Ingress peer
  check, single-use CSRF, the same-principal rule, the exact object-set match,
  hash binding and dispatch recheck. Each needs a negative test.
- **Assistant self-approval.** Prevented by channel separation (no MCP decision
  path), proven by catalog and route tests.
- **Downgrade by plan splitting.** Each operation is classified
  independently, and creates and scripts are covered, so splitting a change
  across plans cannot avoid the acknowledgement.

### D10. Tests the implementation must add

- **Success:**
  - exact script and automation updates with `changed` effects are actionable
    end to end after plan approval plus the acknowledgement (useful authorized
    success);
  - `equivalent` plans need only plan approval;
  - the audit-shaped plan (wording, reorder with `continue_on_error`,
    logbook-only reference, mode change on a cover-free warning automation) is
    not prohibited.
- **Refusal and failure preservation:**
  - incomplete execution contract → `prohibited`;
  - retry/inverse marker that fails → `prohibited`;
  - unresolved target → acknowledgement shows "cannot be determined";
  - all approval forgery and staleness negatives from D1;
  - dispatch refuses a missing or mismatched acknowledgement;
  - a pending `f2-v2` plan cannot be approved after upgrade.
- **E013 regressions:** one test per symptom in §1.3.
- **Mutations:** remove each of the reference-only table, the abort-point rule,
  the mode rule, the run-occupancy rule and the object-set check; each
  mutation must fail a test.
- **PR001 regression fixtures:** the anonymized fixtures in
  `docs/evidence/hamcp213/pr001/` (or test copies with the same provenance
  note) must give op1 `none`, op2 `equivalent` (or `changed` if OD-10 chooses
  the conservative rule), op3 `equivalent` and op4 `none`, with no recipient
  reason and no retry warning. Each of the seven D6 mutations must give
  `changed`.

### D11. Proposed PR slicing (OD-8)

- **Recommended:** one protected-runtime PR. D1–D4 interlock: the precision
  fixes lower classifications, and that is safe only once the acknowledgement
  and comparison exist.
- D5 (registry `device_class`) can follow as a second PR.
- Alternative: land D4 alone first. The precision fixes only remove
  false-positive triggers, but alone they would move some plans from
  `prohibited` to plan-approval-only without the new acknowledgement.

---

## Owner decisions required before code

| ID | Decision | Recommendation |
| --- | --- | --- |
| OD-1 | New action kind inside `elevated_admin`, or a new policy class | New action kind |
| OD-2 | Approver: any verified HA administrator, or `is_owner` only | Any verified administrator, same principal as plan approval |
| OD-3 | Per-object checkboxes, or typed entity ids | Checkboxes |
| OD-4 | Replace the `no_external_approver_exists` disclosure | Yes: `no_decision_recorded` / `not_approvable` |
| OD-5 | Approval for `equivalent` | `elevated_admin`, plan approval only |
| OD-6 | Cover (a) creates and (b) `lock.unlock` / `alarm_disarm` | (a) yes, (b) yes (supersedes part of ADR-012) |
| OD-7 | Protected-object rules | Keep the floor; add cover `device_class` garage/gate/door and `siren` |
| OD-8 | PR slicing | One protected PR for D1–D4; D5 separately |
| OD-9 | Added notification buttons that lead to a protected effect elsewhere (e.g. "close garage") | Disclose as reference evidence; not an effect of the sending operation |
| OD-10 | Do non-aborting reference-only steps (pushes, logbook with `continue_on_error`) count for `equivalent`, before an effect (latency) and after it (run occupancy)? | Ignore them; all other added steps count everywhere. Conservative alternative: a reference step added before an effect is `changed` (makes PR001 op2 `changed`) |

## Not done in this task

- No runtime, schema, policy, test, workflow, release or deployment change.
- No live Home Assistant or MCP access. Plan `7b651ef4…` was not read live;
  the owner supplied its configurations offline, and only anonymized
  structural transforms are committed (D6).
- The D2 checker in `evidence/hamcp213/pr001/` is a design prototype. It does
  not handle `unresolved` (templated targets, blueprints) or transitive callee
  comparison, and it is not a proposed implementation.
- No validation tiers were run, because the change is documentation and
  offline probes.
- No claims about Home Assistant script-abort semantics or script-provider
  exactness were verified. Both are listed as prerequisites in D2 and D3.
