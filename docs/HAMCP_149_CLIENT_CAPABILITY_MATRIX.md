# HAMCP-149 approval completion: client capability matrix

Status: Layer A (offline, synthetic) rows are recorded by
[`tests/test_hamcp149_approval_completion_fixture.py`](../tests/test_hamcp149_approval_completion_fixture.py).
Layer B (real-client observation) rows are **unobserved — pending Josh (Q1)**.
This file records observation only. A row is never a trust decision, an
advertised capability, or execution authority.

## Implementation default — pending Josh (Q2)

Signal **S0**: no runtime change. The completion check is the existing
persisted-only `get_change_plan` read. The server adds no completion field, no
server-to-client notification, no push, and no persistence. A recognized
completion only prompts the requesting conversation to call `apply_change_plan`
with the exact plan id and plan hash it already holds. Apply re-checks approval
validity, stale state, and at-most-once dispatch on its own.

Binding default — pending Josh (Q3): the plan id and plan hash held by the
requesting conversation. The stored `requested_by` is a hash of the relay peer
address (`sha256(client_ip)[:12]`), not a client or conversation identity. It
is not projected by `get_change_plan` and is not enforced at apply.

## Reference completion check (test-local recipe)

Read `get_change_plan(plan_id)` and use `data.canonical_summary`
(`change-plan-observability-v1`). Fields read: `status`, `plan_hash`,
`approval_state`, `approval_lifecycle`, `approval_bundle_state`,
`approval_actionable`, `apply_allowed`, `next_required_operation`.

| Summary | Outcome | Stop checking | Call apply |
| --- | --- | --- | --- |
| Different `plan_id` or `plan_hash` | `binding_mismatch` | yes | no |
| Unknown model, lifecycle, or non-boolean `apply_allowed` | `unknown` | yes | no |
| `approval_not_requested` | `approval_not_requested` | yes | no |
| `approval_pending_external` | `awaiting_decision` | no | no |
| `pending_elevated_risk_acknowledgement` or `pending_safety_critical_acknowledgement` | `awaiting_second_action` | no | no |
| `approved` and `apply_allowed` and status `approved` and bundle `fully_approved` | `approved_apply_ready` | yes | yes, exact hash |
| `apply_allowed` true with any other combination | `unknown` | yes | no |
| `approval_rejected` | `rejected` | yes | no |
| `approval_expired` (challenge) | `expired` | yes | no |
| `approval_invalidated` with status `expired` | `plan_expired` | yes | no |
| `approval_invalidated` with status `superseded` | `replan_required` | yes | no |
| `approval_consumed` | `consumed` | yes | no |
| `prohibited` | `prohibited` | yes | no |
| Read error (for example `change_plan_storage_error`) | `read_failed` | yes | no |

Active-turn budget: at most 6 re-reads, 10 seconds apart, inside one active
turn. That is well under the add-on default inbound limits (burst 25, 120 per
minute). When the budget runs out the turn ends without completion; a later
turn starts again with a fresh read.

Recorded characterizations:

- A challenge expiry is projected on read without persistence; it is persisted
  only when another path touches the plan. While expired,
  `next_required_operation` is empty, yet a new `approve_change_plan` for the
  same hash is accepted. Recovery is a new owner decision, not a continuation.
- The read is persisted-only, so it cannot see Home Assistant drift. Stale
  state after approval is refused by apply, not by the completion check.
- Apply authority binds the plan id and hash, not the requesting caller (Q3).

## Transport rows

| Row | Layer | Protocol version | Session id | Server capability for resume (`resources.subscribe`, `tasks`) | `clientInfo` visible to tools | Unrelated notification delivered | Mid-call client close |
| --- | --- | --- | --- | --- | --- | --- | --- |
| In-process Streamable HTTP, stateless, pinned SDK client | A (synthetic) | `2025-11-25` (mcp 1.30.0 latest) | none | none | no | no (dropped, no GET stream) | plan unchanged; re-read reports true state |
| In-process Streamable HTTP, stateful, pinned SDK client | A (synthetic) | `2025-11-25` (mcp 1.30.0 latest) | issued | none | yes (observation only; not stored or bound) | not asserted | not run |
| In-process Streamable HTTP, stateful, raw HTTP without GET stream | A (synthetic) | `2025-11-25` (mcp 1.30.0 latest) | issued | none | not applicable | no (response carries only the call result) | not run |
| Claude.ai via Nabu Casa relay | B | unobserved — pending Josh (Q1) | unobserved | unobserved | unobserved | unobserved | unobserved |
| Claude Code (remote MCP) | B | unobserved — pending Josh (Q1) | unobserved | unobserved | unobserved | unobserved | unobserved |
| Claude mobile app | B | unobserved — pending Josh (Q1) | unobserved | unobserved | unobserved | unobserved | unobserved |
| Real socket (uvicorn) Streamable HTTP, any mode | A (synthetic) | not exercised in CI | not exercised in CI | not exercised in CI | not exercised in CI | not exercised in CI | not exercised in CI |

Layer A rows run the real gateway and FastMCP Streamable HTTP app in the test
process through `httpx.ASGITransport`, with the app lifespan entered directly.
They use no socket, no uvicorn server and no signal handler. The mid-call row
closes the client session and cancels its in-flight request; a TCP-level
disconnect is not exercised in CI. A uvicorn peer was removed because its
shutdown latched the process-global `sse_starlette` exit flag and ended later
SSE responses in the same test process. The rows now assert that this flag and
the SIGTERM handler are unchanged after each test.

Layer B rows need manual observation with a real client against a disposable
or owner-approved deployment. That is outside HAMCP-149 PR-0 and needs Josh's
direction before it is run.
