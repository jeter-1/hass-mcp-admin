# Static automation lifecycle analysis

This bounded first increment adds `data.lifecycle_analysis` to the existing
`automation_reliability_analysis` result. Its arguments, registration, tool count,
provider collection and execution authority are unchanged. It inspects one
captured automation's triggers and inline action sequences. It performs no new
reads, template evaluation, service calls, planning, approvals, restarts or reloads.
Separately stored scripts and blueprint expansion are outside this increment.
This document is a source contract, not release or installed-acceptance evidence.

## Interpretation and eligibility

Every hazard means **if this path is reached and suspension occurs**. It does not
establish a pending run, observed failure, downtime, missed event, long runtime or
nontermination. An automation currently off retains that existing state report;
its configuration is inspected for what could happen if enabled.

| Rule | Classification and evidence |
| --- | --- |
| `trigger_for_interruption` | Hazard: supported state/numeric-state trigger with positive literal `for`. Detaching an affected trigger or restarting Core can lose pending qualification. |
| `suspended_sequence_interruption` | Hazard: positive literal delay, literal-unsatisfied wait template or supported wait-for-trigger with positive/absent timeout. Affected stop/unload/restart can prevent later actions. |
| `repeat_reconciliation_review` | Review note for a conditional repeat. Presence does not establish an infinite or long-running loop. |
| `scheduled_trigger_catchup_review` | Review note for a literal time schedule. No missed occurrence is inferred. |
| `dynamic_suspension_review` | Review note when recognized suspension syntax has unresolved inputs. This is not an eligible hazard. |

Literal disabled actions/triggers suppress their subtrees. Literal zero durations,
zero wait timeouts and already-satisfied literal waits are not suspension hazards.
A zero timeout normally continues; explicit `continue_on_timeout: false` can stop
later actions, but an already-satisfied wait template completes before that check.
Counted finite repeats are not hazards by themselves. Zero-count/empty-for-each
and literal-false while bodies are suppressed; until always permits its first
iteration. Branches and local suffixes are suppressed only where the supported
literal syntax proves they cannot be reached. A failed condition in a nested
sequence does not itself terminate the caller's sequence.

Parallel siblings remain eligible even when another branch contains a proven
stop. After inspecting the siblings, that stop suppresses the enclosing
sequence's unreachable suffix. A branch-local failed condition, disabled stop or
unresolved conditional stop does not establish an enclosing stop. Evidence paths
refer to the captured configuration: mapping-form parallel actions and direct
action shorthand do not acquire synthetic list indices during inspection.

Container stop proofs require absent or supported literal-false
`continue_on_error`. For parallel, sequence, if, choose and repeat, enabled or
unresolved error continuation withdraws a would-be enclosing stop proof and
records `container_error_continuation_unresolved` at that selector's raw path.
Outer findings remain conditional and coverage becomes partial. A prior error
can prevent a stop from being reached; parallel raises the first exception in
branch order, which may mask a later stop and then be ignored by the container.
This slice conservatively makes no exception-order or error-freedom proof, even
when a particular run would still stop. It does not evaluate dynamic selectors;
malformed selectors also remain coverage gaps. Literal disabled containers stay
suppressed, and a direct stop remains terminating: Core does not ignore a raised
stop signal itself under `continue_on_error`.

Only recognized action positions are traversed: sequence, choose/default,
if/then/else, repeat/sequence and parallel. Service data, aliases, variable
payloads and notification text are opaque. Unknown branch conditions retain
conditional neighboring findings and an explicit gap. Literal template values
`true`/`false` (including booleans) and simple conjunctions can establish local
reachability. Jinja, even apparently constant Jinja, is never evaluated. Other
conditions remain explicit gaps; this is not a general control-flow evaluator.
Ambiguous/malformed structures and unsupported trigger families (including
`template` with `for`) cannot yield an unqualified complete negative result.

A literal Home Assistant start trigger is an observation only. It does not prove
reconciliation. Unchanged automations can be preserved during reload; the result
must never say that every reload necessarily interrupts every run.

