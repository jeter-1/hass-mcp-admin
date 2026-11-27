"""HAMCP-471: strict JSON decoders refuse an exponent overflow.

Python's decoder calls ``parse_constant`` only for the literals ``NaN``,
``Infinity`` and ``-Infinity``. A number whose exponent overflows, such as
``1e999``, goes to the float parser, which returns infinity. Each strict
decoder that rejects the literals now rejects the overflow too, with the same
failure the literal gets. A finite float still decodes.
"""

from __future__ import annotations

import json
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "hass_mcp_engineering_beta"))

from ha_mcp_engineering import compatibility_family, inbound_topology_observer  # noqa: E402
from ha_mcp_engineering import upstream_tool_policy  # noqa: E402
from ha_mcp_engineering.audit_baseline import capture_contracts, validation  # noqa: E402
from ha_mcp_engineering.clients import logbook  # noqa: E402
from ha_mcp_engineering.dashboard_analysis import contracts as dashboard_contracts  # noqa: E402
from ha_mcp_engineering.ha_core_readmission.source import AiohttpCoreSnapshotSource  # noqa: E402
from ha_mcp_engineering.ha_mcp_readmission import registry  # noqa: E402
from ha_mcp_engineering.providers import upstream_read_gateway as gateway  # noqa: E402
from ha_mcp_engineering.providers import upstream_yaml_reader  # noqa: E402
from ha_mcp_engineering.providers.operational_backup import (  # noqa: E402
    ReviewedOperationalBackupProvider,
)
from ha_mcp_engineering.providers.operational_lifecycle import (  # noqa: E402
    OperationalLifecycleProviderError,
    ReviewedOperationalLifecycleProvider,
)
from ha_mcp_engineering.providers.supervisor_self import (  # noqa: E402
    SelfAddonIdentityError,
    SupervisorSelfAddonIdentityResolver,
)
from ha_mcp_engineering.signed_registry import models as signed_models  # noqa: E402

OVERFLOWS = ("1e999", "-1e999", "1E+400", "-2.5e400")


def document(number: str) -> str:
    """An object in which only the overflowing number is wrong."""

    return '{"success":true,"nested":{"value":%s}}' % number


class _RawJsonResponse:
    status = 200

    def __init__(self, payload: bytes):
        self.content = self
        self._payload = payload

    async def read(self, _size: int) -> bytes:
        payload, self._payload = self._payload, b""
        return payload


