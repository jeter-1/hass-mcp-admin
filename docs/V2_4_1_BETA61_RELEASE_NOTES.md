# Engineering 2.4.1-beta.61 release notes

Engineering 2.4.1-beta.61 (HAMCP-253) lets an administrator see why one exact
stuck governance record is stuck and acknowledge it. Health then warns only on
unacknowledged records. Records are never deleted or rewritten, and nothing in
Home Assistant changes. It builds on 2.4.1-beta.60. 2.4.1-beta.61 is held by
draft #353 (HAMCP-285). Whichever of the two merges first keeps a contested
number, and the other restages.

Acceptance: [`V2_4_1_BETA61_ACCEPTANCE.md`](V2_4_1_BETA61_ACCEPTANCE.md).
Design: [`HAMCP_253_STUCK_RECORD_ACKNOWLEDGEMENT_DESIGN.md`](HAMCP_253_STUCK_RECORD_ACKNOWLEDGEMENT_DESIGN.md).

## The change

- **Inspect (new read-only MCP tool).** `inspect_governance_record(record_kind,
  record_id)` returns bounded metadata for one exact change plan or execution
  task, including plans that `get_change_plan` refuses because they fail
  projection verification. The metadata is:
  - kind, ID, plan or task type, status and timestamps;
  - policy class, approval state and bundle state;
  - failure reason code and category;
  - the ordered approval events and every event row, bounded to 64 with
    truncation reported;
  - the acknowledgement state and a record fingerprint.

  It never returns payloads, proposed configuration, targets, notes,
  principals, secrets or approver tokens. It reports explicitly whatever
  cannot be read (`readable: false`, `unreadable_fields`).
- **Acknowledge (Ingress only).** On the approval panel, the new **Stuck
  records** page lists the records. From a record's page, an administrator
  records one decision:
  - acknowledge as `historical_record` or `current_defect`, with a reason of
    at most 500 characters; or
  - revoke an earlier acknowledgement.

  The decision binds the fingerprint the administrator reviewed. It is
  appended to Engineering's own acknowledgement ledger and takes effect
  only once its audit event is durably written; with the audit log disabled
  or failing, it is refused. An identical repeat is a no-op, a
  reclassification keeps history, and a revoke is a superseding entry that
  brings the warning back. The Engineering MCP connector exposes no
  acknowledgement tool; an authenticated administrator browser session on
  Ingress can acknowledge. Inspection and the ledger make no Home Assistant
  call and no provider dispatch; the Ingress gate's read-only
  `config/auth/list` role lookup is unchanged.
- **Health.** `governance.record_acknowledgement` reports, for
  projection-failed plans and manual-review tasks separately:
  - `unacknowledged`, `acknowledged_historical`,
    `acknowledged_current_defect` and `stale` counts;
  - ID lists of at most 8 each.

  `projection_failure_warning` and the new `manual_review_warning` appear
  only while unacknowledged records exist. Acknowledged current defects stay
  visible as information. Raw counts are unchanged.

## Unchanged by design

- Integrity and projection verification, startup validation, and the
  approve, apply and recovery paths. An acknowledged failed plan is refused
  exactly as before.
- Persisted plan and task formats. The acknowledgement ledger is a new,
  separate file.
- Existing tool schemas, providers, signed profiles, fingerprints and add-on
  options.

Static tools go from 79 to 80. The advertised catalog is 108, or 109 with
the optional YAML reader.

## Known limitations

- An acknowledgement binds the exact metadata reviewed. If the record changes
  later, the acknowledgement becomes `stale` and the record warns again.
- Prohibited contract-v3 operational plans with a family-specific creation
  event fail projection as `approval_sequence_inconsistent`. Live plan
  `4cf748d1…` is one example. That is a projection-verification defect,
  reported as a follow-up and not fixed here.

## Rollback

Reinstall 2.4.1-beta.60. The ledger file is left in place and ignored, and
health warns on every stuck record again. No plan or task record changes.
