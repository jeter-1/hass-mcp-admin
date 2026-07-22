# RC2dev12 immutable historical release record

Version: `2.0.0-rc2-dev12`
Status: Historical only; cannot authorize acceptance, release, or deployment

This is an immutable historical release record, not active authority. It
records the already published candidate without changing, replacing, or
endorsing it.

RC2dev12 tag `v2.0.0-rc2-dev12` targets release commit
`430b0aaf6bf7f9e5e55b701b277c636ea34e5014`. The recorded published image
digest is
`sha256:a55b4a85aa47efa4c2b4f628a7d50cc4f0b3d8710bedbbdb254e23e633d28ca4`.

The candidate passed identity/build, 40 statically registered + 26 delegated
read catalog admission, delegated execution, no-write/no-fallback, performance,
controlled interruption and call recovery, governance/audit persistence,
dependency-index prewarm, dashboard-provider admission, and manual Engineering
add-on restart recovery checks.

It failed the full-host reboot gate: Engineering attempted read-gateway
admission before ha-mcp was ready, remained at 40 statically registered tools,
and required a manual Engineering add-on restart to reach 66. Separately,
`ha_search` could return top-level `partial: true` while Engineering reported
`metadata.completeness: complete`.

Do not retag, overwrite, redeploy, accept, promote, or use RC2dev12 as a
rollback target. Corrections are staged only for a new RC2dev13 candidate.
