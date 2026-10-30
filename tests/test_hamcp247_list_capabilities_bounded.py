"""HAMCP-247: list_capabilities returns usable data within the response cap.

Live beta.44 returned success with ``/data`` omitted and an empty retrieval
list for the unfiltered call (96,902 chars) and ``status=delegated``
(91,274 chars): beta_native, provider_matrix and the gateway snapshot were
never narrowed by a filter, and the envelope's generic bound drops ``/data``
whole.  The tool now returns a bounded summary by default, narrows every
section by a filter, and returns one full section on request.

The committed catalog fixture is a 105-tool synthetic, live-shaped catalog:
the real UpstreamReadGateway is driven offline with the captured stock
ha-mcp 8.6.0 descriptors and the signed release registry disabled (77 native
+ 28 dynamic = 105 registered; live had 106).  The 106-tool cases use the
exact ha-mcp 8.5.0 component capture with the optional ``ha_config_get_yaml``
admitted (77 + 29 = 106), as the round-1 review did; neither is a claim about
the household's current provider version.  Every call shape is rendered by
the real tool and envelope, at that size and with every section grown by
half.  Nothing is sent anywhere.

A requested section too large for the response limit is served in pages of
its actual rows; every page request is followed to completion and the rows
are compared byte-for-byte with the filtered source (round-1 R1-M1, R1-M2).
"""

from __future__ import annotations

import asyncio
import base64
from contextlib import contextmanager
from dataclasses import replace
import json
import math
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "hass_mcp_engineering_beta"), str(ROOT / "tests")]

from mcp.server.fastmcp import FastMCP  # noqa: E402

from ha_mcp_engineering import capabilities  # noqa: E402
from ha_mcp_engineering.capabilities import (  # noqa: E402
    CATALOG_SECTIONS,
    GATEWAY_DETAIL_FIELDS,
    build_capability_catalog,
    build_capability_view,
)
from ha_mcp_engineering.errors import InvalidRequestError  # noqa: E402
from ha_mcp_engineering.health import HEALTH  # noqa: E402
from ha_mcp_engineering.providers.upstream_read_gateway import (  # noqa: E402
    UpstreamReadGateway,
)
from ha_mcp_engineering.tools import compatibility  # noqa: E402
from test_ha_mcp_production_readmission import _GatewayTransport, _settings  # noqa: E402
from test_hamcp162_yaml_admission import captured  # noqa: E402
from test_upstream_8_5_compatibility import capture  # noqa: E402

LIMIT = compatibility.SETTINGS.response_size_limit
STATUSES = ("native", "transitional", "delegated", "deprecated", "beta_native")
CATEGORIES = (
    "foundation", "evidence", "verification", "observability", "discovery",
    "configuration", "execution",
)
# The extra categories the beta-native and delegated entries carry.
EXTRA_CATEGORIES = (
    "analysis", "execution_task", "governance", "governed_configuration",
    "governed_runtime_action", "operational_administration",
    "ordinary_runtime_action", "upstream_read_gateway",
)
SHAPES = (
    {},
    *({"status": value} for value in STATUSES),
    *({"category": value} for value in CATEGORIES + EXTRA_CATEGORIES),
    *({"detail_section": value} for value in CATALOG_SECTIONS),
    *(
        {"detail_section": section, "status": "delegated"}
        for section in CATALOG_SECTIONS
    ),
    *(
        {"detail_section": section, "category": "foundation"}
        for section in CATALOG_SECTIONS
    ),
)
# Keys every pre-HAMCP-247 catalog response carried.
LEGACY_KEYS = {
    "count", "filters", "tools", "planned", "beta_native", "core_admission",
    "dynamic_upstream", "upstream_read_gateway", "provider_matrix",
    "registered_count", "engineering_registered_count",
    "dynamic_upstream_count", "status_values",
}


def _grow_rows(rows, factor):
    """``rows`` extended to ``ceil(len * factor)`` with renamed copies."""

    rows = list(rows)
    extra = []
    for index in range(math.ceil(len(rows) * factor) - len(rows)):
        row = rows[index % len(rows)]
        suffix = f"_grown{index}"
        if isinstance(row, str):
            extra.append(row + suffix)
            continue
        copy = dict(row)
        for key in ("tool", "upstream_tool", "upstream_name", "exposed_name"):
            if isinstance(copy.get(key), str):
                copy[key] += suffix
        extra.append(copy)
    return rows + extra


def _grow_gateway(gateway, factor):
    grown = dict(gateway)
    for field in GATEWAY_DETAIL_FIELDS:
        rows = grown.get(field)
        if isinstance(rows, list) and rows:
            grown[field] = _grow_rows(rows, factor)
        elif isinstance(rows, dict) and rows:
            keys = _grow_rows(list(rows), factor)
            values = list(rows.values())
            grown[field] = {
                key: values[index % len(values)] for index, key in enumerate(keys)
            }
    return grown


def _compact(value):
    return json.dumps(value, separators=(",", ":"), default=str)


def _call(**arguments):
    return asyncio.run(compatibility.list_capabilities(**arguments))


def _call_at(limit, **arguments):
    """The public call with the supported response limit set to ``limit``."""

    settings = replace(compatibility.SETTINGS, response_size_limit=limit)
    with mock.patch.object(compatibility, "SETTINGS", settings):
        return _call(**arguments)


class _LiveShapedCatalog(unittest.TestCase):
    # The 105-tool synthetic fixture (stock ha-mcp 8.6.0 descriptors).
    @staticmethod
    def upstream_capture():
        return capture("8.6.0")["tools"], "8.6.0"

    @classmethod
    def setUpClass(cls):
        cls._saved = (
            capabilities._DYNAMIC_UPSTREAM_CAPABILITIES,
            capabilities._UPSTREAM_READ_GATEWAY_SUMMARY,
        )

        async def publish():
            tools, version = cls.upstream_capture()
            transport = _GatewayTransport(tools, version=version)
            gateway = UpstreamReadGateway()
            gateway.configure(
                replace(
                    _settings("synthetic-no-key"),
                    ha_mcp_release_registry_enabled=False,
                ),
                transport=transport,
            )
            await gateway.initialize(FastMCP("hamcp247"))

        asyncio.run(publish())
        cls.dynamic = capabilities._DYNAMIC_UPSTREAM_CAPABILITIES
        cls.gateway = capabilities._UPSTREAM_READ_GATEWAY_SUMMARY

    @classmethod
    def tearDownClass(cls):
        (
            capabilities._DYNAMIC_UPSTREAM_CAPABILITIES,
            capabilities._UPSTREAM_READ_GATEWAY_SUMMARY,
        ) = cls._saved

    def setUp(self):
        capabilities._DYNAMIC_UPSTREAM_CAPABILITIES = self.dynamic
        capabilities._UPSTREAM_READ_GATEWAY_SUMMARY = self.gateway
        # HAMCP-248: Core admission is not evaluated here, whatever an
        # earlier module in the same shard left configured.
        governance = mock.patch.object(
            HEALTH, "governance", SimpleNamespace(service=None)
        )
        governance.start()
        self.addCleanup(governance.stop)

    @contextmanager
    def grown(self, factor=1.5):
        with mock.patch.multiple(
            capabilities,
            CAPABILITIES=tuple(_grow_rows(capabilities.CAPABILITIES, factor)),
            BETA_NATIVE_CAPABILITIES=tuple(
                _grow_rows(capabilities.BETA_NATIVE_CAPABILITIES, factor)
            ),
            CAPABILITY_PROVIDER_MATRIX=tuple(
                _grow_rows(capabilities.CAPABILITY_PROVIDER_MATRIX, factor)
            ),
            _DYNAMIC_UPSTREAM_CAPABILITIES=tuple(_grow_rows(self.dynamic, factor)),
            _UPSTREAM_READ_GATEWAY_SUMMARY=_grow_gateway(self.gateway, factor),
        ):
            yield

    def assert_usable(self, text, arguments):
        self.assertLessEqual(len(text), LIMIT, arguments)
        result = json.loads(text)
        self.assertTrue(result["success"], arguments)
        self.assertNotIn("response_completeness", result, arguments)
        data = result["data"]
        self.assertIn(data["view"], {"summary", "filtered", "section"})
        if data["size_limit"]["omitted_sections"]:
            self.assertTrue(data["size_limit"]["retrieval"], arguments)
        return data

    def section(self, data, name):
        """``data[name]``, or the section fetched by its advertised request."""

        if name in data:
            return data[name]
        entry = data["sections"][name]
        self.assertEqual(entry["included"], "omitted_size_limit")
        self.assertIn(_request_of(entry), data["size_limit"]["retrieval"])
        fetched = self.assert_usable(_call(**entry["request"]), entry["request"])
        self.assertEqual(fetched["sections"][name]["included"], "full")
        return fetched[name]