A useful follow-up may consider a persisted deadline plus explicit startup
reconciliation. The analyzer does not verify helper restoration, timer behavior,
`last_changed`, external recovery logic or a missed-event history. A timer is not
certified durable merely because restoration is enabled. No configuration change
or remediation is performed or authorized by this result.

## Additive output and response decision

The object uses `model_version: lifecycle-static-v1` and scope
`one_automation_triggers_and_inline_actions`. It contains:

- `hazards_detected`, `hazard_count`, `count_precision`, `review_note_count`;
- `assessment`, `coverage_complete`, `truncated`, `limiting_reasons`;
- bounded `findings`, `review_notes`, gap categories/paths and suppression counts;
- startup syntax observation with `recovery_verified: false`;
- source provider `direct_ha_api`, derivation by Engineering, no fallback;
- the existing analysis timestamp and **sanitized** configuration fingerprint,
  plus a deterministic lifecycle evidence fingerprint and `atomic_snapshot: false`;
- processing limits and intentional `display_omission` counts.

Counts measure detected eligible configuration sites, not executions or total
possible household risks. All text is fixed explanatory text; only generated
structural paths identify source locations. No raw configuration or templates
are copied into findings, warnings or audit payloads.

Let L mean any legacy finding, H mean an eligible lifecycle hazard, C mean complete
lifecycle inspection/retention, B mean partial existing source evidence and P mean
another legacy page remains. Notes are neither L nor H.

| Condition after existing error gates | Overall / result status | Lifecycle assessment |
| --- | --- | --- |
| C, no B/P, no L/H | `no_findings` / `success` | `no_detected_hazards_in_scope` |
| C, no B/P, L only | `findings_present` / `success` | `no_detected_hazards_in_scope` |
| C, no B/P, H with or without L | `findings_present` / `success` | `potential_hazards` |
| Not C | `partial_evidence` / `partial` | `partial` |
| C, B or P | `partial_evidence` / `partial` | `potential_hazards` if H, otherwise `no_detected_hazards_in_scope` |

Positive booleans/counts survive gaps and truncation. False with incomplete
coverage means none detected, not none exist. Unsupported/dynamic evidence makes
coverage incomplete without pretending a processing limit was reached. Limits
make counts `lower_bound` and set `truncated`; exact counts otherwise mean exact
*detected sites*, not a complete inventory outside this scope.

**The foundational trace-failure gate is unchanged.** Failed/unavailable traces
without an existing independent legacy finding still return
`ANALYSIS_UNAVAILABLE` or `PROVIDER_TIMEOUT`. Lifecycle inspection occurs after
that gate and cannot bypass it. Successful empty traces use normal response
rules. An independent legacy finding may already permit a partial response; that
response includes lifecycle analysis. Whole-provider failure, missing automation,
timeout, invalid input and cursor errors retain their previous classifications.

Legacy severity counts, root-cause groups/counts, IDs, findings, finding metrics
and pagination totals/offsets remain legacy-only. A lifecycle-only positive can
therefore have `findings_present` and zero legacy counts: its separate
`hazard_count` explains the result. Terminal partial-status metrics reflect the
actual combined response. There is no second cursor or combined finding count.

## Bounds, detail and freshness

The scanner shares a 10,000-item work budget across branches, rejected-depth
items and malformed members. Maximum depth is 32, generated paths 256 characters
and inspected scalar values 2,000 characters. A rejected subtree is not enumerated.
Disabled or proven-unreachable descendants are not inspected; suppression counts
refer to suppressed roots/suffixes, not a fabricated descendant inventory.

Hazards, notes and gap entries share 32 retained entries. The richest lifecycle
JSON is capped at 16,384 bytes before detail projection. Retention/byte limits
preserve positive counts and prioritize retaining a hazard over auxiliary detail.
Bounds apply to the new scanner/output, not to the entire pre-existing provider
pipeline. Raw configurations are never copied/serialized to enforce these bounds.

