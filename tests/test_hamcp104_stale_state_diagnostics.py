"""HAMCP-104 Slice A: precise stale-state pre-dispatch refusal reasons.

Reporting only.  Each code below was already stored by an adapter on the
child's durable terminal event but read as
``unclassified_pre_dispatch_refusal``; it now names a precise reason with an
existing check.  Every refusal still happens before dispatch with zero
attempts.  The dashboard adapter now reports a changed dashboard apart from
provider drift, and the helper-options adapter reports a config entry that
moved to another integration domain as an identity change.

Compatibility is proven against the exact shipped reasons module: removing
the one HAMCP-104 block from the current source must reproduce, byte for
byte, the module blob shipped in v2.4.2-beta.6 (identical at the
release/2.6.0 base), and that shipped code then writes and reads the records
the tests compare.  No fixture is reconstructed by hand.
"""

from __future__ import annotations

from dataclasses import replace
import hashlib
import json
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "hass_mcp_engineering_beta"), str(ROOT / "tests")]

from ha_mcp_engineering.f3_configuration.adapter import (  # noqa: E402
    ConfigurationOperationAdapter,
)
from ha_mcp_engineering.f3_configuration.helper_options import (  # noqa: E402
    HelperOptionsAdapter,
)
from ha_mcp_engineering.f3_dashboard import adapter as dashboard_adapter  # noqa: E402
from ha_mcp_engineering.f3_dashboard.identity import (  # noqa: E402
    build_operational_identity,
)
from ha_mcp_engineering.f3_runtime import pre_dispatch_reasons as reasons  # noqa: E402
from ha_mcp_engineering.governance.normalize import stable_hash  # noqa: E402
from tests import test_hamcp249_pre_dispatch_reasons as t249  # noqa: E402
from tests import test_hamcp177_dashboard_write_admission as t177  # noqa: E402
from tests.hamcp232_helper_options_fixtures import (  # noqa: E402
    HelperOptionsServiceTestCase,
)
from tests.test_hamcp232_helper_options import TIME_CONSTANT_20  # noqa: E402
from tests.test_hamcp285_evidence_labels import InputBooleanCase  # noqa: E402

ENGINEERING = ROOT / "hass_mcp_engineering_beta" / "ha_mcp_engineering"

# The reasons module blob shipped in v2.4.2-beta.6 (commit af5ec837) and
# unchanged at the release/2.6.0 base 7edddf38:
#   git rev-parse v2.4.2-beta.6:hass_mcp_engineering_beta/ha_mcp_engineering/f3_runtime/pre_dispatch_reasons.py
SHIPPED_REASONS_BLOB = "872e6a5f8cdf929ea9204688af484b7d7197a9cd"
BLOCK_START = "    # HAMCP-104: stale-state and baseline codes adapters already store that\n"
BLOCK_END = '    ("dashboard_provider_contract_changed", "provider_admission"),\n'

# New code -> (check, the source file that stores the literal).
NEW_REASONS = {
    "delete_target_missing": ("target_state", "f3_configuration/delete.py"),
    "config_entry_missing": ("target_state", "f3_configuration/helper_options.py"),
    "config_entry_not_loaded": ("target_state", "f3_configuration/helper_options.py"),
    "entry_entities_changed": ("target_state", "f3_configuration/helper_options.py"),
    "options_schema_changed": ("target_state", "f3_configuration/helper_options.py"),
    "option_not_available": ("target_state", "f3/operational_strategies.py"),
    "stale_dashboard_state": ("target_state", "f3_dashboard/adapter.py"),
    "stale_baseline": ("target_state", "f3/operational_strategies.py"),
    "dependency_risk_drift": ("target_state", "f3/operational_strategies.py"),
    "current_state_binding_mismatch": (
        "plan_binding", "f3_configuration/helper_options.py",
    ),
    "label_namespace_unavailable": ("provider_read", "governance/resources.py"),
    "dashboard_provider_contract_changed": (
        "provider_admission", "f3_dashboard/adapter.py",
    ),
}
UMBRELLA = "stale_or_provider_contract_mismatch"
IDENTITY = "resource_identity_mismatch"
STALE = "stale_target_state"


def _git_blob_sha(text: str) -> str:
    data = text.encode("utf-8")
    return hashlib.sha1(b"blob %d\0" % len(data) + data).hexdigest()