def _request_of(entry):
    return {"tool": "list_capabilities", "arguments": entry["request"]}


class LiveShapedFixtureTests(_LiveShapedCatalog):
    def test_the_fixture_reproduces_the_live_overflow(self):
        self.assertEqual(len(self.dynamic), 28)
        self.assertEqual(build_capability_catalog()["registered_count"], 105)
        for arguments in ({}, {"status": "delegated"}):
            catalog = build_capability_catalog(**arguments)
            # The established filter-less catalog alone exceeds the cap.
            if not arguments:
                self.assertGreater(
                    len(json.dumps({"data": catalog}, separators=(",", ":"))), LIMIT
                )


class SizeTests(_LiveShapedCatalog):
    def test_every_call_shape_returns_data_under_the_cap(self):
        for arguments in SHAPES:
            with self.subTest(**arguments):
                self.assert_usable(_call(**arguments), arguments)

    def test_every_call_shape_returns_data_under_the_cap_at_half_again_growth(self):
        with self.grown():
            self.assertEqual(build_capability_catalog()["registered_count"], 158)
            for arguments in SHAPES:
                with self.subTest(**arguments):
                    self.assert_usable(_call(**arguments), arguments)

    def test_at_live_size_every_documented_view_is_complete(self):
        for arguments in SHAPES:
            if arguments.get("status") == "beta_native":
                continue
            with self.subTest(**arguments):
                data = self.assert_usable(_call(**arguments), arguments)
                self.assertEqual(data["size_limit"]["omitted_sections"], [])

    def test_every_section_is_complete_at_half_again_growth(self):
        with self.grown():
            for section in CATALOG_SECTIONS:
                with self.subTest(section=section):
                    data = self.assert_usable(
                        _call(detail_section=section), {"detail_section": section}
                    )
                    self.assertEqual(data["size_limit"]["omitted_sections"], [])
                    self.assertEqual(
                        data[section], build_capability_catalog()[section]
                    )


class AmbientGovernanceTests(_LiveShapedCatalog):
    """HAMCP-198: an earlier module's governance service cannot leak in."""

    SNAPSHOT = {"status": "current", "generation": 1, "tools": {}}

    def ambient(self):
        service = mock.Mock(core_admission_snapshot=lambda: dict(self.SNAPSHOT))
        return mock.Mock(service=service)

    def test_the_tool_projects_a_configured_service_admission(self):
        # Positive control: the pin hides nothing the tool would report.
        with mock.patch.object(compatibility.HEALTH, "governance", self.ambient()):
            data = self.assert_usable(_call(), {})
        expected = build_capability_catalog(core_admission=dict(self.SNAPSHOT))
        self.assertEqual(data["core_admission"], expected["core_admission"])
        self.assertNotEqual(
            data["core_admission"], build_capability_catalog()["core_admission"]
        )

    def test_the_fixture_pins_and_restores_an_ambient_service(self):
        ambient = self.ambient()
        probe = SummaryTests("test_the_unfiltered_call_is_a_bounded_summary")
        probe.dynamic, probe.gateway = self.dynamic, self.gateway
        with mock.patch.object(compatibility.HEALTH, "governance", ambient):
            probe.setUp()
            try:
                self.assertIsNone(compatibility.HEALTH.governance.service)
                probe.test_the_unfiltered_call_is_a_bounded_summary()
            finally:
                probe.doCleanups()
            self.assertIs(compatibility.HEALTH.governance, ambient)


class SummaryTests(_LiveShapedCatalog):
    def test_the_unfiltered_call_is_a_bounded_summary(self):
        data = self.assert_usable(_call(), {})
        catalog = build_capability_catalog()
        self.assertEqual(data["view"], "summary")
        self.assertEqual(data["tools"], catalog["tools"])
        for key in ("count", "registered_count", "engineering_registered_count",
                    "dynamic_upstream_count", "status_values", "planned",
                    "core_admission"):
            self.assertEqual(data[key], catalog[key], key)
        self.assertEqual(data["registered_count"], 105)
        self.assertEqual(
            data["server"],
            {
                "id": capabilities.SERVER_ID,
                "name": capabilities.SERVER_NAME,
                "version": capabilities.SERVER_VERSION,
                "schema_version": capabilities.SCHEMA_VERSION,
                "build_sha": capabilities.BUILD_SHA,
            },
        )
        index = {item["tool"]: item for item in data["tool_index"]}
        self.assertEqual(len(index), 105)
        for item in (*catalog["tools"], *catalog["beta_native"],
                     *catalog["dynamic_upstream"]):
            self.assertEqual(
                index[item["tool"]],
                {"tool": item["tool"], "status": item["status"],
                 "category": item["category"]},
            )
        for name in ("beta_native", "dynamic_upstream", "provider_matrix"):
            self.assertNotIn(name, data)
            self.assertEqual(data["sections"][name]["included"], "omitted")
            self.assertEqual(data["sections"][name]["item_count"], len(catalog[name]))
            self.assertEqual(data["sections"][name]["request"], {"detail_section": name})
        self.assertEqual(data["sections"]["tools"]["included"], "full")
        self.assertEqual(data["sections"]["upstream_read_gateway"]["included"], "summary")
        self.assertIn("upstream_tool", {k for i in catalog["dynamic_upstream"] for k in i})
        self.assertEqual(sorted(data["category_values"]),
                         sorted(set(CATEGORIES + EXTRA_CATEGORIES)))

    def test_the_summary_gateway_keeps_identity_and_states_detail_counts(self):
        data = self.assert_usable(_call(), {})
        summary = data["upstream_read_gateway"]
        self.assertEqual(summary["detail_scope"], "summary")
        for field in GATEWAY_DETAIL_FIELDS:
            self.assertNotIn(field, summary)
            if isinstance(self.gateway.get(field), (list, dict)):
                self.assertEqual(
                    summary["detail_total_counts"][field], len(self.gateway[field])
                )
        for key, value in self.gateway.items():
            if key not in GATEWAY_DETAIL_FIELDS and key != "automatic_readmission":
                self.assertEqual(summary[key], value, key)
        self.assertEqual(
            data["sections"]["upstream_read_gateway"]["item_count"],
            sum(summary["detail_total_counts"].values()),
        )


