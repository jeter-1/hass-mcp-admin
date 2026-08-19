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

The reviewed semantic-registry declaration is independent of the runtime output
and generates the checked-in registry reproducibly offline. Generation verifies
provenance against independent witnesses rather than against its own declared
tuples: Jinja path/blob pairs are recomputed as git blob SHA-1 values from the
installed pinned `Jinja2` distribution, and Home Assistant path/blob pairs are
checked against the immutable captured evidence in
`docs/evidence/home-assistant-template-source-blobs.json`. A wrong blob, a
copied attribution, or a path that does not exist at a supported tag fails
generation.

The standard Jinja filter and test vocabulary is derived from the pinned
package rather than hand-listed, so every name Jinja binds - including the
`d`/`default`, `e`/`escape`, `count`/`length`, and comparison-test aliases - is
classified by construction. Beyond the standard vocabulary it covers state
helpers,
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
Raw `use_blueprint` configuration also retains a bounded external-source
obligation until the provider reads and analyzes that exact blueprint. Local F3
configuration projection cannot inspect the source body, so blueprint create,
update, and removal use the conservative helper-dependency guard rather than
claiming that missing raw references prove exclusion. Provider-side discharge
is atomic and binds the raw path/configuration, resolved configuration, and
complete resolved ledger; there is no Boolean or caller-asserted bypass.

Configuration-provided values now follow their runtime scope. Root defaults retain
Home Assistant's caller-supplied run-variable override alternative; variables
actions render and publish values in insertion order; disabled actions are not
analyzed as reachable; and parallel siblings cannot exchange branch-local
bindings. Exact zone-trigger provenance, Jinja loop metadata, Home Assistant
`repeat` values, event/payload mappings, and reviewed date/time results remain
typed. Runtime context data stays low-friction when formatted for display and
becomes conservative only when later consumed as an entity selector. These
transfers share aggregate depth, member, scalar, scope, capture, and work bounds.

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
projection. Final preflight reanalyzes after locks are held, behind a
source-read fence: the refresh is satisfied only by a scan whose source read
began after the lock was taken, so a build that was already running before the
lock cannot decide execution eligibility, and an invalidation raised during a
build is not cleared by that build. A material change requires a fresh plan and
approval.

The reviewed semantics are now bound to the connected instance. Each
dependency scan reads the running Home Assistant version from `GET /config`
and admits it against the registry's supported release tags - 2026.7.2,
2026.8.0, and 2026.8.1. Only an exact match keeps obligation-ledger evidence
execution-authoritative. An unsupported release, an unreachable instance, a
malformed configuration response, and a missing or empty version field each
fail closed as a coverage failure with its own distinguishable reason code,
so the plan is not approvable and no dispatch can occur. The observed version
and the gate's decision are bound into dependency evidence, into its
fingerprint, and into the approval provenance trail.

The version is read as part of the dependency scan rather than on every
eligibility check, so its freshness is exactly the snapshot's freshness and is
governed by the same source-read fence as the rest of the evidence: a fenced
post-lock refresh cannot be satisfied by a scan that observed an older
version. It is not a per-request read. A version change between scans is
observed at the next scan, and because the version participates in the
snapshot fingerprint, evidence produced against a different release cannot be
mistaken for evidence produced against this one.

The gate admits or refuses; it does not select per-version semantics. The
registry still asserts one shared reviewed `semantics` block across all three
supported releases.

Selector evidence is bounded per value and in aggregate before an obligation
exists. An oversized or secret-bearing value is replaced by a deterministic
digest that preserves drift detection, and losing target-specific detail
reclassifies the obligation as bounded semantic opacity with a conservative
lock rather than truncating it silently.

Compatibility with a superseded helper dependency-risk model is readability,
not execution authority. A plan carrying superseded evidence stays readable and
keeps readback-first recovery, but it is non-actionable, projects no locks, and
reports `next_required_operation: create_change_plan`; regaining execution
authority requires a replan.

## Known limitations

The registry declares an exact CI image digest per supported Home Assistant
release, but generation does not verify image digests offline; that field is
recorded as declared-but-unverified in `source_provenance`.

Version admission is scan-scoped, not per-request. The running version is read
once per dependency scan, so a Home Assistant upgrade is admitted or refused
from the next scan rather than the instant it happens. The source-read fence
bounds that window for anything that reaches dispatch, because the governed
post-lock refresh requires a scan that began after the lock.

Home Assistant's own template extensions beyond the reviewed vocabulary - most
notably `as_timestamp` - remain unknown and therefore conservative. See the
opacity measurement in the Beta 39 acceptance document for the observed
distribution.

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
