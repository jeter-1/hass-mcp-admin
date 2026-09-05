# Beta 35 mobile approval navigation

This document records the historical Beta 35 notification contract and its
RC1 replacement. Neither contract changes approval authority, challenge
lifecycle, or decision handling.

## Historical Beta 35 behavior

Beta 35 emitted one local Ingress target containing the exact plan ID:

```text
/hassio/ingress/{verified_addon_slug}/plans/{plan_id}
```

Its notification navigation fields were derived from that target:

- iOS `url`:
  `homeassistant://navigate/hassio/ingress/{verified_addon_slug}/plans/{plan_id}`
- Android `clickAction`:
  `deep-link://homeassistant://navigate/hassio/ingress/{verified_addon_slug}/plans/{plan_id}`
- shared explicit `URI` action:
  `/hassio/ingress/{verified_addon_slug}/plans/{plan_id}`

This is immutable release history. Current Frontend app-panel routing and the
deployed failure showed that the historical `/hassio/ingress/...` navigation
target is no longer the correct notification entry point.

## RC1 replacement

RC1 emits one relative same-server target for the notification body, Android
`clickAction`, and explicit URI action:

```text
/app/{verified_addon_slug}
```

The target opens the authenticated approval inbox. It deliberately omits the
plan ID, external URL schemes, hostnames, Nabu Casa URLs, and Ingress session
material. Android binds a relative notification path to the Home Assistant
server that delivered the notification. Exact plan selection occurs only
inside the authenticated approval panel.

The installed add-on slug still comes only from verified Supervisor self-info.
The plan identity remains required to be the existing lower-case 32-character
hex form even though it is no longer carried in the notification URL. Invalid
identity or navigation components fail before notification dispatch; no
fallback or guessed identity is permitted.

## Authority and result semantics

The notification is an advisory navigation hint only. It contains no approval
or rejection action, challenge secret, CSRF material, plan hash, authority
token, nonce, diff, or proposed configuration. Approval and rejection remain
available only after the authenticated Ingress review page loads.

A successful Home Assistant service response continues to mean `submitted`.
Engineering does not claim handset delivery, successful navigation, or handset
clear without independently observable evidence.

## Live acceptance still required

Source and deterministic tests prove exact target construction, authority
exclusion, fail-closed identity handling, distinct notification tags, and
notification-clear correlation. They cannot prove Companion app lifecycle
behavior. After RC1 is separately deployed, a fresh unconsumed plan must
exercise both notification body tap and **Open Approval Panel** on Android with
the app backgrounded and cold. Success requires the authenticated approval
inbox to load on the correct server without a 404, connection dialog, external
browser, or manual refresh. iOS remains unverified until it is physically
tested.
