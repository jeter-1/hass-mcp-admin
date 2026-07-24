# RC2dev16 pre-deployment acceptance contract

Version: `2.0.0-rc2-dev16`
Status: development-candidate verification procedure only; not published,
deployed, or accepted

This document does not declare a release, record an acceptance pass, authorize
publication, authorize deployment, or authorize access to a live Home
Assistant system. Deployed checks require separate authorization tied to an
exact candidate identity and exact environment.

## Repository gates

1. Review the complete diff from the exact base and confirm stable v1.1.2 is
   unchanged.
2. Confirm the only public behavior change is bounded delegated error
   normalization through existing response structures.
3. Confirm public delegated schemas, reviewed input/runtime-description/
   annotation/output fingerprints, and policy classifications are unchanged.
4. Confirm the static catalog remains 41 tools and the reviewed automatic-read
   inventory remains 26 tools, for 67 total when all reviewed reads are
   admitted.
5. Confirm direct Home Assistant fallback, upstream fallback, writes, service
   calls, physical actions, reloads, restarts, and arbitrary forwarding remain
   unreachable.
6. Run focused gateway, audit, redaction, observability, admission, dashboard,
   metadata, documentation, schema, and stable-v1 regression tests.
7. Run the complete offline unittest, compilation, metadata, YAML, dependency,
   secret-scan, PowerShell, protected-path, whitespace, and Evidence gates from
   a clean committed head.
8. Require the CI exact-image gate to retain the pinned ha-mcp 7.14.1,
   78-tool, 26-read, 67-total contract and exercise its real validation,
   missing-entity, missing-automation, and provider-failure envelopes through
   the Engineering gateway.

## Disposable normalization scenarios

Use deterministic fixtures or disposable systems. Do not use a household Home
Assistant instance.

1. Return a normal delegated read and require complete success,
   `upstream_read_gateway` attribution, exact reviewed upstream identity, and
   zero fallback.
2. Return valid `ha_search` data with `success: true` and `partial: true`.
   Require successful partial semantics, bounded `partial_reason`, no
   exhaustive-coverage claim, and no provider operational failure.
3. Return each reviewed structured validation code. Require bounded,
   non-retryable `invalid_request`, a failed audit outcome, completed dispatch
   accounting, and no provider operational-failure increment.
4. Return reviewed optional-capability-unavailable evidence. Require bounded,
   non-retryable `unsupported_operation`, no connection-failure
   classification, no installation recommendation, and no provider
   operational-failure increment.
5. Return reviewed authentication, connection, timeout, and internal-provider
   codes independently. Require their Engineering-owned public categories,
   retryability, audit outcomes, and provider-health effects.
6. Return the reviewed tool/code pairs for missing entity state or registry
   data, automation configuration, calendar entity, category, label, scene,
   script, blueprint, device, HACS repository, skill-guide resource, and zone.
   Require the tool-aware bounded message, non-retryability, domain-outcome
   accounting, and no provider operational-failure increment.
7. Return `RESOURCE_NOT_FOUND`, `ENTITY_NOT_FOUND`, `CONFIG_NOT_FOUND`, or
   `ENTITY_INVALID_ID` for a tool/code pair that is not explicitly reviewed.
   Require the bounded generic provider-failure path; do not infer meaning
   from the code alone.
8. Return duplicate JSON members at the envelope, error, and nested-data
   levels in both allowlisted/unknown orders. Return `NaN`, `Infinity`, and
   `-Infinity` at top-level and nested positions. Require strict rejection
   before classification and a bounded operational provider failure.
9. Return unknown, malformed, nested, ambiguous, oversized, and hostile error
   content containing token-like strings, authorization headers, secret-path
   URLs, control characters, and prompt-like instructions. Require bounded
   generic provider failure with no reflected code, prose, credential,
   metadata, or instruction.
10. Confirm every failure preserves provider/upstream attribution, the request
   ID, `fallback: none`, and `fallback_occurred: false`.

## Search-routing review

Confirm operator/client guidance recommends:

- filtered `ha_search` or `search_entities` for entity discovery;
- `entity_dependency_analysis` for exact static entity references;
- `ha_config_get_automation` or `get_automation_config` for a known
  automation, according to the required provider surface;
- `ha_search` with explicit `search_types` for arbitrary configuration text;
- `config_time_budget` when accepting truthful partial coverage from a broad
  configuration scan; and
- the standard Home Assistant MCP action capability for routine household
  actions, never an Engineering fallback.

The guidance must not promise latency, treat a partial result as exhaustive,
describe the Engineering dependency index as free-text search, require the
optional companion component, or introduce a new Engineering search
responsibility.

## Separately authorized deployed-runtime checks

Do not execute these steps without distinct live-environment authorization.

1. Call `server_info`. Expect the approved build/version and 67 tools: 41
   Engineering plus 26 delegated.
2. Call `ha_get_state`. Expect complete success, `upstream_read_gateway`, and
   no fallback.
3. Call `ha_search` with intentionally invalid structured input accepted by
   the MCP schema, such as an empty `search_types` array if still applicable.
   Expect bounded non-retryable caller validation, no raw upstream text, and no
   provider operational-failure increment.
4. Call `ha_get_state` for a guaranteed nonexistent test entity. Expect
   bounded non-retryable `entity_not_found`, no raw upstream text, and no
   provider operational-failure increment.
5. Call `ha_config_get_automation` for a guaranteed nonexistent test
   automation. Expect bounded non-retryable `automation_not_found`, no raw
   upstream text, and no provider operational-failure increment.
6. Call one known optional component-dependent read that is unavailable in the
   deployed environment, if applicable. Expect non-retryable
   capability-unavailable semantics rather than a connection error.
7. Call `ha_search` with a small `config_time_budget`. If the scan cannot
   complete, expect a successful partial result and no provider-failure
   classification.
8. Call `get_server_health`. Confirm coherent operational counters and zero
   fallback.
9. Inspect `get_audit_log`. Confirm bounded, redacted, truthful classifications
   for success, partial, validation, capability, authentication, connection,
   timeout, reviewed domain outcomes, and generic provider outcomes.

Acceptance fails on schema or fingerprint drift, tool-count change,
self-authorized upstream behavior, leaked provider content, inflated
operational-failure counters, partial-to-failure conversion, fallback, stable
v1 change, or any new write/action/deployment reachability.
