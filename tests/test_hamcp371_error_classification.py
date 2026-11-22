"""HAMCP-371 step SE: what Engineering's upstream error classifiers do today.

Offline and synthetic.  Every body, code and exception here is made up in this
file; nothing is read from an upstream image, a capture or a network peer.

The test pins current behaviour.  It does not justify a classifier change: if
one of these assertions fails, the classifier is not edited to match; the
difference is reported.

The classifiers take no upstream version.  A version enters only through the
compiled error-contract entry (``_ERROR_ADAPTERS``), whose probes name the
(tool, structured code) pairs reviewed for that release.  Every probe pair of
the 9.2.0 entry is classified against the pinned categories.  HAMCP-437: only
9.2.0 is admitted (D1), so the 9.1.2 entry check and the 9.1.2-against-9.2.0
comparison are retired; the pinned categories are the N31 shapes both
releases reproduced.
"""

import json
from pathlib import Path
import sys
import unittest

from mcp.shared.exceptions import McpError
from mcp.types import ErrorData

ROOT = Path(__file__).resolve().parents[1]
BETA = ROOT / "hass_mcp_engineering_beta"
sys.path.insert(0, str(BETA))

from ha_mcp_engineering.clients.mcp import (  # noqa: E402
    DashboardTransportError,
    _classified_transport_error,
    _classify_transport_exception,
)
from ha_mcp_engineering.errors import ErrorCode  # noqa: E402
from ha_mcp_engineering.ha_mcp_readmission import ha_mcp as readmission  # noqa: E402
from ha_mcp_engineering.providers import operational_backup  # noqa: E402
from ha_mcp_engineering.providers import operational_lifecycle  # noqa: E402
from ha_mcp_engineering.providers import upstream_dashboard  # noqa: E402
from ha_mcp_engineering.providers import upstream_read_gateway as gateway  # noqa: E402

CANDIDATE_VERSION = "9.2.0"
ENTRY_VERSIONS = (CANDIDATE_VERSION,)

# JSON-RPC numbers an upstream client exception may carry.
INVALID_PARAMS = -32602
INTERNAL_ERROR = -32603

VALIDATION_BODY = {"success": False, "error": {"code": "VALIDATION_FAILED"}}

# (probe id, tool, structured code) -> category, with the reviewed
# single-entity argument form for the registry probe.
EXPECTED_PROBE_CATEGORIES = {
    ("invalid_search", "ha_search", "VALIDATION_FAILED"): "invalid_request",
    ("missing_state", "ha_get_state", "ENTITY_NOT_FOUND"): "entity_not_found",
    (
        "missing_automation",
        "ha_config_get_automation",
        "RESOURCE_NOT_FOUND",
    ): "automation_not_found",
    (
        "missing_registry_entity",
        "ha_get_entity",
        "SERVICE_CALL_FAILED",
    ): "entity_not_found",
}
SYNTHETIC_ENTITY_ARGUMENTS = {"entity_id": "sensor.synthetic_missing_entity"}


def error_result(body):
    """An ``isError`` tool result carrying one synthetic structured body."""

    return {
        "isError": True,
        "content": [{"type": "text", "text": json.dumps(body, sort_keys=True)}],
    }


def client_exception(code):
    return McpError(ErrorData(code=code, message="synthetic upstream error"))


def version_bound_entry(version):
    entries = [
        adapter
        for adapter in readmission._ERROR_ADAPTERS
        if adapter.upstream_versions is not None
        and version in adapter.upstream_versions
    ]
    return entries[0] if len(entries) == 1 else None


def probe_categories(entry):
    return {
        (probe.probe_id, probe.capability_id, probe.structured_code): (
            gateway._classify_upstream_error_payload(
                probe.capability_id,
                {"success": False, "error": {"code": probe.structured_code}},
                dict(SYNTHETIC_ENTITY_ARGUMENTS)
                if probe.capability_id == "ha_get_entity"
                else None,
            )
        )
        for probe in entry.probes
    }


