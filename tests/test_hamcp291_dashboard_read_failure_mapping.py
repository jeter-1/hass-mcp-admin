"""HAMCP-291/273: dashboard plan creation names its read failures.

HAMCP-291: a pre-read that the response limit refused was reported as
``dashboard_provider_identity_unavailable`` (retryable), which hid the real
cause. Plan creation now maps it to the existing
``upstream_dashboard_response_too_large`` refusal with its two sizes, and
every other provider failure keeps its closed ``failure_category``.

HAMCP-273: a plan whose resulting configuration the apply-time read-back
must refuse was admitted, written, and then ended unverified. Plan creation
now sizes that read-back in the HAMCP-306 measure and refuses before any
plan, artifact or write.

Every test is plan-only against offline fakes; all data is synthetic.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
import json
from pathlib import Path
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "hass_mcp_engineering_beta"))
sys.path.insert(0, str(Path(__file__).parent))

from ha_mcp_engineering.audit import AuditLogger  # noqa: E402
from ha_mcp_engineering.errors import (  # noqa: E402
    DASHBOARD_ADMISSION_REFUSAL_CATEGORIES,
    DashboardProviderError,
    ErrorCode,
    GovernanceError,
)
from ha_mcp_engineering.f3_dashboard.gateway import (  # noqa: E402
    DashboardExecutionGateway,
)
from ha_mcp_engineering.f3_dashboard.errors import RawEvidenceError  # noqa: E402
from ha_mcp_engineering.governance import service as governance_service  # noqa: E402
from ha_mcp_engineering.governance.service import (  # noqa: E402
    ChangeGovernanceService,
)
from ha_mcp_engineering.governance.storage import (  # noqa: E402
    ChangePlanRepository,
)
from ha_mcp_engineering.providers.upstream_dashboard import (  # noqa: E402
    FAILURE_CATEGORIES,
    UpstreamDashboardProvider,
)
from test_dashboard_update_mvp import (  # noqa: E402
    _DashboardGateway,
    _ExactProviderTransport,
    _UnusedConfigurationGateway,
    _unrelated_lifecycle_provider_identity,
)


RETITLE = [
    {
        "operation_id": "retitle",
        "operation": "replace",
        "path": "/title",
        "value": "Renamed synthetic dashboard",
    }
]


def provider_failure(category: str, **details) -> DashboardProviderError:
    """The exact error the provider raises for ``category``."""

    try:
        UpstreamDashboardProvider()._raise(category, dispatched=True, details=details)
    except DashboardProviderError as error:
        return error
    raise AssertionError("the provider did not raise")


class _FailingPrereadGateway(_DashboardGateway):
    def __init__(self, error: BaseException) -> None:
        super().__init__(version="9.2.0")
        self.error = error

    async def preread(self, *, url_path: str):
        self.preread_count += 1
        raise self.error


def note_card(content: str) -> dict:
    return {"type": "markdown", "title": "Synthetic note", "content": content}


def notes_dashboard(cards: int, *, content: str = "Synthetic note text. " * 5) -> dict:
    return {
        "title": "Synthetic notes",
        "views": [
            {
                "title": "Synthetic view",
                "path": "synthetic-view",
                "cards": [note_card(content) for _ in range(cards)],
            }
        ],
    }


def grow_first_note(by: int) -> list[dict]:
    return [
        {
            "operation_id": "grow",
            "operation": "replace",
            "path": "/views/0/cards/0/content",
            "value": "Synthetic grown note. " * (by // 22),
        }
    ]


class _ServiceHarness(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.repository = ChangePlanRepository(self.root / "plans")

    async def asyncTearDown(self):
        self.temporary.cleanup()

    def service_for(self, gateway) -> ChangeGovernanceService:
        return ChangeGovernanceService(
            self.repository,
            _UnusedConfigurationGateway(),
            AuditLogger(str(self.root / "audit.jsonl"), "test-access-secret"),
            dashboard_gateway=gateway,
            provider_identity_reader=_unrelated_lifecycle_provider_identity,
        )

    async def create(self, service, operations, *, url_path="operations"):
        return await service.create_dashboard_update_plan(
            title="Synthetic dashboard change",
            description="Plan-only offline regression.",
            url_path=url_path,
            patch_operations=operations,
            expiration_minutes=30,
        )

    def assert_no_plan(self):
        self.assertEqual(self.repository.list(), [])


class PrereadFailureMappingTests(_ServiceHarness):
    """HAMCP-291."""

    async def refusal(self, error: BaseException) -> GovernanceError:
        gateway = _FailingPrereadGateway(error)
        with self.assertRaises(GovernanceError) as caught:
            await self.create(self.service_for(gateway), RETITLE)
        self.assertEqual(gateway.preread_count, 1)
        self.assertEqual(gateway.write_count, 0)
        self.assert_no_plan()
        return caught.exception

    async def test_an_oversized_preread_names_the_response_limit(self):
        refusal = await self.refusal(
            provider_failure(
                "response_too_large",
                config_hash="0123456789abcdef",
                engineering_config_hash="f" * 64,
                estimated_serialized_size=61_234,
                response_limit=60_000,
                configuration_returned=False,
            )
        )
        self.assertEqual(refusal.code, ErrorCode.UPSTREAM_DASHBOARD_RESPONSE_TOO_LARGE)
        self.assertFalse(refusal.retryable)
        self.assertEqual(
            refusal.details,
            {
                "reason": "dashboard_read_exceeds_response_limit",
                "estimated_serialized_size": 61_234,
                "response_limit": 60_000,
            },
        )
        self.assertNotIn("dashboard_provider_identity_unavailable", json.dumps(refusal.details))
        # The refusal is not chained to the provider error, so no provider
        # detail rides along on the exception.
        self.assertIsNone(refusal.__cause__)

    async def test_sizes_that_are_not_integers_are_dropped(self):
        for size in ("61234", 61_234.0, True, None):
            with self.subTest(size=size):
                refusal = await self.refusal(
                    provider_failure(
                        "response_too_large",
                        estimated_serialized_size=size,
                        response_limit=size,
                    )
                )
                self.assertEqual(
                    refusal.code, ErrorCode.UPSTREAM_DASHBOARD_RESPONSE_TOO_LARGE
                )
                self.assertEqual(
                    refusal.details, {"reason": "dashboard_read_exceeds_response_limit"}
                )

    async def test_every_other_category_keeps_its_closed_category(self):
        others = [
            category
            for category in FAILURE_CATEGORIES
            if category != "response_too_large"
            and category not in DASHBOARD_ADMISSION_REFUSAL_CATEGORIES
        ]
        self.assertIn("timeout", others)
        self.assertIn("connection_failed", others)
        for category in others:
            with self.subTest(category=category):
                refusal = await self.refusal(provider_failure(category))
                self.assertEqual(refusal.code, ErrorCode.OPERATIONAL_PROVIDER_UNAVAILABLE)
                self.assertEqual(
                    refusal.details,
                    {
                        "reason": "dashboard_provider_identity_unavailable",
                        "failure_category": category,
                    },
                )

    async def test_an_unknown_category_is_normalized_before_it_is_named(self):
        refusal = await self.refusal(provider_failure("synthetic_unlisted_category"))
        self.assertEqual(
            refusal.details,
            {
                "reason": "dashboard_provider_identity_unavailable",
                "failure_category": "internal_error",
            },
        )

    async def test_admission_categories_still_refuse_as_not_admitted(self):
        for category in sorted(
            DASHBOARD_ADMISSION_REFUSAL_CATEGORIES & set(FAILURE_CATEGORIES)
        ):
            with self.subTest(category=category):
                refusal = await self.refusal(provider_failure(category))
                self.assertEqual(refusal.code, ErrorCode.PROVIDER_UNAVAILABLE)
                self.assertEqual(
                    refusal.details, {"reason": "dashboard_provider_not_admitted"}
                )

    async def test_a_provider_error_without_a_category_is_unchanged(self):
        refusal = await self.refusal(
            DashboardProviderError(ErrorCode.UPSTREAM_DASHBOARD_UPSTREAM_ERROR, details={})
        )
        self.assertEqual(refusal.code, ErrorCode.OPERATIONAL_PROVIDER_UNAVAILABLE)
        self.assertEqual(
            refusal.details, {"reason": "dashboard_provider_identity_unavailable"}
        )

    async def test_any_other_exception_is_unchanged(self):
        refusal = await self.refusal(RuntimeError("synthetic transport failure"))
        self.assertEqual(refusal.code, ErrorCode.OPERATIONAL_PROVIDER_UNAVAILABLE)
        self.assertTrue(refusal.retryable)
        self.assertEqual(
            refusal.details, {"reason": "dashboard_provider_identity_unavailable"}
        )


class ReadBackBoundTests(_ServiceHarness):
    """HAMCP-273, through the real provider and execution gateway."""

    async def asyncSetUp(self):
        await super().asyncSetUp()
        self.transport = _ExactProviderTransport(version="9.2.0")
        self.provider = UpstreamDashboardProvider()
        self.provider._transport = self.transport

    async def read_measure(self, configuration: dict) -> int:
        """The provider's own HAMCP-306 measure of a read of ``configuration``."""

        saved = self.transport.configuration
        self.transport.configuration = deepcopy(configuration)
        try:
            result = await self.provider.get_dashboard_config(
                url_path="operations", force_reload=True, response_limit=1_000_000
            )
        finally:
            self.transport.configuration = saved
        self.assertEqual(result.completeness, "complete")
        return result.data["estimated_serialized_size"]

    @staticmethod
    def resulting(current: dict, operations: list[dict]) -> dict:
        """The resulting configuration of ``grow_first_note`` on ``current``."""

        (operation,) = operations
        assert operation["path"] == "/views/0/cards/0/content"
        resulting = deepcopy(current)
        resulting["views"][0]["cards"][0]["content"] = operation["value"]
        return resulting

    async def plan_at(self, limit: int, current: dict, operations: list[dict]):
        self.transport.configuration = deepcopy(current)
        reads = self.transport.read_count
        service = self.service_for(DashboardExecutionGateway(self.provider, response_limit=limit))
        try:
            return await self.create(service, operations)
        finally:
            # One inventory read and one configuration read, never a write.
            self.assertEqual(self.transport.read_count - reads, 2)
            self.assertEqual(self.transport.write_count, 0)
            self.assertEqual(self.transport.guide_count, 0)

    async def test_the_preread_envelope_reproduces_the_provider_measure(self):
        cases = {
            "small": {"title": "Before", "views": []},
            "notes": notes_dashboard(40),
            "non-ascii": notes_dashboard(12, content="Synthetic café ☃ note. " * 4),
            # The measure crosses from four to five digits between these two.
            "four digits": notes_dashboard(40),
            "five digits": notes_dashboard(60),
        }
        for label, configuration in cases.items():
            with self.subTest(case=label):
                self.transport.configuration = deepcopy(configuration)
                preread = await DashboardExecutionGateway(
                    self.provider, response_limit=1_000_000
                ).preread(url_path="operations")
                measure = await self.read_measure(configuration)
                self.assertIsInstance(preread.response_envelope_size, int)
                self.assertEqual(
                    governance_service._dashboard_read_back_size(
                        preread.response_envelope_size, configuration
                    ),
                    measure,
                )
        self.assertLess(await self.read_measure(cases["four digits"]), 10_000)
        self.assertGreaterEqual(await self.read_measure(cases["five digits"]), 10_000)

    async def test_the_envelope_predicts_a_different_configurations_read_back(self):
        current = notes_dashboard(20)
        self.transport.configuration = deepcopy(current)
        preread = await DashboardExecutionGateway(
            self.provider, response_limit=1_000_000
        ).preread(url_path="operations")
        for grown in (notes_dashboard(21), notes_dashboard(400)):
            with self.subTest(cards=len(grown["views"][0]["cards"])):
                self.assertEqual(
                    governance_service._dashboard_read_back_size(
                        preread.response_envelope_size, grown
                    ),
                    await self.read_measure(grown),
                )

    async def bound_case(self):
        # Large enough that the inventory read is complete at these limits.
        current = notes_dashboard(120)
        operations = grow_first_note(1_500)
        resulting = self.resulting(current, operations)
        read_back = await self.read_measure(resulting)
        # The pre-read itself fits at every limit used below.
        self.assertLess(await self.read_measure(current), read_back - 1_000)
        return current, operations, read_back

    async def test_a_read_back_at_the_limit_plans(self):
        current, operations, read_back = await self.bound_case()
        created = await self.plan_at(read_back, current, operations)
        self.assertIsNotNone(self.repository.get(created["plan_id"]))

    async def test_a_read_back_just_under_the_limit_plans(self):
        current, operations, read_back = await self.bound_case()
        created = await self.plan_at(read_back + 1, current, operations)
        self.assertIsNotNone(self.repository.get(created["plan_id"]))

    async def test_a_read_back_just_over_the_limit_refuses_before_any_plan(self):
        current, operations, read_back = await self.bound_case()
        with self.assertRaises(GovernanceError) as caught:
            await self.plan_at(read_back - 1, current, operations)
        refusal = caught.exception
        self.assertEqual(refusal.code, ErrorCode.UPSTREAM_DASHBOARD_RESPONSE_TOO_LARGE)
        self.assertFalse(refusal.retryable)
        self.assertEqual(
            refusal.details,
            {
                "reason": "dashboard_read_back_exceeds_response_limit",
                "estimated_serialized_size": read_back,
                "response_limit": read_back - 1,
            },
        )
        self.assertNotIn("Synthetic grown note", json.dumps(refusal.details))
        self.assert_no_plan()

    async def test_a_cap_sized_result_reads_back_at_the_default_limit(self):
        current = notes_dashboard(225)
        resulting = self.resulting(current, grow_first_note(1_000))
        self.assertGreater(len(json.dumps(resulting, separators=(",", ":"))), 35_000)
        self.assertLess(await self.read_measure(resulting), 60_000)
        created = await self.plan_at(60_000, current, grow_first_note(1_000))
        self.assertIsNotNone(self.repository.get(created["plan_id"]))

    async def test_a_reader_that_does_not_measure_is_not_sized(self):
        # The offline fake gateway has no response limit and its pre-read no
        # envelope, so the read-back bound does not apply to it.
        gateway = _DashboardGateway(version="9.2.0")
        gateway.configuration = notes_dashboard(3)
        created = await self.create(
            self.service_for(gateway), RETITLE, url_path="operations"
        )
        self.assertIsNotNone(self.repository.get(created["plan_id"]))
        self.assertEqual(gateway.write_count, 0)


