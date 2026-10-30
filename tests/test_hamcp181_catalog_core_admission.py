"""HAMCP-181 PR2 review 1 (M2): the catalog and health agree on Core admission.

``list_capabilities`` published a static ``pending_signed_registry_admission``
(and requirement-only wording for entity metadata) while health reported the
current admission.  Each governed tool's static catalog entry now states only
its policy requirement (``core_admission_requirement``) and, unevaluated,
``core_admission: not_evaluated``; the tool projects the current admission
from the governance service's one admission snapshot, the snapshot health
projects, read from a single Core generation.

Each case evaluates health and the real ``list_capabilities`` tool together
against the same local admitted generation: admitted, refused, partially
admitted, a withdrawal, a generation that moves during the read, a failing
read and no governance service.  The last class uses the real Core runtime
with a TEST-signed registry.  Every runtime is local; nothing is sent.
"""

from __future__ import annotations

import asyncio
from copy import deepcopy
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "hass_mcp_engineering_beta"), str(ROOT)]

import tests.test_core_2026_10_device_registry as october  # noqa: E402
import tests.test_hamcp155_configuration_delete_admission as delete_admission  # noqa: E402
from ha_mcp_engineering.capabilities import (  # noqa: E402
    CORE_ADMISSION_NOT_EVALUATED,
    build_capability_catalog,
    capability_for_tool,
)
from ha_mcp_engineering.governance import configuration_delete  # noqa: E402
from ha_mcp_engineering.governance.service import ChangeGovernanceService  # noqa: E402
from ha_mcp_engineering.governance.storage import ChangePlanRepository  # noqa: E402
from ha_mcp_engineering.health import HEALTH  # noqa: E402
from ha_mcp_engineering.tools import compatibility  # noqa: E402

from tests.test_hamcp181_health_core_admission import PROVIDERS, _runtime  # noqa: E402

# health provider key -> the catalog tool that projects the same admission
TOOLS = {
    "entity_metadata_provider": "create_entity_metadata_plan",
    "configuration_delete_provider": "create_configuration_delete_plan",
    "entity_availability_exposure_provider": "create_entity_availability_exposure_plan",
    "label_registry_provider": "create_label_plan",
    # HAMCP-232/145: the three dormant providers project through the same snapshot.
    "input_select_provider": "create_input_select_option_plan",
    "script_trace_provider": "list_script_traces",
    "helper_options_provider": "create_helper_options_plan",
    # HAMCP-106: the dormant dashboard metadata provider, same snapshot.
    "dashboard_metadata_provider": "create_dashboard_metadata_plan",
}
# The script-trace provider backs a second tool, which must agree with the first.
SECOND_SURFACES = {"get_script_trace": "script_trace_provider"}
GOVERNED_TOOLS = (*TOOLS.values(), *SECOND_SURFACES)
EVERY_PROFILE = {item for profiles, _ in PROVIDERS.values() for item in profiles}
PENDING = {key: pending for key, (_, pending) in PROVIDERS.items()}


class _GenerationCore:
    """Answers ``route_status`` from one local admitted generation."""

    def __init__(self, admitted=(), *, generation=1, moving=0, error=None):
        self.admitted = set(admitted)
        self.generation = generation
        self.moving = moving
        self.error = error
        self.calls = 0

    def withdraw(self, *profiles):
        self.admitted -= set(profiles)
        self.generation += 1

    def route_status(self, capability_ids, **_):
        self.calls += 1
        if self.error is not None:
            raise self.error
        generation = self.generation
        if self.moving:
            # The generation advances between two group reads.
            self.moving -= 1
            self.generation += 1
        return {
            # As CoreRuntime: no admitted generation decides nothing.
            "available": generation is not None
            and bool(capability_ids)
            and all(item in self.admitted for item in capability_ids),
            "generation": generation,
        }

    def __getattr__(self, name):
        raise AssertionError(f"admission display used core_runtime.{name}")


def _service(temp):
    return ChangeGovernanceService(ChangePlanRepository(Path(temp)), object())


