# Engineering 2.4.2-beta.1 release notes

Engineering 2.4.2-beta.1 (HAMCP-301, Option A) stops one rejected
`/api/states` record from holding every input_boolean and input_select plan
as `group_membership_unresolved`. It follows 2.4.1-rc.1 (HAMCP-302, #359).
If #361 (HAMCP-306) merges first and takes beta.1, this change restages once
to 2.4.2-beta.2.

Acceptance: [`V2_4_2_BETA1_ACCEPTANCE.md`](V2_4_2_BETA1_ACCEPTANCE.md).
Diagnosis: the HAMCP-301 diagnosis on
[PR #360](https://github.com/jeter-1/hass-mcp-admin/pull/360).

## The defect

The dependency snapshot rejects a state record whose id fails the
Engineering `valid_entity_id` rule, which requires a letter in the object id.
Home Assistant accepts digit-only object ids. On Josh's installation the
connectivity sensors `binary_sensor.1_1_1_1` and `binary_sensor.8_8_8_8` were
rejected (observed live by the architect, 2026-11-04). One rejected record
marked the whole expand snapshot incomplete, and then:

- every helper plan carried `group_membership_unresolved`
  (`group_membership_state_inventory_incomplete`), with the "Group membership
  keeps this plan elevated" warning and the coarse `reload:automation` fence;
- every `expand()` member absent from the snapshot became the coverage
  failure `expand_state_inventory_limit_exceeded`.

## The change

Each rejected state record is now attributed or counted. A rejected record
releases the group hold for a target only when all of these hold:

1. its `entity_id` matches Core's grammar (`homeassistant/core.py:180-183`,
   Core 2026.10.3);
2. its domain is not `group`, not `zone` and not the target's own domain;
   Core's `expand` (`helpers/template/extensions/state.py:145-185`) expands
   only `group` entities, group-sourced entities and `zone` entities
   (`persons`, `const.py:461`);
3. its attributes are a dict with no `entity_id` key. A rejected record cannot
   prove its source integration, so this stands in for a group-sourced entity.

Anything else still holds: a non-dict record or attributes, a non-string or
Core-invalid id, a `group.*` or `zone.*` record, a record in the target's
domain, a record with an `entity_id` attribute, a duplicate that fails the
rule in either copy, and a state list over the 10,000-record snapshot bound.

For `expand()`, a member absent from the snapshot is now
`expand_member_state_missing` (bounded semantic opaque) when every rejected
record is attributable. Otherwise it stays the coverage failure
`expand_state_inventory_limit_exceeded`. The rejection fingerprint is bound
into the expand resolution fingerprint only when the inventory is incomplete,
so complete-inventory fingerprints are unchanged.

The new evidence stores the attributed ids, a count of unattributable records
and a fingerprint. It stores no attribute value.

## Unchanged by design

- `valid_entity_id` itself, the obligation ledger, the template semantics
  registry, policy classes, exact-obligation handling, action profiles,
  coverage and bounded-source logic.
- Tools, input and output schemas, providers, signed profiles, persisted plan
  and task formats, add-on options and Home Assistant writes. Static tools
  stay at 80; the advertised catalog stays 108, or 109 with the optional YAML
  reader.
- A helper that really is a group member stays held, and `guest_mode` stays
  `safety_critical`.
- A helper with opaque dynamic consumers, such as `engineering_acceptance`,
  loses only the group code: it stays `elevated_admin` because of
  `opaque_downstream_obligations` (Options B and C are later work).

## Known limitations

- `valid_entity_id` still rejects Core-valid digit-only ids, so those
  entities are still not projected as expand entities or members.
- The `entity_id` attribute test is conservative: an ordinary rejected
  entity that carries an `entity_id` attribute still holds every helper.
- `entity_ids.STRICT_ENTITY_ID` and the `entity_filter.py` Core pin are
  labelled 2026.10.1; this release does not relabel them.

## Rollback

Reinstall 2.4.1-rc.1. Helper plans are held again as before. No persisted
format changes.
