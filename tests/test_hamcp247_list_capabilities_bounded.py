"""HAMCP-247: list_capabilities returns usable data within the response cap.

Live beta.44 returned success with ``/data`` omitted and an empty retrieval
list for the unfiltered call (96,902 chars) and ``status=delegated``
(91,274 chars): beta_native, provider_matrix and the gateway snapshot were
never narrowed by a filter, and the envelope's generic bound drops ``/data``
whole.  The tool now returns a bounded summary by default, narrows every
section by a filter, and returns one full section on request.

The catalog is live-shaped: the real UpstreamReadGateway is driven offline
with the reviewed ha-mcp 8.6.0 capture (28 dynamic tools, 105 registered).
Every call shape is rendered by the real tool and envelope, at that size and
with every section grown by half.  Nothing is sent anywhere.
"""

from __future__ import annotations

import asyncio
from contextlib import contextmanager
from dataclasses import replace
import json
import math
from pathlib import Path
import sys
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
from ha_mcp_engineering.providers.upstream_read_gateway import (  # noqa: E402
    UpstreamReadGateway,
)
from ha_mcp_engineering.tools import compatibility  # noqa: E402
from test_ha_mcp_production_readmission import _GatewayTransport, _settings  # noqa: E402
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


class _LiveShapedCatalog(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._saved = (
            capabilities._DYNAMIC_UPSTREAM_CAPABILITIES,
            capabilities._UPSTREAM_READ_GATEWAY_SUMMARY,
        )

        async def publish():
            transport = _GatewayTransport(capture("8.6.0")["tools"], version="8.6.0")
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


class SizeLimitTests(_LiveShapedCatalog):
    """Reachable size-limit paths, forced with a small response limit."""

    def crawl(self, arguments, limit, seen, depth=0):
        self.assertLess(depth, 4, arguments)
        data = build_capability_view(**arguments, response_limit=limit)
        self.assertLessEqual(len(_compact({"data": data})),
                             limit - capabilities.CATALOG_ENVELOPE_RESERVE_CHARS)
        if data["size_limit"]["omitted_sections"]:
            self.assertTrue(data["size_limit"]["retrieval"], arguments)
        for name in ("tools", "beta_native", "dynamic_upstream", "provider_matrix"):
            value = data.get(name)
            if isinstance(value, dict):
                value = value.get("unpartitioned_items", [])
            if isinstance(value, list):
                seen.setdefault(name, set()).update(
                    (item["tool"], json.dumps(item, sort_keys=True)) for item in value
                )
        for request in data["size_limit"]["retrieval"]:
            self.assertEqual(request["tool"], "list_capabilities")
            self.crawl(request["arguments"], limit, seen, depth + 1)
        return data

    def test_following_retrieval_recovers_every_item_under_a_small_limit(self):
        # Each limit is below the summary and the largest sections and above
        # the largest status-and-category class at that size.
        for factor, limit in ((1.0, 24_000), (1.5, 30_000)):
            with self.subTest(factor=factor), self.grown(factor):
                catalog = build_capability_catalog()
                seen = {}
                data = self.crawl({}, limit, seen)
                self.assertTrue(data["size_limit"]["omitted_sections"])
                for section in CATALOG_SECTIONS:
                    self.crawl(data["sections"][section]["request"], limit, seen)
                for name in ("tools", "beta_native", "dynamic_upstream", "provider_matrix"):
                    self.assertEqual(
                        seen[name],
                        {(item["tool"], json.dumps(item, sort_keys=True))
                         for item in catalog[name]},
                        name,
                    )

    def test_an_oversize_section_names_its_partitions(self):
        data = build_capability_view(detail_section="beta_native", response_limit=20_000)
        record = data["beta_native"]
        self.assertTrue(record["omitted"])
        self.assertEqual(record["reason"], "section_exceeds_response_limit")
        self.assertEqual(record["item_count"], 52)
        self.assertEqual(len(record["item_names"]), 52)
        self.assertEqual(record["unpartitioned_item_count"], 0)
        self.assertEqual(record["unpartitioned_items"], [])
        self.assertEqual(
            [request["arguments"] for request in record["retrieval"]],
            [
                {"detail_section": "beta_native", "category": category}
                for category in sorted(
                    {item["category"] for item in capabilities.BETA_NATIVE_CAPABILITIES}
                )
            ],
        )
        self.assertEqual(data["size_limit"]["retrieval"], record["retrieval"])
        self.assertEqual(data["sections"]["beta_native"]["included"], "omitted_size_limit")

    def test_a_category_filtered_oversize_section_partitions_by_status(self):
        data = build_capability_view(
            detail_section="provider_matrix", category="discovery", response_limit=8_000
        )
        record = data["provider_matrix"]
        self.assertTrue(record["omitted"])
        self.assertTrue(record["retrieval"])
        for request in record["retrieval"]:
            self.assertEqual(request["arguments"]["category"], "discovery")
            self.assertIn(request["arguments"]["status"], STATUSES)

    def test_an_oversize_gateway_drops_detail_fields_and_says_so(self):
        for arguments in ({}, {"status": "delegated"}):
            with self.subTest(**arguments), self.grown(4.0):
                data = build_capability_view(
                    detail_section="upstream_read_gateway", response_limit=13_000,
                    **arguments,
                )
                gateway = data["upstream_read_gateway"]
                source = build_capability_catalog(**arguments)["upstream_read_gateway"]
                self.assertIs(gateway["detail_complete"], False)
                self.assertTrue(gateway["detail_omitted_fields"])
                for field, count in gateway["detail_omitted_fields"].items():
                    self.assertNotIn(field, gateway)
                    self.assertEqual(count, len(source[field]))
                self.assertTrue(gateway["retrieval"])
                self.assertEqual(data["size_limit"]["retrieval"], gateway["retrieval"])
                self.assertLessEqual(
                    len(_compact({"data": data})),
                    13_000 - capabilities.CATALOG_ENVELOPE_RESERVE_CHARS,
                )

    def test_a_gateway_whose_identity_cannot_fit_is_omitted_with_retrieval(self):
        data = build_capability_view(
            detail_section="upstream_read_gateway", response_limit=9_000
        )
        record = data["upstream_read_gateway"]
        self.assertTrue(record["omitted"])
        self.assertEqual(record["reason"], "section_exceeds_response_limit")
        self.assertEqual(
            record["retrieval"],
            [{"tool": "list_capabilities",
              "arguments": {"detail_section": "upstream_read_gateway",
                            "status": "delegated"}}],
        )
        self.assertEqual(data["size_limit"]["retrieval"], record["retrieval"])
        self.assertLessEqual(
            len(_compact({"data": data})),
            9_000 - capabilities.CATALOG_ENVELOPE_RESERVE_CHARS,
        )

    def test_a_class_with_no_narrower_filter_says_so(self):
        data = build_capability_view(
            detail_section="dynamic_upstream", status="delegated",
            category="upstream_read_gateway", response_limit=12_000,
        )
        record = data["dynamic_upstream"]
        self.assertTrue(record["omitted"])
        self.assertEqual(record["retrieval"], [])
        self.assertEqual(record["retrieval_unavailable_reason"], "no_narrower_filter")
        self.assertEqual(record["unpartitioned_item_count"], 28)
        self.assertEqual(record["item_names"], [item["tool"] for item in self.dynamic])
        self.assertNotIn("unpartitioned_items", record)

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

    def test_an_oversize_gateway_retrieval_is_a_different_call(self):
        for arguments, expected in (
            ({}, {"detail_section": "upstream_read_gateway", "status": "delegated"}),
            ({"status": "delegated"},
             {"detail_section": "dynamic_upstream", "status": "delegated"}),
        ):
            with self.subTest(**arguments), self.grown(4.0):
                data = build_capability_view(
                    detail_section="upstream_read_gateway", response_limit=13_000,
                    **arguments,
                )
                self.assertEqual(
                    data["upstream_read_gateway"]["retrieval"],
                    [{"tool": "list_capabilities", "arguments": expected}],
                )
                self.assertNotEqual(
                    expected, {"detail_section": "upstream_read_gateway", **arguments}
                )

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
