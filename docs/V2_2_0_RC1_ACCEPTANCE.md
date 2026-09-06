# Engineering 2.2.0-rc.1 acceptance

Engineering 2.2.0-rc.1 is the materialized source candidate for a narrow
approval-notification navigation correction. The advertised Engineering source
is 2.2.0-rc.1, `.release/next-version` has been consumed, and stable remains
1.1.2. Materialization did not merge, publish, deploy, restart anything, access
Home Assistant, or send a real notification.

## Exact navigation authority

Home Assistant Core commit
`53998d7710b4ac280658511c24a2a3e2651f9873` pins
`home-assistant-frontend==20260729.6` in `requirements_all.txt`. Exact Frontend
tag `20260729.6` resolves to commit
`235541748ba1c291171d3ec759dd473685c8f2be`; its
`src/data/panel.ts` defines the internal app panel as `app`, and
`src/panels/app/ha-panel-app.ts` resolves the add-on slug from the
`/app/<slug>` route before fetching the Supervisor app and creating its Ingress
session.

The implementation therefore uses exactly:

```text
/app/{verified_addon_slug}
```

for notification `data.url`, `data.clickAction`, and the one explicit URI
action. The slug continues to come only from Supervisor `/addons/self/info`.
The route opens the authenticated approval inbox; it deliberately does not
carry a plan ID or an Ingress session.

Current Android implementation-discovery source at commit
`d3af0e4c91484b69f8a175c86d1514ea2ae2c907` confirms the boundary in
`MessagingManager.kt`: relative notification paths are passed to
`intentLaunchWithNavigateTo(..., serverId)`, while absolute URLs and
`deep-link://` values use an external `ACTION_VIEW` intent. This current Android
source informs the correction but is not physical handset acceptance.

Beta 58 constructs `/hassio/ingress/{slug}/plans/{plan_id}` plus
`homeassistant://navigate` and `deep-link://` wrappers. The deployed 404 or
connection failure and the exact current Frontend route contradict that
generated navigation contract. Historical Beta 35 and Beta 36 documents remain
release history; RC1 replaces only newly generated notification navigation.

## Acceptance requirements

Source and deterministic validation must prove:

- all three navigation fields equal `/app/{verified_addon_slug}`;
- the payload contains no retired Ingress route, URL scheme, hostname, Nabu
  Casa URL, Ingress session, plan identifier, challenge, CSRF value, plan hash,
  approval token, configuration, credential, approve action, or reject action;
- different plans retain distinct notification tags while sharing the inbox;
- invalid plan or Supervisor identity evidence stops before notification
  dispatch;
- timeout, authentication failure, provider rejection, unavailable Home
  Assistant, clearing, restart reconciliation, and advisory submission
  semantics remain unchanged;
- notification activity cannot approve, consume, apply, create an execution
  task, or dispatch an operation provider; and
- approval authority remains version 3, task schema remains 1, no public tool
  changes occur, and fallback remains zero.

The canonical release transition must accept exact next beta increments,
`beta.N -> rc.1`, exact next RC increments, same-core RC to stable, and newer-
core stable to `beta.1`. It must reject skipped sequences, downgrades, cross-
core RC transitions, and noncanonical prerelease spelling. Both promotion and
metadata validation consume the same predicate.

RC1 otherwise preserves Beta 58 behavior: stable 1.1.2, 51 statically
registered Engineering tools, 25 admitted ha-mcp 8.4.3 delegated reads, 76
client-visible tools, only `ha_get_operation_status` held, task schema 1,
approval authority 3, reviewed provider routing, governance, F3, audit,
dashboard authority, and zero fallback.

## Separate physical Android acceptance

After separately authorized merge, publication, and deployment, verify the
exact RC1 runtime and establish a quiet governance baseline. Create one harmless
governed plan without applying it, request its normal notification, and test
both the body and **Open Approval Panel** action with Android backgrounded and,
if the notification remains available, cold.

Both interactions must open the authenticated HA MCP Approval inbox on the
server that delivered the notification with no 404, no “Unable to connect”
dialog, no external browser, and no manual refresh. The pending plan must be
visible. Reject it in the panel, then require notification clearing, zero
execution task, zero provider dispatch, zero fallback, no Home Assistant
mutation, F3 ready, and no residual approval, challenge, lock, or execution
state. iOS physical navigation remains **NOT TESTED** unless separately
exercised.

## Rollback and authority boundary

The immutable rollback artifact is Beta 58 at commit
`c19018f67fc3c5dff8e60105f1f8112e5baa7415`, digest
`sha256:202fe3121ed8713096a64a4e5fdfdc2584c3ad7480a3c084990254367dbce353`.
Before merge, rollback is closing the draft PR. After a future merge but before
deployment, rollback is reverting the RC1 merge. After a future deployment,
operational rollback is redeploying that exact Beta 58 artifact and verifying
version, build identity, storage, F3, provider admission, and zero fallback.

Merge, publication, deployment, and live handset acceptance remain separate
owner-authorized actions.
