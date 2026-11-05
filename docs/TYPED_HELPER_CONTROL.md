# Ordinary helper value control

`control_helper` changes one exact helper's value through the public Engineering
connector and its reviewed internal ha-mcp provider (HAMCP-311). Helper value
changes are ordinary runtime control, like a switch: there is no plan, no
notification and no panel approval.

**Authority.** Josh decided on 2026-11-04 (architect chat, relayed in the
HAMCP-311 contract) that:
- helper value changes are treated like switches, with no plan and no approval
  panel;
- the chat warns before a safety-critical change;
- the result shows known automation consumers as information;
- the scope is all helper value types.

Helper CONFIGURATION (options, ranges, names, creation and deletion) still uses
the governed configuration plans. `create_helper_state_plan` and
`create_input_select_option_plan` remain available and unchanged. Their
descriptions now say that `control_helper` supersedes them for value changes.

## Request

`control_helper(entity_id, action, operation_id, value=None,
acknowledge_safety_critical=false)`. The request is closed: extra keys,
`data`, `service`, `domain`, `target`, area or device selectors, lists and
wildcards are refused by the schema before any Home Assistant call.
`entity_id` must be one exact entity in a helper domain.

| Domain | Action | `value` | Service and fixed data keys |
|---|---|---|---|
| input_boolean | turn_on, turn_off | none | `input_boolean.<action>`, `{}` |
| input_select | select_option | a live option, exact case | `select_option`, `{option}` |
| input_number | set_value | a number within live min/max, on step | `set_value`, `{value}` |
| input_text | set_value | text within live min/max length and pattern | `set_value`, `{value}` |
| input_datetime | set_datetime | `YYYY-MM-DD`, `HH:MM:SS` or both, per has_date/has_time | `set_datetime`, `{date?, time?}` |
| input_button | press | none | `press`, `{}` |
| counter | increment, decrement, reset | none | `<action>`, `{}` |
| counter | set_value | an integer within live minimum/maximum, a multiple of step | `set_value`, `{value}` |
| timer | start | optional `H:MM:SS` duration | `start`, `{duration?}` |
| timer | pause, cancel, finish | none | `<action>`, `{}` |

Any other domain, action, value shape or key refuses. No toggle, reload,
configuration, script, automation or other domain service is reachable. The
operation ID works as for `control_power`:
- it is current UTC epoch seconds, a hyphen and 32 lowercase hex digits, and
  expires after five minutes;
- the same ID and arguments only reconcile; changed arguments refuse;
- after a timeout, use `get_execution_task`. Never create a fresh ID to retry
  uncertain work.

## Validation against live state

Before any durable declaration, Engineering reads the exact entity's state, its
revision (`last_updated`) and only the attributes the action needs. It refuses
an `unavailable` or missing state, an `unknown` state where the domain has no
such value (`input_boolean`, `counter`, `timer`), and missing or malformed
attributes. An empty state is valid only where the domain and the live
configuration allow it:
- an `input_text` whose live `min`, `max` and `pattern` accept the empty
  string;
- an `input_select` whose live `options` include the empty string.

Every other empty state refuses (`helper_target_state_unavailable`). The same
rule applies to the baseline, the no-op check and the readback, so clearing a
text helper verifies and an already-empty value is `no_change`.

It also refuses any value that Home Assistant would reject, or would silently
ignore or clamp:

- **input_select:** the value must be one of the live `options`, including
  an empty option.
- **input_number:** `min <= value <= max`, and `(value - min)` is a multiple of
  `step`.
  - The check uses exact decimal arithmetic on each number's shortest
    round-trip form. It accepts an error of at most 1e-9 of one step, at any
    magnitude, to absorb binary noise such as `0.1 * 3`.
  - A half step refuses however large the value is: with step 1,
    `1000000000.5` refuses (`helper_value_not_on_step`).
- **input_text:** `min <= len(value) <= max`, and the full value matches the
  live `pattern`. An empty value is checked the same way.
  - Home Assistant does not enforce `pattern`, and it silently ignores an
    out-of-range length, so Engineering checks both.
  - The pattern runs with Python `re`. A pattern over 255 characters, or one
    that does not compile, refuses (`helper_pattern_unsupported`).
- **input_datetime:** the value has exactly the fields that `has_date` and
  `has_time` allow, with a valid calendar date and time.
- **counter set_value:** within the configured minimum and maximum, and a
  multiple of `step`.
- **counter increment and decrement:** refuse at the bound.
  - Home Assistant clamps these silently, so no transition could be verified
    (`helper_counter_at_limit`).