class FilterSemanticsTests(_LiveShapedCatalog):
    def test_a_status_filter_narrows_every_section(self):
        full = build_capability_catalog()
        for status in STATUSES:
            with self.subTest(status=status):
                data = self.assert_usable(_call(status=status), {"status": status})
                self.assertEqual(data["view"], "filtered")
                data = {
                    **data,
                    **{name: self.section(data, name) for name in CATALOG_SECTIONS},
                }
                for name in ("tools", "beta_native", "dynamic_upstream"):
                    self.assertEqual(
                        data[name],
                        [item for item in build_capability_catalog()[name]
                         if item["status"] == status],
                    )
                selected = {
                    name
                    for item in (*data["tools"], *data["beta_native"],
                                 *data["dynamic_upstream"])
                    for name in (item["tool"], item.get("upstream_tool"))
                    if name
                }
                self.assertEqual(
                    data["provider_matrix"],
                    [row for row in full["provider_matrix"] if row["tool"] in selected],
                )
                gateway = data["upstream_read_gateway"]
                self.assertEqual(gateway["detail_scope"], "selected_tools")
                for field in GATEWAY_DETAIL_FIELDS:
                    rows = gateway.get(field)
                    if isinstance(rows, list):
                        for row in rows:
                            self.assertTrue(
                                capabilities._gateway_row_names(row) & selected, row
                            )
                    elif isinstance(rows, dict):
                        self.assertLessEqual(set(rows), selected)

    def test_delegated_narrows_beta_native_matrix_and_gateway(self):
        full = build_capability_catalog()
        data = self.assert_usable(_call(status="delegated"), {"status": "delegated"})
        self.assertEqual(data["beta_native"], [])
        self.assertEqual(data["tools"], [])
        self.assertEqual(len(data["dynamic_upstream"]), 28)
        self.assertLess(len(data["provider_matrix"]), len(full["provider_matrix"]))
        self.assertTrue(data["provider_matrix"])
        self.assertLessEqual(
            {row["tool"] for row in data["provider_matrix"]},
            {item["tool"] for item in data["dynamic_upstream"]}
            | {item["upstream_tool"] for item in data["dynamic_upstream"]},
        )
        gateway = data["upstream_read_gateway"]
        exposed = {item["tool"] for item in data["dynamic_upstream"]} | {
            item["upstream_tool"] for item in data["dynamic_upstream"]
        }
        self.assertTrue(gateway["exposed_tools"])
        self.assertLessEqual(set(gateway["exposed_tools"]), exposed)
        # Blocked upstream tools are not delegated capabilities.
        self.assertEqual(gateway["blocked_tools"], [])
        self.assertEqual(
            gateway["detail_total_counts"]["blocked_tools"],
            len(self.gateway["blocked_tools"]),
        )

    def test_category_matching_semantics_are_unchanged(self):
        for category in CATEGORIES + EXTRA_CATEGORIES + ("FOUNDATION ", "unknown"):
            with self.subTest(category=category):
                data = self.assert_usable(_call(category=category), {"category": category})
                expected = category.strip().lower()
                self.assertEqual(data["filters"]["category"], expected)
                for name, source in (
                    ("tools", capabilities.CAPABILITIES),
                    ("beta_native", capabilities.BETA_NATIVE_CAPABILITIES),
                    ("dynamic_upstream", self.dynamic),
                ):
                    self.assertEqual(
                        [item["tool"] for item in data[name]],
                        [item["tool"] for item in source
                         if item["category"] == expected],
                    )

    def test_filters_combine_as_a_conjunction(self):
        data = self.assert_usable(
            _call(status="beta_native", category="governance"),
            {"status": "beta_native", "category": "governance"},
        )
        self.assertTrue(data["beta_native"])
        self.assertEqual(
            [item["tool"] for item in data["beta_native"]],
            [item["tool"] for item in capabilities.BETA_NATIVE_CAPABILITIES
             if item["status"] == "beta_native" and item["category"] == "governance"],
        )
        self.assertEqual(data["tools"], [])
        self.assertEqual(data["dynamic_upstream"], [])

    def test_an_unknown_filter_value_is_an_empty_narrowing_not_an_error(self):
        data = self.assert_usable(_call(status="unknown"), {"status": "unknown"})
        for name in ("tools", "beta_native", "dynamic_upstream", "provider_matrix"):
            self.assertEqual(data[name], [])

    def test_filtered_responses_keep_the_established_shape(self):
        for arguments in ({"status": "native"}, {"status": "deprecated"},
                          {"status": "transitional"}, {"category": "foundation"}):
            with self.subTest(**arguments):
                data = self.assert_usable(_call(**arguments), arguments)
                self.assertLessEqual(LEGACY_KEYS, set(data))
                self.assertEqual(
                    data["tools"], build_capability_catalog(**arguments)["tools"]
                )
                self.assertEqual(
                    data["filters"],
                    {"status": arguments.get("status"),
                     "category": arguments.get("category"),
                     "detail_section": None},
                )

    def test_no_argument_catalog_builder_is_unchanged(self):
        catalog = build_capability_catalog()
        self.assertEqual(
            catalog["provider_matrix"],
            [dict(row) for row in capabilities.CAPABILITY_PROVIDER_MATRIX],
        )
        self.assertEqual(catalog["upstream_read_gateway"], self.gateway)
        self.assertEqual(len(catalog["beta_native"]), 52)


class SectionSelectorTests(_LiveShapedCatalog):
    def test_each_section_is_returned_in_full(self):
        catalog = build_capability_catalog()
        for section in CATALOG_SECTIONS:
            with self.subTest(section=section):
                data = self.assert_usable(
                    _call(detail_section=section), {"detail_section": section}
                )
                self.assertEqual(data["view"], "section")
                self.assertEqual(data[section], catalog[section])
                self.assertEqual(data["filters"]["detail_section"], section)
                for other in set(CATALOG_SECTIONS) - {section}:
                    self.assertNotIn(other, data)
                    self.assertEqual(data["sections"][other]["included"], "omitted")
                self.assertEqual(data["sections"][section]["included"], "full")

    def test_filters_still_apply_to_the_section(self):
        for section in CATALOG_SECTIONS:
            for arguments in ({"status": "delegated"}, {"category": "foundation"},
                              {"status": "beta_native", "category": "evidence"}):
                with self.subTest(section=section, **arguments):
                    data = self.assert_usable(
                        _call(detail_section=section, **arguments),
                        {"detail_section": section, **arguments},
                    )
                    self.assertEqual(
                        data[section], build_capability_catalog(**arguments)[section]
                    )
                    self.assertEqual(
                        data["sections"][section]["request"],
                        {"detail_section": section, **arguments},
                    )

    def test_the_section_name_is_normalized(self):
        data = self.assert_usable(
            _call(detail_section=" Beta_Native "), {"detail_section": " Beta_Native "}
        )
        self.assertEqual(data["filters"]["detail_section"], "beta_native")
        self.assertEqual(len(data["beta_native"]), 52)

    def test_an_unknown_section_is_refused_clearly(self):
        for section in ("planned", "everything", "tool_index", "beta-native"):
            with self.subTest(section=section):
                result = json.loads(_call(detail_section=section))
                self.assertFalse(result["success"])
                self.assertEqual(result["error"], "InvalidRequestError")
                self.assertEqual(result["error_code"], "invalid_request")
                self.assertNotIn("data", result)
                details = result["details"]
                self.assertEqual(details["field"], "detail_section")
                self.assertEqual(details["reason"], "unknown_section")
                self.assertEqual(details["allowed"], list(CATALOG_SECTIONS))
                with self.assertRaises(InvalidRequestError):
                    build_capability_view(detail_section=section)

    def test_every_advertised_request_returns_its_section(self):
        for arguments in ({}, {"status": "delegated"}, {"category": "evidence"}):
            data = build_capability_view(**arguments)
            for section, entry in data["sections"].items():
                with self.subTest(section=section, **arguments):
                    view = build_capability_view(**entry["request"])
                    self.assertEqual(view["sections"][section]["included"], "full")
                    self.assertIn(section, view)


LIST_SECTIONS = ("tools", "beta_native", "dynamic_upstream", "provider_matrix")


