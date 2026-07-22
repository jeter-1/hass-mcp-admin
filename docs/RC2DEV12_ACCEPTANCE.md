# RC2dev12 rejected acceptance record

Version: `2.0.0-rc2-dev12`
Status: Immutable historical release; historical only and cannot authorize work

This file records a failed acceptance conclusion. It is not an acceptance
procedure, is not active authority, and cannot authorize release, deployment,
promotion, rollback, or reuse of RC2dev12.

## Recorded conclusion

RC2dev12 passed its smoke, delegated-read, interruption/recovery, persistence,
prewarm, dashboard admission, and manual add-on restart checks. It failed the
full Home Assistant host reboot recovery gate because the one startup admission
attempt occurred before ha-mcp readiness and did not recover automatically from
40 to 66 tools. A manual Engineering restart was required.

The independently confirmed `ha_search` semantic-completeness defect also
remained: upstream `partial: true` could be reported as Engineering
`metadata.completeness: complete`.

The accurate historical result is therefore: smoke and controlled-interruption
testing passed; full-host reboot acceptance failed; semantic completeness was
incorrect. RC2dev12 was not accepted.

## Immutable disposition

Preserve tag `v2.0.0-rc2-dev12`, release commit
`430b0aaf6bf7f9e5e55b701b277c636ea34e5014`, image digest
`sha256:a55b4a85aa47efa4c2b4f628a7d50cc4f0b3d8710bedbbdb254e23e633d28ca4`,
and all failure evidence unchanged. Never substitute this record for the exact
RC2dev13 acceptance procedure.
