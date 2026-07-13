# Beta 17 release notes

HA MCP Engineering Server `2.0.0-beta.17` adds the read-only
`configuration_integrity_analysis` capability. It detects exact missing entity
references, references to disabled and registry-only entities, conservative
orphan-registry candidates, and unresolved dynamic references as separate
finding classes.

The analyzer reuses the shared dependency index, obtains one bounded state and
entity-registry inventory per new analysis, reports unsupported source coverage,
and continues through Beta 16 signed immutable pagination snapshots. It does not
automatically remove registry entries, rewrite references, modify configuration,
execute a service, or create a governance plan.

This release increases the beta MCP catalog from 35 to 36 tools. A beta connector
with a cached catalog may need to be refreshed or recreated after deployment.
Production v1.1.2 remains unchanged. Incident correlation, handoff generation,
and RC stabilization remain later milestones.
