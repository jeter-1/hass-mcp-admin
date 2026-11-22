"""HAMCP-371 S13: the Q8 pre-SR equality check.

Q8 ruling (07:40, amended): "At S13, before SR, add a check that the 9.2.0
registry row and attestation equal 9.1.2's in disposition and attestation
state. No quarantined 9.2.0 state may reach the S16 push."

HAMCP-437: only 9.2.0 is admitted (D1) and the 9.1.2 row goes in I2a-D, so the
check no longer reads 9.1.2. It compares 9.2.0 with the 9.1.2 values frozen
below, copied from the committed registry and attestation documents at
release/2.9.0 d24f0ec3, where 9.2.0 met this check.

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

CANDIDATE = "9.2.0"
# The 9.1.2 values Q8 compared against, frozen at d24f0ec3.
BASE_DISPOSITIONS = {"backup": "held", "dashboard": "admitted", "lifecycle": "held", "read_gateway": "admitted"}
BASE_STATUS = "reviewed"
BASE_ATTESTATION = {
    "contract_family": "ha_mcp_dashboard_read_v3",
    "input_contract_fingerprint": "1ea37859cf37426edf61bbe5987b1f144af74f1e6f2da8c43a02de1b06e24bc6",
    "output_contract_fingerprint": "9db14b83579f9ec897cc6d97640a3ad67899055b57d18c815c4119192a04dbb7",
    "raw_input_schema_fingerprint": "3d5eb7f07319fc0007f535ef316091a11c15863262f3cb6b03ae279474dfdca7",
    "runtime_contract_fingerprint": "78b74a6c193af3de32730c878e094250ad3ec5a304724529d917d1195e8783d9",
    "security_contract_fingerprint": "e9770b0461efed6a85e0527fc301578ec0d3069f3725b9884373d4216d119cd7",
    "revoked": False,
}
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
    if CANDIDATE not in rows:
        problems.append(f"registry row {CANDIDATE} missing")
    if CANDIDATE not in entries:
        problems.append(f"attestation entry {CANDIDATE} missing")
    if problems:
        return problems
    candidate = rows[CANDIDATE]
    expected_dispositions = {**BASE_DISPOSITIONS, **CANDIDATE_DISPOSITION_OVERRIDES}
    if candidate.get("provider_dispositions") != expected_dispositions:
        problems.append("provider_dispositions differ from 9.1.2")
    status = (candidate.get("dashboard_attestation") or {}).get("status")
    if status != BASE_STATUS:
        problems.append(f"dashboard_attestation.status {status!r} is not 9.1.2's {BASE_STATUS!r}")
    if status != "reviewed":
        problems.append(f"dashboard_attestation.status {status!r} is not 'reviewed'")
    if (candidate.get("dashboard_attestation") or {}).get("entry_id") != entries[CANDIDATE].get("entry_id"):
        problems.append("registry attestation entry_id does not name the built-in 9.2.0 entry")
    for field in ATTESTATION_EQUAL_FIELDS:
        if entries[CANDIDATE].get(field) != BASE_ATTESTATION[field]:
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