class _Crawler:
    """Follows every retrieval request through the public callable."""

    def crawl(self, arguments, limit):
        """Every response from ``arguments`` on, each request followed once.

        Asserts what every response must hold: the cap, success with
        ``/data``, no envelope truncation, a request for each omission, a
        nonempty row list on every page, retrieval exactly on non-final
        pages, and no request repeated (so the crawl ends).
        """

        responses = []
        queue = [dict(arguments)]
        issued = set()
        while queue:
            current = queue.pop(0)
            key = json.dumps(current, sort_keys=True)
            self.assertNotIn(key, issued, "repeated request")
            issued.add(key)
            self.assertLess(len(issued), 500, "the crawl does not end")
            text = _call_at(limit, **current)
            self.assertLessEqual(len(text), limit, current)
            result = json.loads(text)
            self.assertTrue(result["success"], (current, result))
            self.assertNotIn("response_completeness", result, current)
            data = result["data"]
            size = data["size_limit"]
            self.assertEqual(bool(size["omitted_sections"]), bool(size["retrieval"]), current)
            for request in size["retrieval"]:
                self.assertEqual(request["tool"], "list_capabilities")
                self.assertTrue(request["arguments"], current)
            section = current.get("detail_section")
            value = data.get(section) if section else None
            if isinstance(value, dict) and value.get("paged"):
                self.assertTrue(value["rows"], "empty page")
                self.assertEqual(value["page"]["row_count"], len(value["rows"]))
                self.assertEqual(value["page"]["final"], not value["retrieval"])
                self.assertEqual(data["sections"][section]["included"], "paged")
            if isinstance(value, dict) and "detail_cursor" in json.dumps(
                size["retrieval"]
            ):
                for request in size["retrieval"]:
                    # A page request keeps the section and every filter.
                    self.assertEqual(
                        {k: v for k, v in request["arguments"].items()
                         if k != "detail_cursor"},
                        {k: v for k, v in current.items() if k != "detail_cursor"},
                    )
            responses.append((current, data))
            queue.extend(request["arguments"] for request in size["retrieval"])
        return responses

    def list_rows(self, responses, section):
        """The section's rows reassembled from one section crawl."""

        rows = []
        offsets = []
        for arguments, data in responses:
            if arguments.get("detail_section") != section:
                continue
            value = data[section]
            if isinstance(value, list):
                self.assertEqual(rows, [], "a full section after a page")
                return value
            self.assertTrue(value["paged"], value)
            # Each page starts where the previous one ended: no gap, no overlap.
            self.assertEqual(value["page"]["offset"], len(rows))
            offsets.append(value["page"]["offset"])
            rows.extend(value["rows"])
        self.assertEqual(len(offsets), len(set(offsets)))
        return rows

    def gateway_rows(self, responses):
        """The gateway block reassembled from one gateway section crawl."""

        rebuilt = None
        offsets = {}
        for arguments, data in responses:
            value = data["upstream_read_gateway"]
            if "detail_cursor" not in arguments:
                self.assertIsNone(rebuilt)
                self.assertNotIn("omitted", value)
                if not value.get("detail_omitted_fields"):
                    return value
                rebuilt = {
                    key: item for key, item in value.items()
                    if key not in ("detail_complete", "detail_omitted_fields", "retrieval")
                }
                if "automatic_readmission" in rebuilt:
                    rebuilt["automatic_readmission"] = dict(rebuilt["automatic_readmission"])
                continue
            field = value["field"]
            self.assertEqual(value["page"]["offset"], offsets.get(field, 0), field)
            offsets[field] = value["page"]["offset"] + len(value["rows"])
            if field == capabilities._READMISSION_FIELD:
                target = rebuilt["automatic_readmission"].setdefault(
                    "readmission_withheld_tools", []
                )
            else:
                target = rebuilt.setdefault(
                    field, {} if isinstance(value["rows"], dict) else []
                )
            self.assertIsInstance(value["rows"], type(target))
            if isinstance(target, dict):
                self.assertFalse(set(target) & set(value["rows"]), "duplicate rows")
                target.update(value["rows"])
            else:
                target.extend(value["rows"])
        return rebuilt

    def assert_same(self, recovered, source):
        # Byte-for-byte, in source order.  A reassembled gateway re-adds its
        # paged fields after the identity fields, so its keys are compared as
        # a set and each field's value byte-for-byte.
        if isinstance(source, dict):
            self.assertEqual(sorted(recovered), sorted(source))
            for key in source:
                self.assertEqual(
                    json.dumps(recovered[key]), json.dumps(source[key]), key
                )
            return
        self.assertEqual(json.dumps(recovered), json.dumps(source))


