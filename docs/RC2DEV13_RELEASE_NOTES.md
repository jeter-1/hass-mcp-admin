# RC2dev13 release notes

Version: `2.0.0-rc2-dev13`
Status: staged corrective candidate; not published, deployed, or accepted

## Why this candidate exists

RC2dev12 passed its identity, catalog, delegated-read, no-write, no-fallback,
performance, interruption, persistence, prewarm, dashboard-admission, and manual
add-on restart checks. It did not pass acceptance: after a full Home Assistant
host reboot, Engineering attempted upstream admission before ha-mcp was ready,
remained at 40 statically registered tools, and required a manual Engineering restart to reach
66 tools. A separate confirmed defect allowed `ha_search` data with
`partial: true` to be labeled `metadata.completeness: complete`.

RC2dev12 is immutable failed history. Tag `v2.0.0-rc2-dev12` remains bound to
release commit `430b0aaf6bf7f9e5e55b701b277c636ea34e5014`; its published image digest is
`sha256:a55b4a85aa47efa4c2b4f628a7d50cc4f0b3d8710bedbbdb254e23e633d28ca4`.
Do not retag, overwrite, redeploy, accept, or use RC2dev12 as a rollback target.

## Corrections

Engineering now starts both listeners with its 40 statically registered tools
(25 canonical plus 15 Engineering-native) and supervises one background
reconciliation loop for the reviewed upstream read gateway. The
loop probes immediately and retries with capped exponential delays. Every
attempt repeats exact server identity, version, protocol, catalog, policy, and
schema admission. Failed attempts expose no delegated tool, no fallback, and no
write path. Successful admission replaces only this provider's dynamic tools,
so a subsequent `tools/list` reaches the reviewed 40 + 26 = 66 catalog without
an Engineering add-on restart.

The transport is stateless and does not broadcast `tools/list_changed`.
Clients that cached the initial 40-tool list must call `tools/list` again or
reconnect after reconciliation; editing that notification architecture is not
part of this correction.

For `ha_search`, Engineering now preserves the reviewed upstream top-level
boolean `partial`. `true` is reported as partial; `false` is complete unless
local response bounding also makes the result partial. A missing or malformed
signal fails closed as successful-but-partial with a fixed local warning.
Untrusted upstream diagnostic prose is not promoted into Engineering-authored
metadata.

## Boundaries and promotion workflow

This candidate changes no public input schema, native tool registration,
reviewed catalog or policy entry, provider route, trust data, governance or
approval rule, stable-v1 file, container file, deployment operation, or
credential behavior. It makes one narrow change to
`.github/workflows/publish-rc-image.yml`: both staged and preversioned release
modes must pass exact release-notes and acceptance-authority validation before
publication can proceed. Workflow triggers, permissions, action references,
immutable-reference checks, and generated release references are unchanged.
Once the additional fail-closed authority prerequisite passes, publication
semantics are unchanged. The gateway still exposes reviewed pure reads only.
Writes, service calls, arbitrary forwarding, and direct-HA fallback remain
unreachable.

## Promotion and rollback

The protected promotion may consume `.release/next-version` only after the
exact RC2dev13 release-notes and acceptance pair resolves as active staged
authority and all repository gates pass. That future action must create new
immutable RC2dev13 artifacts; it must never mutate RC2dev12.

Deployment and the full-host reboot gate require separate authorization. Before
deployment, record and anonymously verify the exact version and digest of a
previously independently accepted Engineering rollback image. If no such image
is known and available, deployment is a stop condition. Do not infer a rollback
target and do not use RC2dev12.
