# HA MCP Engineering 2.2.0-beta.22 approval-review release notes

Beta 22 makes complete reviewability a plan-creation invariant. Every governed
configuration operation now persists a deterministic versioned before/after
semantic projection before an external approval challenge can exist.
Unsupported, non-serializable, incomplete, or genuinely oversized projections
return one stable refusal and create no approval or F3 task.

The projection preserves every non-sensitive changed value, including long and
deeply nested automation, script, template, and helper content. Protected
fields use category-based redaction rather than arbitrary clipping. The private
Ingress page renders all one-to-eight ordered operations from the persisted
record, keeps long values fully inspectable in escaped expandable sections,
and never re-queries Home Assistant merely to reconstruct approval meaning.

The canonical projection digest is included in F2 policy authority and the
exact plan hash. The unchanged F3 prepared-operation authority includes that
plan hash, binding the displayed review transitively to what can execute.
Tampered identity, order, before/after values, schema, completeness, prepared
configuration, or projection digest fails closed before provider dispatch.

Pre-Beta-22 plans without a complete bound projection remain readable for
audit but cannot be approved, upgraded from mutable current state, or
redispatched. A new inspected plan is required.

Beta 22 preserves the Beta 20 F3 runtime and Beta 21 exact `ha-mcp` 8.1.0
admission, 24 delegated reads, two held tools, 48 Engineering-local tools,
zero fallback, protocol `2025-03-26`, stable v1.1.2, and the exact dependency
pins. It does not add a public tool, broaden execution, deploy, restart,
publish, or run a production canary.
