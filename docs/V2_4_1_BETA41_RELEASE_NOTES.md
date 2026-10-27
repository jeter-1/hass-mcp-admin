# Engineering 2.4.1-beta.41 release notes

Engineering 2.4.1-beta.41 builds the November Core batch **inactive**
(HAMCP-232, HAMCP-145 and the owner-added script-trace read). It follows
2.4.1-beta.40 and includes it. It **activates nothing new**: every new tool
refuses `pending_signed_registry_admission` before any Home Assistant request,
read or persistence until a signed Core registry sequence names its profile.
No signed registry, signed profile, fingerprint or existing input schema
changes.

Acceptance: [`V2_4_1_BETA41_ACCEPTANCE.md`](V2_4_1_BETA41_ACCEPTANCE.md).
Design: [`HAMCP_232_145_HELPER_OPTIONS_AND_SELECT_DESIGN.md`](HAMCP_232_145_HELPER_OPTIONS_AND_SELECT_DESIGN.md)
(owner decisions in section 0, the script-trace profile in section 4b, the
Part 2 build in section 6).

## What a client sees

Four new tools appear in the catalog (static tools 72 → 76 on the beta.39
base; advertised catalog 104, 105 with the optional YAML reader admitted;
each count is one higher once beta.40 merges in). Each is listed with its
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
  `domain: script` and an exact validated script key. Every result, with the
  identifiers it is returned under, leaves through the one HAMCP-225 trace
  exit (`trace_normalization.sanitize_trace_detail`): the shared
  `credentials.screen` on every string and key at every depth (identifiers
  in default mode, values in labelled mode), then the strict sanitizer,
  truncation last. There is no script-trace screen of its own. `trace/contexts`, `trace/debug/*` and the list-all form are never
  sent. Not-found is reported as not-found, without fallback.

### Review 1 corrections

- **H1.** Script traces use the HAMCP-225 trace exit above instead of a
  parallel screen.
- **H2.** No options flow is left open: the held flow is registered at the
  open, its bounded abort completes even when the caller is cancelled, a 404
  counts as gone, and a lost open is disclosed as
  `options_flow_open_unconfirmed`.
- **H3.** The submit must be the complete intended set (baseline plus the
  approved changes), checked at create and again at apply. An incomplete
  intent is refused at create, before any flow opens (owner decision: this
  stricter refusal supersedes one reviewer test, registered as the one
  expected failure in `tests/test_hamcp232_review1_superseded.py`).
- **M1.** A retained, non-terminal task with no child dispatch intent is
  admitted before anything is persisted; the recovery sweep retries an
  unadmitted pre-intent task without persisting, until admission or expiry.
- **M2.** Trigger records carry their automation's enablement: an automation
  that is off, or `enabled: false`, is disclosed as `disabled`, "would not
  fire while disabled"; a non-literal `enabled` is unknown. Enablement is
  part of the binding fingerprint, so enabling or disabling a dependent
  automation between plan and apply refuses the apply as stale.

### Correction 2 (delta review H2-R1)

- **Failed abort.** Every options flow is recorded in a durable
  Engineering-local ledger (`helper-options-flows-v1`) before its open is
  sent, with its flow id before any abort. A failed or unknown abort is
  retried by each recovery sweep, also after a restart, until Core confirms
  the flow gone (aborted, or a 404); after 10 retries it is reported
  orphaned, with its entry and flow id, in server health
  (`helper_options_flows`). An open whose record cannot be written is
  refused before anything is sent.
- **Lost open (owner decision (b), accepted residual).** Pinned Core
  2026.10.1 has no read-only command that lists in-progress options flows,
  so a flow whose open answer was lost cannot be found. It is reported, in
  health and in the task (`options_flow_open_unconfirmed`), as "options flow
  possibly left open on entry X (no flow id received); it holds no
  configuration change and is discarded at the next Home Assistant
  restart". The reviewer's two lost-open probes are registered as expected
  failures in `tests/test_pr314_delta_cleanup_accepted.py`.
- The retry is the existing reviewed DELETE, admitted and in an
  exact-target Core window: no new Core command, write or fallback, and no
  profile fingerprint change.

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
  can be written only after signed admission, so a beta.41 install under the
  current signed registry (sequence 14) writes no new record shape. The new
  options-flow ledger (`helper-options-flows-v1/flows.json` under the plan
  storage) is written only while an options flow is open or unconfirmed, so
  also only after admission, and is removed when empty.
- Server health gains one additive `helper_options_flows` section.
- Rollback is reinstalling 2.4.1-beta.40.
- Stable v1.1.2 is unchanged.