- **timer:** pause needs an active timer. Cancel and finish need an active or
  paused timer. Home Assistant silently ignores the others.
- **timer start duration:** must be positive. It is canonicalized to `H:MM:SS`.

Service discovery must list the exact service. If it does not, the result is
`provider_unavailable` (`helper_service_unavailable`).

## No-op

If a value action (`turn_on`/`turn_off`, `select_option`, `set_value`,
`set_datetime`, counter `reset`) is already satisfied, it returns `no_change`.
There is no dispatch, no dispatch intent and no safety evaluation. Press,
counter increment/decrement and all timer actions are transitions and always
dispatch.

## Dispatch and verification

There is at most one `ha_call_service` dispatch, with `wait=false`,
`return_response=false`, `verbose=false` and the fixed data above. The shared
F3 ordinary lifecycle (the same as fan and power) records durable intent before
dispatch, never replays an uncertain dispatch, and verifies with an independent
authoritative state read.

| Action | What the readback proves (`verification_basis`) |
|---|---|
| value actions | `exact_state_value`: the state equals the requested value |
| input_button press | `last_pressed_timestamp_changed`: the state differs from the pre-dispatch press timestamp |
| counter increment/decrement | `counter_value_equals_baseline_plus_or_minus_step` |
| counter reset | `counter_value_equals_initial` |
| timer start | `timer_active_with_new_finishes_at` (and `duration`, if given) |
| timer pause | `timer_paused` |
| timer cancel | `timer_idle_last_transition_cancelled` |
| timer finish | `timer_idle_last_transition_finished` |

Verification proves Home Assistant's reported helper state. It does not prove
what automations did in response. Home Assistant has no compare-and-set: an
outside actor can still change the helper after the final preread.

## Statuses

Each result has a `status`, derived only from durable facts:

| Status | Meaning |
|---|---|
| `succeeded_verified` | dispatched once; the readback proves the transition |
| `no_change` | already satisfied; nothing dispatched |
| `changed_unverified` | dispatched or possibly dispatched; verification is still pending, or could not decide |
| `verification_failed` | dispatched, and the terminal readback did not prove the transition |
| `in_progress` | declared, with no dispatch intent recorded yet |
| `confirmation_required` | the safety-critical warning; nothing was saved or sent |
| `refused` | a request, validation or authorization refusal; nothing was sent |
| `provider_unavailable` | the provider, the service or Core authority was unavailable; nothing was sent, or the dispatch failure was confirmed |

`in_progress` is an addition to the six statuses the contract listed. A
repeated request can observe it while another owner is preparing the dispatch.
Every result also carries an audit entry.

## Safety-critical warning

The warning reads the shipped helper dependency evidence. It adds no new
analysis:
- input_boolean uses the target-scoped v14 binding;
- input_select uses its v2 binding;
- other helper domains use the base target-scoped helper binding.

It warns only on EXACT evidence. Both of these must hold:
- the binding's `physical_consequence` is `safety_critical`;
- at least one consumer whose evidence class is `referenced` (it names the
  helper exactly) carries `safety_critical`.

A binding with zero exact dependency obligations never warns.

- **Without the acknowledgement**, a warning returns `confirmation_required`.
  - It lists the exact consequential automations (IDs and names, at most 20).
  - It reports `dispatch: no_dispatch` and `task_created: false`.
  - Nothing is saved, so the same operation ID can be repeated with the
    acknowledgement.
- **With `acknowledge_safety_critical=true`**, the change proceeds. The audit
  records `safety_gate: acknowledged`,
  `safety_confirmation: model_conveyed_chat_confirmation` and
  `external_approval: false`. The user's confirmation reached Engineering only
  through the model. It is not external approval, and the tool tells the model
  never to set it pre-emptively.
- **Opaque or unresolved template readers never warn**, whatever their own
  action's consequence. They are listed as information: "may read via
  templates: N, including …" (up to three names).
- **Evidence unavailable.** If the index is unavailable, the reader raises, or
  the evidence is incomplete, the change proceeds with
  `safety.evidence: unavailable` and a note. The audit records
  `safety_gate: evidence_unavailable`. No private failure text is returned.

Every non-warning result includes `safety`:
- the known exact consumers (bounded) with their count;
- the template reader count and line;
- a short consequence line;
- `coverage_complete`.

Coverage is incomplete by design, and the result says so.

**Safety notes are per call.** They are not persisted with the task. A
repeated request that reconciles an existing task reports
`safety: {evaluated: false, reason: reconciliation_of_existing_task}`.

## Closed provider authority

