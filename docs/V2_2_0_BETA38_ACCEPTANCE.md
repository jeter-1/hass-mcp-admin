# Engineering 2.2.0-beta.38 acceptance

This procedure applies only after independent source review, protected merge,
promotion, publication, separately authorized deployment, and separately
authorized live mutation. Development and source validation must not access the
deployed MCP server or household Home Assistant.

## Pre-deployment gates

1. Confirm the source baseline is current `main` after Beta 37 promotion,
   advertised Engineering source remains `2.2.0-beta.37`, stable remains
   `1.1.2`, and `.release/next-version` stages only `2.2.0-beta.38`.
2. Confirm the public helper tool still accepts only exact
   `input_boolean.<object_id>`, explicit `on|off`, and bounded expiration. No
   toggle, arbitrary service/data, physical domain, delegated write, or
   fallback may be reachable.
3. Require positive and negative tests for literal lists, list-of-dictionary
   fields, literal maps, finite conditionals, exact literal labels,
   label-plus-list unions, exact target inclusion/exclusion, static dependency
   preservation, consequential dependencies, unknown expressions, dynamic
   labels, malformed candidates, selector/read failure, overflow, automation
   drift, label drift, stale evidence, exact state-read functions, direct and
   negated tests, finite `select`/`reject` tests, finite `map` filters,
   target/non-target domain collections, locks, duplicate apply, response
   loss, and recovery.
4. Require `get_server_health` tests for REST and WebSocket connected,
   REST-only, unavailable, and unprobed Home Assistant states. The helper
   operation must use provider
   `direct_home_assistant_state`, contract
   `direct-ha-exact-input-boolean-v1`, fallback `none`, and no upstream
   substitution.
5. Require Full and Evidence gates, full unit discovery, compilation,
   dependency and vulnerability checks, YAML, PowerShell, secret, whitespace,
   stable-v1, exact-image/readmission, exact ha-mcp, and disposable Home
   Assistant 2026.7.2, 2026.8.0, and 2026.8.1 jobs.
6. Confirm exact tool accounting remains 51 static tools: 25 canonical and 26
   Engineering-native. Do not loosen exact equality assertions.

## Resolver acceptance contract

The implementation may statically resolve only the reviewed finite grammar:

- literal exact entity lists;
- literal maps/dictionaries and finite field/value selection;
- finite conditional unions whose branches are exact entity IDs;
- exact non-target domains already proven by the reviewed Beta 37 grammar;
- exact entity operands used with reviewed Home Assistant `states`,
  `state_attr`, and `has_value` filters;
- exact entity operands used with reviewed Home Assistant `is_state`,
  `is_state_attr`, and `has_value` tests;
- finite exact entity collections used with reviewed `is_state`,
  `is_state_attr`, or `has_value` tests through `select`/`reject`;
- exact-domain state collections used through `selectattr`/`rejectattr` when
  the exact tested attribute is `entity_id`; other attribute collections stay
  non-conclusive unless their candidates are otherwise finitely proven;
- finite exact entity collections used with reviewed `states`, `state_attr`,
  or `has_value` filters through `map`;
- direct `is not <reviewed-test>` forms, which retain the same entity
  dependency as the positive test;
- `states.<domain>` collections as exact domain evidence, with the target
  domain remaining relevant and a proven non-target domain excluded;
- literal `label_entities('exact-label')` membership from bounded entity and
  label registry evidence;
- bounded unions and nesting of those constructs.

The resolver must not render templates. Dynamic/computed label names, arbitrary
calls, unreviewed filters/tests, unrestricted iteration, custom functions,
macros, invalid entity IDs, read failures, partial membership, and any exceeded
bound remain non-conclusive. A reviewed direct or collection entity-read form
whose collection, operator, or candidate set is not exact or finitely resolved
must emit incomplete evidence; it must never become zero evidence. The plan
must contain bounded deterministic candidate, operator, selector, membership,
and expression evidence. Material candidate or operator changes must alter the
dependency fingerprint checked at final preflight.

An exact target absent from complete candidates is excluded only for that
target. An exact target present in the candidates remains a true dependency and
retains its downstream consequence. Direct/static references are always
retained.

## Post-deployment read-only entry gate

Before any mutation:

1. Confirm version/build identity is exactly `2.2.0-beta.38` and matches the
   approved artifact.
2. Confirm static/delegated tool counts, schemas, annotations, and catalog
   fingerprint match the approved release contract.
3. Call `get_server_health` and require the helper-state provider entry and the
   `set_input_boolean_state` operation to report:
   `direct_home_assistant_state`,
   `direct-ha-exact-input-boolean-v1`, and fallback `none`.
4. Require Home Assistant REST and read-only WebSocket probes both connected,
   the helper provider available/healthy, governance storage and F3 healthy,
   approval authority 3, task schema 1, external approval available, and no
   provider substitution. REST-only evidence is not sufficient.
5. Confirm the dependency index is current, automation coverage complete,
   blueprint coverage complete, and failed automation reads zero for the live
   canary evidence.

Stop on any version, schema, tool-count, provider, fallback, health, approval,
storage, or evidence mismatch.

## Separately approved live canary

Candidate: `input_boolean.mcp_f2_standard_admin_test_flag`.

Expected pre-state from the Beta 37 investigation: `off`. Re-read it; do not
assume it remains unchanged. The acceptance sequence is `off -> on -> off` only
when the observed pre-state is still `off`.

Before creating the forward plan, require:

- exact entity and `off` state readback;
- automation and blueprint coverage complete;
- zero failed automation reads and zero exact static references;
- every bounded unrelated dynamic expression resolved target-specifically;
- complete, current, non-truncated dependency evidence;
- no consequential downstream dependency;
- low/standard/no-consequence classification produced from that evidence;
- exact healthy helper provider attribution and zero fallback.

Then:

1. Obtain Josh's explicit approval for the exact `off -> on` proposal.
2. Require durable intent before exactly one
   `input_boolean.turn_on` dispatch, authoritative `on` reread, terminal
   `succeeded_verified`, and zero fallback.
3. Apply the completed plan again and require `already_applied` with zero
   additional dispatch.
4. Create a fresh `on -> off` proposal. Obtain separate explicit approval.
5. Require exactly one `input_boolean.turn_off` dispatch, authoritative `off`
   reread, terminal verified success, and zero fallback.

If candidate membership, label membership, relevant automation configuration,
coverage, freshness, or the helper baseline changes after approval, final
preflight must reject before dispatch and require a fresh plan. A response-loss
case must reread first and must not redispatch.

The Beta 37 Gate 4 result remains blocked/unfinished until this post-deployment
canary passes. Source tests and disposable Home Assistant CI are not household
acceptance and do not authorize this mutation.

## Non-goals

No dashboard, HAMCP-106, Android/iOS, notification, scene, group, script,
generic service, new helper family, physical action, deployment, credential,
or fallback work is part of Beta 38.
