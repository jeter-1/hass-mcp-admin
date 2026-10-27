# Engineering 2.4.1-beta.40 release notes

Engineering 2.4.1-beta.40 adds HAMCP-226 (named bounded sources and
unavailable entity references in the integrity, dependency and helper scans)
and HAMCP-228 (a durable automation baseline store, resume of timed-out
records, and `compare_automation_baselines`). It follows 2.4.1-beta.39
(HAMCP-225, PR #315) and includes it. It adds no Home Assistant write,
dependency, Dockerfile, add-on configuration, workflow, profile, fingerprint
or signed-registry change.

Acceptance: [`V2_4_1_BETA40_ACCEPTANCE.md`](V2_4_1_BETA40_ACCEPTANCE.md).
Design and owner decisions S, T, B and P:
[`HAMCP_225_226_228_AUDIT_TOOLING_DESIGN.md`](HAMCP_225_226_228_AUDIT_TOOLING_DESIGN.md).

## HAMCP-226: named bounds and unavailable references

- `configuration_integrity_analysis` adds the `unavailable_entity_reference`
  finding (medium for an enabled source, low for a disabled one; an unknown
  state is low or info).
- The integrity, dependency and helper scans name each automation, blueprint
  or script that reached a bound (at most 50, each with the bound it hit) in
  `bounded_sources`. Read failures are not bounds; an unknown code is
  `unclassified`. The helper `bounded_sources` is persisted in new plans
  outside `evidence_fingerprint`.
- `entity_dependency_analysis` adds `target_availability`.
- Home Assistant ids and names in these outputs pass the default-mode
  credential screen (`untrusted_text.screen`: `credentials.screen` in default
  mode plus the PEM private-key header check, which default mode did not
  include). A withheld name keeps its finding with a marker; a withheld
  source id fails closed as a coverage failure. Long template values are
  screened over the shown part plus a 1,024-character margin before any cut,
  and a hit withholds the whole value.
  See [`CONFIGURATION_INTEGRITY_ANALYSIS.md`](CONFIGURATION_INTEGRITY_ANALYSIS.md)
  and [`ENTITY_DEPENDENCY_ANALYSIS.md`](ENTITY_DEPENDENCY_ANALYSIS.md).

## HAMCP-228: durable baselines, resume and compare

See [`AUTOMATION_BASELINE_CAPTURE.md`](AUTOMATION_BASELINE_CAPTURE.md).

- **Store.** Each finished `capture_automation_baseline` is committed to
  `<governance_path>/audit-baselines-v1/`: 12 baselines, 2 MiB each, 180 days,
  bound to the capturing caller, digest-verified on every read, refused at
  capacity (`store_full` with the oldest age), never evicted. Only the
  validated report is stored, never a configuration body. Continuations page
  the stored copy. The capture tool is now Engineering local evidence capture
  (`readOnlyHint=false`); it still writes nothing to Home Assistant.
- **Schema v2.** Captures write `automation-audit-baseline-v2`: a `resume`
  header and the `timed_out` status for records never attempted before the
  deadline reserve. v1 still loads (the compatibility fixture was written by
  the shipped beta.37 code).
- **Resume.** `resume_capture_id` re-collects only a stored capture's
  timeouts into a new baseline. It refuses `resume_not_needed` before any
  Home Assistant read and `resume_inventory_changed` when the installation,
  authority generation, declared count or ordered mapping changed.
- **Compare.** New `compare_automation_baselines`: two stored captures
  through the existing comparator, or one stored capture against recorded
  fingerprints. A 16..63-character matching prefix is `PREFIX_MATCH`
  (`prefix_match:<n>`), counted separately from `UNCHANGED`; an id on one
  side only is `UNKNOWN`. No Home Assistant read; the output is
  credential-screened and bounded.
- New capture page fields: `snapshot.storage` is
  `durable_engineering_store`, plus `retention_seconds` and `resumed_from`.
  New capture reasons: `store_full`, `store_unavailable`,
  `baseline_not_found`, `baseline_damaged`, `resume_inventory_changed`,
  `resume_not_needed`.

## Counts

Static tools 73 (beta.39's 72 plus `compare_automation_baselines`);
advertised catalog 101 (102 with the optional YAML reader).