class ErrorContractEntryTests(unittest.TestCase):
    def test_the_9_2_0_error_contract_entry_exists(self):
        self.assertIsNotNone(
            version_bound_entry(CANDIDATE_VERSION),
            "no version-bound error-contract entry for 9.2.0 yet; it is added "
            "by the HAMCP-371 admission step that binds 9.2.0, and this check "
            "is expected to fail until then",
        )

    def test_every_existing_entry_classifies_its_probes_as_pinned(self):
        seen = 0
        for version in ENTRY_VERSIONS:
            entry = version_bound_entry(version)
            if entry is None:
                continue
            seen += 1
            with self.subTest(version=version):
                self.assertEqual(probe_categories(entry), EXPECTED_PROBE_CATEGORIES)
        self.assertGreaterEqual(seen, 1)

    def test_validation_body_is_invalid_request_for_every_entry_tool(self):
        for version in ENTRY_VERSIONS:
            entry = version_bound_entry(version)
            if entry is None:
                continue
            for tool in sorted({probe.capability_id for probe in entry.probes}):
                with self.subTest(version=version, tool=tool):
                    self.assertEqual(
                        gateway._classify_upstream_error_payload(
                            tool, dict(VALIDATION_BODY)
                        ),
                        "invalid_request",
                    )
                    self.assertEqual(
                        gateway._classify_upstream_tool_error(
                            tool, error_result(VALIDATION_BODY)
                        ),
                        "invalid_request",
                    )


class GatewayPayloadClassifierTests(unittest.TestCase):
    def test_validation_string_codes_are_invalid_request(self):
        for code in ("VALIDATION_FAILED", "VALIDATION_INVALID_JSON"):
            with self.subTest(code=code):
                self.assertEqual(
                    gateway._classify_upstream_error_payload(
                        "ha_search", {"success": False, "error": {"code": code}}
                    ),
                    "invalid_request",
                )

    def test_the_classifier_does_not_key_on_json_rpc_numbers(self):
        for code in (INVALID_PARAMS, INTERNAL_ERROR, None, "SYNTHETIC_UNREVIEWED"):
            with self.subTest(code=code):
                self.assertEqual(
                    gateway._classify_upstream_error_payload(
                        "ha_search", {"success": False, "error": {"code": code}}
                    ),
                    "upstream_error",
                )

    def test_a_body_that_is_not_a_failure_envelope_is_not_reclassified(self):
        for body in (
            {"success": True, "error": {"code": "VALIDATION_FAILED"}},
            {"success": False, "error": "VALIDATION_FAILED"},
            {"error": {"code": "VALIDATION_FAILED"}},
        ):
            with self.subTest(body=body):
                self.assertEqual(
                    gateway._classify_upstream_error_payload("ha_search", body),
                    "upstream_error",
                )

    def test_the_registry_probe_needs_the_reviewed_argument_form(self):
        body = {"success": False, "error": {"code": "SERVICE_CALL_FAILED"}}
        self.assertEqual(
            gateway._classify_upstream_error_payload("ha_get_entity", body),
            "upstream_error",
        )
        self.assertEqual(
            gateway._classify_upstream_error_payload(
                "ha_get_entity", body, dict(SYNTHETIC_ENTITY_ARGUMENTS)
            ),
            "entity_not_found",
        )


class TransportExceptionClassifierTests(unittest.TestCase):
    def test_invalid_params_and_internal_error_are_both_protocol_error(self):
        for code in (INVALID_PARAMS, INTERNAL_ERROR):
            with self.subTest(code=code):
                self.assertEqual(
                    _classify_transport_exception(client_exception(code)),
                    "protocol_error",
                )

    def test_the_number_alone_does_not_decide_the_category(self):
        # The category comes from the exception type; an unrelated exception
        # that happens to carry the same number stays on the generic path.
        for code in (INVALID_PARAMS, INTERNAL_ERROR):
            with self.subTest(code=code):
                unrelated = RuntimeError("synthetic")
                unrelated.error = ErrorData(code=code, message="synthetic")
                self.assertEqual(
                    _classify_transport_exception(unrelated), "internal_error"
                )

    def test_grouped_client_exceptions_stay_protocol_error(self):
        grouped = ExceptionGroup(
            "synthetic",
            [client_exception(INVALID_PARAMS), client_exception(INTERNAL_ERROR)],
        )
        self.assertEqual(_classify_transport_exception(grouped), "protocol_error")


