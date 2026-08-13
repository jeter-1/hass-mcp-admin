# Engineering 2.2.0-beta.36 acceptance

This procedure applies only after independent review, protected merge,
promotion, publication, separately authorized deployment, and separately
authorized live acceptance. Development validation must not perform handset
acceptance or mutate Home Assistant. Use fresh plans and challenges; never
reuse consumed or historical authority.

## Pre-deployment gates

1. Confirm source, generated metadata, tag, immutable images, attestations, and
   the deployment candidate identify `2.2.0-beta.36` from the accepted commit.
2. Confirm the advertised source before promotion remains
   `2.2.0-beta.35`, `.release/next-version` stages only Beta 36, and stable
   v1.1.2 remains unchanged.
3. Require focused identity, notification, navigation, authority, clear,
   lifecycle, and no-fallback regressions plus the complete unit, Fast, Full,
   Evidence, promotion-candidate, exact-image, immutable-runtime,
   compatibility, architecture, dependency, vulnerability, YAML, PowerShell,
   secret, whitespace, and stable-v1 gates.
4. Require exact-image evidence that the 33,732-byte Supervisor fixture was
   delivered in multiple 1,024-byte fragments, fully consumed below the fixed
   512 KiB ceiling, resolved through `/addons/self/info`, and led to exactly one
   allowlisted notification submission with zero fallback.
5. Confirm the response body, add-on options, configuration, translations,
   tokens, notification service, raw plan ID, and authority material are absent
   from logs, audit, evidence artifacts, exceptions, health, and persisted
   records.
6. Confirm public tool schemas/counts, task schema, approval authority, policy
   authority, F3, provider admission/routing, dashboard behavior, historical
   projection, reviewed Home Assistant/ha-mcp matrices, and fallback policy are
   unchanged.

## Post-deployment entry gate

Before handset testing, confirm:

- Engineering identifies as `2.2.0-beta.36` and build/image provenance matches
  the published artifact;
- Home Assistant and ha-mcp identify as their exact admitted releases;
- Supervisor identity status is `verified_supervisor_self_info` after a fresh
  notification request;
- notification configuration and worker state are healthy;
- governance storage, external approval, F3, provider routing, and audit are
  healthy; and
- fallback remains zero.

Stop if identity resolution guesses a slug, reports a failure for the valid
bounded response, or uses any authority other than `/addons/self/info`.

## Android navigation matrix

Use a fresh uniquely identifiable challenge for every row.

| Entry point | Companion state |
| --- | --- |
| Notification body tap | Cold/not running |
| Notification body tap | Backgrounded |
| Notification body tap | Foregrounded |
| **Open Approval Panel** action | Cold/not running |
| **Open Approval Panel** action | Backgrounded |
| **Open Approval Panel** action | Foregrounded |

For every row require the notification to appear, the selected interaction to
open Home Assistant, and the exact Engineering plan review to load without a
manual refresh, stale plan, wrong tab, or duplicate navigation. Merely opening
the Home Assistant shell is not success.

## iOS navigation matrix

Exercise the same six body/action and cold/background/foreground combinations
on an actual iOS Companion device. Passing Android or deterministic source
tests does not establish iOS acceptance. If no physical iOS device is
available, record exactly:

```text
IOS_LIVE_ACCEPTANCE_NOT_EXECUTED
```

Do not claim iOS handset acceptance in that state.

## Approval lifecycle

### Fresh benign approval

1. Create a fresh reversible approval-requiring plan from authoritative state.
2. Request external approval and confirm one notification is queued and one
   Home Assistant service submission succeeds.
3. Open the exact review page through the notification, inspect the semantic
   diff, and approve only through authenticated Ingress.
4. Require exact authority binding, one apply dispatch, authoritative reread,
   terminal `succeeded_verified`, and zero fallback.
5. Repeat apply and require zero additional dispatch.
6. Confirm notification clear is submitted without claiming handset removal
   until it is directly observed.

### Fresh rejection

1. Create a separate fresh plan and request its notification.
2. Open its exact authenticated review and reject it through Ingress.
3. Require no apply authority, execution task, provider dispatch, redispatch,
   fallback, or Home Assistant mutation.
4. Confirm clear submission remains advisory and independently accounted.

For both paths, verify the notification contains navigation only. It must not
contain an approve/reject action, challenge ID, plan hash, CSRF material,
approval token, nonce, configuration, diff, or credential.

## Failure and truthfulness acceptance

Source and exact-image evidence must preserve these outcomes:

- response above 512 KiB: `response_too_large`;
- non-success status: `http_status`;
- complete unsupported/malformed JSON contract: `malformed_response`;
- timeout: `timeout`;
- transport failure: `transport_failure`.

Every identity failure prevents notification submission and guessed identity.
Home Assistant service acceptance increments `submitted`, not `delivered`;
clear service acceptance increments `clear_submitted`, not `cleared`. Handset
delivery, navigation, and removal remain physical observations.

## Operational boundaries

- Do not reuse the Beta 35 canary or any consumed challenge.
- Do not execute a self-add-on restart to validate identity.
- Do not modify dashboards, dashboard metadata, automations, devices,
  credentials, SSH/security controls, or provider policy during acceptance.
- Do not add an identity fallback to make acceptance pass.
