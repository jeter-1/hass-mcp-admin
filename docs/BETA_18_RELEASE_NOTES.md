# Beta 18 release notes

HA MCP Engineering Server `2.0.0-beta.18` is a corrective, read-only release for
the shared entity-reference classifier used by configuration-integrity,
dependency, change-impact, and reliability analysis.

## Corrected behavior

Beta 17 promoted generic dotted tokens from some bracketed automation templates
into exact dependency edges. That made decimals and Jinja member access such as
`1.1`, `c.id`, and `ns.lines` appear as high-severity missing entities. Beta 18
removes the global dotted-token scan and requires both:

1. a trusted entity-bearing structured or Home Assistant template context; and
2. a canonical literal entity ID.

Explicit `entity_id` fields and bounded lists remain supported throughout
triggers, conditions, targets, nested choose/if/repeat structures, groups,
scenes, and supported blueprint inputs. Literal references remain supported for
`states`, `is_state`, `is_state_attr`, `state_attr`, and `expand`, plus literal
`states[...]` and `states.domain.object` access. Dynamic arguments remain
separate, target-free, limited-confidence evidence requiring manual review.

Decimals, versions, IP addresses, URLs, hostnames, service names, device and area
identifiers, filenames, package names, UUIDs, MAC addresses, object/member
access, template comments, quoted prose, and free-form messages no longer become
exact entity targets solely because they contain a period.

The correction occurs in the shared dependency-index extractor. Invalid edges
therefore disappear for every downstream consumer. A rebuilt index can have a
different edge count and fingerprint; this is expected. Finding IDs for retained
exact relationships remain deterministic, and discarded text never changes
totals, ordering, evidence IDs, or health aggregates.

## Compatibility and safety

- No MCP tool was added or removed.
- The runtime remains at 36 registered tools and 25 canonical tools.
- No public tool input schema, routing policy, pagination contract, health
  contract, or audit contract changed.
- No write capability, cleanup action, incident correlation, handoff generation,
  or RC work was added.
- Production v1.1.2, `hass_mcp_admin`, and port 8099 are unchanged.
- The beta slug remains `hass_mcp_engineering_beta` on port 8100.
- Connector recreation is not normally required because the tool manifest and
  schemas are unchanged.

Use the read-only deployed acceptance procedure in
[`CONFIGURATION_INTEGRITY_ANALYSIS.md`](CONFIGURATION_INTEGRITY_ANALYSIS.md).