def shipped_reasons() -> SimpleNamespace:
    """Execute the exact shipped reasons module (proven by its blob hash)."""

    source = Path(reasons.__file__).read_text(encoding="utf-8").replace("\r\n", "\n")
    assert source.count(BLOCK_START) == 1 and source.count(BLOCK_END) == 1
    start = source.index(BLOCK_START)
    end = source.index(BLOCK_END) + len(BLOCK_END)
    shipped = source[:start] + source[end:]
    if _git_blob_sha(shipped) != SHIPPED_REASONS_BLOB:
        raise AssertionError(
            "pre_dispatch_reasons.py changed outside the HAMCP-104 block; "
            "update this derivation of the shipped module"
        )
    namespace: dict = {"__name__": "shipped_pre_dispatch_reasons"}
    exec(compile(shipped, "shipped_pre_dispatch_reasons", "exec"), namespace)
    return SimpleNamespace(**namespace)


DECLARATION = {
    "child_id": "child_hamcp104",
    "operation_id": "op_hamcp104",
    "operation_ordinal": 0,
    "target_type": "automation",
    "target_id": "hamcp104_target",
}


class ShippedVocabularyCompatibilityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.shipped = shipped_reasons()

    def test_new_codes_classify_precisely_and_were_unclassified_before(self):
        for code, (check, _source) in NEW_REASONS.items():
            with self.subTest(code=code):
                self.assertEqual(reasons.classify((code,)), (code, check, None))
                self.assertIn(check, reasons._CHECK_DETAILS)
                self.assertEqual(
                    self.shipped.classify((code,)),
                    (reasons.UNCLASSIFIED, "adapter_preflight", None),
                )
        added = set(reasons.REASON_CHECKS) - set(self.shipped.REASON_CHECKS)
        self.assertEqual(added, set(NEW_REASONS))

    def test_no_shipped_code_check_or_detail_changes(self):
        for code, check in self.shipped.REASON_CHECKS.items():
            self.assertEqual(reasons.REASON_CHECKS[code], check, code)
        self.assertEqual(reasons._CHECK_DETAILS, self.shipped._CHECK_DETAILS)
        self.assertEqual(reasons.REASON_FIELDS, self.shipped.REASON_FIELDS)
        self.assertEqual(reasons.REASON_SCHEMA_VERSION, 1)
        self.assertLessEqual(self.shipped.REASON_CODES, reasons.REASON_CODES)
        # The shipped priority order is the prefix of the current one.
        self.assertEqual(
            reasons._REASONS[: len(self.shipped._REASONS)], self.shipped._REASONS
        )

    def test_every_shipped_precise_classification_is_unchanged(self):
        shipped_codes = [code for code, _check in self.shipped._REASONS]
        new_codes = list(NEW_REASONS)
        samples = [(code,) for code in shipped_codes]
        samples += [(old, new) for old in shipped_codes for new in new_codes]
        samples += [(old, *new_codes) for old in shipped_codes]
        samples += [(old, "fallback_none", UMBRELLA) for old in shipped_codes]
        for codes in samples:
            ordered = tuple(sorted(codes))
            with self.subTest(codes=ordered):
                self.assertEqual(
                    reasons.classify(ordered), self.shipped.classify(ordered)
                )

    def test_lock_and_plan_binding_codes_still_outrank_the_new_codes(self):
        for winner, check in (
            ("lock_storage_failure", "lock_storage"),
            ("lock_conflict", "lock_acquisition"),
            ("proposed_hash_mismatch", "plan_binding"),
            ("plan_expired", "plan_expiry"),
            ("provider_not_admitted", "provider_admission"),
            (STALE, "target_state"),
            (IDENTITY, "target_state"),
        ):
            for code in NEW_REASONS:
                with self.subTest(winner=winner, code=code):
                    self.assertEqual(
                        reasons.classify(tuple(sorted((winner, code))))[:2],
                        (winner, check),
                    )

    def test_records_the_shipped_code_wrote_read_back_unchanged(self):
        for code in sorted(self.shipped.REASON_CODES):
            with self.subTest(code=code):
                if code == self.shipped.UNKNOWN_LEGACY:
                    written = self.shipped.unknown_legacy(DECLARATION)
                elif code == self.shipped.UNCLASSIFIED:
                    written = self.shipped.build_reason(
                        codes=("stale_baseline",), declaration=DECLARATION,
                        normalized_outcome="preflight_rejected",
                    )
                else:
                    written = self.shipped.build_reason(
                        codes=(code,), declaration=DECLARATION,
                        normalized_outcome="preflight_rejected",
                    )
                stored = json.loads(json.dumps(written))
                self.assertEqual(stored["error_code"], code)
                self.assertEqual(reasons.recorded(stored), stored)
                self.assertEqual(reasons.log_reason(stored), stored)
        # A stored unclassified record is never re-derived into a new code.
        legacy = self.shipped.build_reason(
            codes=("stale_baseline",), declaration=DECLARATION,
            normalized_outcome="preflight_rejected",
        )
        self.assertEqual(
            reasons.recorded(legacy)["error_code"], reasons.UNCLASSIFIED
        )

    def test_a_downgraded_reader_falls_back_without_crashing(self):
        for code in NEW_REASONS:
            with self.subTest(code=code):
                written = json.loads(json.dumps(reasons.build_reason(
                    codes=(code, "fallback_none"), declaration=DECLARATION,
                    normalized_outcome="preflight_rejected",
                )))
                self.assertEqual(written["reason_schema_version"], 1)
                self.assertEqual(sorted(written), sorted(self.shipped.REASON_FIELDS))
                # Shipped reader: the stored reason is out of its vocabulary,
                # so the runtime re-derives from the child codes, which read
                # as unclassified -- the shipped behavior for these codes.
                self.assertIsNone(self.shipped.recorded(written))
                self.assertEqual(
                    self.shipped.build_reason(
                        codes=tuple(written["diagnostic_codes"]),
                        declaration=DECLARATION,
                        normalized_outcome="preflight_rejected",
                    )["error_code"],
                    reasons.UNCLASSIFIED,
                )
                # The shipped log reader keeps it: the check is a fixed one.
                self.assertEqual(self.shipped.log_reason(written), written)

    def test_new_codes_are_literals_their_adapters_store(self):
        for code, (_check, relative) in NEW_REASONS.items():
            with self.subTest(code=code):
                text = (ENGINEERING / relative).read_text(encoding="utf-8")
                self.assertIn(f'"{code}"', text)

    def test_unknown_codes_still_never_fabricate_a_reason(self):
        for codes in (("some_future_code",), (UMBRELLA, "fallback_none"),
                      ("stale_fp_expected_0123456789ab",)):
            with self.subTest(codes=codes):
                self.assertEqual(
                    reasons.classify(tuple(sorted(codes))),
                    (reasons.UNCLASSIFIED, "adapter_preflight", None),
                )


