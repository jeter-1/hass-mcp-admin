# Engineering Runtime Instructions

These are more-specific instructions for `hass_mcp_engineering_beta/`.
Nonconflicting repository-root instructions still apply; this file takes
precedence if guidance conflicts. This subtree contains the v2 Engineering
runtime and is security-sensitive.

- Preserve every public schema unless the task explicitly authorizes a compatible
  schema change and its migration review.
- Keep routing fail-closed. Only reviewed provider contracts may dispatch, and an
  upstream observation must never admit itself.
- Do not add unreviewed write, physical-action, arbitrary-forwarding, direct-HA,
  or fallback reachability.
- Preserve bounded output, sanitization, redaction, audit attribution, and
  governance/external-approval boundaries.
- Treat the Engineering MCP as the supported client-facing endpoint. Reviewed
  `ha-mcp` tools are internal capability providers selected through explicit
  admission and routing; they are not a second client-facing server and cannot
  self-authorize, silently fall back, or bypass provider attribution.
- Preserve hard technical refusal when exact target, operation, arguments,
  authority, provider contract, stale-state protection, concurrency safety,
  one-dispatch ownership, or verification cannot be established. Do not use a
  risk label, physical consequence, or incomplete consequence analysis alone as
  a technical refusal once the operation itself is exact and the owner has made
  the required decision. Runtime changes implementing this policy require their
  own explicit scope, tests, review, release, and deployment; these instructions
  do not alter currently shipped authority.
- Security-sensitive changes require focused success tests plus negative tests for
  rejected identities, versions, schemas, arguments, unavailable providers, and
  forbidden fallback paths.
- Review registration, capability metadata, routing, provider policy, docs, and
  acceptance guidance together; a change in one surface must remain consistent
  with the others.