def health_and_catalog(service):
    """Health and the real list_capabilities tool, evaluated together."""

    operational = service.health_summary()["operational_administration"]
    health = {key: operational[key]["core_admission"] for key in PROVIDERS}
    with mock.patch.object(HEALTH, "governance", SimpleNamespace(service=service)):
        result = json.loads(
            asyncio.run(compatibility.list_capabilities(detail_section="beta_native"))
        )
    assert result["success"], result
    catalog = result["data"]
    by_tool = {item["tool"]: item for item in catalog["beta_native"]}
    tools = {key: by_tool[tool]["core_admission"] for key, tool in TOOLS.items()}
    second = {tool: by_tool[tool]["core_admission"] for tool in SECOND_SURFACES}
    return {
        "health": health,
        "health_generation": operational["core_admission_generation"],
        "catalog": tools,
        "catalog_second_surfaces": second,
        "catalog_admission": catalog["core_admission"],
        "requirements": {
            tool: by_tool[tool]["core_admission_requirement"] for tool in GOVERNED_TOOLS
        },
    }


class CatalogHealthAgreementTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.service = _service(temp.name)

    def evaluate(self, core):
        self.service.f3_runtime = _runtime(core_runtime=core)
        return health_and_catalog(self.service)

    def assert_agree(self, views, expected, generation, status="current"):
        self.assertEqual(views["health"], expected)
        self.assertEqual(views["catalog"], expected)
        self.assertEqual(
            views["catalog_second_surfaces"],
            {tool: expected[key] for tool, key in SECOND_SURFACES.items()},
        )
        self.assertEqual(views["health_generation"], generation)
        self.assertEqual(
            views["catalog_admission"], {"status": status, "generation": generation}
        )
        # The requirement is policy, never the current status.
        self.assertEqual(
            set(views["requirements"].values()), {"signed_core_registry_admission"}
        )

    def test_an_admitted_generation_is_admitted_in_both(self):
        self.assert_agree(
            self.evaluate(_GenerationCore(EVERY_PROFILE, generation=7)),
            {key: "admitted" for key in PROVIDERS},
            7,
        )

    def test_a_refused_generation_is_pending_in_both(self):
        self.assert_agree(self.evaluate(_GenerationCore((), generation=8)), PENDING, 8)

    def test_a_partially_admitted_generation_agrees_per_group(self):
        # Deletion admitted; only availability of the 069B pair admitted.
        core = _GenerationCore(
            (configuration_delete.PROFILE, "core.entity_registry_availability_operation"),
            generation=9,
        )
        expected = dict(PENDING)
        expected["configuration_delete_provider"] = "admitted"
        self.assert_agree(self.evaluate(core), expected, 9)

    def test_each_069b_type_alone_keeps_the_pair_pending(self):
        for profile in (
            "core.entity_registry_availability_operation",
            "core.entity_assistant_exposure_operation",
        ):
            with self.subTest(profile=profile):
                views = self.evaluate(_GenerationCore((profile,), generation=3))
                self.assert_agree(views, PENDING, 3)

    def test_a_withdrawal_moves_both_together(self):
        core = _GenerationCore(EVERY_PROFILE, generation=10)
        self.assert_agree(self.evaluate(core), {key: "admitted" for key in PROVIDERS}, 10)
        core.withdraw("core.label_registry_operation", "core.entity_registry_metadata_operation")
        expected = {key: "admitted" for key in PROVIDERS}
        expected["label_registry_provider"] = PENDING["label_registry_provider"]
        expected["entity_metadata_provider"] = PENDING["entity_metadata_provider"]
        self.assert_agree(self.evaluate(core), expected, 11)

    def test_a_generation_that_moves_once_is_read_again(self):
        core = _GenerationCore(EVERY_PROFILE, generation=20, moving=1)
        views = self.evaluate(core)
        # The first read straddled two generations; each later read is whole.
        self.assertEqual(views["health_generation"], 21)
        self.assert_agree(views, {key: "admitted" for key in PROVIDERS}, 21)

    def test_a_generation_that_never_settles_reports_pending(self):
        core = _GenerationCore(EVERY_PROFILE, generation=30, moving=10_000)
        self.assert_agree(self.evaluate(core), PENDING, None, status="generation_unsettled")

    def test_a_failing_read_is_pending_in_both(self):
        core = _GenerationCore(EVERY_PROFILE, error=RuntimeError("synthetic read failure"))
        self.assert_agree(self.evaluate(core), PENDING, None, status="no_admitted_generation")

    def test_no_runtime_is_pending_in_both(self):
        self.service.f3_runtime = None
        self.assert_agree(
            health_and_catalog(self.service), PENDING, None, status="no_admitted_generation"
        )

    def test_no_admitted_generation_is_not_reported_as_current(self):
        core = _GenerationCore(EVERY_PROFILE, generation=None)
        self.assert_agree(self.evaluate(core), PENDING, None, status="no_admitted_generation")


