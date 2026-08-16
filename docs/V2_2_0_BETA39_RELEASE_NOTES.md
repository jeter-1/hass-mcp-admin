# Engineering 2.2.0-beta.39 release notes

Beta 39 is a corrective HAMCP-089 release for exact governed
`input_boolean` state changes. It replaces wrapper-specific helper dependency
classification with whole-template, whole-configuration obligation accounting.
Beta 38 remains advertised until a separate protected promotion.

## Whole-template dependency obligations

Engineering now parses each complete Jinja template with pinned Jinja 3.1.6
syntax compatible with the supported Home Assistant 2026.7.2, 2026.8.0, and
2026.8.1 releases. Parsing is static: templates are never loaded, rendered, or
executed, and Home Assistant helpers are never called.

Every dependency-sensitive construct creates an internal obligation before
precision analysis. Each obligation ends as an exact dependency, proven target
exclusion, proven dependency-neutral result, bounded semantic opacity, or
coverage failure. Unknown nodes and callables can no longer disappear as zero
evidence. Shared binding state covers assignments, loops, branches, local
macros, collections, attributes/items, filters/tests, and later invocation.
Imports, includes, extends, and unavailable imported macros remain explicit
bounded external opacity; Beta 39 does not retrieve custom-template source.

The reviewed semantic-registry declaration is independent of the runtime output,
binds immutable Home Assistant and Jinja provenance, and generates the checked-in
registry reproducibly offline. It covers state helpers,
translated state helpers, entity-set producers, dynamic filter/test dispatch,
ordinary methods with proven receiver provenance, and state-bearing trigger,
wait, and automation context. Unknown future vocabulary remains opaque rather
than harmless.

Jinja mappings, sequences, and namespaces retain distinct receiver semantics,
including bounded positional construction, mapping-key iteration, and method-name
collisions. Runtime strings returned by state helpers or trigger/event metadata
remain harmless when used only for display, but retain provenance if later fed into
an entity selector. Inventory, blueprint-source, event-selector, AST, value, and
evidence limits terminate as explicit coverage failure instead of silent clipping.

## Proportional risk and truthful actionability

The helper-risk projection distinguishes exact evidence, bounded semantic
opacity, and evidence coverage failure. Bounded opacity may remain actionable
when its automation identities, downstream effects, and conservative lock scope
are all known and bounded. Benign effects may remain low/standard; consequential
or unknown effects remain elevated/high with visible acknowledgement.

Coverage failure—such as stale or failed inventory, hidden identities,
unbounded truncation, missing profiles, or unavailable lock scope—is not
approval-actionable. This preserves useful operation without representing
missing evidence as harmless or turning every bounded uncertainty into blanket
prohibition.

## Shared locks and drift evidence

Risk and F3 consume the same ledger. Helper execution holds both its exact
dependency key and a shared conservative introduction guard so a newly added
dependency cannot race the final refresh. Automation configuration takes the
matching exclusive exact key for exact references, the exclusive conservative
key for bounded opacity, and neither for proven exclusions. Exact and opaque
evidence also bind the known relevant automation resources, so unrelated work
remains concurrent. External-template opacity is represented by a deterministic
reload lock identity without adding any custom-template write capability.

Approval binds semantic-registry identity, obligation outcomes, source and
context provenance, possible automation effects, coverage/bounds, and lock
projection. Final preflight reanalyzes after locks are held. A material change
requires a fresh plan and approval.

## Preserved boundaries

- Public registration remains 51 tools: 25 canonical and 26
  Engineering-native.
- Helper inputs remain `entity_id`, `desired_state`, and
  `expiration_minutes`; only exact `input_boolean.*` and `on|off` are accepted.
- Exact `input_boolean.turn_on`/`turn_off`, durable intent, one dispatch,
  authoritative reread, duplicate suppression, recovery, audit attribution,
  and fallback `none` are unchanged.
- Provider identity remains `direct_home_assistant_state`, contract
  `direct-ha-exact-input-boolean-v1`.
- Approval authority remains 3; task schema remains 1.
- Stable v1.1.2, standard ha-mcp admission, workflows, containers, deployment,
  dashboards, and mobile navigation are unchanged.
- No live Home Assistant or deployed MCP endpoint is used during development.

The post-deployment `off -> on -> off` canary remains separately authorized and
is defined in the Beta 39 acceptance document.
