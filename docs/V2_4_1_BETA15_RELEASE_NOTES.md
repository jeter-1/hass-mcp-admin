# Engineering 2.4.1-beta.15 release notes

Engineering 2.4.1-beta.15 closes the remaining HAMCP-189 residuals from the
HAMCP-164 triage (PR B and PR C, delivered together). It builds on beta.14
(HAMCP-189 PR A).

No tool, input schema, description, provider route, signed registry, HOLD,
support window, persisted format, workflow or stable-v1 change is included. No
new write, dispatch, service-call or fallback path is reachable. The design
note is [`HAMCP_189_PR_B_DESIGN.md`](HAMCP_189_PR_B_DESIGN.md), and the
triage is [`HAMCP_189_TRIAGE.md`](HAMCP_189_TRIAGE.md).

**Rollback planning holds its own Core lease (R1).** `rollback_change` is
Core-independent at the gateway because returning an existing rollback plan
is local. Before beta.15, fresh rollback planning then read current
configuration and registry state with no Core authority. Now the rollback
tool acquires and consumes a Core lease before any rollback planning read:
- every planning read runs under an authorizer that revalidates the lease;
- for a configuration or garage-inverse rollback, the lease must still be
  current where the rollback plan is saved, after every planning read;
- entity-metadata rollback binds the exact registry target, as
  `create_entity_metadata_plan` does.

Under degraded Core, fresh rollback planning is refused before any Home
Assistant read as `provider_unavailable` with reason
`core_capability_unavailable`, and no plan is created. Core retirement during
planning also refuses: a read after retirement is refused before it is sent,
and no rollback plan is saved if Core retired during the builder's last read.
For an ordinary configuration rollback that check runs through a private,
default-off save hook in `governance/service.py` (Josh's narrow O8
amendment); no other plan creation changes. Returning an existing or cached rollback plan still
works while Core is degraded. With Core degraded, a local planning refusal
that comes after the lease point (for example an unsupported task state) now
reports the Core refusal instead; with Core admitted it is unchanged.

**Cancel telemetry counts only admitted upstream attempts (R2-telemetry).** An
observed operational read now begins its upstream attempt only after the Core
dispatch authorizer passes. A refused read reports zero upstream requests, and
a transport failure before dispatch is no longer counted as an attempt. A
retained-session dispatch whose caller has stopped waiting is refused instead
of starting an attempt that nothing would finish.

**Expired pre-intent tasks terminate truthfully (O9).** Before beta.15, a
contract-v3 task (operational, exact helper state or dashboard) whose approval
expired before its dispatch intent was recorded failed every F3 recovery
sweep. Its child stayed in preflight with a bounded backoff, and the readiness
fault kept new F3 applies blocked until the separate service reconciler ended
the task (as a cancel nobody requested). Now the F3 runtime terminates such a
task itself:
- bounded retries continue for 600 seconds after the dispatch authority
  expires (at most 7 retries with the existing backoff);
- after that, the child ends `preflight_rejected` with diagnostic
  `plan_expired`, the task ends `failed_pre_dispatch` and the plan `FAILED`.
  These are the strings configuration plans already produce for the same
  cause;
- it applies only when the child has no dispatch intent, no dispatch count,
  no live claim, no recorded cancel and only its own recorded locks;
- its exact locks are released and readiness is restored. Nothing is
  dispatched.

The same applies to a task saved before its first child record exists (for
example a restart between saving the task and creating its first child):
after the same deadline, the runtime writes the child directly as
`preflight_rejected` / `plan_expired`, without contacting any provider or
renewing dispatch authority. A recorded cancel and a competing first claim
keep precedence. Dashboard tasks were never stuck: the existing executor
preflight already rejects the expired plan.

A task whose approval is still valid keeps its unchanged bounded retry.

**The gateway audit keeps the refusal reason (R8, L1).**
- A tool name that is not a string (for example a list or object) no longer
  breaks the gateway's audit step. Every non-string name, including falsey
  ones (`null`, `false`, `0`, `[]`, `{}`), writes exactly one generic MCP
  request record and one error count, without the raw value.
- An empty tool name refused as `core_requirement_unmapped` now writes the
  full unmapped-refusal audit record and its error metric, counted once.

**Beta.13 release-notes correction (L2).** The repository's beta.13 release
notes overstated the Core-independent allowlist as "local state only". They
now carry a dated correction: `get_execution_task`, `cancel_execution_task`
and `approve_change_plan` are not purely local, and `rollback_change`
planning read without Core authority (fixed above). The published
v2.4.1-beta.13 GitHub Release is unchanged.

**Disclosed residuals:**
- A task that keeps failing recovery while its approval is still valid keeps
  retrying, as before. That is outside O9.
- Pre-existing and unchanged: re-applying an expired contract-v3 plan that is
  already terminal returns the operational adapter's raw `plan_expired`
  error instead of a governed refusal, and cancelling an expired task that
  has no child record fails the same way. Neither dispatches.
- `hass_mcp_engineering_beta/ha_mcp_engineering/ha_core_readmission/routes.py` still justifies `rollback_change` with
  the pre-R1 wording; it is HAMCP-162 territory and is not edited here.
- The configuration stale-state race (R3c) is inherent to the Home Assistant
  configuration API.

The healthy catalog remains **87 tools: 59 native and 28 delegated reads**.

Downgrade: beta.14 reads every beta.15 record, since no persisted format
changes and every stored string already exists. A downgraded server again
plans rollbacks without Core authority, again counts refused observed reads,
and again leaves expired contract-v3 tasks to the service reconciler.

Follow the [beta.15 acceptance contract](V2_4_1_BETA15_ACCEPTANCE.md).