class SizeLimitTests(_LiveShapedCatalog, _Crawler):
    """Reachable size-limit paths, forced with a small response limit."""

    def test_following_retrieval_recovers_every_section_under_a_small_limit(self):
        # Each limit is below the summary and the largest sections.
        for factor, limit in ((1.0, 24_000), (1.5, 30_000), (1.0, 16_000)):
            with self.subTest(factor=factor, limit=limit), self.grown(factor):
                catalog = build_capability_catalog()
                summary = self.crawl({}, limit)
                self.assertTrue(summary[0][1]["size_limit"]["omitted_sections"])
                for section in CATALOG_SECTIONS:
                    responses = self.crawl(
                        summary[0][1]["sections"][section]["request"], limit
                    )
                    recovered = (
                        self.gateway_rows(responses)
                        if section == "upstream_read_gateway"
                        else self.list_rows(responses, section)
                    )
                    self.assert_same(recovered, catalog[section])

    def test_every_filtered_section_crawl_recovers_its_rows(self):
        catalog = build_capability_view(response_limit=10**6)
        for status in ("", *STATUSES):
            for category in ("", "upstream_read_gateway", "evidence", "discovery"):
                filters = {k: v for k, v in (("status", status), ("category", category)) if v}
                source = build_capability_catalog(**filters)
                for section in CATALOG_SECTIONS:
                    with self.subTest(section=section, **filters):
                        # The gateway's identity fields alone need ~13k.
                        limit = 16_000 if section == "upstream_read_gateway" else 12_000
                        responses = self.crawl(
                            {"detail_section": section, **filters}, limit
                        )
                        recovered = (
                            self.gateway_rows(responses)
                            if section == "upstream_read_gateway"
                            else self.list_rows(responses, section)
                        )
                        self.assert_same(recovered, source[section])
        self.assertTrue(catalog["category_values"])

    def test_an_oversize_section_is_paged_in_catalog_order(self):
        data = build_capability_view(detail_section="beta_native", response_limit=20_000)
        record = data["beta_native"]
        self.assertIs(record["paged"], True)
        self.assertEqual(record["item_count"], 52)
        self.assertEqual(record["page"]["offset"], 0)
        self.assertIs(record["page"]["final"], False)
        count = record["page"]["row_count"]
        self.assertGreaterEqual(count, 1)
        self.assertEqual(record["page"]["next_offset"], count)
        self.assertEqual(record["rows"], build_capability_catalog()["beta_native"][:count])
        [request] = record["retrieval"]
        arguments = dict(request["arguments"])
        self.assertTrue(arguments.pop("detail_cursor").startswith("lc1."))
        self.assertEqual(arguments, {"detail_section": "beta_native"})
        self.assertEqual(data["size_limit"]["retrieval"], record["retrieval"])
        self.assertEqual(data["size_limit"]["omitted_sections"], ["beta_native"])
        self.assertEqual(data["sections"]["beta_native"]["included"], "paged")
        for key in ("omitted", "item_names", "unpartitioned_items",
                    "retrieval_unavailable_reason"):
            self.assertNotIn(key, record)

    def test_the_largest_page_that_fits_is_served(self):
        # One more row than served would not fit; the page is not short.
        for arguments, limit in (
            ({"detail_section": "beta_native"}, 20_000),
            ({"detail_section": "provider_matrix", "category": "discovery"}, 8_000),
        ):
            with self.subTest(**arguments):
                data = build_capability_view(**arguments, response_limit=limit)
                section = arguments["detail_section"]
                record = data[section]
                count = record["page"]["row_count"]
                source = build_capability_catalog(
                    **{k: v for k, v in arguments.items() if k != "detail_section"}
                )[section]
                self.assertLess(count, len(source))
                longer = json.loads(json.dumps(data))
                longer[section]["rows"] = source[: count + 1]
                longer[section]["page"]["row_count"] = count + 1
                longer[section]["page"]["next_offset"] = count + 1
                self.assertGreater(
                    len(_compact({"data": longer})),
                    limit - capabilities.CATALOG_ENVELOPE_RESERVE_CHARS,
                )

    def test_a_category_filtered_oversize_section_pages_with_its_filters(self):
        responses = self.crawl(
            {"detail_section": "provider_matrix", "category": "discovery"}, 8_000
        )
        self.assertGreater(len(responses), 1)
        self.assert_same(
            self.list_rows(responses, "provider_matrix"),
            build_capability_catalog(category="discovery")["provider_matrix"],
        )

    def test_an_oversize_gateway_drops_detail_fields_and_says_so(self):
        # Between the identity floor and the full section, per filter.
        for arguments, limit in (({}, 14_000), ({"status": "delegated"}, 13_000)):
            with self.subTest(**arguments), self.grown(4.0):
                data = build_capability_view(
                    detail_section="upstream_read_gateway", response_limit=limit,
                    **arguments,
                )
                gateway = data["upstream_read_gateway"]
                source = build_capability_catalog(**arguments)["upstream_read_gateway"]
                self.assertIs(gateway["detail_complete"], False)
                self.assertTrue(gateway["detail_omitted_fields"])
                for field, count in gateway["detail_omitted_fields"].items():
                    self.assertNotIn(field, gateway)
                    self.assertEqual(count, len(source[field]))
                self.assertEqual(
                    len(gateway["retrieval"]), len(gateway["detail_omitted_fields"])
                )
                self.assertEqual(data["size_limit"]["retrieval"], gateway["retrieval"])
                self.assertEqual(
                    data["sections"]["upstream_read_gateway"]["included"], "partial"
                )
                self.assertLessEqual(
                    len(_compact({"data": data})),
                    limit - capabilities.CATALOG_ENVELOPE_RESERVE_CHARS,
                )

    def test_a_gateway_whose_identity_cannot_fit_still_pages_every_field(self):
        data = build_capability_view(
            detail_section="upstream_read_gateway", response_limit=9_000
        )
        record = data["upstream_read_gateway"]
        self.assertTrue(record["omitted"])
        self.assertEqual(record["reason"], "section_exceeds_response_limit")
        source = build_capability_catalog()["upstream_read_gateway"]
        nonempty = {
            field: len(source[field]) for field in GATEWAY_DETAIL_FIELDS
            if source.get(field)
        }
        self.assertEqual(record["detail_omitted_fields"], nonempty)
        self.assertEqual(len(record["retrieval"]), len(nonempty))
        self.assertEqual(data["size_limit"]["retrieval"], record["retrieval"])
        self.assertLessEqual(
            len(_compact({"data": data})),
            9_000 - capabilities.CATALOG_ENVELOPE_RESERVE_CHARS,
        )
        responses = self.crawl({"detail_section": "upstream_read_gateway"}, 9_000)
        for field, count in nonempty.items():
            rows = [
                row for arguments, page in responses[1:]
                if page["upstream_read_gateway"]["field"] == field
                for row in (
                    page["upstream_read_gateway"]["rows"].items()
                    if isinstance(source[field], dict)
                    else page["upstream_read_gateway"]["rows"]
                )
            ]
            self.assertEqual(len(rows), count, field)

    def test_no_single_class_outgrows_the_real_cap(self):
        # Every one of the 77 tools ha-mcp 8.6.0 offers, delegated at once,
        # still fits in one status-and-category class (84 rows here).
        self.assertEqual(len(capture("8.6.0")["tools"]), 77)
        with self.grown(3.0):
            self.assertEqual(len(capabilities._DYNAMIC_UPSTREAM_CAPABILITIES), 84)
            arguments = {"detail_section": "dynamic_upstream", "status": "delegated",
                         "category": "upstream_read_gateway"}
            data = self.assert_usable(_call(**arguments), arguments)
            self.assertEqual(data["size_limit"]["omitted_sections"], [])
            self.assertEqual(len(data["dynamic_upstream"]), 84)

    def test_an_oversized_terminal_class_is_paged_at_the_real_cap(self):
        # R1-M1 at 60k: a synthetic delegated upstream_read_gateway class
        # (both filters set) larger than the whole response.
        with self.grown(10.0):
            arguments = {"detail_section": "dynamic_upstream", "status": "delegated",
                         "category": "upstream_read_gateway"}
            source = build_capability_catalog(
                status="delegated", category="upstream_read_gateway"
            )["dynamic_upstream"]
            self.assertEqual(len(source), 280)
            self.assertGreater(len(_compact(source)), LIMIT)
            responses = self.crawl(arguments, LIMIT)
            self.assertGreaterEqual(len(responses), 3)
            self.assert_same(self.list_rows(responses, "dynamic_upstream"), source)

    def test_the_tool_never_reaches_the_envelope_truncation(self):
        small = replace(compatibility.SETTINGS, response_size_limit=24_000)
        with mock.patch.object(compatibility, "SETTINGS", small):
            for arguments in SHAPES:
                with self.subTest(**arguments):
                    text = _call(**arguments)
                    self.assertLessEqual(len(text), 24_000)
                    result = json.loads(text)
                    self.assertTrue(result["success"])
                    self.assertNotIn("response_completeness", result)
                    data = result["data"]
                    if data["size_limit"]["omitted_sections"]:
                        self.assertTrue(data["size_limit"]["retrieval"])

    def test_a_limit_too_small_for_one_row_says_so(self):
        # Below the default-size scope: the floor is stated, never a loop.
        data = build_capability_view(detail_section="beta_native", response_limit=6_000)
        record = data["beta_native"]
        self.assertEqual(record["reason"], "page_row_exceeds_response_limit")
        self.assertEqual(record["retrieval"], [])
        self.assertEqual(record["item_count"], 52)
        self.assertEqual(data["sections"]["beta_native"]["included"], "omitted_size_limit")