def _preflight_refuses(code):
    """The configuration adapter refuses in preflight with ``code`` alone."""

    async def refuse(adapter, operation, *, acquired_locks):
        del acquired_locks
        return adapter._preflight_rejected(operation, (code,))

    return patch.object(ConfigurationOperationAdapter, "preflight", refuse)


SURFACE_CASES = tuple(
    t249._Case(
        path=f"HAMCP-104 preflight refusal {code}",
        arrange=(lambda test, code=code: _preflight_refuses(code)),
        code=code, check=check, outcome="preflight_rejected",
    )
    for code, (check, _source) in NEW_REASONS.items()
)


class PreciseReasonSurfaceTests(t249.PreDispatchReasonSurfaceTests):
    """The HAMCP-249 surface matrix, run for every HAMCP-104 code.

    Zero writes, ``failed_pre_dispatch``, and the precise reason on the apply
    result, stored task, child, task view, lifecycle event, log, audit and a
    repeated apply.  Only the matrix test runs here; the inherited ones run
    in their own module.
    """

    async def test_every_refusal_path_names_its_reason_on_every_surface(self):
        with patch.object(t249, "REFUSAL_CASES", SURFACE_CASES):
            await super().test_every_refusal_path_names_its_reason_on_every_surface()


for _name in dir(t249.PreDispatchReasonSurfaceTests):
    if _name.startswith("test_") and _name != (
        "test_every_refusal_path_names_its_reason_on_every_surface"
    ):
        setattr(PreciseReasonSurfaceTests, _name, None)


def _evidence_drifts(*, compatibility=False, target=False):
    """Seam: the real preread, then provider or identity drift on its evidence.

    A real provider version change is refused earlier (see the test below),
    so the comparison's provider-drift branch is reached through this seam.
    """

    original = dashboard_adapter.build_raw_dashboard_evidence

    def drifted(preread, *, requested_url_path):
        evidence = original(preread, requested_url_path=requested_url_path)
        changes = {}
        if compatibility:
            changes["compatibility_entry"] = "synthetic-other-compatibility-entry"
        if target:
            identity = evidence.operational_identity
            changes["operational_identity"] = build_operational_identity(
                identity.authority,
                target_url_path="synthetic-other-dashboard",
                storage_mode=identity.storage_mode,
                baseline_upstream_config_hash=identity.baseline_upstream_config_hash,
                baseline_engineering_sha256=identity.baseline_engineering_sha256,
            )
        return replace(evidence, **changes)

    return patch.object(dashboard_adapter, "build_raw_dashboard_evidence", drifted)


