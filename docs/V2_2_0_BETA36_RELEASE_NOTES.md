# Engineering 2.2.0-beta.36 release notes

Beta 36 is a focused approval-notification corrective release. It carries the
independently reviewed cross-platform action-navigation correction and fixes
the bounded Supervisor self-identity read that blocked Beta 35 notification
submission. Beta 35 remains the advertised release until the protected
promotion workflow separately publishes Beta 36.

## Complete bounded Supervisor self-info reads

Supervisor `/addons/self/info` remains the sole installed-add-on identity
authority. The Beta 35 fetcher made one bounded `aiohttp` stream read and then
treated the bytes already available as the complete response. A legitimate
33,732-byte live response arrived in multiple transport fragments, so the
first fragment reached JSON decoding as incomplete input and was reported as
`malformed_response`.

Beta 36 repeatedly reads the same response until EOF while retaining at most
512 KiB plus one detection byte. The configured ceiling is unchanged. A
response above it still reports `response_too_large`; non-success status,
malformed complete JSON, timeout, and transport failure retain their existing
safe categories. The response body, options, configuration, translations,
tokens, and credentials are never logged or persisted. Only the validated
slug, name, version, and optional repository identity survive parsing.

There is no manifest, environment, configured-slug, repository, add-on-list,
service-name, or other identity fallback. Failure to verify the Supervisor
response still prevents notification submission and governed self-add-on
classification. Approval notifications and self-add-on lifecycle planning
continue to share one resolver implementation.

The exact-image fixture now matches the bounded live envelope size and schema
shape and deliberately streams it in 1,024-byte fragments. Acceptance requires
the baked runtime to consume the complete fragmented response, verify the
installed slug, and submit exactly one allowlisted advisory notification.

## Cross-platform approval navigation

Every navigation representation remains derived from one authenticated plan
review path:

```text
/hassio/ingress/{verified_addon_slug}/plans/{validated_plan_id}
```

- iOS notification body `url`:
  `homeassistant://navigate{review_path}`
- Android notification body `clickAction`:
  `deep-link://homeassistant://navigate{review_path}`
- shared **Open Approval Panel** action `uri`: `{review_path}`

The action uses the reviewed cross-platform relative Ingress target rather
than Android's body-only wrapper. The notification remains navigation-only and
contains no approve/reject action, challenge secret, plan authority, CSRF
value, nonce, configuration, or diff. Decisions remain exclusively inside
authenticated Ingress.

A successful Home Assistant notify service response means `submitted`; it
does not prove handset delivery, navigation, or clearing. Android and iOS
physical lifecycle matrices remain post-deployment acceptance work and are not
claimed by this source release.

## Preserved boundaries

- Stable v1.1.2 is unchanged.
- Public tools and schemas, task schema, approval authority, policy authority,
  provider admission/routing, zero fallback, F3 behavior, dashboard behavior,
  and historical projection behavior are unchanged.
- The 512 KiB identity-response ceiling and existing failure taxonomy are
  unchanged.
- No new provider, tool, write reachability, capability, deployment,
  credential rotation, or live Home Assistant mutation is included.
