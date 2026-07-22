# Engineering Runtime Instructions

The repository-root instructions also apply here. This subtree contains the v2
Engineering runtime and is security-sensitive.

- Preserve every public schema unless the task explicitly authorizes a compatible
  schema change and its migration review.
- Keep routing fail-closed. Only reviewed provider contracts may dispatch, and an
  upstream observation must never admit itself.
- Do not add unreviewed write, physical-action, arbitrary-forwarding, direct-HA,
  or fallback reachability.
- Preserve bounded output, sanitization, redaction, audit attribution, and
  governance/external-approval boundaries.
- Security-sensitive changes require focused success tests plus negative tests for
  rejected identities, versions, schemas, arguments, unavailable providers, and
  forbidden fallback paths.
- Review registration, capability metadata, routing, provider policy, docs, and
  acceptance guidance together; a change in one surface must remain consistent
  with the others.