_MISSING = object()
_KEEP = object()


class _SizeOverrideProvider(UpstreamDashboardProvider):
    """The real provider, with one reply's reported size replaced."""

    def __init__(self, size) -> None:
        super().__init__()
        self.size = size

    async def get_dashboard_config(self, **kwargs):
        result = await super().get_dashboard_config(**kwargs)
        if self.size is _KEEP:
            return result
        data = dict(result.data)
        if self.size is _MISSING:
            del data["estimated_serialized_size"]
        else:
            data["estimated_serialized_size"] = self.size
        return replace(result, data=data)


class PrereadSizeFailClosedTests(_ServiceHarness):
    """HAMCP-273: a complete pre-read without a usable size fails closed.

    Without its envelope a pre-read would skip the read-back bound, so the
    gateway refuses it as invalid raw evidence instead.
    """

    # Missing, a bool, a string, and an int that makes the envelope negative.
    UNAVAILABLE = {"missing": _MISSING, "bool": True, "string": "4096"}
    MALFORMED = {"negative envelope": 1}

    async def asyncSetUp(self):
        await super().asyncSetUp()
        self.transport = _ExactProviderTransport(version="9.2.0")
        self.transport.configuration = notes_dashboard(3)

    def gateway(self, size) -> DashboardExecutionGateway:
        provider = _SizeOverrideProvider(size)
        provider._transport = self.transport
        return DashboardExecutionGateway(provider, response_limit=60_000)

    async def test_the_unchanged_size_plans(self):
        # Control: the same wrapper, keeping the real size, plans normally.
        created = await self.create(self.service_for(self.gateway(_KEEP)), RETITLE)
        self.assertIsNotNone(self.repository.get(created["plan_id"]))
        self.assertEqual(self.transport.write_count, 0)

    async def test_the_gateway_refuses_a_preread_without_a_usable_size(self):
        cases = [
            *((label, size, "Dashboard configuration size is unavailable")
              for label, size in self.UNAVAILABLE.items()),
            *((label, size, "Dashboard configuration size is malformed")
              for label, size in self.MALFORMED.items()),
        ]
        for label, size, message in cases:
            with self.subTest(size=label):
                with self.assertRaises(RawEvidenceError) as caught:
                    await self.gateway(size).preread(url_path="operations")
                self.assertEqual(caught.exception.code, "invalid_raw_dashboard_evidence")
                self.assertEqual(str(caught.exception), message)
        self.assertEqual(self.transport.write_count, 0)
        self.assertEqual(self.transport.guide_count, 0)

    async def test_plan_creation_refuses_a_preread_without_a_usable_size(self):
        for label, size in {**self.UNAVAILABLE, **self.MALFORMED}.items():
            with self.subTest(size=label):
                reads = self.transport.read_count
                service = self.service_for(self.gateway(size))
                with self.assertRaises(GovernanceError) as caught:
                    await self.create(service, RETITLE)
                refusal = caught.exception
                self.assertEqual(refusal.code, ErrorCode.CONFIGURATION_VALIDATION_FAILED)
                self.assertFalse(refusal.retryable)
                self.assertEqual(
                    refusal.details, {"reason": "invalid_raw_dashboard_evidence"}
                )
                self.assert_no_plan()
                # One inventory read and one configuration read, never a write.
                self.assertEqual(self.transport.read_count - reads, 2)
                self.assertEqual(self.transport.write_count, 0)
                self.assertEqual(self.transport.guide_count, 0)


if __name__ == "__main__":
    unittest.main()