class SizeGuardTests(_LiveShapedCatalog):
    """The size guard's order, stopping point and statements, at limits
    derived from the measured sizes so each case hits one transition."""

    RESERVE = capabilities.CATALOG_ENVELOPE_RESERVE_CHARS

    def limit_dropping(self, arguments, *names):
        """A limit at which ``names`` together, and no fewer, must go."""

        full = build_capability_view(**arguments, response_limit=10**6)
        dropped = sum(len(_compact(full[name])) for name in names)
        last = len(_compact(full[names[-1]]))
        return len(_compact({"data": full})) + self.RESERVE - dropped + last // 2

    def test_the_largest_secondary_section_goes_first_and_only_as_needed(self):
        arguments = {"status": "delegated"}
        limit = self.limit_dropping(arguments, "provider_matrix")
        data = build_capability_view(**arguments, response_limit=limit)
        self.assertEqual(data["size_limit"]["omitted_sections"], ["provider_matrix"])
        self.assertNotIn("provider_matrix", data)
        for name in ("upstream_read_gateway", "dynamic_upstream", "beta_native", "tools"):
            self.assertIn(name, data)
        entry = data["sections"]["provider_matrix"]
        self.assertEqual(entry["included"], "omitted_size_limit")
        self.assertEqual(data["size_limit"]["retrieval"], [_request_of(entry)])
        self.assertLessEqual(len(_compact({"data": data})), limit - self.RESERVE)

    def test_dropping_the_tool_index_names_every_list_it_carries(self):
        limit = self.limit_dropping({}, "upstream_read_gateway", "tool_index")
        data = build_capability_view(response_limit=limit)
        self.assertEqual(
            data["size_limit"]["omitted_sections"],
            ["upstream_read_gateway", "tool_index"],
        )
        self.assertIn("tools", data)
        self.assertEqual(
            data["size_limit"]["retrieval"],
            [_request_of(data["sections"][name]) for name in (
                "upstream_read_gateway", "tools", "beta_native", "dynamic_upstream")],
        )
        self.assertEqual(
            data["sections"]["upstream_read_gateway"]["included"], "omitted_size_limit"
        )

    def oversize_gateway(self, gateway, arguments, dropped):
        """The gateway section at a limit where ``dropped`` alone must go."""

        with mock.patch.object(capabilities, "_UPSTREAM_READ_GATEWAY_SUMMARY", gateway):
            full = build_capability_view(
                detail_section="upstream_read_gateway", response_limit=10**6, **arguments
            )
            limit = (len(_compact({"data": full})) + self.RESERVE
                     - len(_compact(dropped)) // 2)
            data = build_capability_view(
                detail_section="upstream_read_gateway", response_limit=limit, **arguments
            )
        self.assertLessEqual(len(_compact({"data": data})), limit - self.RESERVE)
        return data["upstream_read_gateway"]

    def test_an_oversize_gateway_drops_its_largest_field_first(self):
        exposed = [f"ha_tool_{index:04d}" for index in range(400)]
        gateway = {"status": "ready", "exposed_tools": exposed,
                   "blocked_tools": [{"tool": "ha_x", "reason": "blocked"}]}
        value = self.oversize_gateway(gateway, {}, exposed)
        self.assertEqual(value["detail_omitted_fields"], {"exposed_tools": 400})
        self.assertEqual(value["blocked_tools"], gateway["blocked_tools"])
        self.assertEqual(value["status"], "ready")

    def test_an_oversize_gateway_drops_the_nested_readmission_list(self):
        withheld = [{"tool": f"ha_tool_{index:04d}", "reason": "withheld"}
                    for index in range(300)]
        gateway = {"status": "ready",
                   "automatic_readmission": {"state": "withheld",
                                             "readmission_withheld_tools": withheld}}
        value = self.oversize_gateway(gateway, {}, withheld)
        self.assertNotIn("omitted", value)
        self.assertEqual(
            value["detail_omitted_fields"],
            {"automatic_readmission.readmission_withheld_tools": 300},
        )
        self.assertEqual(value["automatic_readmission"], {"state": "withheld"})

    def test_an_oversize_gateway_pages_the_omitted_field_itself(self):
        # R1-M2: the request for an omitted field pages that field's own
        # rows, never a delegated-only or different-section call.
        # Between the identity floor and the full section, per filter.
        for arguments, limit in (({}, 14_000), ({"status": "delegated"}, 13_000)):
            with self.subTest(**arguments), self.grown(4.0):
                data = build_capability_view(
                    detail_section="upstream_read_gateway", response_limit=limit,
                    **arguments,
                )
                value = data["upstream_read_gateway"]
                for request, field in zip(value["retrieval"], value["detail_omitted_fields"]):
                    request_arguments = dict(request["arguments"])
                    cursor = request_arguments.pop("detail_cursor")
                    self.assertEqual(
                        request_arguments,
                        {"detail_section": "upstream_read_gateway", **arguments},
                    )
                    page = build_capability_view(
                        **request["arguments"], response_limit=limit
                    )["upstream_read_gateway"]
                    self.assertEqual(page["field"], field)
                    self.assertEqual(page["page"]["offset"], 0)
                    source = build_capability_catalog(**arguments)["upstream_read_gateway"]
                    expected = (
                        list(source[field].items())[: page["page"]["row_count"]]
                        if isinstance(source[field], dict)
                        else source[field][: page["page"]["row_count"]]
                    )
                    self.assertEqual(
                        list(page["rows"].items())
                        if isinstance(page["rows"], dict) else page["rows"],
                        expected,
                    )
                    self.assertTrue(cursor.startswith("lc1."))

    def test_an_oversize_section_counts_its_size_statement_at_every_limit(self):
        # Stepping through each limit where the section's record changes shape
        # finds any limit at which part of the response was left unsized.
        # Each range starts above the smallest record the section can return.
        for arguments, limits in (
            ({"detail_section": "upstream_read_gateway"}, range(6_000, 18_000, 11)),
            ({"detail_section": "upstream_read_gateway", "status": "delegated"},
             range(6_500, 12_000, 11)),
            ({"detail_section": "provider_matrix", "category": "discovery"},
             range(7_000, 12_000, 11)),
            ({"detail_section": "beta_native"}, range(10_000, 40_000, 23)),
        ):
            with self.subTest(**arguments):
                for limit in limits:
                    data = build_capability_view(**arguments, response_limit=limit)
                    self.assertLessEqual(
                        len(_compact({"data": data})), limit - self.RESERVE, limit
                    )


class _Catalog106(_LiveShapedCatalog):
    """The exact ha-mcp 8.5.0 component capture: 77 native + 29 upstream."""

    @staticmethod
    def upstream_capture():
        return captured("8.5.0"), "8.5.0"


M1_ARGUMENTS = {"detail_section": "dynamic_upstream", "status": "delegated",
                "category": "upstream_read_gateway"}


class ReviewReproductionTests(_Catalog106, _Crawler):
    """Round-1 R1-M1 and R1-M2, through the real callable and envelope."""

    def test_the_fixture_is_the_106_tool_catalog(self):
        catalog = build_capability_catalog()
        self.assertEqual(catalog["registered_count"], 106)
        self.assertEqual(catalog["dynamic_upstream_count"], 29)
        self.assertEqual(len(catalog["upstream_read_gateway"]["blocked_tools"]), 49)

    def test_r1_m1_a_terminal_class_is_crawled_to_completion(self):
        source = build_capability_catalog(
            status="delegated", category="upstream_read_gateway"
        )["dynamic_upstream"]
        self.assertEqual(len(source), 29)
        responses = self.crawl(M1_ARGUMENTS, 12_000)
        self.assertGreater(len(responses), 1)
        recovered = self.list_rows(responses, "dynamic_upstream")
        self.assert_same(recovered, source)
        names = [row["tool"] for row in recovered]
        self.assertEqual(len(names), len(set(names)))
        last = responses[-1][1]["dynamic_upstream"]
        self.assertIs(last["page"]["final"], True)
        self.assertEqual(last["retrieval"], [])
        self.assertEqual(responses[-1][1]["size_limit"]["retrieval"], [])

    def test_r1_m2_every_blocked_row_is_recovered_with_its_reason(self):
        source = build_capability_catalog()["upstream_read_gateway"]
        responses = self.crawl({"detail_section": "upstream_read_gateway"}, 16_000)
        first = responses[0][1]["upstream_read_gateway"]
        self.assertEqual(first["detail_omitted_fields"], {"blocked_tools": 49})
        for _arguments, data in responses[1:]:
            self.assertEqual(data["upstream_read_gateway"]["field"], "blocked_tools")
        rebuilt = self.gateway_rows(responses)
        self.assertEqual(len(rebuilt["blocked_tools"]), 49)
        self.assert_same(rebuilt["blocked_tools"], source["blocked_tools"])
        for row in rebuilt["blocked_tools"]:
            self.assertIn("classification", row)
        self.assertEqual(rebuilt, source)

    def test_r1_m2_active_filters_page_the_projected_gateway(self):
        for filters in ({"status": "delegated"}, {"category": "upstream_read_gateway"},
                        {"status": "delegated", "category": "upstream_read_gateway"}):
            with self.subTest(**filters):
                responses = self.crawl(
                    {"detail_section": "upstream_read_gateway", **filters}, 16_000
                )
                self.assert_same(
                    self.gateway_rows(responses),
                    build_capability_catalog(**filters)["upstream_read_gateway"],
                )

    def test_nominal_sections_are_complete_at_the_real_cap(self):
        for arguments in (M1_ARGUMENTS, {"detail_section": "upstream_read_gateway"}):
            with self.subTest(**arguments):
                data = self.assert_usable(_call(**arguments), arguments)
                self.assertEqual(data["sections"][arguments["detail_section"]]["included"], "full")
                self.assertEqual(data["size_limit"]["retrieval"], [])


def _synthetic_gateway(registered):
    """A gateway with every pageable field populated, naming ``registered``
    tools (so filters keep rows) and unregistered ones."""

    unregistered = [f"ha_synthetic_{index:03d}" for index in range(60)]
    names = [*registered, *unregistered]
    reason = "synthetic reason text " * 6

    def rows(field, key="tool"):
        return [
            {key: name, "reason": f"{field}:{copy}:{reason}"}
            for name in names for copy in range(2)
        ]

    return {
        "status": "ready",
        "upstream_version": "synthetic",
        "exposed_tools": list(registered),
        "collision_mappings": [
            {"upstream_name": name, "exposed_name": f"{name}_up", "reason": reason}
            for name in names
        ],
        "blocked_tools": [
            {"upstream_name": name, "classification": "physical_or_high_risk_action",
             "reason": reason}
            for name in names
        ],
        "held_tools": list(names),
        "live_canary_required_tools": list(names),
        "core_withheld_tools": {name: {"reason": reason} for name in names},
        "readmission_withheld_tools": rows("readmission_withheld_tools"),
        "quarantined_tools": rows("quarantined_tools"),
        "missing_tools": rows("missing_tools"),
        "unreviewed_tools": list(unregistered),
        "optional_tools": {name: {"status": "admitted", "contract": reason} for name in names},
        "optional_sibling_drift": rows("optional_sibling_drift"),
        "automatic_readmission": {
            "state": "withheld",
            "readmission_withheld_tools": rows("automatic_readmission"),
        },
    }


class GatewayFieldPagingTests(_Catalog106, _Crawler):
    """R1-M2 on every pageable gateway field, with active filters."""

    def setUp(self):
        super().setUp()
        self.synthetic = _synthetic_gateway([item["tool"] for item in self.dynamic])

    def test_every_field_is_recovered_under_every_filter(self):
        with mock.patch.object(
            capabilities, "_UPSTREAM_READ_GATEWAY_SUMMARY", self.synthetic
        ):
            for filters in ({}, {"status": "delegated"},
                            {"category": "upstream_read_gateway"},
                            {"status": "delegated", "category": "upstream_read_gateway"},
                            {"status": "native"}):
                with self.subTest(**filters):
                    source = build_capability_catalog(**filters)["upstream_read_gateway"]
                    responses = self.crawl(
                        {"detail_section": "upstream_read_gateway", **filters}, 16_000
                    )
                    rebuilt = self.gateway_rows(responses)
                    self.assert_same(rebuilt, source)
                    paged = {
                        data["upstream_read_gateway"]["field"]
                        for _arguments, data in responses[1:]
                    }
                    expected = {
                        field for field in capabilities.GATEWAY_PAGE_FIELDS
                        if capabilities._gateway_field_rows(source, field)
                    }
                    if filters.get("status") == "native":
                        # Nothing selected: every field is empty and fits.
                        self.assertEqual(expected, set())
                        self.assertEqual(len(responses), 1)
                    else:
                        self.assertTrue(
                            {"blocked_tools", "held_tools", "missing_tools",
                             "quarantined_tools", "optional_tools",
                             capabilities._READMISSION_FIELD} <= expected
                        )
                        self.assertLessEqual(paged, expected)
                        for field in expected - paged:
                            # A field not paged was returned whole.
                            first = responses[0][1]["upstream_read_gateway"]
                            self.assertNotIn(field, first["detail_omitted_fields"])

    def test_nested_readmission_and_mapping_rows_keep_their_shape(self):
        with mock.patch.object(
            capabilities, "_UPSTREAM_READ_GATEWAY_SUMMARY", self.synthetic
        ):
            responses = self.crawl({"detail_section": "upstream_read_gateway"}, 16_000)
            pages = {}
            for _arguments, data in responses[1:]:
                value = data["upstream_read_gateway"]
                pages.setdefault(value["field"], []).append(value)
            self.assertIn(capabilities._READMISSION_FIELD, pages)
            for value in pages["optional_tools"]:
                self.assertIsInstance(value["rows"], dict)
                self.assertTrue(value["rows"])
            for value in pages["blocked_tools"]:
                for row in value["rows"]:
                    self.assertEqual(set(row), {"upstream_name", "classification", "reason"})
            for field, values in pages.items():
                self.assertEqual(values[-1]["page"]["final"], True, field)
                self.assertEqual(
                    sum(len(value["rows"]) for value in values),
                    values[0]["item_count"],
                    field,
                )


class CursorTests(_Catalog106, _Crawler):
    """A cursor is honored only for the catalog, section and filters it was
    issued for; anything else is refused and the crawl restarts."""

    def first_cursor(self, arguments=M1_ARGUMENTS, limit=12_000):
        data = json.loads(_call_at(limit, **arguments))["data"]
        [request] = data["size_limit"]["retrieval"]
        return request["arguments"]

    def refusal(self, reason, cause, limit=12_000, **arguments):
        result = json.loads(_call_at(limit, **arguments))
        self.assertFalse(result["success"], arguments)
        self.assertEqual(result["error_code"], "invalid_request")
        self.assertNotIn("data", result)
        details = result["details"]
        self.assertEqual(details["field"], "detail_cursor")
        self.assertEqual(details["reason"], reason)
        if cause is not None:
            self.assertEqual(details["cause"], cause)
        return details

    def assert_restart_works(self, details, limit=12_000):
        restart = details["restart"]
        self.assertEqual(restart["tool"], "list_capabilities")
        self.assertNotIn("detail_cursor", restart["arguments"])
        self.assertTrue(json.loads(_call_at(limit, **restart["arguments"]))["success"])

    def test_a_cursor_from_a_changed_catalog_is_stale(self):
        arguments = self.first_cursor()
        changes = (
            ("_DYNAMIC_UPSTREAM_CAPABILITIES", self.dynamic[:-1]),
            ("_UPSTREAM_READ_GATEWAY_SUMMARY", {**self.gateway, "status": "changed"}),
        )
        for name, value in changes:
            with self.subTest(changed=name), mock.patch.object(capabilities, name, value):
                details = self.refusal("stale_cursor", "catalog_changed", **arguments)
                self.assertEqual(
                    details["restart"]["arguments"],
                    {k: v for k, v in arguments.items() if k != "detail_cursor"},
                )
                self.assert_restart_works(details)
        # A changed Core admission projection changes the catalog too.
        with self.assertRaises(InvalidRequestError) as raised:
            build_capability_view(
                **arguments, response_limit=12_000,
                core_admission={"status": "current", "generation": 99, "tools": {}},
            )
        self.assertEqual(raised.exception.details["reason"], "stale_cursor")
        # Unchanged, the same cursor still serves its page.
        self.assertTrue(json.loads(_call_at(12_000, **arguments))["success"])

    def test_a_cursor_with_different_filters_is_refused(self):
        arguments = self.first_cursor()
        cursor = arguments["detail_cursor"]
        for filters in ({"status": "delegated"}, {"category": "upstream_read_gateway"},
                        {"status": "native", "category": "upstream_read_gateway"}, {}):
            with self.subTest(**filters):
                details = self.refusal(
                    "invalid_cursor", "filter_mismatch",
                    detail_section="dynamic_upstream", detail_cursor=cursor, **filters,
                )
                self.assert_restart_works(details)

    def test_a_cursor_for_another_section_is_refused(self):
        arguments = self.first_cursor()
        for section in ("beta_native", "upstream_read_gateway", "tools"):
            with self.subTest(section=section):
                self.refusal(
                    "invalid_cursor", "section_mismatch",
                    **{**arguments, "detail_section": section},
                )

    def test_a_malformed_cursor_is_refused(self):
        cursor = self.first_cursor()["detail_cursor"]
        body, digest = cursor[len("lc1."):].split(".")
        flipped = ("B" if body[5] == "A" else "A")
        valid = json.loads(base64.urlsafe_b64decode(body + "=" * (-len(body) % 4)))

        def encoded(state):
            return capabilities._encode_cursor(state)

        malformed = {
            "garbage": "not-a-cursor",
            "prefix_only": "lc1.",
            "wrong_prefix": "lc2." + cursor[4:],
            "body_changed": f"lc1.{body[:5]}{flipped}{body[6:]}.{digest}",
            "digest_changed": f"lc1.{body}.{'0' * len(digest)}",
            "extra_part": cursor + ".x",
            "not_base64": "lc1.***.abc",
            "overlong": "lc1." + "A" * 3_000,
            # Correctly signed, so only the length bound refuses it.
            "overlong_signed": encoded({**valid, "ca": "x" * 3_000}),
            "missing_key": encoded({k: v for k, v in valid.items() if k != "o"}),
            "extra_key": encoded({**valid, "x": 1}),
            "negative_offset": encoded({**valid, "o": -1}),
            "string_offset": encoded({**valid, "o": "1"}),
            "boolean_offset": encoded({**valid, "o": True}),
            "unknown_section": encoded({**valid, "s": "planned"}),
            "field_on_list_section": encoded({**valid, "f": "blocked_tools"}),
            "not_an_object": capabilities._encode_cursor([1, 2]),
        }
        for label, value in malformed.items():
            with self.subTest(label=label):
                details = self.refusal(
                    "invalid_cursor", "malformed",
                    **{**M1_ARGUMENTS, "detail_cursor": value},
                )
                self.assert_restart_works(details)
        gateway = self.first_cursor({"detail_section": "upstream_read_gateway"}, 16_000)
        gateway_state = json.loads(base64.urlsafe_b64decode(
            gateway["detail_cursor"][4:].split(".")[0] + "=="))
        for label, state in (
            ("no_field_on_gateway", {**gateway_state, "f": None}),
            ("unknown_field", {**gateway_state, "f": "status"}),
        ):
            with self.subTest(label=label):
                self.refusal(
                    "invalid_cursor", "malformed", limit=16_000,
                    detail_section="upstream_read_gateway", detail_cursor=encoded(state),
                )

    def test_a_forged_position_past_the_end_is_refused(self):
        cursor = self.first_cursor()["detail_cursor"]
        state = json.loads(base64.urlsafe_b64decode(cursor[4:].split(".")[0] + "=="))
        for offset in (29, 10_000):
            with self.subTest(offset=offset):
                self.refusal(
                    "invalid_cursor", "position_out_of_range",
                    **{**M1_ARGUMENTS,
                       "detail_cursor": capabilities._encode_cursor({**state, "o": offset})},
                )

    def test_a_cursor_requires_a_detail_section(self):
        cursor = self.first_cursor()["detail_cursor"]
        for arguments in ({}, {"status": "delegated"},
                          {"status": "delegated", "category": "upstream_read_gateway"}):
            with self.subTest(**arguments):
                details = self.refusal(
                    "cursor_requires_detail_section", None,
                    detail_cursor=cursor, **arguments,
                )
                self.assertNotIn("restart", details)
        with self.assertRaises(InvalidRequestError):
            build_capability_view(detail_cursor="anything")

    def test_an_unknown_section_is_refused_before_the_cursor(self):
        result = json.loads(_call(detail_section="everything", detail_cursor="x"))
        self.assertEqual(result["details"]["reason"], "unknown_section")

    def test_an_empty_cursor_is_the_first_page(self):
        for cursor in ("", "   "):
            with self.subTest(cursor=cursor):
                self.assertEqual(
                    json.loads(_call_at(12_000, **M1_ARGUMENTS, detail_cursor=cursor))["data"]["dynamic_upstream"]["page"]["offset"],
                    0,
                )

    def test_a_cursor_names_its_section_field_filters_position_and_catalog(self):
        cursor = self.first_cursor()["detail_cursor"]
        state = json.loads(base64.urlsafe_b64decode(cursor[4:].split(".")[0] + "=="))
        self.assertEqual(
            {k: state[k] for k in ("s", "f", "st", "ca")},
            {"s": "dynamic_upstream", "f": None, "st": "delegated",
             "ca": "upstream_read_gateway"},
        )
        self.assertGreater(state["o"], 0)
        self.assertEqual(state["fp"], capabilities._catalog_fingerprint(None))


class GatewayProjectionTests(unittest.TestCase):
    """The gateway projection on synthetic rows of every shape it narrows."""

    GATEWAY = {
        "status": "ready",
        "exposed_tools": ["ha_a", "ha_b"],
        "collision_mappings": [
            {"upstream_name": "ha_a", "exposed_name": "ha_a_up", "reason": "x"},
            {"upstream_name": "ha_b", "exposed_name": "ha_b_up", "reason": "y"},
        ],
        "blocked_tools": [{"tool": "ha_b", "reason": "blocked"}],
        "core_withheld_tools": {"ha_a": "core", "ha_b": "core"},
        "automatic_readmission": {
            "state": "withheld",
            "readmission_withheld_tools": [
                {"tool": "ha_a", "reason": "r"},
                {"tool": "ha_b", "reason": "r"},
            ],
        },
    }

    def test_rows_naming_a_selected_tool_by_any_key_are_kept(self):
        for names, kept in (
            (frozenset({"ha_a"}), "ha_a"),
            (frozenset({"ha_b_up"}), "ha_b"),
        ):
            with self.subTest(names=sorted(names)):
                value = capabilities._project_gateway(
                    self.GATEWAY, names, scope="selected_tools"
                )
                self.assertEqual(
                    [row["upstream_name"] for row in value["collision_mappings"]],
                    [kept],
                )

    def test_string_dict_and_nested_rows_are_narrowed(self):
        value = capabilities._project_gateway(
            self.GATEWAY, frozenset({"ha_a"}), scope="selected_tools"
        )
        self.assertEqual(value["exposed_tools"], ["ha_a"])
        self.assertEqual(value["blocked_tools"], [])
        self.assertEqual(value["core_withheld_tools"], {"ha_a": "core"})
        self.assertEqual(
            value["automatic_readmission"],
            {"state": "withheld",
             "readmission_withheld_tools": [{"tool": "ha_a", "reason": "r"}]},
        )
        self.assertEqual(value["status"], "ready")
        self.assertEqual(value["detail_scope"], "selected_tools")
        self.assertEqual(
            value["detail_total_counts"],
            {"exposed_tools": 2, "collision_mappings": 2, "blocked_tools": 1,
             "core_withheld_tools": 2},
        )
        # The published snapshot itself is never changed.
        self.assertEqual(
            len(self.GATEWAY["automatic_readmission"]["readmission_withheld_tools"]), 2
        )

    def test_the_summary_scope_drops_every_detail_list(self):
        value = capabilities._project_gateway(self.GATEWAY, None, scope="summary")
        for field in GATEWAY_DETAIL_FIELDS:
            self.assertNotIn(field, value)
        self.assertEqual(value["automatic_readmission"], {"state": "withheld"})
        self.assertEqual(value["detail_total_counts"]["exposed_tools"], 2)

    def test_no_selection_keeps_every_row(self):
        value = capabilities._project_gateway(self.GATEWAY, None, scope="full")
        for field in ("exposed_tools", "collision_mappings", "blocked_tools",
                      "core_withheld_tools", "automatic_readmission"):
            self.assertEqual(value[field], self.GATEWAY[field])


if __name__ == "__main__":
    unittest.main()
