# HA MCP Engineering 2.2.0-beta.28 governed dashboard-update release notes

Beta 28 adds one bounded governed write capability for an existing Home
Assistant storage-mode dashboard. `create_dashboard_update_plan` accepts an
exact canonical dashboard `url_path` and at most 16 ordered JSON Pointer
`add`, `replace`, or `remove` operations. Planning performs a complete exact
preread, compiles the patch locally, builds a bounded approval projection, and
performs no Home Assistant mutation.

An external Home Assistant administrator must approve the exact plan before
the existing `apply_change_plan` path can execute it. F3 then acquires the
dashboard and provider-dependency locks, repeats exact release, target,
storage-mode, and configuration-hash preflight, writes durable dispatch intent,
calls `ha_config_set_dashboard` at most once, rereads the complete dashboard,
and requires exact full-result verification.

The upstream hash check and save are separate operations. Beta 28 therefore
labels the operation `operator_accepted_non_atomic` and requires the operator
not to edit the target dashboard in Home Assistant or another client while the
approved task executes. Readback detects a conflicting final result but cannot
prove that no external edit was overwritten inside the provider read/save
window. Beta 28 does not claim atomicity.

The write gateway is admitted only against exact reviewed `ha-mcp` 8.1.1 and
its complete reviewed catalog. The upstream setter and best-practices reader
are not exposed as normal delegated tools. There is no fallback, direct Home
Assistant write, arbitrary forwarding, generated or caller Python, screenshot,
metadata, resource, create, delete, retry-after-intent, or rollback path.
Recovery is exact readback only.

Approval review retains bounded sanitized before/after previews for declared
changed paths, while complete current/result configurations and setter payloads
remain private integrity-bound artifacts. Audit and health output retain only
bounded identifiers, hashes, provider identity, dispatch state, and normalized
outcomes; best-practices receipt material and unrestricted payloads are never
persisted.

The local Engineering catalog grows from 49 to 50 tools because the new tool is
proposal-only. The 25 canonical tools, 25 exact 8.1.1 delegated reads, held
`ha_get_operation_status`, ordinary dashboard reads, provider routing, approval
authority, other F3 semantics, zero-fallback guarantee, and stable v1.1.2
remain unchanged.

The published Engineering version remains `2.2.0-beta.27` until a separately
authorized promotion consumes `.release/next-version`. This draft stages
`2.2.0-beta.28`; it does not publish, tag, deploy, or mutate a live Home
Assistant environment.
