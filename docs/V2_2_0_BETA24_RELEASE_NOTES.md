# HA MCP Engineering 2.2.0-beta.24 held-read canary release notes

Beta 24 adds one Engineering-native, read-only `run_held_read_canary`
capability. It can execute a reviewed upstream tool only while that tool's
source policy classification remains exactly `held_for_canary` and the caller's
expected compatibility-entry ID equals the active exact entry.

The canary uses the existing upstream read transport and revalidates exact
server identity, release, protocol, reviewed input schema, description,
annotations/security, output contract, and runtime contract in the same MCP
session before dispatch. It has no fallback. Arguments are validated before
network dispatch; results remain untrusted, are sanitized and bounded, and are
checked against a declared output schema where one exists.

This capability is an unapproved read-only operator diagnostic. Invocation
does not convey approval, create approval state, admit a tool, or authorize
promotion.

Canary evidence reports identity and compatibility binding, reviewed and
observed input-schema fingerprints, match status for the relevant annotation,
security, output, and runtime contracts, dispatch and provider status, truthful
success/partial/failure outcome, completeness/truncation, bounded structured
error code-and-shape evidence, and `promotion_performed: false`. Audit retains
bounded routing and outcome evidence without caller payloads or upstream result
content.

`ha_search` and `ha_get_operation_status` remain held, absent from the dynamic
delegated catalog, and independently testable after deployment. Beta 24 does
not promote either tool, alter the compatibility registry or policy, create
governance or execution state, or add Home Assistant writes, service calls,
physical actions, fallback, deployment, or artifact-provenance authority.

The static catalog becomes 49 tools: 25 canonical and 24 Engineering-native.
Exact 8.0.0, 8.1.0, and 8.1.1 continue to expose exactly 24 automatic delegated
reads, for 73 total tools. Dashboard-provider behavior is unchanged.
