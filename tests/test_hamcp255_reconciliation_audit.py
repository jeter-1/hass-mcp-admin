"""HAMCP-255: the Core-authority reconciliation audit record stays whole.

v1 listed six fields per profile; at Engineering 2.4.1-beta.48 the record was
8972 characters and ``AuditLogger`` stored a ``payload_truncated`` stub, so no
per-profile disposition survived.  v2 groups profiles by disposition, reason
code and authority source.  These tests prove every profile's disposition and
reason code is reconstructable and that twice today's profile count still fits
the unchanged 8192-character bound with headroom.
"""

from __future__ import annotations

import asyncio
from dataclasses import replace
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "hass_mcp_engineering_beta"))
sys.path.insert(0, str(ROOT))

from ha_mcp_engineering.audit import AuditLogger  # noqa: E402
from ha_mcp_engineering.ha_core_readmission.models import (  # noqa: E402
    CoreAuthoritySource,
    CoreDisposition,
)
from ha_mcp_engineering.ha_core_readmission.profiles import (  # noqa: E402
    CORE_CAPABILITY_PROFILES,
)
from ha_mcp_engineering.ha_core_readmission.runtime import (  # noqa: E402
    RECONCILED_AUDIT_MODEL,
    CoreRuntime,
)

from tests.test_ha_core_2026_9_integration import (  # noqa: E402
    _MutableSource,
    _core_2026_9_snapshot,
)

AUDIT_BOUND = 8_192
# Twice today's profile count must leave at least a quarter of the bound.
HEADROOM_LIMIT = AUDIT_BOUND * 3 // 4


def _reconcile() -> tuple[CoreRuntime, dict]:
    events: list[dict] = []
    runtime = CoreRuntime()
    runtime.configure(
        object(),
        source=_MutableSource(_core_2026_9_snapshot()),
        audit_sink=lambda event: events.append(event) or True,
    )
    asyncio.run(runtime.reconcile_once("startup"))
    assert len(events) == 1
    return runtime, events[0]


def _reconstruct(summary: dict) -> dict[str, tuple]:
    """Each capability's (disposition, reason, source, profile) from v2 alone."""

    rebuilt: dict[str, tuple] = {}
    for group in summary["dispositions"]:
        for capability_id, profile_id in group["profiles"].items():
            assert capability_id not in rebuilt, capability_id
            rebuilt[capability_id] = (
                group["disposition"],
                group["reason_code"],
                group["authority_source"],
                profile_id,
            )
    return rebuilt


def _encoded_size(event: dict) -> int:
    # What AuditLogger.sanitize measures against max_payload_chars.
    return len(json.dumps(event, default=str, sort_keys=True))


def _doubled(decisions) -> list:
    """Today's decisions plus a renamed copy of each (longer ids)."""

    copies = [
        replace(
            item,
            capability_id=item.capability_id + "_successor",
            profile_id=item.profile_id + "_successor",
        )
        for item in decisions
    ]
    return [*decisions, *copies]


def _event_for(decisions) -> dict:
    observation = SimpleNamespace(version="2026.10.3", identity_agrees=True)
    generation = SimpleNamespace(
        decisions=tuple(decisions),
        generation=7,
        disposition=SimpleNamespace(value="partial"),
    )
    return CoreRuntime._audit_event_locked(
        trigger="startup", observation=observation, generation=generation
    )


