# Engineering 2.4.1-beta.38 release notes

Engineering 2.4.1-beta.38 builds the November Core batch **inactive**
(HAMCP-232, HAMCP-145 and the owner-added script-trace read). It follows
2.4.1-beta.37 and includes it. It **activates nothing new**: every new tool
refuses `pending_signed_registry_admission` before any Home Assistant request,
read or persistence until a signed Core registry sequence names its profile.
No signed registry, signed profile, fingerprint or existing input schema
changes.

Acceptance: [`V2_4_1_BETA38_ACCEPTANCE.md`](V2_4_1_BETA38_ACCEPTANCE.md).
Design: [`HAMCP_232_145_HELPER_OPTIONS_AND_SELECT_DESIGN.md`](HAMCP_232_145_HELPER_OPTIONS_AND_SELECT_DESIGN.md)
(owner decisions in section 0, the script-trace profile in section 4b, the
Part 2 build in section 6).

## What a client sees

Four new tools appear in the catalog (static tools 68 → 72; advertised catalog
100, 101 with the optional YAML reader admitted). Each is listed with its
requirement and refuses until signed admission:

| Tool | Profile (runtime-only) | Kind |
|---|---|---|
| `create_helper_options_plan` | `core.config_entry_helper_options_operation` | governed write plan |
| `create_input_select_option_plan` | `core.typed_input_select_option_operation` | governed write plan |
| `list_script_traces` | `core.script_trace_read` | read |
| `get_script_trace` | `core.script_trace_read` | read |

`create_helper_state_plan`'s schema and the input_boolean path are
byte-identical.

## What is built (inactive)

- **Helper options (HAMCP-232, OD-1 to OD-8).** Edit existing UI-created
  template (sensor, binary_sensor), filter and statistics helpers through their
  config-entry options flow. One shared profile with a closed domain list;
  utility_meter is deferred (OD-2). 1–8 entries per plan, one helper domain,
  non-atomic, stop on first failure. The baseline and readback open and abort
  the options flow only for the plan's exact entries, only inside this
  profile; there is no general options read (OD-1, narrowing HAMCP-132
  decision (i) for this use only). Template types with ActionSelector fields
  are refused; `read_only` fields are pinned to the baseline (OD-3). Injected
  defaults are disclosed as separate `normalization_changes` (OD-8). The
  residual concurrency window between the dispatch guard and the submit is
  accepted and disclosed in the plan and in the apply response (OD-7). Rollback is a
  separately approved plan (OD-6).
- **input_select option (HAMCP-145, OD-9 to OD-12).** A governed plan through
  the new `create_input_select_option_plan` tool. Single
  `input_select.select_option` call for one exact entity and an option in its
  current list; the domain-parameterized dependency reader discloses per-option
  triggers. Drift (state or options list) is refused at dispatch with
  `stale_target_state`. A Core error after the send is decided by readback,
  never assumed to be a failure. Rollback is a separately approved plan.
- **Script-trace reads (owner addition).** `trace/list` and `trace/get` with
  `domain: script` and an exact validated script key, sanitized through the
  shared untrusted-data sanitizer before output, like the automation trace
  reads. `trace/contexts`, `trace/debug/*` and the list-all form are never
  sent. Not-found is reported as not-found, without fallback.

Every write path uses the built-in lessons (design section 6.1): admission
first at the service layer and again at the persist boundary; dispatch
admission on every F3 path (first apply, retained approved plan, resume,
recovery, read-only reconciliation); F3 entry refusal before initialization;
pre-dispatch reads inside their own Core-authority window; closed command
vocabularies with explicit never-sent lists; `mark_provider_send()` at the
exact send boundary.

## Evidence lanes and canaries

- Dormant sequence-15 lanes (`scripts/seq15_contract_acceptance.py`) for
  helper options, input_select and script traces, wired in `ci.yml` behind
  `REAL_HA_SEQ15_LANES: dormant` and the candidate support slot (OD-14).
  No existing step changed. They have not run.
- Post-signing canaries (design section 6.6) are defined, not run.

## Compatibility

- No F3 persisted-format change for any existing family. New enum values
  (`set_input_select_option`, the `config_entry_helper_options` resource type)
  can be written only after signed admission, so a beta.38 install under the
  current signed registry (sequence 14) writes no new record shape.
- Rollback is reinstalling 2.4.1-beta.37.
- Stable v1.1.2 is unchanged.
