"""HAMCP-255: the Core-authority reconciliation audit record stays whole.

v1 listed six fields per profile; at Engineering 2.4.1-beta.48 the record was
8972 characters and ``AuditLogger`` stored a ``payload_truncated`` stub, so no
per-profile disposition survived.  v2 is columnar: sorted vocabularies of
dispositions, reason codes and authority sources, and per capability the
indexes of its values (plus its profile id when that is not the compiled
convention).  These tests prove every profile's disposition and reason code is
reconstructable and that twice today's profile count, even with no two
profiles sharing an outcome, fits in three quarters of the unchanged
8192-character bound.
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

    columns = summary["dispositions"]
    rebuilt: dict[str, tuple] = {}
    for capability_id, entry in columns["profiles"].items():
        assert len(entry) in (3, 4), entry
        profile_id = (
            entry[3]
            if len(entry) == 4
            else capability_id.replace(".", "_") + "_v1"
        )
        rebuilt[capability_id] = (
            columns["disposition"][entry[0]],
            columns["reason_code"][entry[1]],
            columns["authority_source"][entry[2]],
            profile_id,
        )
    return rebuilt


def _encoded_size(event: dict) -> int:
    # What AuditLogger.sanitize measures against max_payload_chars.
    return len(json.dumps(event, default=str, sort_keys=True))


def _doubled(decisions) -> list:
    """Today's decisions plus a renamed copy of each (longer ids).

    Copies follow the compiled profile-id convention (profiles.py), as every
    compiled profile does today.
    """

    copies = [
        replace(
            item,
            capability_id=item.capability_id + "_successor",
            profile_id=(item.capability_id + "_successor").replace(".", "_")
            + "_v1",
        )
        for item in decisions
    ]
    return [*decisions, *copies]


def _one_outcome_per_profile(decisions) -> list:
    """No two profiles share a (disposition, reason, source) outcome."""

    return [
        replace(item, reason_code=f"{item.reason_code}_{index:02d}")
        for index, item in enumerate(decisions)
    ]


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

    def _assert_canonical(self, columns: dict) -> None:
        self.assertEqual(
            list(columns),
            ["disposition", "reason_code", "authority_source", "profiles"],
        )
        for name in ("disposition", "reason_code", "authority_source"):
            vocabulary = columns[name]
            with self.subTest(column=name):
                self.assertEqual(len(vocabulary), len(set(vocabulary)))
                self.assertEqual(vocabulary, sorted(vocabulary, key=str))
        self.assertEqual(list(columns["profiles"]), sorted(columns["profiles"]))
        # Every vocabulary entry is used: no stale or padding values.
        for position, name in enumerate(
            ("disposition", "reason_code", "authority_source")
        ):
            used = {entry[position] for entry in columns["profiles"].values()}
            self.assertEqual(used, set(range(len(columns[name]))))

    def test_encoding_is_deterministic_and_canonical(self):
        _runtime, first = _reconcile()
        _runtime, second = _reconcile()
        self.assertEqual(
            json.dumps(first["analysis_summary"]),
            json.dumps(second["analysis_summary"]),
        )
        columns = first["analysis_summary"]["dispositions"]
        self._assert_canonical(columns)
        self.assertEqual(columns["authority_source"], [None, "compiled_exact"])
        # Today's compiled profiles all follow the profile-id convention.
        self.assertTrue(
            all(len(entry) == 3 for entry in columns["profiles"].values())
        )

    def test_encoding_does_not_depend_on_decision_order(self):
        runtime, _event = _reconcile()
        decisions = list(runtime._coordinator.current_generation.decisions)
        for variant in (decisions, _one_outcome_per_profile(decisions)):
            expected = json.dumps(
                _event_for(variant)["analysis_summary"]["dispositions"]
            )
            self.assertGreater(len(json.loads(expected)["reason_code"]), 1)
            for order in (variant[::-1], variant[1::2] + variant[::2]):
                with self.subTest(first=order[0].capability_id):
                    actual = _event_for(order)["analysis_summary"]["dispositions"]
                    # json.dumps keeps key and list order; dict == does not.
                    self.assertEqual(json.dumps(actual), expected)
                    self._assert_canonical(actual)

    def test_unconventional_profile_id_is_carried_explicitly(self):
        runtime, _event = _reconcile()
        decisions = list(runtime._coordinator.current_generation.decisions)
        decisions[0] = replace(decisions[0], profile_id="reviewed_profile_v2")
        summary = _event_for(decisions)["analysis_summary"]
        entry = summary["dispositions"]["profiles"][decisions[0].capability_id]
        self.assertEqual(entry[3:], ["reviewed_profile_v2"])
        rebuilt = _reconstruct(summary)
        for item in decisions:
            self.assertEqual(rebuilt[item.capability_id][3], item.profile_id)

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
        self.assertEqual(stored, json.loads(json.dumps(event, default=str)))
        rebuilt = _reconstruct(stored["analysis_summary"])
        self.assertEqual(len(rebuilt), len(decisions))
        for item in decisions:
            self.assertEqual(
                rebuilt[item.capability_id],
                (
                    item.disposition.value,
                    item.reason_code,
                    item.authority_source.value
                    if item.authority_source is not None
                    else None,
                    item.profile_id,
                ),
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

    def test_twice_today_with_every_profile_in_its_own_group_fits(self):
        # Worst case for the vocabulary: no two profiles share an outcome,
        # so every profile carries its own reason code.
        decisions = _one_outcome_per_profile(_doubled(self._real_decisions()))
        event = _event_for(decisions)
        self.assertEqual(
            len(event["analysis_summary"]["dispositions"]["reason_code"]),
            len(decisions),
        )
        self._assert_fits(decisions)

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
