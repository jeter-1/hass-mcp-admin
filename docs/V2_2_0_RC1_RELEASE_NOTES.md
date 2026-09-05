# Engineering 2.2.0-rc.1 release notes

Engineering 2.2.0-rc.1 is the materialized source candidate for one approval-
notification navigation break fix and the canonical SemVer release-stage
transition that materialized it. The advertised Engineering source is
2.2.0-rc.1, `.release/next-version` has been consumed, and stable remains 1.1.2.

## Approval notification navigation

New approval notifications use the current Home Assistant app-panel route:

```text
/app/{verified_addon_slug}
```

The same relative, same-server path is supplied as `data.url`,
`data.clickAction`, and the explicit **Open Approval Panel** URI action. It
opens the authenticated approval inbox. The prior `/hassio/ingress/...` target,
`homeassistant://navigate`, and `deep-link://` wrappers are no longer generated,
and no absolute Home Assistant, Nabu Casa, or Ingress-session URL is introduced.

Supervisor `/addons/self/info` remains the sole slug authority. Strict plan and
slug validation, per-plan notification tags and clearing, privacy-minimal body,
advisory submission semantics, approval/governance/F3/provider/audit behavior,
and zero fallback are unchanged. A notification cannot approve, reject, apply,
or dispatch anything.

## Canonical release transition

Promotion and metadata validation now share one canonical transition model. It
supports exact beta increments, beta to `rc.1`, exact RC increments, same-core
RC to stable, and stable to `beta.1` of a newer core version. Sequence skips,
downgrades, cross-core RC movement, same-version transitions, and legacy or
malformed prerelease spellings remain fail-closed.

RC1 otherwise preserves materialized Beta 58 behavior: stable 1.1.2, 51
statically registered Engineering tools, 25 admitted ha-mcp 8.4.3 reads, 76
client-visible tools, only `ha_get_operation_status` held, task schema 1,
approval authority 3, existing reviewed providers and dashboard authority, and
fallback zero.

Physical Android notification navigation remains a separate post-deployment
acceptance gate. iOS remains unverified unless it is physically tested. The
immutable rollback artifact is Beta 58 commit
`c19018f67fc3c5dff8e60105f1f8112e5baa7415`, digest
`sha256:202fe3121ed8713096a64a4e5fdfdc2584c3ad7480a3c084990254367dbce353`.

This source work does not merge, publish, deploy, restart anything, access Home
Assistant, or send a real notification. Those later actions remain separately
owner-authorized.
