# Engineering 2.4.2-beta.3 release notes

Engineering 2.4.2-beta.3 (HAMCP-311) adds `control_helper`. It makes a helper
value change ordinary runtime control, like `control_power` and `control_fan`,
with no plan and no approval panel. It builds on 2.4.2-beta.2 (HAMCP-306,
#361).

Acceptance: [`V2_4_2_BETA3_ACCEPTANCE.md`](V2_4_2_BETA3_ACCEPTANCE.md).
Design: [`TYPED_HELPER_CONTROL.md`](TYPED_HELPER_CONTROL.md).
Per-release records: [release index](RELEASES.md).

## The change

**Authority.** Josh decided on 2026-11-04, in the architect chat, relayed in
the HAMCP-311 contract:
- helper value changes are treated like switches, with no plan and no
  approval panel;
- the chat warns before a safety-critical change;
- the result shows known automation consumers as information;
- the scope is all helper value types.

**The tool.** `control_helper(entity_id, action, operation_id, value?,
acknowledge_safety_critical=false)` changes one exact helper. The allowed
actions are below; any other domain, action or value refuses before
dispatch.

| Helper | Actions |
| --- | --- |
| `input_boolean` | `turn_on`, `turn_off` |
| `input_select` | `select_option` (a live option) |
| `input_number` | `set_value` (within the live min, max and step) |
| `input_text` | `set_value` (within the live length and pattern) |
| `input_datetime` | `set_datetime` (fields per `has_date` and `has_time`) |
| `input_button` | `press` |
| `counter` | `increment`, `decrement`, `reset`, `set_value` |
| `timer` | `start` (optional duration), `pause`, `cancel`, `finish` |

**What it does:**
- **Validates** the request against the helper's live state and attributes.
- **No-op.** A value that is already current returns `no_change` with no
  dispatch. This does not apply to press, increment, decrement or timer
  actions.
- **Dispatch.** It dispatches at most once.
- **Readback.** It reads back independently and returns one of:
  - `succeeded_verified`;
  - `changed_unverified`;
  - `verification_failed`;
  - `no_change`;
  - `refused`;
  - `provider_unavailable`.

  Every call writes an audit entry. The design document says what each
  button, counter and timer readback proves.

**Safety warning.** The tool reads the existing helper dependency evidence;
it adds no new analysis.
- **Warns.** It returns `confirmation_required` if the exact evidence
  classifies the change as `safety_critical`. Nothing is sent, and the
  result lists the exact consequential automations.
- **With the acknowledgement**, the call proceeds. The audit records the
  acknowledgement as a model-conveyed chat confirmation, not as external
  approval.
- **Never warns.** Opaque or template readers are listed as information,
  for example "may read via templates: N, including …".
- **Evidence unavailable.** The call proceeds and says so.

**Results.** Each result lists:
- the known exact consumers, up to 20;
- the count of opaque readers;
- a short consequence line.

**Existing plans.**
- `create_helper_state_plan` and `create_input_select_option_plan` are
  unchanged and keep working. Their descriptions now say they are
  superseded for value changes.
- Helper configuration plans are unchanged.

**Provider.**
- The tool uses one reviewed internal ha-mcp provider: 8.4.3, 8.5.0 or
  8.6.0, through `ha_get_state`, `ha_list_services` and `ha_call_service`.
- It has no fallback, no Core-direct path and no generic service input.

**Core authority.** Core authority is signed-only, through a new compiled
profile, `core.typed_helper_value_operation`.

Static tools go from 80 to 81. The advertised catalog goes from 108 to 109,
or from 109 to 110 with the optional YAML reader.

## Unchanged by design

- Helper configuration plans, the governed plan and approval path, and every
  other control tool.
- Persisted plan, task and audit formats. The new tool writes the existing
  ordinary task format under its own namespace.
- Provider admission for every existing family, contract fingerprints of
  existing tools, and add-on options.

## Known limitations

- **Dormant until signed authority exists.**
  - Until an owner-signed Core registry sequence names
    `core.typed_helper_value_operation`, every call returns
    `provider_unavailable` without a provider call.
  - Sequence 16 (HAMCP-280) does not name it.
  - Publishing such a sequence is a separate owner decision.
- **Coverage.** Dependency coverage can be incomplete. A missing warning is
  not proof that a change is safe.
- **Per-call safety notes.** Safety notes are not persisted. Reconciling an
  existing task reports that the gate was not re-evaluated.
- **Password text.** Password-mode `input_text` values are redacted in
  results but stored in the local task declaration.
- **Pattern matching.** Patterns are matched with Python `re` and bounded
  at 255 characters. A pathological pattern is a residual cost risk.
- **`in_progress`.** A non-terminal task with no dispatch intent yet reports
  `in_progress`.
- **The 8.6.0 WebSocket path.** On ha-mcp 8.6.0, `ha_call_service` uses
  WebSocket `call_service` for non-admin tokens. The call stays exact and
  dispatches once.

## Rollback

Reinstall 2.4.2-beta.2. It has the same storage, plan and task formats.
`control_helper` disappears from the catalog. Any helper task it wrote stays
on disk and is not read by 2.4.2-beta.2. An image rollback does not restore
household configuration or physical state.
