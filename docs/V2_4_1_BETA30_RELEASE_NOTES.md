# Engineering 2.4.1-beta.30 release notes

Engineering 2.4.1-beta.30 adds two read-only tools (HAMCP-131):
- `get_incident_logs` reads one bounded incident window from the Home
  Assistant Core journal and Engineering's own add-on journal. It merges them
  in time order and reports per-source coverage. Sources it cannot read are
  listed with the exact reason.
- `get_history_slices` reads entity history continuously across pages,
  through the sanitized delegated `ha_get_history` route. It uses fixed time
  slices and a cursor that fails closed when earlier history changes.

Neither tool can write, reload, restart or call a service. Neither adds a
permission or changes add-on configuration.

Sources report, authority table and design:
[`HAMCP_131_INCIDENT_LOGS.md`](HAMCP_131_INCIDENT_LOGS.md).
Acceptance: [`V2_4_1_BETA30_ACCEPTANCE.md`](V2_4_1_BETA30_ACCEPTANCE.md).

beta.30 follows 2.4.1-beta.29 (HAMCP-213 PR 1) and includes it. It adds no
dependency change; both hashed locks are beta.29's.

The catalog grows from 90 to **92** tools (93 with the optional YAML reader
admitted): 64 static plus 28 delegated. Nothing else changes: no existing
tool, input schema, description, signed registry entry, HOLD, support window,
workflow, add-on permission or stable-v1 file.

## `get_incident_logs`

Inputs:
- `sources`: 1-4 of `core`, `supervisor`, `host`, `addon:<slug>`;
- `start`, and optionally `end`: half-open `[start, end)`, **at most
  6 hours**;
- optional `min_level`;
- optional literal `text` filter, at most 128 characters;
- `limit`: 1-200, default 100;
- `cursor`.

| Source | Supervisor route | Status |
| --- | --- | --- |
| `core` | `/core/logs` | read |
| `addon:self` | `/addons/self/logs` | read (Engineering's own journal) |
| `supervisor`, `host`, `addon:<other>` | needs role `manager` | `unavailable`, `authority_not_granted`, 0 requests |

Engineering runs as `hassio_role: homeassistant`, and this release adds no
role. The authority table, with citations from Supervisor 2026.09.1, is in
the design notes.

Output:
- `entries`: timestamp, source, process, level and sanitized message.
- `sources[]`, one row per source: status, reason, HTTP status, earliest and
  latest retained entry, retention edge, scanned range, consumed, returned,
  filter-skipped and outside-window counts, and gaps with reasons.
- `omissions`, `truncated`, `has_more` and a signed `next_cursor`.
- the disclosed bounds and pins.

A vacuum or rotation between pages is detected and refused as stale.
Nothing is skipped or repeated silently.

## `get_history_slices`

Inputs:
- `entity_id`;
- `start`, and optionally `end`: **at most 7 days**;
- `limit`: 1-100, default 50;
- `cursor`.

The window is read in fixed 6-hour slices. Each slice is one absolute-bounded,
ascending, minimal `ha_get_history` request, with a reviewed version of 8.5.0
or 8.6.0. Each call makes at most 12 upstream calls.

Each page reports:
- `state_at_window_start`, the states, and per-slice coverage;
- `coverage.complete_until`;
- the provider version, with fallback `none`.

The cursor binds the query, the fixed end and the provider contract. It is
refused as stale in these cases:
- a purge;
- a late recorder write;
- a changed slice-start state;
- a different provider contract.

## Security

- Each tool sanitizes in exactly one place: the strict shared sanitizer
  runs at its single exit, with the known secrets. A redaction makes the
  result partial.
- Every journal request uses a HAMCP-200 typed route.
- An add-on slug must be a validated single segment. A traversal attempt is
  refused with zero requests.
- History goes only through the admitted gateway route. It never uses the
  native `get_history`, and there is no direct fallback.

## Rollback

Reinstall the 2.4.1-beta.29 artifact. No stored format changes. Cursors are
process-keyed and do not survive a restart.