class DashboardStaleVersusProviderDriftTests(t177.Harness):
    def capture_rejections(self):
        original = dashboard_adapter.DashboardUpdateAdapter._preflight_rejection
        captured = []

        def spy(*args, **kwargs):
            result = original(*args, **kwargs)
            captured.append(result)
            return result

        self.enterContext(patch.object(
            dashboard_adapter.DashboardUpdateAdapter, "_preflight_rejection",
            staticmethod(spy),
        ))
        return captured

    async def refused(self, change, name, seam=None):
        peer = t177.DashboardPeer(t177.catalog(t177.EXTRAS))
        _p, service, runtime, created = await self.planned(peer, name)
        change(peer)
        captured = self.capture_rejections()
        if seam is None:
            result = await service.apply(created["plan_id"], created["plan_hash"])
        else:
            with seam:
                result = await service.apply(created["plan_id"], created["plan_hash"])
        self.assertEqual(result["task_state"], "failed_pre_dispatch", result)
        child = self.child(runtime, result["task_id"])
        self.assertEqual(child["normalized_outcome"], "preflight_rejected")
        self.assertTrue(child["terminal"])
        self.assertIsNone(child["dispatch_intent"])
        self.assertEqual(child["dispatch_count"], 0)
        self.assertNotIn(t177.SETTER, peer.names())
        self.assertNotEqual(peer.configuration.get("title"), "After")
        self.balanced()
        codes = self.diagnostics(child)
        # The former umbrella code and the no-fallback code are kept, and the
        # preflight evidence hash is the one the shipped code computed.
        self.assertIn(UMBRELLA, codes)
        self.assertIn("fallback_none", codes)
        (rejection,) = captured
        self.assertEqual(rejection.evidence_hash, stable_hash({"category": UMBRELLA}))
        self.assertFalse(rejection.eligible)
        reason = result["pre_dispatch_refusal"]
        stored = service.task_repository.get(result["task_id"])
        self.assertEqual(stored.verification_summary["pre_dispatch_refusal"], reason)
        self.assertEqual(stored.last_error["error_code"], reason["error_code"])
        self.assertIn(reason["error_code"], rejection.diagnostic_codes)
        for text in (json.dumps(reason), json.dumps(self.audit(name))):
            self.assertNotIn(t177.SECRET, text)
            self.assertNotIn("synthetic-other", text)
        return reason, codes

    @staticmethod
    def edit_dashboard(peer):
        peer.configuration = {"title": t177.SECRET, "views": []}

    async def test_a_changed_dashboard_is_stale_dashboard_state(self):
        reason, codes = await self.refused(self.edit_dashboard, "stale")
        self.assertEqual(
            (reason["error_code"], reason["check"]),
            ("stale_dashboard_state", "target_state"),
        )
        self.assertNotIn("dashboard_provider_contract_changed", codes)

    async def test_compatibility_drift_is_provider_contract_drift(self):
        reason, codes = await self.refused(
            lambda peer: None, "compat", _evidence_drifts(compatibility=True)
        )
        self.assertEqual(
            (reason["error_code"], reason["check"]),
            ("dashboard_provider_contract_changed", "provider_admission"),
        )
        self.assertNotIn("stale_dashboard_state", codes)

    async def test_operational_identity_drift_is_provider_contract_drift(self):
        reason, codes = await self.refused(
            lambda peer: None, "identity", _evidence_drifts(target=True)
        )
        self.assertEqual(reason["error_code"], "dashboard_provider_contract_changed")
        self.assertNotIn("stale_dashboard_state", codes)

    async def test_identity_drift_beside_a_changed_dashboard_is_provider_drift(self):
        reason, codes = await self.refused(
            self.edit_dashboard, "both", _evidence_drifts(target=True)
        )
        self.assertEqual(reason["error_code"], "dashboard_provider_contract_changed")
        self.assertNotIn("stale_dashboard_state", codes)

    async def test_provider_drift_outranks_a_changed_dashboard(self):
        reason, codes = await self.refused(
            self.edit_dashboard, "compat-both", _evidence_drifts(compatibility=True)
        )
        self.assertEqual(reason["error_code"], "dashboard_provider_contract_changed")
        self.assertNotIn("stale_dashboard_state", codes)

    async def test_a_real_version_change_is_still_refused_earlier_unchanged(self):
        peer = t177.DashboardPeer(t177.catalog(t177.EXTRAS))
        _p, service, runtime, created = await self.planned(peer, "version")
        peer.version = "8.6.0"
        result = await service.apply(created["plan_id"], created["plan_hash"])
        self.assertEqual(result["task_state"], "failed_pre_dispatch", result)
        self.assertNotIn(t177.SETTER, peer.names())
        codes = self.diagnostics(self.child(runtime, result["task_id"]))
        self.assertEqual(
            codes, {"dashboard_provider_unavailable_or_unreviewed", "fallback_none"}
        )
        self.balanced()

    async def test_an_unchanged_dashboard_still_applies(self):
        peer = t177.DashboardPeer(t177.catalog(t177.EXTRAS))
        _p, service, _r, created = await self.planned(peer, "fresh")
        result = await service.apply(created["plan_id"], created["plan_hash"])
        self.assertEqual(result["task_state"], "succeeded_verified", result)
        self.assertEqual(peer.names().count(t177.SETTER), 1)
        self.assertEqual(peer.configuration["title"], "After")


