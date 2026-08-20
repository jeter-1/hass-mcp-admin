# Dashboard patch planner and approval projection correction

Status: local corrective workstream for independent review (2026-08-14)

This change repairs the bounded existing-storage-dashboard update path. It does
not add dashboard creation, deletion, registry metadata, title/icon/sidebar
administration, or another write authority. The existing external approval,
stale-state check, single dispatch, authoritative reread, exact verification,
duplicate-apply protection, and zero-fallback lifecycle remain authoritative.

## RFC 6902 array additions

The compiler continues to accept canonical RFC 6901 paths and the existing
`add`, `replace`, and `remove` operation set. For `add` only:

- a final `-` token is array append;
- numeric indices from `0` through the current array length are accepted;
- an index below the current length inserts before that element and shifts the
  existing suffix; and
- an index equal to the length appends.

The compiler rejects negative, nonnumeric, and leading-zero array indices;
indices greater than the current length; intermediate `-`; `-` on `replace` or
`remove`; and an array token resolved against a non-array parent. Object `add`
retains its stricter existing rule: the member must be absent. Duplicate paths,
parent/child conflicts, caller order, pointer escaping, and deep-copy behavior
are unchanged.

## Bounds and diagnostics

The recursive semantic-leaf ceiling is 256. It is solely a deterministic
compiler complexity bound and does not establish human reviewability.

The material limits remain unchanged:

- 16 patch operations;
- 16,384 canonical patch bytes;
- 8,192 bytes per declared value;
- 16,384 bytes of result growth; and
- 40,000 bytes for the resulting dashboard.

Compiler and planning failures retain the compatible public governance error
while adding bounded diagnostic fields: reason, constraint, observed count,
limit, stage, and the typed dashboard error category. Diagnostics contain no
dashboard values, arbitrary provider content, credentials, or tokens.

The reproduced whole-view test serializes one replacement value to 9,537
bytes. It remains rejected by the 8,192-byte individual-value limit. No
material bound was raised for arbitrary whole-view replacement.

## Complete approval projection

Planning constructs `f3-dashboard-approval-projection-v1` from the compiler's
exact effects. Every declared operation carries its canonical operation ID,
operation kind, bounded path, complete previous target when present, and
complete proposed target when present. Inserting or replacing a card therefore
shows that complete card; removing one shows the complete removed target.

The projection is hash-bound to the exact preread, canonical patch, and
resulting dashboard. It is persisted with the private plan material and
removed from the public MCP plan projection. Before an approval challenge is
created or consumed, governance validates its strict shape, completeness,
digest bindings, protected-data boundary, and 131,072-byte plan-level review
limit. A projection that cannot fit fails during planning; a missing or
tampered projection fails closed. The approval page renders JSON as
HTML-escaped inert text and never executes dashboard strings or templates.

Semantic summaries remain useful risk evidence, but collection previews are
not approval authority and do not replace the complete declared-operation
projection.

## Regression evidence

The small map-title canary continues to compile into a complete review without
dispatch during planning. A deterministic Home-shaped fixture covers the
requested Cleaner insertion, replacement of Handled/Prompted with Outdoor, and
the Needs Attention card conditions. The four-operation patch compiles to 51
semantic leaves, preserves unrelated content, and has deterministic hashes.

The fixture is derived from the bounded requested delta and repository-owned
dashboard structures; it is not a production dashboard capture. The exact
household baseline is intentionally not accessed or committed. Post-deployment
acceptance must begin with a fresh authoritative preread, bind the then-current
baseline, and use a reversible canary rather than assuming this fixture is the
live dashboard.

Lifecycle tests retain these requirements:

- a baseline change after approval fails before setter dispatch;
- a verified task cannot dispatch again on duplicate apply;
- structured rejection and unchanged reread remain truthful;
- 5xx, silence, or lost response never permits blind redispatch; and
- only exact matching readback produces `succeeded_verified`.

## Explicit exclusions

This work does not stage a release, modify stable v1.1.2, change tool or task
schemas, alter provider admission/routing, add fallback or direct Home
Assistant access, implement HAMCP-106, or overlap the Beta 37 helper-state
runtime and release work.