- **Provider.** `upstream_typed_helper_value` validates the three tools it
  names (`ha_list_services`, `ha_get_state` and `ha_call_service`) against the
  reviewed ha-mcp 8.4.3, 8.5.0 and 8.6.0 contracts. Other advertised tools grant
  nothing.
- **Provider authority.** Its authority token is distinct from the power and
  fan tokens and respects retained deny-only decisions.
- **Contracts.** Each receipt names
  `ha-mcp-<version>-single-helper-value-v1`.
- **No fallback.** Engineering never calls Core directly and never falls back
  to another provider.

**Core authority is signed-only.** The family requires a new compiled profile,
`core.typed_helper_value_operation`, plus the existing signed state-read,
service-discovery and F3-verification capabilities.
- There is no legacy compiled Core version path.
- The existing `core.typed_helper_operation` profile belongs to the governed
  helper plan adapter. It is not reused, because reusing it would reinterpret
  signed semantics.
- **The tool ships dormant.** It returns `provider_unavailable`
  (`helper_core_authority_unavailable`) without any provider call until an
  owner-signed Core registry sequence names the new profile.
- Signing that sequence is a separate owner decision.

ha-mcp `ha_call_service` forwards `data` unchanged and has no domain allowlist.
That is why the request is closed. With `wait=false` it has no component
fallback. In 8.6.0, non-admin tokens use the WebSocket `call_service` path.

**Source applicability** was inspected at these immutable revisions:

- Home Assistant Core 2026.10.3, `1f214b8461dd1fbbd66e10765c6905f49d3c1d86`,
  `homeassistant/components/<domain>/__init__.py` SHA256:

  | Domain | SHA256 |
  |---|---|
  | input_boolean | `37a53911f8079c87d7c0efe30591b31fdebd9b39db5fc57ca3c0ecd597aeda2e` |
  | input_select | `7ddb97c52ea39ccbdb57c6dcdba6f91e3749c3ef74618c01aa58e087202522a7` |
  | input_number | `125533042f60e6e3a58d31f971b85d3ba4df42a7c47f5eccc4a9f26ed52927d2` |
  | input_text | `268709f6805f8cdfbe6d46f38dd220b200cf4dceb9c86f98e005db8c2b86fbb9` |
  | input_datetime | `ca929454dd9a58cba05a0aafa1fcedeea182d89ee31e068f8866afdda6f4f065` |
  | input_button | `8a9b9921f44362ad6e3b317bf8b7b216e177ee286648879c69394b4067c47241` |
  | counter | `b9338ffb84b44e36bd7b01b2e8e5249a82a0aae2797ee3296dc73e3b3c7d3e22` |
  | timer | `fcf6bba1b5331a0ff47ed8a75c2e4de8012d55b3d3b407f2aadd91c4ef376947` |

- ha-mcp `src/ha_mcp/tools/tools_service.py` SHA256:
  - 8.4.3 `eac7a3aa7063432e9af17e7d7726040e909c7b8f`:
    `3a8e95bc99287c999f71c3debc1dd1e5694b654de7c660db2c225a35c4efbe73`;
  - 8.5.0 `311d6dc273fb4e9a5b8cde0de15f69472a64fe44`:
    `4cc47219bea7e19c707ca529f98f75e202a24d6638ae656f17bfccad13906d57`;
  - 8.6.0 `fc54437a804858732e4bc927add98e202d879a09`:
    `2974c5d4ca3177a96e170182ae65e6d63d20bc952421bb671ff3d98eb078612a`.

Source inspection is distinct from exact-image execution and installed
acceptance.

## Ownership, audit and recovery

Declarations are stored under `<governance>/ordinary-helper-value-v1/`. They use
the shared F3 lock, execution repository, recovery and receipt model from
`control_fan` and `control_power`, and `get_execution_task` reads them.
- **Audit.** Rows use `typed_helper_value_lifecycle`.
- **Request summaries.** They add `helper_value_outcome` and `safety_gate`.
- **Health.** It reports `ordinary_helper_value`.

## Disclosures

- **Password mode.** An input_text in `password` mode has its value redacted
  (`redacted_password_mode`) in results and the confirmation payload. It is
  not compared in the result's state comparison. The plaintext value remains
  in the local declaration file under the governance directory, as every
  request field does.
- **Pattern matching.** input_text patterns are owner-configured and run
  with Python `re` against values of at most 255 characters. A pathological
  pattern could still be slow. The residual risk is bounded by the length
  limits and by owner control of the pattern.
- **Coverage.** Consumer coverage is incomplete. The warning is decision
  support for an exact, owner-authorized operation, not a technical veto.
