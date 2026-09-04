# Held-read canary error taxonomy

`run_held_read_canary` is an operator-only evidence surface. It does not publish
or promote an upstream tool. `ha_get_operation_status` remains
`held_for_canary`, is absent from ordinary `tools/list`, and can run only when
the live catalog still matches an exact compiled compatibility entry.

## Reviewed not-found outcome

The reviewed `ha_get_operation_status` implementations in ha-mcp 8.0.0,
8.1.0, 8.1.1, 8.2.0, and 8.4.1 return a structured
`RESOURCE_NOT_FOUND` error when an operation identifier is absent or no longer
retained. Engineering recognizes that code only from a bounded structured MCP
error envelope for this exact tool. It maps to the existing canonical
`resource_not_found` application outcome and is non-retryable.

Free-form messages, exception text, descriptions, annotations, and substring
matches never select the outcome. Missing, incorrectly typed, unknown,
oversized, malformed, or conflicting structured codes remain fail-closed
provider errors. Transport, timeout, session, identity, protocol, and contract
failures retain their existing provider classifications.

For a bounded batch request, each item is checked against the reviewed
operation-status result shape. A missing operation remains an item-level
non-retryable `resource_not_found`; it does not turn sibling results into a
transport outage. Malformed batch items fail the canary response contract.
Bounded item evidence excludes operation identifiers and upstream messages.

## Security boundary

The canary performs one validated upstream read call and never calls Home
Assistant directly. It does not create a plan, approval, execution task, or
write. Every outcome keeps `promotion_performed=false` and `fallback=none`, and
the admitted catalog is unchanged. A positive operation-status result still
requires a legitimate naturally occurring operation identifier and separate
authorization; this correction establishes negative-result truthfulness only.

The Beta 39 promotion capture remains byte-for-byte historical synthetic
evidence of the old misclassification. It is not exact-writer output and is not
rewritten by this correction. Current behavior is covered by a separate,
deterministic synthetic fixture that runs through the production canary path.
The historical sentinel's expected-failure status must not be changed until a
future staged release is deployed and a separately authorized read-only
capture confirms the corrected runtime behavior.
