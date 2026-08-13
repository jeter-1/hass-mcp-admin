# Beta 35 mobile approval navigation

This workstream corrects mobile navigation to a governed approval review. It
does not change approval authority, challenge lifecycle, or decision handling.

## Canonical target and platform representations

Engineering constructs one local authenticated Ingress target:

```text
/hassio/ingress/{verified_addon_slug}/plans/{plan_id}
```

Every notification navigation value is derived from that target:

- iOS `url`:
  `homeassistant://navigate/hassio/ingress/{verified_addon_slug}/plans/{plan_id}`
- Android `clickAction`:
  `deep-link://homeassistant://navigate/hassio/ingress/{verified_addon_slug}/plans/{plan_id}`
- shared explicit `URI` action:
  `/hassio/ingress/{verified_addon_slug}/plans/{plan_id}`

The Android wrapper follows the Companion notification contract for sending a
specific deep link to another app. The nested Home Assistant URL follows the
Companion URL-handler contract and enters its exported navigation path instead
of relying on the notification's relative-path body launch behavior. The
shared action deliberately uses the canonical relative frontend path: current
Android and iOS Companion action handlers both resolve that form against the
configured Home Assistant frontend. Android does not treat an unwrapped
`homeassistant://navigate...` action URI as an external action target, so that
scheme is not used for the shared button. No platform is inferred from the
`notify.mobile_app_*` service name.

The installed add-on slug still comes only from verified Supervisor self-info.
The plan identity is required to be the existing lower-case 32-character hex
form. Invalid identity or navigation components fail before notification
dispatch; no fallback or guessed identity is permitted.

## Authority and result semantics

The notification is an advisory navigation hint only. It contains no approval
or rejection action, challenge secret, CSRF material, plan hash, authority
token, nonce, diff, or proposed configuration. Approval and rejection remain
available only after the authenticated Ingress review page loads.

A successful Home Assistant service response continues to mean `submitted`.
Engineering does not claim handset delivery, successful navigation, or handset
clear without independently observable evidence.

## Live acceptance still required

Source and deterministic tests prove exact target construction, platform payload
derivation, authority exclusion, fail-closed identity handling, distinct plan
identity, and notification-clear correlation. They cannot prove Companion app
lifecycle behavior. After the corrective artifact is separately deployed,
fresh unconsumed plans must exercise both notification body tap and action
button with the app cold, backgrounded, and foregrounded on every supported
platform. Android and iOS require independent six-case matrices. Success
requires the exact plan review to load without manual refresh, stale navigation,
or a duplicate navigation loop. iOS support remains unaccepted until all six
iOS cases have actually been exercised on an iOS device.
