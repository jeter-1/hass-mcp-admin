# RC2dev16 development notes

Version: `2.0.0-rc2-dev16`
Status: development candidate; not published, deployed, or accepted

Repository version metadata advertises this local candidate identity. This
document does not publish, deploy, or accept it. It records the bounded issue
#57 correction that must be independently reviewed and validated before any
separate release decision.

## Delegated structured-error normalization

Dev16 preserves safe distinctions from the reviewed ha-mcp 7.14.1 structured
error envelope. Engineering recognizes only exact allowlisted error codes and
maps them to Engineering-owned categories, messages, and retryability:

- invalid caller arguments are non-retryable `invalid_request` outcomes;
- an unavailable optional capability is a non-retryable
  `unsupported_operation` outcome;
- authentication failures remain non-retryable authentication failures;
- connection failures and timeouts retain their existing retryable provider
  classifications; and
- genuine internal, unknown, malformed, ambiguous, or oversized errors use the
  bounded generic provider-error path.

Upstream messages, details, suggestions, metadata, and retryability claims are
untrusted. They are not reflected to clients, logs, audits, or health state.
Unknown codes do not create new public classifications. Existing redaction,
output bounds, exact admission, pre-dispatch contract checks, provider
attribution, request IDs, and no-fallback enforcement remain in force.

A caller-validation or capability-unavailable answer is a completed upstream
dispatch and a failed tool outcome, but not a provider operational outage.
Authentication, connection, timeout, and genuine provider failures continue to
degrade provider health according to the existing counter contract. Successful
reads and valid `ha_search` partial results retain their existing response,
audit, completeness, and counter semantics.

## Search routing

Dev16 documents the narrowest existing capability for each search intent:
filtered `ha_search` or `search_entities` for entity discovery,
`entity_dependency_analysis` for exact static references, a direct automation
configuration read for a known automation, and `ha_search` with explicit
`search_types` for arbitrary configuration text.

Broad automation configuration-body search can be materially slower when
upstream bulk access is unavailable. `config_time_budget` bounds the
configuration-fetch phase rather than promising a strict end-to-end deadline,
and its truthful partial result is not exhaustive. The Engineering dependency
index remains a structured-reference facility, not a free-text index.

The optional companion component is not required by Engineering and is not
recommended as a routine latency fix. This correction adds no search tool,
cache, index, bulk-search implementation, direct Home Assistant fallback, or
provider route.

## Preserved boundaries

The candidate preserves 25 canonical tools, 16 additive Engineering tools, 26
reviewed delegated reads, and 67 total registered tools when every reviewed
read is admitted. It changes no delegated input/output schema, reviewed
fingerprint, tool classification, dashboard contract, governance behavior,
write reachability, approval lifecycle, add-on option, workflow permission,
container behavior, or stable v1.1.2 source.

The generic signed-registry milestone previously described as planned Dev16
work remains deferred and is not part of this candidate. This document creates
no release, tag, image, registry mutation, deployment, or Home Assistant
mutation.
