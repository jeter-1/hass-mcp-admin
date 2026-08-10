# Engineering 2.2.0-beta.32 release notes

Beta 32 corrects the approval-notification payload rejected by the Android
Home Assistant Companion App and makes notification observability match what
Engineering can actually prove. It is staged from `2.2.0-beta.31`.
Publication, deployment, live Home Assistant access, and reuse of any prior
notification challenge are not part of this change.

## Cross-platform approval link

The privacy-minimal notification continues to contain one advisory **Open
Approval Panel** URI action. Its relative authenticated Ingress path is now
provided consistently as Android `clickAction`, iOS `url`, and the URI action
target. The iOS-only `authenticationRequired` action property that caused the
live Pixel provider rejection is no longer sent.

The payload still contains no approval or rejection action, challenge ID, plan
hash, CSRF nonce, credential, diff, configuration, or unrestricted plan data.
Opening the notification remains navigation only. Existing authenticated
administrator Ingress, persisted plan authority, exact binding, principal
separation, and one-time CSRF validation remain the only decision path.

## Truthful submission evidence

A successful Home Assistant REST service response proves that Engineering
submitted the notify or clear request to Home Assistant. It does not prove that
the handset received or cleared the notification. Runtime status, health, and
audit therefore report `submitted` and `clear_submitted` instead of claiming
`delivered` or `cleared`.

The existing `delivered` and `cleared` health counters remain available for
shape compatibility and stay zero because this provider supplies no handset
delivery receipt. Health explicitly reports
`handset_delivery_observable: false` and `handset_clear_observable: false`.
Provider rejections remain failures with `provider_response_received: true`;
timeouts and transport failures do not falsely claim a response.

## Corrected exact-image contract

The exact-image Home Assistant fixture now enforces the documented Android
payload. It requires `clickAction` to equal the exact Ingress path and rejects
the iOS-only action property. The baked runtime acceptance requires exactly one
allowlisted submission, truthful submitted-versus-delivered counters, verified
Supervisor self identity, authority `none`, and fallback zero.

## Preserved behavior

- The configured mobile-app notify service and Supervisor identity authority
  are unchanged.
- Notification work remains advisory and cannot approve, reject, consume,
  extend, apply, roll back, recover, or otherwise change a plan.
- Approval authority, governance storage, F3 sequencing, one-dispatch, and
  no-fallback behavior are unchanged.
- The public MCP tool catalog, schemas, ha-mcp 8.2.0 admission, dashboard
  provider behavior, and Home Assistant compatibility declarations are
  unchanged.
- Stable v1.1.2 is unchanged.
