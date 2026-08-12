# Engineering 2.2.0-beta.35 acceptance

This procedure applies only after independent merge-gate review, merge,
protected promotion, publication, and separately authorized deployment. Source
validation must not access the deployed Home Assistant environment. Use fresh,
uniquely identifiable plans and challenges; never reuse consumed approval
authority.

## Pre-deployment gates

1. Confirm source declaration, promoted metadata, image labels, tag, accepted
   commit, and deployed image all identify `2.2.0-beta.35`.
2. Confirm stable v1.1.2, the public tool catalog and schemas, task schema,
   approval authority, policy authority, provider routing/admission,
   zero-fallback policy, F3 behavior, dashboard behavior, and historical
   projection behavior are unchanged.
3. Require the complete unit suite, Fast/Full/Evidence gates,
   promotion-candidate validation, exact-image and immutable add-on lanes,
   declared architecture builds, dependency and vulnerability checks, YAML,
   PowerShell, secret, whitespace, and stable-v1 checks to pass.
4. Require exact Home Assistant 2026.7.2, 2026.8.0, and 2026.8.1 lanes and exact
   ha-mcp 8.0.0, 8.1.0, 8.1.1, and 8.2.0 lanes to pass with no fallback.
5. Confirm the notification remains navigation-only and reports Home Assistant
   service acceptance as `submitted`, not handset delivery.

## A — Mobile navigation matrix

Test the following six combinations with a fresh pending plan or challenge for
each one-time authority context. Do not accept merely opening Home Assistant as
success.

| Entry point | Companion state |
| --- | --- |
| Notification body tap | Cold/not running |
| Notification body tap | Backgrounded |
| Notification body tap | Foregrounded |
| **Open Approval Panel** action | Cold/not running |
| **Open Approval Panel** action | Backgrounded |
| **Open Approval Panel** action | Foregrounded |

For every row:

1. Confirm Engineering queued the intended fresh plan's notification and Home
   Assistant accepted one submission.
2. Confirm the intended notification appears on the handset without treating
   service acceptance as delivery proof.
3. Use only the matrix entry point under test.
4. Require the Companion app to open the exact authenticated Engineering
   Ingress route for the intended plan.
5. Require the correct plan identity and semantic review to be visible without
   a manual refresh, stale prior plan, wrong tab/page, or duplicate navigation
   loop.
6. Confirm approve/reject controls exist only on the authenticated review page;
   the notification itself must contain no decision authority.

Any wrong/stale route, required refresh, duplicate loop, authority in the
payload, or navigation that only opens the Home Assistant shell blocks mobile
navigation acceptance.

## B — Approval and rejection semantics

Use separate fresh plans for these paths.

### Approval

1. Create a benign, reversible approval-requiring configuration plan from a
   fresh authoritative read.
2. Open its exact review through the notification and inspect the semantic diff.
3. Approve only through authenticated Ingress and require exact plan/hash and
   policy-selected authority binding.
4. Apply once, require exactly one provider dispatch, authoritative reread, and
   terminal `succeeded_verified`.
5. Confirm duplicate apply produces no additional dispatch and no fallback.
6. Confirm notification clear was submitted while handset removal is reported
   only from direct observation.
7. Restore the benign canary, if required, through a separate fresh governed
   plan and approval.

### Rejection

1. Create another fresh benign approval-requiring plan and open its exact
   review through the notification.
2. Reject only through authenticated Ingress.
3. Require no apply authority, task creation, provider dispatch, redispatch, or
   fallback.
4. Confirm clear submission follows the existing contract without claiming an
   unobservable handset outcome.

Any notification-side decision, weakened principal separation, mismatched plan
binding, extra dispatch, or overclaimed delivery/clear result blocks
acceptance.

## C — Light and dark presentation

Exercise a fresh unconsumed review page in Companion/Home Assistant light and
dark presentation states. Where the app exposes a separate system-following
mode, test it too.

For each state verify:

- page and surface backgrounds are coherent with the effective device/browser
  scheme and do not show a persistent incorrect fixed-light background;
- body, muted, warning, and error text are readable;
- semantic change tables and code/diff surfaces are readable and horizontally
  usable on mobile;
- links and approve/reject controls remain distinct in default, hover/active
  where applicable, focus, and disabled states;
- keyboard focus indicators are visible where the client supports keyboard
  navigation;
- no route, form method, hidden authority field, decision endpoint, CSRF
  behavior, or approval semantics changed.

The embedded Ingress document has no reviewed contract for receiving a Home
Assistant-only custom theme that differs from the device preference. Record
that case separately as an environmental limitation rather than claiming exact
custom-theme matching. Light and dark readability must still pass.

## Operational boundaries

- Do not reuse Beta 32/33/34 plans or consumed challenges.
- Do not introduce dashboard metadata or another capability during acceptance.
- Do not rotate credentials as part of this release; that remains a separately
  authorized operation.
- Do not infer handset receipt, navigation, or notification clearing solely
  from Engineering counters.