class DashboardDecoderTests(unittest.TestCase):
    def test_validation_body_is_the_dashboard_upstream_error(self):
        provider = upstream_dashboard.UpstreamDashboardProvider()
        with self.assertRaises(upstream_dashboard.DashboardProviderError) as raised:
            provider._decode_call_result(error_result(VALIDATION_BODY))
        error = raised.exception
        self.assertIs(error.code, ErrorCode.UPSTREAM_DASHBOARD_UPSTREAM_ERROR)
        self.assertEqual(error.details["failure_category"], "upstream_error")
        self.assertEqual(error.details["upstream_error_code"], "VALIDATION_FAILED")
        self.assertIs(error.details["upstream_dispatch_occurred"], True)

    def test_client_exceptions_are_the_dashboard_protocol_error(self):
        for code in (INVALID_PARAMS, INTERNAL_ERROR):
            with self.subTest(code=code):
                classified = _classified_transport_error(client_exception(code))
                self.assertIsInstance(classified, DashboardTransportError)
                self.assertEqual(classified.category, "protocol_error")
                self.assertEqual(
                    classified.failure_kind, "protocol_or_transport_failure"
                )
                category = upstream_dashboard._normalized_category(
                    classified.category
                )
                self.assertEqual(category, "protocol_error")
                self.assertIs(
                    upstream_dashboard._ERROR_CODES[category],
                    ErrorCode.UPSTREAM_DASHBOARD_PROTOCOL_ERROR,
                )


class OperationalDecoderTests(unittest.TestCase):
    def test_validation_body_is_backup_rejected(self):
        provider = operational_backup.ReviewedOperationalBackupProvider()
        with self.assertRaises(
            operational_backup.OperationalBackupProviderError
        ) as raised:
            provider._decode_result(error_result(VALIDATION_BODY))
        self.assertEqual(raised.exception.category, "backup_rejected")
        self.assertIs(raised.exception.dispatched, True)

    def test_validation_body_is_operation_rejected(self):
        provider = operational_lifecycle.ReviewedOperationalLifecycleProvider()
        with self.assertRaises(
            operational_lifecycle.OperationalLifecycleProviderError
        ) as raised:
            provider._decode(error_result(VALIDATION_BODY), dispatched=True)
        self.assertEqual(raised.exception.category, "operation_rejected")
        self.assertIs(raised.exception.dispatched, True)
        self.assertEqual(
            operational_lifecycle._addon_error_category("VALIDATION_FAILED"),
            "operation_rejected",
        )

    def test_client_exceptions_are_the_operational_protocol_error(self):
        for code in (INVALID_PARAMS, INTERNAL_ERROR):
            with self.subTest(code=code):
                category = _classified_transport_error(client_exception(code)).category
                self.assertEqual(
                    operational_backup._transport_category(category),
                    "protocol_error",
                )
                self.assertEqual(
                    operational_lifecycle._transport_category(category),
                    "protocol_error",
                )

    def test_an_unreviewed_code_is_the_generic_provider_error(self):
        for code in ("SYNTHETIC_UNREVIEWED", INVALID_PARAMS, None):
            with self.subTest(code=code):
                self.assertEqual(
                    operational_backup._upstream_error_category(code),
                    "provider_error",
                )
                self.assertEqual(
                    operational_lifecycle._upstream_error_category(code),
                    "provider_error",
                )


if __name__ == "__main__":
    unittest.main()