Summary displays at most three compact hazards and three notes, with at most three
gap paths. Standard includes retained entries and next investigations; evidence
adds bounded derivation metadata. All levels retain classifications, counts,
coverage status, source/freshness and the same fingerprint. Intentional summary
omission is separate from processing truncation and does not lower precision.

Each successful initial analysis computes lifecycle once. Legacy pagination freezes
its public object, including timestamp/fingerprint, in the existing sanitized
five-minute snapshot. Continuations neither recollect nor recompute it. The final
page still considers all legacy findings and H; remaining source/lifecycle gaps
stay partial. Processing omissions cannot be recovered through that cursor. A new
analysis recollects evidence. Neither snapshot reuse nor a stable dependency index
establishes atomic HA state, execution, full coverage or absence of past events.

## Exact upstream authority

Reviewed Core 2026.9.3 commit:
`6de5eb18cd4502f94af44cfff3a02250d88716ed`. These are semantic-source references,
not a new runtime version pin or extension of signed compatibility authority.

| Core source at that commit | Evidence |
| --- | --- |
| `homeassistant/helpers/script.py:499–569` | Stop checking, condition/stop scope and disabled actions. |
| `homeassistant/helpers/script.py:538–578`, `601–637` | Per-action error continuation, ignored Home Assistant errors and non-ignorable explicit stop signals. |
| `homeassistant/helpers/script.py:711–842` | Parallel/sequence, conditions, choose and if execution. |
| `homeassistant/helpers/script.py:844–1037` | Counted/conditional repeats, first until iteration and stop signal. |
| `homeassistant/helpers/script.py:1199–1365` | Positive/zero delays; wait readiness, timeout and continuation ordering. |
| `homeassistant/helpers/script.py:1413–1459`, `2011–2036` | Shutdown stopping and unload of in-flight runs. |
| `homeassistant/helpers/condition.py:377–382`, `1403–1427`, `1812–1828` | Disabled checkers, conjunction and literal template truth. |
| `homeassistant/helpers/config_validation.py:247–260`, `521–533`, `569–628`, `1932–2047`, `2095–2105` | Literal booleans/durations, action structures and dispatch syntax. |
| `homeassistant/components/homeassistant/triggers/state.py:191–248`, `numeric_state.py:208–241` | Qualification listeners and detach cleanup. |
| `homeassistant/components/homeassistant/triggers/time.py:102–311` | Scheduled listeners and removal; no incident history. |
| `homeassistant/components/automation/__init__.py:865–880`, `1169–1225` | Affected stop and exact-configuration reload preservation. |
| `homeassistant/components/script/__init__.py:389–411`, `773–779` | Removal/unload and script lifecycle boundaries. |

Upstream test evidence inspected: `tests/helpers/test_script.py` delay/zero/cancel
(711–985), wait timeout/continuation (1296–1599), zero repeat (2397–2434), conditional
repeat (2740 onward), disabled if condition (3568–3605), stop tests (6240 onward),
and error continuation/explicit stop controls (6474–6563);
`tests/components/automation/test_init.py:848–942` and
`tests/components/script/test_init.py:447–499` preserve unchanged running objects;
script removal test at 1927 onward; state/numeric-state/time trigger tests cover
qualification and scheduling. Exact file hashes are retained with local source
inspection evidence. Full upstream pytest and a disposable Core container run are
separate from executing selected source functions with inert collaborators.

## Acceptance and recovery

Offline tests must prove useful positives plus disabled/zero/unreachable/dynamic
pairs, repeats without nontermination claims, script/blueprint gaps, exact paths,
combined depth/width/malformed budgets, positive-preserving output limits,
sanitization, all L/H/C/B combinations, legacy pages, detail levels, unchanged
source failures and provider counts. Public response tests verify the serialized
additive field and separate counters. Independent review and final-head
Full/Evidence remain required. Actual container/CI and installed acceptance are
separate evidence; no household restart or execution is a test fixture.

This patch changes no persisted execution format, provider access or live state.
Before delivery, recovery is retaining/discarding the isolated patch. A later
release, deployment, rollback or live read acceptance requires its own authority.
