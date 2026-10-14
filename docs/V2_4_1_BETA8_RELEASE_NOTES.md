# Engineering 2.4.1-beta.8 release notes

Engineering 2.4.1-beta.8 repairs governed dashboard writes (HAMCP-177) on top
of beta.7. With ha-mcp's three filesystem tools enabled (77 to 80 advertised
tools: `ha_config_get_yaml`, `ha_list_files` and `ha_read_file`), the
dashboard provider's write authority used whole-catalog admission. Dashboard
reads still succeeded, but they carried no write authority. As a result,
`create_dashboard_update_plan` refused, and the best-practices and setter
exchanges refused `reviewed_contract_mismatch`, even though none of the tools
that dashboard writes use had changed. This release applies the beta.6 power
and fan design to dashboard writes, so unrelated catalog additions no longer
disable them.

**F1, dependency-scoped admission.** Dashboard write authority is now admitted
on exactly the three upstream tools that the governed write path names:
- `ha_config_get_dashboard`: preread, inventory and readback;
- `ha_get_skill_guide`: strict best-practices acknowledgement;
- `ha_config_set_dashboard`: the fixed setter.

Each tool must appear exactly once and match every reviewed contract
component: input schema, description, annotations, output schema, title,
runtime descriptor and policy classification. The following checks are
unchanged:
- server identity, exact release and protocol;
- the reviewed dashboard attestation entry and fingerprints;
- revocation and HOLD;
- Core authority;
- same-session revalidation before each exchange.

The release must also carry the `admitted` dashboard provider disposition;
this explicit check is new, so a held disposition refuses even when the
attestation fields are present.
Any other advertised tool grants nothing and is never exposed or dispatched;
the provider still refuses any name outside its fixed tools. The authority
evidence names the reviewed release, so it is identical for the 77- and
80-tool catalogs, and nothing persisted changes. The whole-catalog validator
itself is unchanged and still gates the generic read gateway, backups and
lifecycle evidence.

**F2, truthful refusal.**
- `create_dashboard_update_plan` now refuses with non-retryable
  `provider_unavailable` (reason `dashboard_provider_not_admitted`) when the
  write dependencies are not admitted. Previously, getter drift was reported
  as retryable `operational_provider_unavailable`, and setter or guide drift
  as `configuration_validation_failed` (`invalid_raw_dashboard_evidence`).
  Other provider failures keep their earlier retryable outcome.
- During apply, an admission refusal is still `failed_pre_dispatch` with no
  dispatch intent.
- Grouped and direct MCP SDK refusals keep their typed, non-retryable category.
- Real transport loss, timeouts and cancellation keep their earlier
  semantics, including the uncertain outcome after a recorded dispatch intent.

**F3, Core authority after admission.** Core dispatch authority is checked
immediately before the setter's single `tools/call`. Before this release, a
refusal at that point after the setter handshake was recorded as possibly
dispatched, and the task waited in `observing`. It is now recorded as
`dispatch_failed_confirmed`, and the task ends `failed_post_dispatch` without
any write. A refusal raised before the call is the only error that may claim
no dispatch; every other transport error stays conservatively dispatched.

Telemetry and task pointers needed no change: dashboard write and
best-practices exchanges were already counted and finished on every path, and
no task pointer is returned before the task is saved. One minor read-path gap
is known and unchanged: the dashboard read retry after a signed-registry
refresh counts two sessions as one attempt. It is adjacent to signed-registry
code, which is out of scope.

No tool, public schema or description, routing, signed registry, HOLD,
support window, persisted format, workflow or stable-v1 change is included.
The healthy catalog remains **87 tools: 59 native and 28 delegated reads**.
After deployment, Josh may re-enable ha-mcp's filesystem tools as a separate
owner step (see the acceptance contract). They stay unexposed through
Engineering.

Downgrade: beta.7 reads every beta.8 record, and nothing new is persisted. A
downgraded server returns to whole-catalog dashboard write admission, which
refuses dashboard writes while an unreviewed tool is advertised, so disable
the filesystem tools before a downgrade.

Follow the [beta.8 acceptance contract](V2_4_1_BETA8_ACCEPTANCE.md).
