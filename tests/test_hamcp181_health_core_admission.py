"""HAMCP-181 PR2 item 7: health ``core_admission`` reflects the admitted generation.

The four signed-registry providers (entity metadata, configuration delete,
availability/exposure and label registry) reported a static pending reason
even after a signed generation admitted their profiles.  Health now reads the
local admitted generation through ``core_runtime.route_status`` -- no Core
call -- and reports ``admitted`` only when that read says every listed
profile is available.  Anything else (no runtime, unadmitted, partial,
non-boolean or a failing read) keeps the provider's pending reason, and
health never decides admission.

Every runtime is a local stub or a TEST-signed registry with no network.
"""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "hass_mcp_engineering_beta"), str(ROOT)]

import tests.test_core_2026_10_device_registry as october  # noqa: E402
import tests.test_hamcp155_configuration_delete_admission as delete_admission  # noqa: E402
from ha_mcp_engineering.governance import configuration_delete, label_registry  # noqa: E402
from ha_mcp_engineering.governance import helper_options as helper_options_contract  # noqa: E402
from ha_mcp_engineering.governance import input_select_option as input_select_contract  # noqa: E402
from ha_mcp_engineering.ha_core_readmission.routes import SCRIPT_TRACE_PROFILE  # noqa: E402
from ha_mcp_engineering.governance.service import ChangeGovernanceService  # noqa: E402
from ha_mcp_engineering.governance.storage import ChangePlanRepository  # noqa: E402


# health key -> (profiles that health reads, pending reason)
PROVIDERS = {
    "entity_metadata_provider": (
        ("core.entity_registry_metadata_operation",),
        "owner_signed_reference_required",
    ),
    "configuration_delete_provider": (
        (configuration_delete.PROFILE,),
        configuration_delete.PENDING_ADMISSION_REASON,
    ),
    "entity_availability_exposure_provider": (
        (
            "core.entity_registry_availability_operation",
            "core.entity_assistant_exposure_operation",
        ),
        "pending_signed_registry_admission",
    ),
    "label_registry_provider": (
        (label_registry.CORE_CAPABILITY,),
        label_registry.PENDING_ADMISSION_REASON,
    ),
    # HAMCP-232/145: three dormant providers join the same snapshot.
    "input_select_provider": (
        (input_select_contract.PROFILE,),
        input_select_contract.PENDING_ADMISSION_REASON,
    ),
    "script_trace_provider": (
        (SCRIPT_TRACE_PROFILE,),
        "pending_signed_registry_admission",
    ),
    "helper_options_provider": (
        (helper_options_contract.PROFILE,),
        helper_options_contract.PENDING_ADMISSION_REASON,
    ),
}
# The script-trace provider backs two tools (list_script_traces and
# get_script_trace); the snapshot reads its profile once per tool surface.
SURFACE_READS = (
    *(profiles for profiles, _ in PROVIDERS.values()),
    PROVIDERS["script_trace_provider"][0],
)


class _LocalCore:
    """Answers ``route_status`` from a local admitted set; nothing else exists."""

    def __init__(self, admitted=(), *, answer=None, error=None):
        self.admitted = set(admitted)
        self.answer = answer
        self.error = error
        self.calls: list[tuple[str, ...]] = []

    def route_status(self, capability_ids, **kwargs):
        self.calls.append(tuple(capability_ids))
        if self.error is not None:
            raise self.error
        if self.answer is not None:
            return self.answer
        return {
            "available": bool(capability_ids)
            and all(item in self.admitted for item in capability_ids)
        }

    def __getattr__(self, name):
        # Any other Core runtime use (acquire, a probe, a call) is a defect.
        raise AssertionError(f"health used core_runtime.{name}")


def _runtime(**fields):
    """A runtime stand-in: health reads only its summary and ``core_runtime``."""

    return SimpleNamespace(health=lambda: {}, **fields)


class HealthCoreAdmissionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.service = ChangeGovernanceService(
            ChangePlanRepository(Path(self.temp.name)), object()
        )

    def providers(self, core=None):
        self.service.f3_runtime = (
            None if core is None else _runtime(core_runtime=core)
        )
        operational = self.service.health_summary()["operational_administration"]
        return {key: operational[key] for key in PROVIDERS}

    def assert_admission(self, providers, expected):
        self.assertEqual(
            {key: value["core_admission"] for key, value in providers.items()},
            expected,
        )

    def pending(self):
        return {key: pending for key, (_, pending) in PROVIDERS.items()}

    def test_without_a_runtime_every_provider_stays_pending(self):
        self.assert_admission(self.providers(), self.pending())

    def test_an_admitted_generation_reports_admitted(self):
        every = {item for profiles, _ in PROVIDERS.values() for item in profiles}
        core = _LocalCore(every)
        self.assert_admission(
            self.providers(core), {key: "admitted" for key in PROVIDERS}
        )
        # Each provider reads exactly its own profiles.
        self.assertEqual(sorted(core.calls), sorted(SURFACE_READS))

    def test_admission_is_per_provider(self):
        core = _LocalCore((configuration_delete.PROFILE,))
        expected = self.pending()
        expected["configuration_delete_provider"] = "admitted"
        self.assert_admission(self.providers(core), expected)

    def test_a_partially_admitted_pair_stays_pending(self):
        # Availability is admitted, exposure is not: the provider is pending.
        core = _LocalCore(("core.entity_registry_availability_operation",))
        self.assertEqual(
            self.providers(core)["entity_availability_exposure_provider"]["core_admission"],
            "pending_signed_registry_admission",
        )

    def test_only_a_boolean_true_reads_as_admitted(self):
        for answer in ({"available": "true"}, {"available": 1}, {}, {"available": None}):
            with self.subTest(answer=answer):
                self.assert_admission(
                    self.providers(_LocalCore(answer=answer)), self.pending()
                )

    def test_a_failing_read_reports_pending_and_health_still_answers(self):
        core = _LocalCore(error=RuntimeError("synthetic generation read failure"))
        providers = self.providers(core)
        self.assert_admission(providers, self.pending())
        for provider in providers.values():
            self.assertEqual(provider["fallback"], "none")
            self.assertEqual(provider["fallback_count"], 0)

    def test_a_runtime_without_core_reports_pending(self):
        self.service.f3_runtime = _runtime()
        operational = self.service.health_summary()["operational_administration"]
        self.assert_admission(
            {key: operational[key] for key in PROVIDERS}, self.pending()
        )


class SignedRegistryHealthAdmissionTests(unittest.IsolatedAsyncioTestCase):
    """The real Core runtime: a TEST-signed reference admits the delete profile."""

    asyncSetUp = october.OctoberSignedSelectionTests.asyncSetUp
    publish = october.OctoberSignedSelectionTests.publish
    asyncTearDown = delete_admission.SignedTestRegistryAdmissionTests.asyncTearDown

    def delete_admission(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        service = ChangeGovernanceService(ChangePlanRepository(Path(temp.name)), object())
        service.f3_runtime = _runtime(core_runtime=self.core)
        return service.health_summary()["operational_administration"][
            "configuration_delete_provider"
        ]["core_admission"]

    async def test_the_shipped_entry_keeps_delete_pending(self):
        await self.publish()
        self.assertEqual(self.delete_admission(), configuration_delete.PENDING_ADMISSION_REASON)

    async def test_a_signed_reference_shows_delete_admitted(self):
        entry = deepcopy(self.entry)
        entry["capabilities"] = [*self.entry["capabilities"], *delete_admission.references()]
        await self.publish(entry)
        self.assertEqual(self.delete_admission(), "admitted")

    async def test_a_tampered_reference_keeps_delete_pending(self):
        entry = deepcopy(self.entry)
        entry["capabilities"] = [*self.entry["capabilities"], *delete_admission.references()]
        for item in entry["capabilities"]:
            if item["capability_id"] == configuration_delete.PROFILE:
                item["contract_fingerprint"] = "sha256:" + "0" * 64
        await self.publish(entry)
        self.assertEqual(self.delete_admission(), configuration_delete.PENDING_ADMISSION_REASON)


if __name__ == "__main__":
    unittest.main()
