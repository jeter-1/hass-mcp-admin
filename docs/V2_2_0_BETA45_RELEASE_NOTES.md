# Engineering 2.2.0-beta.45 release notes

Beta 45 stages a general provenance correction for helper dependency risk.
Engineering 2.2.0-beta.44 remains advertised until protected promotion. Stable
v1.1.2 and the 51-tool Engineering registration remain unchanged.

## Exact exclusion without weakened ambiguity handling

The obligation ledger now records whether a bounded entity-candidate universe
is complete independently from whether the exact runtime subset is known. A
complete finite sensor-only set can therefore exclude one `input_boolean`
target without leaving a false opaque obligation. If that set contains the
target, the exact dependency remains. Partial, overflowing, malformed, dynamic
or unsupported selectors remain opaque or fail coverage; candidate hints on an
opaque terminal never authorize exclusion.

The same provenance survives reviewed assignments, branches, finite loops,
literal containers and subscriptions, provenance-preserving filters and
complete non-helper state domains. Reviewed timestamp and duration operations
are neutral after retaining any exact source-state dependency.

Literal `label_entities(...)` membership is resolved during dependency-index
construction against the scan's bounded entity and label registry generation.
The resolver uses Home Assistant's ID-first lookup, falling back to label-name
matching with `casefold().replace(" ", "")`; a colliding label ID takes
precedence over another label's matching name. Exact membership, lookup mode,
resolved identity and the complete composite candidate union become
approval-bound evidence. Label candidates never replace independent exact
candidates from finite lists, mappings, branches or loops. Dynamic labels,
failed or partial registry evidence, unresolved composite branches, truncation
and overflow remain conservative, and a proven target inclusion cannot be
erased by those unresolved components.

## Consequence, actionability and locks

Only sources with a surviving authoritative target-relevant edge contribute
downstream consequences. An unrelated cover action no longer contaminates a
helper assessment solely because its automation contains an excluded template.
Independent exact target relationships in that same source continue to retain
the full consequence.

Complete unrelated evidence is low/standard and execution-eligible. Exact
consequential evidence remains elevated. Genuine target-capable opacity is
incomplete, elevated/high, execution-ineligible and non-actionable. Exact,
excluded and opaque evidence respectively produce exact, no unnecessary, and
conservative helper-dependency locks from the same projection.

Candidate membership, label membership, domains, branch provenance, source
configuration, semantic registry, completeness and lock changes remain bound to
approval drift and final preflight.

## Compatibility and non-actions

No public schema, tool registration, helper authority, approval authority, task
schema, provider admission/routing, fallback, workflow, container, deployment
or stable-v1 surface changes. The exact `input_boolean.turn_on`/`turn_off`
provider contract, durable intent, one dispatch attempt, authoritative
readback, duplicate suppression, uncertain-response recovery and audit behavior
remain unchanged.

This staging change performs no merge, promotion, publication, deployment,
restart, live Home Assistant access or helper canary. Post-deployment read-only
verification and any governed write canary require separate authorization.
