# Beta 11 restart-reconciliation defect evidence

## Sanitized observation

Beta 10 could revisit a stale persisted restart plan or execution task at an
approximately 47-second cadence. The recovery path could repeat expensive
Core-facing or lifecycle-provider work even when the record could not obtain
valid new evidence. A single stale record could consume approximately one CPU
core while the Engineering server was otherwise idle. This is a bounded
sanitized defect summary; no production payload, credential, or live record ID
is retained here.

## Root cause and correction

The recovery scheduler did not consistently treat the original durable
post-dispatch deadline as an absolute stop, and unchanged evidence did not
persist an increasing retry schedule. Beta 11 makes the durable task's
`maximum_post_dispatch_deadline` authoritative. Historical taskless plans use
their persisted dispatch timestamp plus the existing maximum interval. A
missing trustworthy timestamp fails closed into terminal manual review.

Before any network work, a cheap persisted gate requires a supported restart,
consumed approval, recorded dispatch, a valid nonterminal record, an unexpired
deadline, a due schedule, structural eligibility, and an unowned task.
Permanent failures and expiry terminalize locally. No expired or terminal
record contacts Home Assistant, Supervisor, or the lifecycle provider.

Unchanged eligible attempts persist capped backoff of one, two, five, then
fifteen minutes. Attempts, timestamps, backoff and deadline survive startup;
the next attempt cannot exceed the deadline. Task-level single flight,
bounded batches and provider timeouts prevent overlap or monopolization.
Reconciliation remains readback-only and has no redispatch path.

Health exposes the active plan/task identity and bounded timing, pending,
avoidance, expiry, collision, terminalization and failure counters. Inactive
health clears active identifiers.
