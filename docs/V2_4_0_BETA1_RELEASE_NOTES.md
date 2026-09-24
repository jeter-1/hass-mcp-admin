# Engineering 2.4.0-beta.1 release notes

This is an authoring-stage candidate, not a published or installed release.
Advertised versions remain 2.3.0 until separately authorized materialization.

The candidate adds bounded script configuration evidence to the existing
`entity_dependency_analysis` tool through the admitted internal ha-mcp reader.
It preserves canonical storage identity across renamed entities, includes
registry-only disabled scripts when identity is exact, and reports source-local
read failures, truncation and incomplete inventory explicitly.

Script diagnostics occupy a separate bounded partition in the shared dependency
index. Existing helper execution/consequence evidence and the other index consumers
retain their current automation/blueprint coverage. No script execution, new write,
direct configuration fallback, public tool/schema, signed authority or persisted
plan-format change is included. Direct script-service call graphs, transitive
effects and complete enumeration of file/package configuration remain outside
coverage. Successful discovered reads do not prove complete inventory.

The expected catalog remains 53 static plus 25 admitted delegated reads (78 when
all reviewed routes are available). Legacy stable-v1 remains frozen at 1.1.2.
See [candidate acceptance](V2_4_0_BETA1_ACCEPTANCE.md); passing source tests is
distinct from publication, deployment and installed acceptance.