class StaticCatalogTests(unittest.TestCase):
    """Without a current read the catalog never presents a static pending."""

    def test_static_entries_state_the_requirement_and_not_evaluated(self):
        for tool in GOVERNED_TOOLS:
            with self.subTest(tool=tool):
                entry = capability_for_tool(tool)
                self.assertEqual(entry["core_admission"], CORE_ADMISSION_NOT_EVALUATED)
                self.assertEqual(
                    entry["core_admission_requirement"], "signed_core_registry_admission"
                )

    def test_without_a_governance_service_the_tool_says_not_evaluated(self):
        with mock.patch.object(HEALTH, "governance", SimpleNamespace(service=None)):
            result = json.loads(
                asyncio.run(
                    compatibility.list_capabilities(detail_section="beta_native")
                )
            )
        catalog = result["data"]
        self.assertEqual(
            catalog["core_admission"],
            {"status": CORE_ADMISSION_NOT_EVALUATED, "generation": None},
        )
        by_tool = {item["tool"]: item for item in catalog["beta_native"]}
        for tool in GOVERNED_TOOLS:
            self.assertEqual(by_tool[tool]["core_admission"], CORE_ADMISSION_NOT_EVALUATED)

    def test_the_overlay_touches_only_governed_entries(self):
        snapshot = {
            "status": "current",
            "generation": 4,
            "providers": {},
            "tools": {tool: "admitted" for tool in GOVERNED_TOOLS},
        }
        plain = build_capability_catalog()
        current = build_capability_catalog(core_admission=snapshot)
        for before, after in zip(plain["beta_native"], current["beta_native"]):
            if before["tool"] in GOVERNED_TOOLS:
                self.assertEqual(after["core_admission"], "admitted")
                before = {**before, "core_admission": "admitted"}
            self.assertEqual(after, before)
        # The overlay never mutates the shared static entries.
        for tool in GOVERNED_TOOLS:
            self.assertEqual(
                capability_for_tool(tool)["core_admission"], CORE_ADMISSION_NOT_EVALUATED
            )


class SignedRegistryCatalogAgreementTests(unittest.IsolatedAsyncioTestCase):
    """The real Core runtime: health and the catalog read one signed generation."""

    asyncSetUp = october.OctoberSignedSelectionTests.asyncSetUp
    publish = october.OctoberSignedSelectionTests.publish
    asyncTearDown = delete_admission.SignedTestRegistryAdmissionTests.asyncTearDown

    def views(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        service = _service(temp.name)
        service.f3_runtime = _runtime(core_runtime=self.core)
        return health_and_catalog(service)

    def current_generation(self):
        return self.core.route_status((configuration_delete.PROFILE,))["generation"]

    def assert_delete(self, views, expected):
        self.assertEqual(views["health"], views["catalog"])
        self.assertEqual(views["health"]["configuration_delete_provider"], expected)
        self.assertEqual(views["health_generation"], self.current_generation())
        self.assertEqual(
            views["catalog_admission"],
            {"status": "current", "generation": self.current_generation()},
        )

    async def test_the_shipped_entry_is_pending_in_both(self):
        await self.publish()
        self.assert_delete(await asyncio.to_thread(self.views),
                           configuration_delete.PENDING_ADMISSION_REASON)

    async def test_a_signed_reference_is_admitted_in_both(self):
        entry = deepcopy(self.entry)
        entry["capabilities"] = [*self.entry["capabilities"], *delete_admission.references()]
        await self.publish(entry)
        self.assert_delete(await asyncio.to_thread(self.views), "admitted")


if __name__ == "__main__":
    unittest.main()
