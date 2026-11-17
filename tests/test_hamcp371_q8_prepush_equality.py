"""HAMCP-371 S13: the Q8 pre-SR equality check.

Q8 ruling (07:40, amended): "At S13, before SR, add a check that the 9.2.0
registry row and attestation equal 9.1.2's in disposition and attestation
state. No quarantined 9.2.0 state may reach the S16 push."

The check reads the committed JSON documents directly, so a hand edit that
the loader would still accept cannot pass unseen:

* the registry rows: ``provider_dispositions`` equal except that HAMCP-401
  Phase 1 admits 9.2.0 ``lifecycle`` (architect ruling 2026-11-16), and
  ``dashboard_attestation.status`` equal and ``reviewed``;
* the built-in dashboard attestation entries: present for both versions, not
  revoked, and the contract fingerprints (input, output, runtime, security,
  raw input schema) and contract family equal;
* no 9.2.0 registry or attestation value is ``quarantined`` anywhere.

The negative cases run the same check on edited copies, so the check is shown
to refuse the state it exists to keep out.
"""
from __future__ import annotations

import json
import unittest
from copy import deepcopy
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ROOT / "hass_mcp_engineering_beta" / "ha_mcp_engineering"
REGISTRY_PATH = PACKAGE / "upstream_release_registry.json"
ATTESTATIONS_PATH = PACKAGE / "providers" / "contracts" / "upstream_dashboard_builtin_attestations.json"

BASE, CANDIDATE = "9.1.2", "9.2.0"
# HAMCP-401 Phase 1: the only 9.2.0 disposition that differs from 9.1.2's.
CANDIDATE_DISPOSITION_OVERRIDES = {"lifecycle": "admitted"}
ATTESTATION_EQUAL_FIELDS = (
    "contract_family",
    "input_contract_fingerprint",
    "output_contract_fingerprint",
    "raw_input_schema_fingerprint",
    "runtime_contract_fingerprint",
    "security_contract_fingerprint",
    "revoked",
)


def _contains(value, needle: str) -> bool:
    if isinstance(value, dict):
        return any(_contains(v, needle) for v in value.values())
    if isinstance(value, list):
        return any(_contains(v, needle) for v in value)
    return value == needle


def q8_problems(registry: dict, attestations: dict) -> list[str]:
    """Every way the committed state breaks the Q8 S13 condition."""

    problems: list[str] = []
    rows = {r.get("version"): r for r in registry.get("releases", ())}
    entries = {e.get("upstream_version"): e for e in attestations.get("entries", ())}
    for version in (BASE, CANDIDATE):
        if version not in rows:
            problems.append(f"registry row {version} missing")
        if version not in entries:
            problems.append(f"attestation entry {version} missing")
    if problems:
        return problems
    base, candidate = rows[BASE], rows[CANDIDATE]
    expected_dispositions = {
        **(base.get("provider_dispositions") or {}),
        **CANDIDATE_DISPOSITION_OVERRIDES,
    }
    if candidate.get("provider_dispositions") != expected_dispositions:
        problems.append("provider_dispositions differ from 9.1.2")
    base_status = (base.get("dashboard_attestation") or {}).get("status")
    status = (candidate.get("dashboard_attestation") or {}).get("status")
    if status != base_status:
        problems.append(f"dashboard_attestation.status {status!r} is not 9.1.2's {base_status!r}")
    if status != "reviewed":
        problems.append(f"dashboard_attestation.status {status!r} is not 'reviewed'")
    if (candidate.get("dashboard_attestation") or {}).get("entry_id") != entries[CANDIDATE].get("entry_id"):
        problems.append("registry attestation entry_id does not name the built-in 9.2.0 entry")
    for field in ATTESTATION_EQUAL_FIELDS:
        if entries[CANDIDATE].get(field) != entries[BASE].get(field):
            problems.append(f"attestation {field} differs from 9.1.2")
    if _contains(candidate, "quarantined"):
        problems.append("9.2.0 registry row holds a quarantined value")
    if _contains(entries[CANDIDATE], "quarantined"):
        problems.append("9.2.0 attestation entry holds a quarantined value")
    return problems


def _load():
    return (json.loads(REGISTRY_PATH.read_text(encoding="utf-8")),
            json.loads(ATTESTATIONS_PATH.read_text(encoding="utf-8")))


def _row(document: dict, version: str) -> dict:
    return next(r for r in document["releases"] if r["version"] == version)


def _entry(document: dict, version: str) -> dict:
    return next(e for e in document["entries"] if e["upstream_version"] == version)


class Q8PrePushEquality(unittest.TestCase):
    def test_committed_state_meets_q8(self):
        registry, attestations = _load()
        self.assertEqual(q8_problems(registry, attestations), [])

    def test_quarantined_attestation_is_refused(self):
        registry, attestations = _load()
        edited = deepcopy(registry)
        _row(edited, CANDIDATE)["dashboard_attestation"]["status"] = "quarantined"
        problems = q8_problems(edited, attestations)
        self.assertIn("dashboard_attestation.status 'quarantined' is not 9.1.2's 'reviewed'", problems)
        self.assertIn("9.2.0 registry row holds a quarantined value", problems)

    def test_changed_disposition_is_refused(self):
        registry, attestations = _load()
        for provider, value in (("dashboard", "held"), ("backup", "admitted"), ("lifecycle", "held")):
            with self.subTest(provider=provider):
                edited = deepcopy(registry)
                _row(edited, CANDIDATE)["provider_dispositions"][provider] = value
                self.assertIn("provider_dispositions differ from 9.1.2", q8_problems(edited, attestations))

    def test_changed_or_revoked_attestation_is_refused(self):
        registry, attestations = _load()
        for field, value in (("revoked", True), ("security_contract_fingerprint", "0" * 64)):
            with self.subTest(field=field):
                edited = deepcopy(attestations)
                _entry(edited, CANDIDATE)[field] = value
                self.assertIn(f"attestation {field} differs from 9.1.2", q8_problems(registry, edited))

    def test_missing_rows_are_refused(self):
        registry, attestations = _load()
        edited = deepcopy(registry)
        edited["releases"] = [r for r in edited["releases"] if r["version"] != CANDIDATE]
        self.assertEqual(q8_problems(edited, attestations), ["registry row 9.2.0 missing"])
        edited = deepcopy(attestations)
        edited["entries"] = [e for e in edited["entries"] if e["upstream_version"] != CANDIDATE]
        self.assertEqual(q8_problems(registry, edited), ["attestation entry 9.2.0 missing"])


if __name__ == "__main__":
    unittest.main()
