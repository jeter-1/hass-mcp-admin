"""The storage-boundary credential screen for a baseline report (HAMCP-226/228 review 2, R1).

Every baseline record and every capture diagnostic is screened here, after
collection and before the report is hashed (``artifact_sha256``) or stored,
so no unscreened Home Assistant text reaches disk. The store screens the
report once more before it writes (``store.put``), and the tool's
final-envelope screen stays as a backstop behind both.

What a withheld value costs (owner decision, Josh, 2026-10-27):

- an optional field (a diagnostic, or a record field other than the
  automation configuration id) is withheld whole and the record and the
  capture are partial, never refused;
- the required identity field, the automation configuration id, withholds
  its record as a content-free stub with a reason, and the capture
  continues, partial;
- the baseline header holds only fixed, validated fields, which a marker
  cannot represent, so a hit there refuses the capture (``redacted_identity``)
  before anything is hashed or stored.

Nothing is derived from a withheld value (owner decision, 2026-10-22): a stub
names its reason and, only when it is itself clean, the entity id, never a
digest of the withheld text.
"""

from .. import untrusted_text
from . import capture_contracts as c
from .validation import BaselineValidationError, _new_baseline, _record

LISTED_STUBS = 16
STUB_REASONS = frozenset({"configuration_id_withheld", "entity_id_withheld", "record_unrepresentable"})
SUMMARY_KEYS = frozenset({"withheld_field_count", "withheld_record_count", "withheld_records"})


def summary(stubs=(), fields=0):
    """The ``diagnostics.credential_screen`` summary: counts plus the first stubs."""

    stubs = list(stubs)
    return {"withheld_field_count": fields, "withheld_record_count": len(stubs),
            "withheld_records": stubs[:LISTED_STUBS]}


def stub(configuration_id, entity_id, reason):
    return {"configuration_id": configuration_id, "entity_id": entity_id, "reason": reason}


def _clean(value, known):
    if type(value) is not str or value in untrusted_text.MARKERS:
        return None
    return value if untrusted_text.screen(value, known) is None else None


def _tree(value, known):
    """``(copy, withheld count)``; a withheld leaf becomes the marker, a withheld key drops its entry."""

    if isinstance(value, str):
        if value in untrusted_text.MARKERS:
            return value, 0
        marker = untrusted_text.screen(value, known)
        return (value, 0) if marker is None else (marker, 1)
    if isinstance(value, dict):
        output, count = {}, 0
        for key, item in value.items():
            if isinstance(key, str) and (key in untrusted_text.MARKERS
                                         or untrusted_text.screen(key, known) is not None):
                count += 1
                continue
            output[key], hits = _tree(item, known)
            count += hits
        return output, count
    if isinstance(value, (list, tuple)):
        items, count = [], 0
        for item in value:
            screened, hits = _tree(item, known)
            items.append(screened)
            count += hits
        return items, count
    return value, 0


def _hits(value, known):
    try:
        return untrusted_text.screen_envelope(value, known)
    except untrusted_text.EnvelopeKeyWithheld:
        return None, None


def _partial_record(row, known, schema):
    """The record with its optional fields withheld and marked partial, or ``None``."""

    screened, hits = _hits(row, known)
    if hits is None:
        return None, 0
    try:
        entity = row["entity_id"]
        if entity is not None and _clean(entity, known) is None:
            # The entity id is nullable: withheld, so the mapping is unverified.
            screened["entity_id"] = None
            screened["mapping_status"] = "missing"
        coverage = screened["configuration"]["coverage"]
        coverage["redacted"] = True
        coverage["warning_count"] += 1
        if coverage["completeness"] == "complete":
            coverage["completeness"] = "partial"
        _record(screened, schema)
    except (BaselineValidationError, KeyError, TypeError, AttributeError):
        return None, 0
    return screened, hits


def _prior_summary(value):
    if (type(value) is not dict or set(value) != SUMMARY_KEYS
            or any(type(value[key]) is not int or value[key] < 0
                   for key in ("withheld_field_count", "withheld_record_count"))
            or type(value["withheld_records"]) is not list):
        return [], 0, 0
    stubs = [item for item in value["withheld_records"]
             if type(item) is dict and set(item) == {"configuration_id", "entity_id", "reason"}
             and item["reason"] in STUB_REASONS]
    return stubs, value["withheld_record_count"], value["withheld_field_count"]


def screen_report(report, known_secrets=()):
    """The report to hash and store, every record and diagnostic screened."""

    known = tuple(item for item in known_secrets if isinstance(item, str) and item)
    baseline, diagnostics = report["baseline"], report["diagnostics"]
    header = {key: value for key, value in baseline.items() if key != "records"}
    if _hits(header, known)[1] != 0:
        raise c.CaptureError("redacted_identity")
    records, stubs, removed, fields = [], [], set(), 0
    for row in baseline["records"]:
        if _hits(row, known)[1] == 0:
            records.append(row)
            continue
        identifier = row.get("configuration_id") if type(row) is dict else None
        configuration_id = _clean(identifier, known)
        entity = _clean(row.get("entity_id"), known) if type(row) is dict else None
        screened, hits = (None, 0) if configuration_id is None else _partial_record(row, known, baseline["schema"])
        if screened is None:
            # The required identity, or a field the record cannot represent:
            # the record becomes a content-free stub.
            stubs.append(stub(configuration_id, entity, "configuration_id_withheld" if configuration_id is None
                              else "record_unrepresentable"))
            if type(identifier) is str:
                removed.add(identifier)
            continue
        records.append(screened)
        fields += hits
    outcomes = diagnostics["record_outcomes"]
    outcomes = {key: value for key, value in outcomes.items() if key not in removed}
    outcomes, hits = _tree(outcomes, known)
    fields += hits
    rest, hits = _tree({key: value for key, value in diagnostics.items() if key != "record_outcomes"}, known)
    fields += hits
    prior, prior_records, prior_fields = _prior_summary(rest.pop("credential_screen", None))
    screen = {"withheld_field_count": prior_fields + fields, "withheld_record_count": prior_records + len(stubs),
              "withheld_records": (prior + stubs)[:LISTED_STUBS]}
    baseline = dict(baseline, records=records)
    if stubs:
        inventory = dict(baseline["inventory"], completeness="partial")
        inventory["omitted_count"] = (inventory["omitted_count"] or 0) + len(stubs)
        baseline["inventory"] = inventory
    try:
        _new_baseline(baseline)
    except BaselineValidationError:
        raise c.CaptureError("redacted_identity") from None
    return {"baseline": baseline, "diagnostics": dict(rest, record_outcomes=outcomes, credential_screen=screen)}


def withheld_counts(diagnostics):
    """``(records, fields)`` withheld by the screens before storage."""

    _, records, fields = _prior_summary(diagnostics.get("credential_screen") if type(diagnostics) is dict else None)
    return records, fields
