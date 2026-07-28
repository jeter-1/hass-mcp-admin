# HA MCP Engineering Server 2.1.1-beta.2

Status: Beta 2 corrective source candidate. Deployment acceptance is outside
this source change.

This corrective release preserves the complete 2.1A Beta 2 governed
operational lifecycle while fixing Engineering add-on self-restart target
identity. It adds no MCP tool, public schema, operation, provider fallback, or
new upstream contract. The fully admitted catalog remains 45 Engineering tools
plus 26 delegated reads, or 71 total.

## Exact self identity

Home Assistant may install a repository add-on with a Supervisor slug such as
`<repository>_hass_mcp_engineering_beta`; that installed slug is not the MCP
server ID or the source add-on slug. Engineering now resolves its own identity
from Supervisor's caller-relative `/addons/self/info` endpoint using the
already injected add-on token. The bounded result supplies the exact installed
slug, name, version, repository identifier when available, and the
`supervisor_self_info` evidence source. No `hassio_api` permission or generic
Supervisor proxy was added.

Planning first requires this authoritative identity, then resolves the
requested installed add-on through the exact reviewed `ha_get_addon` contract.
An exact self match becomes `engineering_addon` and requires
`restart_proof=process_identity`. The admitted upstream target is resolved by
matching the configured MCP endpoint host to the documented Supervisor DNS
form of exactly one complete installed slug, then binding that target to exact
reviewed MCP admission. A bare `ha_mcp` source slug, repository-prefix guess,
display name, version, suffix, or lookalike is not identity evidence. The
bound target becomes `upstream_ha_mcp_addon` with `upstream_readmission`; an
authoritatively unrelated installed add-on remains `other_addon` with the
explicitly weaker `provider_acknowledgement`. Missing, malformed, ambiguous,
or conflicting special-target evidence fails closed.

New plans persist bounded target evidence: requested and resolved slug, name,
version, repository identifier, identity source, authoritative self/upstream
decisions, selected reviewed provider contract, and target class. Apply-time
revalidation must reproduce that evidence. Post-dispatch and recovered-process
verification now resolve the complete upstream binding again. The
`upstream_readmission` proof is emitted only when the fresh binding exactly
matches the immutable baseline and all eight reviewed provider-contract fields
are readmitted. Transient missing evidence remains pending without redispatch;
endpoint, bound-slug, ambiguous-identity, or conclusive contract drift fails
verification and requires a fresh plan.
Historical plans remain readable and retain their existing hashes. A plan made
under the earlier incorrect classification must not be reused; create a fresh
plan after installing this correction.

## Missing add-ons and provider health

The provider performs an exact, read-only installed-add-on inventory check
before the detail lookup. A missing requested slug returns non-retryable
`addon_not_found`, creates no plan or approval, performs no action dispatch,
and leaves fallback at zero. This is an expected domain outcome, recorded
separately from operational provider failures, so the last exact provider
contract state remains healthy. Authentication, connection, timeout, malformed
response, protocol, catalog, reviewed-contract, and genuine upstream failures
continue to degrade provider health.

Proposal audit records retain `access=proposal` and
`operation_class=proposal`, include the bounded requested `addon_slug`, and
classify `addon_not_found` as a domain outcome.

## Version decision and boundaries

The accepted artifact already uses `2.1.0-beta.2`, and Home Assistant add-on
upgrade ordering requires a version greater than the installed version.
`2.1.1-beta.2` is therefore the next ordered Beta 2 corrective version; it is
not a Beta 3 scope promotion.

The source add-on slug, ports, options, `/data`, external approval,
one-dispatch lifecycle, recovery, reload and restart providers, tool schemas,
tool counts, reviewed 7.14.1/7.14.2 fingerprints, dashboard attestations,
stable-v1 source, and zero-fallback policy are unchanged.

Exact-image acceptance for both reviewed upstream releases now uses the
Supervisor-compatible full-slug endpoint identity, reports the lane-specific
installed version, creates the upstream restart proposal through the production
lifecycle provider, and exercises successful and drifted recovered
verification without performing a restart.