class ReconstructionTests(unittest.TestCase):
    def test_every_profile_disposition_and_reason_is_reconstructable(self):
        runtime, event = _reconcile()
        summary = event["analysis_summary"]
        self.assertEqual(summary["model"], RECONCILED_AUDIT_MODEL)
        self.assertEqual(summary["model"], "ha-core-authority-audit-v2")
        self.assertNotIn("capabilities", summary)
        rebuilt = _reconstruct(summary)
        profiles = runtime.health_snapshot()["authority_profiles"]
        self.assertEqual(len(rebuilt), len(profiles))
        for profile in profiles:
            with self.subTest(capability=profile["capability_id"]):
                self.assertEqual(
                    rebuilt[profile["capability_id"]],
                    (
                        profile["disposition"],
                        profile["reason_code"],
                        profile["authority_source"],
                        profile["profile_id"],
                    ),
                )
        admitted = sum(
            1 for item in rebuilt.values() if item[0].startswith("admitted_")
        )
        self.assertEqual(summary["admitted_count"], admitted)
        self.assertEqual(summary["withheld_count"], len(rebuilt) - admitted)
        self.assertEqual(admitted, len(CORE_CAPABILITY_PROFILES))

    def test_groups_are_deterministic_and_distinct(self):
        _runtime, first = _reconcile()
        _runtime, second = _reconcile()
        self.assertEqual(first["analysis_summary"], second["analysis_summary"])
        groups = first["analysis_summary"]["dispositions"]
        keys = [
            (g["disposition"], g["reason_code"], g["authority_source"])
            for g in groups
        ]
        self.assertEqual(len(keys), len(set(keys)))
        self.assertEqual(
            keys, sorted(keys, key=lambda key: tuple(str(p) for p in key))
        )
        for group in groups:
            self.assertEqual(list(group["profiles"]), sorted(group["profiles"]))
            self.assertTrue(group["profiles"])

    def test_grouping_does_not_depend_on_decision_order(self):
        runtime, _event = _reconcile()
        decisions = list(runtime._coordinator.current_generation.decisions)
        expected = _event_for(decisions)["analysis_summary"]["dispositions"]
        self.assertGreater(len(expected), 1)
        for order in (decisions[::-1], decisions[1::2] + decisions[::2]):
            with self.subTest(first=order[0].capability_id):
                self.assertEqual(
                    _event_for(order)["analysis_summary"]["dispositions"], expected
                )
        keys = [
            (g["disposition"], g["reason_code"], g["authority_source"])
            for g in expected
        ]
        self.assertEqual(
            keys, sorted(keys, key=lambda key: tuple(str(p) for p in key))
        )
        for order in (decisions, decisions[::-1]):
            for group in _event_for(order)["analysis_summary"]["dispositions"]:
                self.assertEqual(list(group["profiles"]), sorted(group["profiles"]))

    def test_today_record_is_stored_whole_by_the_audit_logger(self):
        _runtime, event = _reconcile()
        with tempfile.TemporaryDirectory() as directory:
            logger = AuditLogger(str(Path(directory) / "audit.jsonl"), "s" * 32)
            self.assertEqual(logger.max_payload_chars, AUDIT_BOUND)
            stored = logger.sanitize(event)
        self.assertNotIn("payload_truncated", stored)
        self.assertEqual(
            _reconstruct(stored["analysis_summary"]),
            _reconstruct(event["analysis_summary"]),
        )


class CapacityTests(unittest.TestCase):
    def _real_decisions(self):
        runtime, _event = _reconcile()
        return runtime._coordinator.current_generation.decisions

    def _assert_fits(self, decisions) -> None:
        event = _event_for(decisions)
        size = _encoded_size(event)
        self.assertLessEqual(size, HEADROOM_LIMIT, size)
        with tempfile.TemporaryDirectory() as directory:
            logger = AuditLogger(str(Path(directory) / "audit.jsonl"), "s" * 32)
            stored = logger.sanitize(event)
        self.assertNotIn("payload_truncated", stored)
        rebuilt = _reconstruct(stored["analysis_summary"])
        self.assertEqual(len(rebuilt), len(decisions))
        for item in decisions:
            self.assertEqual(
                rebuilt[item.capability_id][:2],
                (item.disposition.value, item.reason_code),
            )

    def test_twice_today_profile_count_fits_with_headroom(self):
        runtime, _event = _reconcile()
        decisions = runtime._coordinator.current_generation.decisions
        self.assertEqual(
            len(decisions), len(runtime.health_snapshot()["authority_profiles"])
        )
        self.assertGreaterEqual(len(decisions), 30)
        self._assert_fits(_doubled(decisions))

    def test_twice_today_all_admitted_fits_with_headroom(self):
        admitted = [
            replace(
                item,
                disposition=CoreDisposition.ADMITTED_EXACT,
                reason_code="compiled_exact_contract_matched",
                authority_source=CoreAuthoritySource.COMPILED_EXACT,
                adapter_id=item.adapter_id or "compiled-adapter-v1",
            )
            for item in self._real_decisions()
        ]
        self._assert_fits(_doubled(admitted))

    def test_today_with_every_profile_in_its_own_group_fits(self):
        # Degenerate grouping: no two profiles share an outcome.  Grouping
        # only saves space when outcomes repeat, so this bounds today's
        # count; the doubled tests above use the real outcome vocabulary.
        decisions = [
            replace(item, reason_code=f"{item.reason_code}_{index:02d}")
            for index, item in enumerate(self._real_decisions())
        ]
        event = _event_for(decisions)
        self.assertEqual(
            len(event["analysis_summary"]["dispositions"]), len(decisions)
        )
        size = _encoded_size(event)
        self.assertLessEqual(size, AUDIT_BOUND, size)

    def test_v1_shape_would_not_have_fit(self):
        # Regression anchor: the per-profile v1 list exceeded the bound.
        decisions = self._real_decisions()
        v1 = [
            {
                "capability_id": item.capability_id,
                "disposition": "admitted_exact",
                "reason_code": "compiled_exact_contract_matched",
                "authority_source": "compiled_exact",
                "profile_id": item.profile_id,
                "adapter_id": item.adapter_id or "compiled-adapter-v1",
            }
            for item in decisions
        ]
        self.assertGreater(len(json.dumps(v1, sort_keys=True)), AUDIT_BOUND)


if __name__ == "__main__":
    unittest.main()
