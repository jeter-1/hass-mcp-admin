# Engineering 2.4.0-beta.7 release notes

Configuration verification and supplementary re-verification. These notes assert
no publication, deployment, installed acceptance or resolution of a household task.

Standalone `condition: trigger` actions now preserve literal string/list `id`
values during verification. Different IDs, ordering, modifiers and representations
remain different; malformed or unsupported forms still refuse. Configuration
preflight now proves that each candidate can be compared before that operation
consumes approval or dispatches. Earlier operations in a non-atomic sequence may
already have completed; this is not an atomic-sequence guarantee.

The new native `reverify_configuration_task(task_id, expected_plan_hash, request_id)`
checks every approved object of an eligible terminal configuration mismatch and
records dated supplementary evidence. Exact original approval/consumption witnesses,
immutable children and dispatch history, current signed Core read authority,
configuration readback/check, concurrency ownership and durable audit/receipt
evidence must agree. It does not resend configuration, operate a device, consume
approval or reopen a task. The original failed outcome remains; the task getter
can display a separate `review_resolution`.

The observation has a 30-second deadline, bounded bodies and object reads, no
automatic retry, redirect or fallback. Results distinguish mismatches, unavailable
evidence, interrupted reads, stale authority, conflicts and uncertain persistence.
Successful readback alone does not mean the receipt was saved. Observations are
non-atomic and do not prove household behavior or continuous correctness.

An optional bounded `configuration-reverification-v1/` ledger is created lazily
in the existing persistence root. Exact completed requests replay dated evidence
without new HA access. Later bound attempts/refusals supersede the prior projection
when they can be recorded. A full ledger or failed persistence reports uncertainty;
process-local withholding is not durable across restart. No pruning or automatic
historical re-verification is added. Preserve this namespace during rollback;
older binaries cannot display its supplementary evidence. The inherited beta.6
expanded-lock reader/recovery limits still apply; binary downgrade alone is not
a recovery plan.

The healthy candidate catalog is **80 = 55 static + 25 delegated reads**. The
new tool is evidence-writing (`readOnlyHint=false`), non-destructive and idempotent
for an exact request binding; HA access is read-and-validate only. Existing tool
inputs, provider admission/fallback, signed Core applicability, approval authority,
dependencies, workflows, installation settings and frozen stable-v1 are preserved.
Core 2026.9.3 / ha-mcp 8.5.0 remains the installed acceptance target. Alarmo is
not included. Deployment and one exact installed re-verification require separate
authorization; there is no fan or other device canary.

See the [beta.7 acceptance contract](V2_4_0_BETA7_ACCEPTANCE.md),
[trigger verifier contract](TRIGGER_CONDITION_VERIFICATION.md),
[supplementary receipt contract](CONFIGURATION_REVERIFICATION.md) and
[inherited beta.6 recovery limits](V2_4_0_BETA6_RELEASE_NOTES.md).