class PureDecoderTests(unittest.TestCase):
    def assert_refused(self, decode, number, exception, check):
        with self.subTest(number=number), self.assertRaises(exception) as raised:
            decode(document(number))
        check(raised.exception)

    def test_each_decoder_refuses_overflow_as_it_refuses_the_literal(self):
        cases = {
            "inbound_topology_observer.parse_arm": (
                lambda text: inbound_topology_observer.parse_arm(text.encode(), None, 0, None),
                inbound_topology_observer.ObservationRefused,
                lambda exc: self.assertEqual(exc.args, ("arm_json_invalid",)),
            ),
            "registry._strict_json_document": (
                lambda text: registry._strict_json_document(text.encode(), "synthetic_reason"),
                registry.ReleaseRegistryOperationalError,
                lambda exc: self.assertEqual(exc.reason_code, "synthetic_reason"),
            ),
            "dashboard_analysis.contracts.parse": (
                lambda text: dashboard_contracts.parse(text.encode()),
                dashboard_contracts.AnalysisError,
                lambda exc: self.assertEqual(exc.details["reason"], "malformed_response"),
            ),
            "signed_registry.models._strict_json_loads": (
                lambda text: signed_models._strict_json_loads(text.encode()),
                signed_models.RegistryValidationError,
                lambda exc: self.assertIs(exc.code, signed_models.RegistryErrorCode.MALFORMED_JSON),
            ),
            "audit_baseline.validation._strict_loads": (
                lambda text: validation._strict_loads(text.encode()),
                validation.BaselineValidationError,
                lambda exc: self.assertEqual(exc.code, "non_finite_number"),
            ),
            "audit_baseline.capture_contracts.parse": (
                lambda text: capture_contracts.parse(text.encode()),
                capture_contracts.CaptureError,
                lambda exc: self.assertEqual(exc.details["reason"], "malformed_response"),
            ),
            "upstream_yaml_reader._strict_json": (
                upstream_yaml_reader._strict_json,
                ValueError,
                lambda exc: self.assertEqual(exc.args, ("non_finite_number",)),
            ),
            "clients.logbook._decode": (
                lambda text: logbook._decode("[%s]" % text),
                Exception,
                lambda exc: self.assertNotIsInstance(exc, (OverflowError, TypeError)),
            ),
        }
        for name, (decode, exception, check) in cases.items():
            with self.subTest(decoder=name):
                for number in OVERFLOWS:
                    self.assert_refused(decode, number, exception, check)

    def test_logbook_overflow_is_the_invalid_response_the_literal_gets(self):
        def failure(text):
            with self.assertRaises(Exception) as raised:
                logbook._decode(text)
            return type(raised.exception), str(raised.exception)

        literal = failure('[{"x":Infinity}]')
        for number in OVERFLOWS:
            with self.subTest(number=number):
                self.assertEqual(failure('[{"x":%s}]' % number), literal)

    def test_policy_file_decoders_refuse_overflow(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "document.json"
            for number in OVERFLOWS:
                path.write_text(document(number), encoding="utf-8")
                with self.subTest(decoder="compatibility_family", number=number):
                    with self.assertRaises(compatibility_family.CompatibilityFamilyError) as raised:
                        compatibility_family.load_strict_json(path, error="synthetic_error")
                    self.assertEqual(raised.exception.args, ("nonfinite_json_value",))
                with self.subTest(decoder="upstream_tool_policy", number=number):
                    with self.assertRaises(upstream_tool_policy.UpstreamToolPolicyError) as raised:
                        upstream_tool_policy._load_strict_json(path, error="synthetic_error")
                    self.assertEqual(raised.exception.args, ("synthetic_error",))
                    self.assertIsInstance(raised.exception.__cause__, upstream_tool_policy.UpstreamToolPolicyError)
                    self.assertEqual(raised.exception.__cause__.args, ("policy_nonfinite_json_constant",))

    def test_reviewed_upstream_error_payload_refuses_overflow_text(self):
        for number in OVERFLOWS:
            with self.subTest(number=number):
                self.assertIsNone(
                    gateway._reviewed_upstream_error_payload(
                        {"content": [{"type": "text", "text": document(number)}]}
                    )
                )

    def test_finite_floats_still_decode(self):
        text = '{"success":true,"nested":{"value":1.5e300,"small":-2.5e-300,"plain":0.25}}'
        expected = json.loads(text)
        self.assertEqual(upstream_yaml_reader._strict_json(text), expected)
        self.assertEqual(validation._strict_loads(text.encode()), expected)
        self.assertEqual(signed_models._strict_json_loads(text.encode()), expected)
        self.assertEqual(registry._strict_json_document(text.encode(), "synthetic_reason"), expected)
        self.assertEqual(capture_contracts.parse(text.encode()), expected)
        self.assertEqual(dashboard_contracts.parse(text.encode()), expected)
        self.assertEqual(logbook._decode("[%s]" % text), [expected])
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "document.json"
            path.write_text(text, encoding="utf-8")
            self.assertEqual(compatibility_family.load_strict_json(path, error="synthetic_error"), expected)
            self.assertEqual(upstream_tool_policy._load_strict_json(path, error="synthetic_error"), expected)


class ProviderDecoderTests(unittest.IsolatedAsyncioTestCase):
    def mcp_result(self, number):
        return {"content": [{"type": "text", "text": document(number)}], "isError": False}

    async def test_backup_provider_refuses_overflow_as_invalid_response(self):
        provider = ReviewedOperationalBackupProvider()
        for number in OVERFLOWS:
            with self.subTest(number=number), self.assertRaises(Exception) as raised:
                provider._decode_result(self.mcp_result(number))
            self.assertEqual(getattr(raised.exception, "category", None), "invalid_response")

    async def test_lifecycle_provider_refuses_overflow_as_invalid_response(self):
        provider = ReviewedOperationalLifecycleProvider()
        for number in OVERFLOWS:
            with self.subTest(number=number):
                with self.assertRaises(OperationalLifecycleProviderError) as raised:
                    provider._decode(self.mcp_result(number), dispatched=True)
                self.assertEqual(raised.exception.category, "invalid_response")
                self.assertTrue(raised.exception.dispatched)

    async def test_supervisor_self_info_refuses_overflow_as_malformed(self):
        for number in OVERFLOWS:
            payload = (
                '{"result":"ok","data":{"slug":"df26dea6_hass_mcp_engineering_beta",'
                '"name":"HA MCP Engineering Server Beta","version":"2.1.1-beta.2",'
                '"repository":"df26dea6","extra":%s}}' % number
            ).encode()

            async def fetch(value=payload):
                return 200, value

            with self.subTest(number=number), self.assertRaises(SelfAddonIdentityError) as raised:
                await SupervisorSelfAddonIdentityResolver(
                    base_url="http://supervisor",
                    token="synthetic-supervisor-token",
                    timeout_seconds=5,
                    fetcher=fetch,
                ).resolve()
            self.assertEqual(raised.exception.failure_category, "malformed_response")

    async def test_core_probe_decoder_refuses_overflow_with_a_fixed_token(self):
        source = AiohttpCoreSnapshotSource(object())
        for number, token in (("1e999", "Infinity"), ("-1e999", "-Infinity"),
                              ("1E+400", "Infinity"), ("-2.5e400", "-Infinity")):
            with self.subTest(number=number), self.assertRaises(ValueError) as raised:
                await source._bounded_response_json(_RawJsonResponse(document(number).encode()))
            # The message is the one the literal gets, never the number text.
            self.assertEqual(raised.exception.args, (f"core_probe_response_non_finite:{token}",))
        with self.assertRaises(ValueError) as raised:
            await source._bounded_response_json(_RawJsonResponse(document("-Infinity").encode()))
        self.assertEqual(raised.exception.args, ("core_probe_response_non_finite:-Infinity",))


if __name__ == "__main__":
    unittest.main()
