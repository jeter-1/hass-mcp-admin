# Engineering 2.2.0-beta.35 release notes

Beta 35 is a focused approval-UX corrective release. It stages two
independently reviewed workstreams from the published Beta 34 source. Beta 34
remains the advertised Engineering release until a separate protected
promotion publishes Beta 35. This source change does not deploy or access Home
Assistant.

## Mobile approval navigation

Beta 35 replaces notification navigation that relied on a relative Ingress URL
with one canonical authenticated Ingress plan path and explicit Companion URL
handler representations derived from it. Notification body taps and the
explicit **Open Approval Panel** action now carry platform-appropriate links to
the exact governed plan-review route.

The notification remains advisory and navigation-only. It carries no approval
or rejection action, challenge secret, CSRF value, approval token, nonce,
configuration diff, or other decision authority. Approval and rejection remain
available only through the authenticated Ingress page. A successful notify
service response continues to mean `submitted`; Beta 35 does not claim handset
delivery or successful handset navigation without live evidence.

The six-case Android lifecycle matrix (body tap and action button with the app
cold, backgrounded, and foregrounded) remains a required post-deployment
acceptance test. No untested lifecycle behavior is claimed by these notes.

## Approval-page light and dark presentation

Beta 35 removes the approval page's fixed light-only palette. The embedded page
now declares light/dark color-scheme support and applies local, contrast-tested
tokens for page and card surfaces, text, borders, links, controls, semantic
review content, warnings, focus, hover, active, and disabled states.

Home Assistant renders Ingress content in a separate document and does not
currently expose the parent frontend's effective theme variables or selected
custom theme to the embedded page. The bounded fallback therefore follows the
browser or Companion device `prefers-color-scheme` value. A Home Assistant-only
or custom-theme selection that differs from the device preference may not match
exactly and must not be represented as accepted until live testing proves it.

This presentation change does not alter routes, form methods, hidden authority
fields, approval binding, CSRF handling, elevated acknowledgement, challenge
lifecycle, or decision semantics.

## Preserved boundaries

- Stable v1.1.2 is unchanged.
- Public tools and schemas, task schema, approval authority, policy authority,
  provider routing and admission, zero-fallback behavior, F3 execution,
  dashboard behavior, and historical projection behavior are unchanged.
- The reviewed Home Assistant and exact ha-mcp compatibility lanes remain
  required.
- No capability expansion, dashboard metadata administration, deployment,
  live mutation, or credential rotation is included.