class _CountingStaleRejections:
    def count_stale_rejections(self):
        original = ConfigurationOperationAdapter._increment
        counted = []

        def spy(adapter, name, *args, **kwargs):
            if name == "stale_rejections":
                counted.append(name)
            return original(adapter, name, *args, **kwargs)

        self.enterContext(patch.object(ConfigurationOperationAdapter, "_increment", spy))
        return counted


class HelperOptionsIdentityChangeTests(_CountingStaleRejections, HelperOptionsServiceTestCase):
    ENTRY = "filterentry01"

    def assert_refused(self, result, code, absent):
        self.assertEqual(result["task_state"], "failed_pre_dispatch", result)
        self.assertFalse(result["provider_dispatch_occurred"])
        self.assertEqual(self.options_rest.submits(), [])
        self.assertEqual(self.options_rest.flows, {}, "no flow left open")
        codes = self.child_codes(result["task_id"])
        self.assertIn(code, codes)
        self.assertNotIn(absent, codes)
        reason = result["pre_dispatch_refusal"]
        self.assertEqual((reason["error_code"], reason["check"]), (code, "target_state"))
        task = self.service.task_repository.get(result["task_id"])
        self.assertEqual(task.last_error["error_code"], code)
        return reason

    async def test_a_domain_change_before_apply_is_an_identity_change(self):
        self.add_filter()
        created = await self.approved_options(TIME_CONSTANT_20)
        opens = len(self.options_rest.opens())
        counted = self.count_stale_rejections()
        self.ws.config_entries[self.ENTRY]["domain"] = "statistics"
        result = await self.apply(created)
        self.assert_refused(result, IDENTITY, STALE)
        self.assertEqual(len(self.options_rest.opens()), opens, "preflight opens no flow")
        self.assertEqual(len(counted), 1)

    async def test_a_modified_at_change_stays_stale_target_state(self):
        self.add_filter()
        created = await self.approved_options(TIME_CONSTANT_20)
        counted = self.count_stale_rejections()
        self.options_rest.touch(self.ENTRY)
        result = await self.apply(created)
        self.assert_refused(result, STALE, IDENTITY)
        self.assertEqual(len(counted), 1)

    async def test_a_domain_change_inside_the_held_window_is_an_identity_change(self):
        self.add_filter()
        created = await self.approved_options(TIME_CONSTANT_20)
        planning_opens = len(self.options_rest.opens())

        def move(entry_id, flow_id, ordinal):
            if ordinal == planning_opens + 1:
                self.ws.config_entries[entry_id]["domain"] = "statistics"

        self.options_rest.after_open = move
        counted = self.count_stale_rejections()
        result = await self.apply(created)
        self.assert_refused(result, IDENTITY, STALE)
        self.assertEqual(len(self.options_rest.opens()), planning_opens + 1)
        self.assertEqual(len(self.options_rest.aborts()), len(self.options_rest.opens()),
                         "every opened flow, the held one included, is aborted")
        self.assertEqual(len(counted), 1)

    async def test_an_unchanged_entry_still_applies(self):
        self.add_filter()
        created = await self.approved_options(TIME_CONSTANT_20)
        result = await self.apply(created)
        self.assertEqual(result["task_state"], "succeeded_verified", result)
        self.assertEqual(len(self.options_rest.submits()), 1)


class OperationalStaleBaselineTests(InputBooleanCase):
    async def test_a_stale_baseline_names_its_precise_reason(self):
        plan = (await self.planned("on"))["plan"]
        await self.grant(plan)
        self.helper.set_observed_state("off", self.clock)
        result = await self.apply(plan)
        await self.assert_refused_before_dispatch(result, "stale_baseline")
        reason = result["pre_dispatch_refusal"]
        self.assertEqual(
            (reason["error_code"], reason["check"]), ("stale_baseline", "target_state")
        )
        self.assertEqual(reason["reason_source"], "child_terminal_event")


if __name__ == "__main__":
    unittest.main()
