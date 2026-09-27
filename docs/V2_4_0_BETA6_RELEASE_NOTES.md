# Engineering 2.4.0-beta.6 release notes

Standalone F3 lock persistence and recovery repair. These notes assert no
publication, deployment, installed acceptance or production lock settlement.

Supported configuration sequences can require more than sixteen locks even
with eight or fewer operations. The repair separates a 256-token lock budget
from the unchanged sixteen-item evidence budget, validates the complete union
before acquisition, and refuses excess capacity without dropping dependency
locks, consuming approval or dispatching a provider operation. Synthetic
coverage includes an eight-operation, eighteen-lock configuration sequence.

If acquired locks cannot be recorded, cleanup uses the exact owned handle under
execution/lock fencing, preserves the failure and reports cleanup separately.
Process loss before token persistence is handled by a narrow recovery proof:
terminal zero-dispatch configuration children may bind and settle only the exact
retained generations whose complete union and durable ownership agree. Missing,
partial, active, held, renewed or conflicting evidence remains unresolved. A
failed child stays failed; settlement grants no execution or approval authority.

Conflict auditing runs outside lock-store transactions. Recovered binding events
remain pending through lock release and audit/reconciliation crash windows, with
idempotent audit replay. Fixed cleanup diagnostics can use the independent audit
channel when child persistence fails. If both channels fail, durable diagnostic
delivery cannot be guaranteed; reporting errors do not replace the primary error.
Existing task and health responses add bounded retained/active/expired/pending
lock diagnostics and explicit unresolved/manual-intervention reporting.

**Deployment can automatically settle qualifying retained locks at startup or a
periodic recovery sweep.** Preserve current durable evidence and explicitly
include that effect in deployment approval. Do not delete lock files or replay
an old failed plan. Source tests and historical incident summaries do not prove
the current production records qualify.

**Backward-reader limitation:** field names and schema versions remain, but the
expanded serialized range is incompatible with beta.5 readers above sixteen
tokens, including dispatch-intent records. A binary downgrade alone is not a
supported recovery. Preserve a compatible repair image and durable history;
restoring stale execution records can lose dispatch history.

The expected catalog remains 79 tools (54 static + 25 delegated reads). Tool
inputs, provider routing/admission, fallback policy, signed Core applicability,
approval boundaries, installation identity/options, dependencies, workflow
permissions and frozen stable-v1 remain unchanged. The intended acceptance
pairing is Core 2026.9.3 / ha-mcp 8.5.0. Prior lifecycle, script-dependency,
Core-log and dashboard capabilities remain; the separate Alarmo inspector is
not included.

See the [beta.6 acceptance contract](V2_4_0_BETA6_ACCEPTANCE.md),
[F3 lock contract](F3_ADAPTER_LOCK_CORE.md),
[runtime recovery contract](F3_RUNTIME_INTEGRATION.md#terminal-tokenless-configuration-lock-settlement-source-candidate)
and [operational recovery limits](GOVERNED_EXECUTION_RECOVERY.md#token-persistence-failure-source-repair-and-operational-acceptance).
