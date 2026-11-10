"""Installed-only Marketplace inventory read (HAMCP-290, Release 2.5.0).

``core.marketplace_read`` is runtime-only.  Until an owner-signed registry
references it the gateway refuses ``get_marketplace_inventory`` with
``pending_signed_registry_admission`` before the app, and the service refuses
``authority_unavailable`` with zero Home Assistant commands.  With admission,
only two constant payloads exist: ``marketplace/repositories/list`` and, only
on an exact ``unknown_command`` error code, ``hacs/repositories/list`` on the
same connection.  Every Marketplace and HACS management command is
unreachable; oversized or malformed stores fail closed with no partial page.

The reviewed ha-mcp ``ha_get_hacs_info`` read is retired (Josh, 2026-11-08):
the gateway never exposes, aliases or dispatches it on any reviewed release,
with or without the native route, and a refused native read never falls back
to it.

Fixtures model Core 2026.11.0 (74cf5a49)
``components/marketplace/websocket/repositories.py:28-89`` (25-key
``repository_summary``) and HACS 2.0.5
``custom_components/hacs/websocket/repositories.py:23-76``.  Every value is
synthetic; credential-shaped values are assembled at test time.  Signed
admission uses the test-only ephemeral ``CoreSigner`` key.
"""

from __future__ import annotations

import ast
import asyncio
from copy import deepcopy
import json
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest import mock

from aiohttp import web

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "hass_mcp_engineering_beta"), str(ROOT / "tests"), str(ROOT)]

import hamcp162_synthetic as syn  # noqa: E402
from ha_mcp_engineering import marketplace_inventory as mi  # noqa: E402
from ha_mcp_engineering.capabilities import capability_for_tool  # noqa: E402
from ha_mcp_engineering.errors import ErrorCode  # noqa: E402
from ha_mcp_engineering.ha_core_readmission.models import CoreCapabilityClass  # noqa: E402
from ha_mcp_engineering.ha_core_readmission import profiles as core_profiles  # noqa: E402
from ha_mcp_engineering.ha_core_readmission.profiles import (  # noqa: E402
    CORE_CAPABILITY_PROFILES,
    CORE_MARKETPLACE_PROFILES,
    CORE_RUNTIME_CAPABILITY_PROFILES,
)
from ha_mcp_engineering.ha_core_readmission.routes import (  # noqa: E402
    DELEGATED_CORE_REQUIREMENTS,
    F3_ENTRY_ADMISSION_PROFILES,
    PENDING_ADMISSION_TOOLS,
    RETIRED_DELEGATED_READ_TOOLS,
    RETIRED_DELEGATED_READS,
    static_tool_requirements,
)
from ha_mcp_engineering.providers.routing import (  # noqa: E402
    ANALYTICAL_PROVIDER_POLICIES,
    TOOL_CAPABILITY_POLICY,
    CapabilityRoute,
    ProviderCapability,
    routing_for_tool,
)
from ha_mcp_engineering.request_context import begin_request, end_request  # noqa: E402
from ha_mcp_engineering.tools import marketplace_inventory as public  # noqa: E402
from ha_mcp_engineering.tools.registry import ENGINEERING_STATIC_TOOL_NAMES  # noqa: E402

PROFILE = "core.marketplace_read"
TOOL = "get_marketplace_inventory"
RETIRED = "ha_get_hacs_info"
PENDING = "pending_signed_registry_admission"
HA_TOKEN = syn.needle("hatoken")
ACCESS_SECRET = syn.needle("accesssecret")
CORE_VERSION = "2026.11.0"
MODULE = ROOT / "hass_mcp_engineering_beta/ha_mcp_engineering/marketplace_inventory.py"
TOOL_MODULE = ROOT / "hass_mcp_engineering_beta/ha_mcp_engineering/tools/marketplace_inventory.py"
REFERENCE_KEYS = (
    "capability_id", "profile_id", "profile_version", "adapter_id", "contract_fingerprint",
)
# The exact Marketplace command set registered by Core 2026.11.0
# components/marketplace/websocket/{__init__,critical,repositories,repository}.py.
CORE_2026_11_MARKETPLACE_COMMANDS = frozenset({
    "marketplace/info", "marketplace/subscribe", "marketplace/github/connect",
    "marketplace/warning/accept", "marketplace/critical/list",
    "marketplace/critical/acknowledge", "marketplace/repositories/list",
    "marketplace/repositories/add", "marketplace/repositories/clear_new",
    "marketplace/repositories/detect", "marketplace/repositories/remove",
    "marketplace/repositories/removed", "marketplace/repository/beta",
    "marketplace/repository/ignore", "marketplace/repository/info",
    "marketplace/repository/install", "marketplace/repository/refresh",
    "marketplace/repository/releases", "marketplace/repository/uninstall",
    "marketplace/repository/version",
})
MANAGEMENT_COMMANDS = frozenset(mi.NEVER_SENT_COMMANDS)
PINNED_FINGERPRINT = "sha256:9393b59801d97786e3ac8cf2b44c1aec121198568bfb017cabaa5691acc0cfeb"


def references() -> list[dict]:
    return [{key: profile.to_mapping()[key] for key in REFERENCE_KEYS} for profile in CORE_MARKETPLACE_PROFILES]


def summary(repository_id: str, full_name: str, *, installed: bool = True, **extra) -> dict:
    """Core 2026.11.0 ``repository_summary`` (25 keys)."""

    record = {
        "authors": ["synthetic"],
        "available_version": "v1.2.0",
        "can_install": True,
        "category": "integration",
        "config_flow": True,
        "custom": False,
        "description": "Synthetic integration for tests",
        "domain": full_name.split("/")[1].replace("-", "_").lower()[:64],
        "downloads": 3,
        "file_name": "",
        "full_name": full_name,
        "hide": False,
        "homeassistant": None,
        "id": repository_id,
        "installed": installed,
        "installed_version": "v1.1.0" if installed else None,
        "integration_categories": [],
        "last_updated": "2026-11-01T00:00:00Z",
        "local_path": "/config/custom_components/synthetic",
        "name": full_name.split("/")[1],
        "new": False,
        "pending_upgrade": installed,
        "stars": 1,
        "status": "installed" if installed else "default",
        "topics": [],
    }
    record.update(extra)
    return record


def store(count_installed: int = 3, count_catalog: int = 2) -> list[dict]:
    items = [summary(str(100 + i), f"owner{i}/repo-{i}") for i in range(count_installed)]
    items += [summary(str(900 + i), f"catalog{i}/repo-{i}", installed=False) for i in range(count_catalog)]
    return items


def result_frame(command_id: int, result) -> str:
    return json.dumps({"id": command_id, "type": "result", "success": True, "result": result})


def error_frame(command_id: int, code, message: str = "Unknown command.") -> str:
    return json.dumps({"id": command_id, "type": "result", "success": False,
                       "error": {"code": code, "message": message}})


class AdmissionCore:
    def __init__(self, available: bool = True, version: str = CORE_VERSION):
        self.available = available
        self.generation = 7
        self.current_observation = SimpleNamespace(version=version)
        self.calls: list[tuple] = []

    def route_status(self, requirements):
        self.calls.append(tuple(requirements))
        return {"available": self.available, "generation": self.generation}


class FakeReader:
    def __init__(self, backend=mi.BACKEND_MARKETPLACE, result=None, error=None, during=None, unsent=False):
        self.backend = backend
        self.result = store() if result is None else result
        self.error = error
        self.during = during
        # The last command is admitted but its frame is never written.
        self.unsent = unsent
        self.calls = 0

    async def read(self, *, core_version, authorize, on_dispatch=lambda _c: None, on_sent=lambda _c: None):
        self.calls += 1
        authorize()
        commands = [mi.MARKETPLACE_LIST_COMMAND]
        if self.backend == mi.BACKEND_HACS:
            commands.append(mi.HACS_LIST_COMMAND)
        for index, command in enumerate(commands):
            on_dispatch(command)
            if not (self.unsent and index == len(commands) - 1):
                on_sent(command)
        if self.during is not None:
            self.during()
        authorize()
        if self.error is not None:
            raise self.error
        return self.backend, deepcopy(self.result)


class ServiceTestCase(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.core = AdmissionCore()
        self.telemetry, self.token = begin_request()
        self.telemetry.caller_id = "synthetic_authenticated_caller"
        self.telemetry.core_dispatch_authorizer = lambda: self.core.available
        self.addCleanup(end_request, self.token)

    def service(self, reader) -> mi.MarketplaceInventoryService:
        return mi.MarketplaceInventoryService(reader, self.core, known_secrets=(ACCESS_SECRET, HA_TOKEN))

    async def refused(self, coroutine, reason: str) -> mi.MarketplaceError:
        with self.assertRaises(mi.MarketplaceError) as raised:
            await coroutine
        self.assertEqual(raised.exception.reason, reason)
        return raised.exception


class ContractTests(unittest.TestCase):
    def test_profile_is_runtime_only_semantic_read_with_declared_fallback(self):
        self.assertIn(PROFILE, {p.capability_id for p in CORE_RUNTIME_CAPABILITY_PROFILES})
        self.assertNotIn(PROFILE, {p.capability_id for p in CORE_CAPABILITY_PROFILES})
        (profile,) = CORE_MARKETPLACE_PROFILES
        self.assertEqual(profile.capability_id, PROFILE)
        self.assertIs(profile.capability_class, CoreCapabilityClass.MARKETPLACE_INVENTORY_READ)
        self.assertFalse(profile.capability_class.mutation_capable)
        self.assertTrue(profile.capability_class.semantic)
        self.assertEqual(profile.adapter_id, mi.PROVIDER_CONTRACT)
        self.assertEqual(profile.contract_fingerprint, PINNED_FINGERPRINT)
        for version, _commit, _digest in core_profiles.SUPPORTED_CORE_RELEASES:
            with self.subTest(version=version):
                self.assertNotIn(PROFILE, {
                    item.capability_ids[0] for item in core_profiles.compiled_exact_authority(version)})

    def test_fallback_keyword_default_leaves_existing_fingerprints_unchanged(self):
        args = ("core.synthetic_read", CoreCapabilityClass.SCRIPT_TRACE_READ, "synthetic-v1", ("check",))
        kwargs = {"provider_boundary": "direct_ha_api", "response_contract": {"write": "none"}}
        default = core_profiles._contract(*args, **kwargs)
        explicit = core_profiles._contract(*args, **kwargs, fallback="none")
        declared = core_profiles._contract(*args, **kwargs, fallback=mi.FALLBACK)
        self.assertEqual(default.contract_fingerprint, explicit.contract_fingerprint)
        self.assertNotEqual(default.contract_fingerprint, declared.contract_fingerprint)
        others = [p for p in CORE_RUNTIME_CAPABILITY_PROFILES if p.capability_id != PROFILE]
        self.assertEqual(len(others), 35)  # HAMCP-134: + core.security_metadata_read; HAMCP-297: + device configuration; HAMCP-231: + thread/matter topology

    def test_tool_is_registered_read_only_and_policy_bound_under_its_own_name(self):
        self.assertIn(TOOL, ENGINEERING_STATIC_TOOL_NAMES)
        self.assertEqual(static_tool_requirements(TOOL, {}), (PROFILE,))
        self.assertEqual(PENDING_ADMISSION_TOOLS[TOOL], ("marketplace_inventory", PROFILE))
        self.assertIn(PROFILE, F3_ENTRY_ADMISSION_PROFILES)
        self.assertEqual(TOOL_CAPABILITY_POLICY[TOOL], ProviderCapability.MARKETPLACE_INVENTORY)
        self.assertEqual(routing_for_tool(TOOL).route, CapabilityRoute.ENGINEERING_NATIVE)
        policy = ANALYTICAL_PROVIDER_POLICIES[TOOL]
        self.assertEqual((policy["access"], policy["writes_allowed"]), ("read", "none"))
        self.assertIn("exactly unknown_command", policy["fallback_policy"])
        capability = capability_for_tool(TOOL)
        self.assertEqual(capability["routing"], "engineering_native")
        self.assertEqual(capability["risk"], "read")
        self.assertEqual(capability["core_capabilities"], [PROFILE])
        self.assertEqual(capability["core_admission_requirement"], "signed_core_registry_admission")
        self.assertEqual(capability["fallback"], mi.FALLBACK)
        # Engineering-owned name: no ha_ prefix and no delegated route.
        self.assertFalse(TOOL.startswith("ha_"))
        self.assertNotIn(TOOL, DELEGATED_CORE_REQUIREMENTS)
        self.assertNotIn(TOOL, RETIRED_DELEGATED_READS)
        registered = public.registered_tool()
        self.assertIs(registered.annotations.readOnlyHint, True)
        self.assertIs(registered.annotations.destructiveHint, False)
        self.assertIs(registered.annotations.openWorldHint, False)
        self.assertIs(registered.parameters.get("additionalProperties"), False)
        properties = registered.parameters["properties"]
        self.assertEqual(set(properties), {"query", "category", "limit", "offset"})
        self.assertEqual(registered.parameters.get("required", []), [])
        self.assertEqual(properties["query"]["maxLength"], mi.MAX_QUERY)
        self.assertEqual((properties["limit"]["minimum"], properties["limit"]["maximum"]), (1, 50))
        self.assertEqual((properties["offset"]["minimum"], properties["offset"]["maximum"]), (0, 20000))

    def test_delegated_hacs_read_is_retired_from_every_engineering_table(self):
        self.assertEqual(RETIRED_DELEGATED_READ_TOOLS, (RETIRED,))
        self.assertEqual(RETIRED_DELEGATED_READS, frozenset({RETIRED}))
        self.assertNotIn(RETIRED, ENGINEERING_STATIC_TOOL_NAMES)
        self.assertNotIn(RETIRED, DELEGATED_CORE_REQUIREMENTS)
        self.assertNotIn(RETIRED, TOOL_CAPABILITY_POLICY)
        self.assertNotIn(RETIRED, ANALYTICAL_PROVIDER_POLICIES)
        self.assertNotIn(RETIRED, PENDING_ADMISSION_TOOLS)
        self.assertFalse(capability_for_tool(RETIRED))
        self.assertEqual(static_tool_requirements(RETIRED, {}), ())
        # No alias spelling of the retired name is Engineering-owned either.
        self.assertFalse({name for name in ENGINEERING_STATIC_TOOL_NAMES if "hacs" in name})

    def test_payloads_are_the_closed_vocabulary(self):
        self.assertEqual(dict(mi.MARKETPLACE_PAYLOAD), {"type": "marketplace/repositories/list"})
        self.assertEqual(dict(mi.HACS_PAYLOAD), {"type": "hacs/repositories/list"})
        with self.assertRaises(TypeError):
            mi.MARKETPLACE_PAYLOAD["categories"] = ["integration"]  # type: ignore[index]
        self.assertEqual(mi.COMMANDS, {"marketplace/repositories/list", "hacs/repositories/list"})
        self.assertEqual(mi.MAX_COMMANDS, 2)
        self.assertFalse(mi.COMMANDS & MANAGEMENT_COMMANDS)
        # Every non-list Core 2026.11.0 Marketplace command is disclosed never-sent.
        self.assertEqual(
            {c for c in MANAGEMENT_COMMANDS if c.startswith("marketplace/")},
            CORE_2026_11_MARKETPLACE_COMMANDS - {"marketplace/repositories/list"},
        )
        (profile,) = CORE_MARKETPLACE_PROFILES
        contract = profile_contract()
        self.assertTrue(set(contract["never_sent"]) <= MANAGEMENT_COMMANDS)
        self.assertEqual(contract["write"], "none")
        self.assertEqual(contract["max_commands"], 2)
        self.assertEqual(contract["fallback_trigger"], {"error.code": "unknown_command", "message_text": "ignored"})

    def test_source_builds_no_other_command(self):
        for path in (MODULE, TOOL_MODULE):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            literals = {
                node.value for node in ast.walk(tree)
                if isinstance(node, ast.Constant) and isinstance(node.value, str)
                and (node.value.startswith("marketplace/") or node.value.startswith("hacs/"))
            }
            with self.subTest(path=path.name):
                self.assertTrue(literals - MANAGEMENT_COMMANDS <= mi.COMMANDS)
        # No "categories" (or any other) argument is ever sent.
        source = MODULE.read_text(encoding="utf-8")
        self.assertNotIn('"categories"', source)


def profile_contract() -> dict:
    """The response contract material is part of the fingerprint; re-derive it."""

    import inspect

    source = inspect.getsource(core_profiles)
    start = source.index("CORE_MARKETPLACE_PROFILES = (")
    end = source.index("CORE_RUNTIME_CAPABILITY_PROFILES = (")
    tree = ast.parse(source[start:end])
    call = tree.body[0].value.elts[0]
    for keyword in call.keywords:
        if keyword.arg == "response_contract":
            return ast.literal_eval(keyword.value)
    raise AssertionError("response_contract missing")


class ArgumentTests(unittest.TestCase):
    def test_defaults_and_bounds(self):
        self.assertEqual(mi.validate_arguments({}), mi.InventoryQuery("", None, 20, 0))
        self.assertEqual(
            mi.validate_arguments({"query": "  Dock ", "category": "plugin", "limit": 50, "offset": 20000}),
            mi.InventoryQuery("Dock", "plugin", 50, 20000),
        )
        self.assertEqual(mi.validate_arguments({"category": None}).category, None)

    def test_refused_values_are_closed_and_never_reflected(self):
        marker = "Refused_" + "q" * 6
        for arguments in (
            None, [], {"extra": marker}, {"query": marker * 30}, {"query": 7},
            {"query": "a\x00b"}, {"query": "a‮b"}, {"query": "line\nbreak"},
            {"category": marker}, {"category": "Integration"}, {"category": 1},
            {"limit": 0}, {"limit": 51}, {"limit": True}, {"limit": 1.0}, {"limit": "5"},
            {"offset": -1}, {"offset": 20001}, {"offset": False}, {"offset": None},
        ):
            with self.subTest(arguments=arguments):
                with self.assertRaises(mi.MarketplaceError) as raised:
                    mi.validate_arguments(arguments)
                self.assertEqual(raised.exception.reason, "invalid_arguments")
                self.assertEqual(raised.exception.code, ErrorCode.INVALID_REQUEST)
                self.assertNotIn(marker, json.dumps(raised.exception.details))

    def test_unknown_reason_collapses_to_provider_error(self):
        error = mi.MarketplaceError("SYNTHETIC_UNLISTED_REASON")
        self.assertEqual(error.reason, "provider_error")
        self.assertNotIn("SYNTHETIC", json.dumps(error.details))


class ClassifyReplyTests(unittest.TestCase):
    def test_success_returns_the_store(self):
        self.assertEqual(mi.classify_reply(json.loads(result_frame(1, [])), 1), [])

    def test_only_the_exact_unknown_command_code_selects_fallback(self):
        self.assertIsNone(mi.classify_reply(json.loads(error_frame(1, "unknown_command")), 1))
        self.assertIsNone(mi.classify_reply(json.loads(error_frame(1, "unknown_command", "other text")), 1))
        for code, reason in (
            ("UNKNOWN_COMMAND", "provider_error"), ("unknown_command ", "provider_error"),
            ("unknown_error", "provider_error"), ("not_found", "provider_error"),
            ("invalid_format", "provider_error"), ("home_assistant_error", "provider_error"),
            ("not_loaded", "marketplace_not_loaded"), ("unauthorized", "marketplace_unauthorized"),
            ("forbidden", "marketplace_unauthorized"),
        ):
            with self.subTest(code=code):
                with self.assertRaises(mi.MarketplaceError) as raised:
                    mi.classify_reply(json.loads(error_frame(1, code, "Unknown command.")), 1)
                self.assertEqual(raised.exception.reason, reason)

    def test_malformed_envelopes_never_select_fallback(self):
        cases = [
            {"id": 1, "type": "result", "success": False, "error": {"message": "Unknown command."}},
            {"id": 1, "type": "result", "success": False, "error": "unknown_command"},
            {"id": 1, "type": "result", "success": False, "error": {"code": ["unknown_command"]}},
            {"id": 1, "type": "result", "success": False},
            {"id": 1, "type": "result", "success": False, "result": [],
             "error": {"code": "unknown_command"}},
            {"id": 2, "type": "result", "success": False, "error": {"code": "unknown_command"}},
            {"id": True, "type": "result", "success": False, "error": {"code": "unknown_command"}},
            {"id": 1, "type": "event", "success": False, "error": {"code": "unknown_command"}},
            {"id": 1, "type": "result", "success": 0, "error": {"code": "unknown_command"}},
            {"id": 1, "type": "result", "success": True},
            {"id": 1, "type": "result", "success": True, "result": {}},
            {"id": 1, "type": "result", "success": True, "result": [], "error": {}},
            [], "result",
        ]
        for message in cases:
            with self.subTest(message=message):
                with self.assertRaises(mi.MarketplaceError) as raised:
                    mi.classify_reply(message, 1)
                self.assertEqual(raised.exception.reason, "malformed_response")

    def test_record_count_bound(self):
        with self.assertRaises(mi.MarketplaceError) as raised:
            mi.classify_reply({"id": 1, "type": "result", "success": True,
                               "result": [{}] * (mi.MAX_RECORDS + 1)}, 1)
        self.assertEqual(raised.exception.reason, "response_too_large")


class ProjectionTests(unittest.TestCase):
    def test_installed_only_fixed_projection(self):
        records = mi.project_installed(store(2, 3))
        self.assertEqual([r["id"] for r in records], ["100", "101"])
        for record in records:
            self.assertEqual(tuple(record), mi.PROJECTED_FIELDS)
            self.assertNotIn("local_path", record)
        self.assertEqual(mi.project_installed([summary(7, "a/b", domain="")])[0]["id"], "7")
        self.assertIsNone(mi.project_installed([summary("7", "a/b", domain="")])[0]["domain"])
        self.assertIsNone(mi.project_installed([summary("7", "a/b", domain=None)])[0]["domain"])

    def test_uninstalled_records_need_only_a_strict_installed_flag(self):
        catalog = summary("x", "ünicode/../path", installed=False, category="BAD", domain="Bad!")
        self.assertEqual(mi.project_installed([catalog]), [])

    def test_any_malformed_installed_record_fails_the_whole_store(self):
        good = summary("1", "good/repo")
        cases = {
            "not_mapping": ["x"],
            "installed_not_bool": [summary("2", "a/b", installed="true")],
            "catalog_installed_missing": [{k: v for k, v in summary("2", "a/b", installed=False).items()
                                           if k != "installed"}],
            "id_bool": [summary(True, "a/b")],
            "id_negative": [summary(-1, "a/b")],
            "id_text": [summary("12a", "a/b")],
            "id_long": [summary("1" * 21, "a/b")],
            "id_missing": [{k: v for k, v in summary("2", "a/b").items() if k != "id"}],
            "full_name_unicode": [summary("2", "üser/repo")],
            "full_name_traversal": [summary("2", "../repo")],
            "full_name_extra_slash": [summary("2", "a/b/c")],
            "full_name_newline": [summary("2", "a/b\n")],
            "category_upper": [summary("2", "a/b", category="Integration")],
            "domain_invalid": [summary("2", "a/b", domain="Bad-Domain")],
            "domain_int": [summary("2", "a/b", domain=3)],
            "pending_upgrade_none": [summary("2", "a/b", pending_upgrade=None)],
            "custom_text": [summary("2", "a/b", custom="false")],
            "name_int": [summary("2", "a/b", name=5)],
            "description_list": [summary("2", "a/b", description=["x"])],
            "installed_version_number": [summary("2", "a/b", installed_version=1.1)],
            "duplicate_id": [summary("2", "a/b"), summary("2", "c/d")],
            "duplicate_normalized_id": [summary("2", "a/b"), summary(2, "c/d")],
        }
        for name, items in cases.items():
            with self.subTest(case=name):
                with self.assertRaises(mi.MarketplaceError) as raised:
                    mi.project_installed([good, *items])
                self.assertEqual(raised.exception.reason, "malformed_response")
        for bad_store in ({}, None, "[]"):
            with self.assertRaises(mi.MarketplaceError):
                mi.project_installed(bad_store)
        with self.assertRaises(mi.MarketplaceError) as raised:
            mi.project_installed([summary(str(i), f"o/r{i}", installed=False) for i in range(mi.MAX_RECORDS + 1)])
        self.assertEqual(raised.exception.reason, "response_too_large")


class PageTests(unittest.TestCase):
    def page(self, items, **arguments):
        return mi.build_page(mi.BACKEND_MARKETPLACE, items, mi.validate_arguments(arguments),
                             (ACCESS_SECRET, HA_TOKEN))

    def test_sorted_bounded_pages_with_exact_continuation(self):
        items = [summary(str(i), f"Owner/repo-{i:02d}") for i in range(7)]
        items.append(summary("50", "aaa/first"))
        items.reverse()
        first = self.page(items, limit=3)
        self.assertEqual([r["full_name"] for r in first["results"]], ["aaa/first", "Owner/repo-00", "Owner/repo-01"])
        self.assertEqual((first["total_matches"], first["count"], first["has_more"], first["next_offset"]),
                         (8, 3, True, 3))
        last = self.page(items, limit=3, offset=6)
        self.assertEqual((last["count"], last["has_more"], last["next_offset"]), (2, False, None))
        beyond = self.page(items, offset=100)
        self.assertEqual((beyond["count"], beyond["has_more"], beyond["next_offset"], beyond["results"]),
                         (0, False, None, []))
        self.assertEqual(first["installed_total"], 8)
        self.assertEqual(first["inventory_fingerprint"], last["inventory_fingerprint"])

    def test_paged_page_is_partial_until_the_last_page(self):
        # A page that leaves matches unreturned is not the complete inventory.
        items = [summary(str(i), f"owner/repo-{i:02d}") for i in range(5)]
        first = self.page(items, limit=2)
        self.assertEqual((first["has_more"], first["completeness"], first["partial_reasons"]),
                         (True, "partial", ["more_pages"]))
        middle = self.page(items, limit=2, offset=2)
        self.assertEqual((middle["completeness"], middle["partial_reasons"]), ("partial", ["more_pages"]))
        # Controls: the last page, an exact single page, offsets past the end
        # and an empty inventory are complete.
        for arguments, store_items in (({"limit": 2, "offset": 4}, items), ({"limit": 5}, items),
                                       ({"offset": 100}, items), ({}, [])):
            with self.subTest(arguments=arguments, empty=not store_items):
                page = self.page(store_items, **arguments)
                self.assertEqual((page["has_more"], page["completeness"], page["partial_reasons"]),
                                 (False, "complete", []))
        # Both causes are reported together.
        items[0] = summary("0", "owner/repo-00", description="d" * (mi.DESCRIPTION_CHARS + 1))
        both = self.page(items, limit=2)
        self.assertEqual((both["completeness"], both["partial_reasons"]), ("partial", ["sanitized_text", "more_pages"]))
        # A filter that narrows matches to one page is complete even when the
        # inventory is larger (the sanitizer screens the whole inventory, so
        # use clean records here).
        clean = [summary(str(i), f"owner/repo-{i:02d}") for i in range(5)]
        self.assertEqual(self.page(clean, limit=2, query="repo-03")["partial_reasons"], [])

    def test_ties_sort_by_id_and_casefold(self):
        items = [summary("2", "Same/Name"), summary("1", "same/name")]
        self.assertEqual([r["id"] for r in self.page(items)["results"]], ["1", "2"])

    def test_query_and_category_filters(self):
        items = [summary("1", "acme/dockge-card", category="plugin", domain=None, name="Dockge Card"),
                 summary("2", "acme/weather", description="Shows DOCKGE status"),
                 summary("3", "zed/theme-x", category="theme", domain=None)]
        self.assertEqual(self.page(items, query="dockge")["total_matches"], 2)
        self.assertEqual(self.page(items, query="dockge", category="plugin")["total_matches"], 1)
        self.assertEqual(self.page(items, category="theme")["results"][0]["id"], "3")
        self.assertEqual(self.page(items, query="nomatch")["total_matches"], 0)
        self.assertEqual(self.page(items, category="template")["installed_total"], 3)

    def test_sanitized_text_is_withheld_partial_and_unsearchable(self):
        leaked = summary("1", "acme/leaky", description=f"token {HA_TOKEN} inside")
        long = summary("2", "acme/long", description="d" * 1000, name="n" * 500)
        page = self.page([leaked, long])
        rendered = json.dumps(page)
        self.assertNotIn(HA_TOKEN, rendered)
        self.assertEqual(page["completeness"], "partial")
        self.assertGreaterEqual(page["redaction"]["redacted_field_count"], 1)
        self.assertGreaterEqual(page["truncated_field_count"], 2)
        by_id = {r["id"]: r for r in page["results"]}
        self.assertLessEqual(len(by_id["2"]["description"]), mi.DESCRIPTION_CHARS + 20)
        self.assertLessEqual(len(by_id["2"]["name"]), mi.NAME_CHARS + 20)
        # A query can never probe withheld text.
        self.assertEqual(self.page([leaked], query=HA_TOKEN)["total_matches"], 0)

    def test_complete_when_nothing_withheld(self):
        page = self.page(store())
        self.assertEqual(page["completeness"], "complete")
        self.assertEqual(page["truncated_field_count"], 0)

    def test_truncation_alone_is_partial(self):
        # Codex D1 (#409): shortened text is withheld text even with nothing redacted.
        for field, bound in (("name", mi.NAME_CHARS), ("description", mi.DESCRIPTION_CHARS),
                             ("installed_version", mi.VERSION_CHARS), ("available_version", mi.VERSION_CHARS)):
            with self.subTest(field=field):
                page = self.page([summary("1", "acme/long", **{field: "v" * (bound + 50)}), summary("2", "acme/short")])
                self.assertEqual((page["completeness"], page["partial_reasons"]), ("partial", ["sanitized_text"]))
                self.assertIsNone(page["redaction"])  # nothing redacted, only shortened
                self.assertFalse(page["sanitization_failed_closed"])
                self.assertEqual(page["truncated_field_count"], 1)
        # Clean control: the same records at the bound are complete.
        for field, bound in (("name", mi.NAME_CHARS), ("description", mi.DESCRIPTION_CHARS),
                             ("installed_version", mi.VERSION_CHARS)):
            with self.subTest(control=field):
                page = self.page([summary("1", "acme/long", **{field: "v" * bound})])
                self.assertEqual((page["completeness"], page["truncated_field_count"]), ("complete", 0))

    def test_search_never_matches_discarded_text(self):
        marker = "zqxdiscardedtail"
        items = [summary("1", "acme/long", description="d" * mi.DESCRIPTION_CHARS + marker),
                 summary("2", "acme/named", name="n" * mi.NAME_CHARS + marker)]
        self.assertEqual(self.page(items, query=marker)["total_matches"], 0)
        self.assertNotIn(marker, json.dumps(self.page(items)))
        # Positive control: the retained prefix stays searchable.
        self.assertEqual(self.page(items, query="d" * 20)["total_matches"], 1)
        self.assertEqual(self.page(items, query="n" * 20)["total_matches"], 1)

    def test_fingerprint_tracks_installed_inventory_only(self):
        base = store(3, 2)
        fingerprint = self.page(base)["inventory_fingerprint"]
        self.assertEqual(self.page(list(reversed(base)))["inventory_fingerprint"], fingerprint)
        self.assertEqual(self.page(base, query="owner1", limit=1)["inventory_fingerprint"], fingerprint)
        catalog_changed = deepcopy(base)
        catalog_changed[-1]["description"] = "changed catalog text"
        self.assertEqual(self.page(catalog_changed)["inventory_fingerprint"], fingerprint)
        upgraded = deepcopy(base)
        upgraded[0]["installed_version"] = "v1.2.0"
        self.assertNotEqual(self.page(upgraded)["inventory_fingerprint"], fingerprint)
        hacs = mi.build_page(mi.BACKEND_HACS, base, mi.validate_arguments({}))
        self.assertNotEqual(hacs["inventory_fingerprint"], fingerprint)
        with self.assertRaises(mi.MarketplaceError):
            mi.build_page("upstream_says_core", base, mi.validate_arguments({}))

    def test_page_size_is_bounded_at_scale(self):
        items = [summary(str(i), f"o{i}/r") for i in range(mi.MAX_RECORDS)]
        page = self.page(items, limit=50, offset=19990)
        self.assertEqual((page["count"], page["has_more"], page["installed_total"]), (10, False, mi.MAX_RECORDS))
        self.assertLess(len(json.dumps(page)), 60_000)


class ServiceAdmissionTests(ServiceTestCase):
    async def test_unadmitted_profile_refuses_with_zero_reads(self):
        self.core.available = False
        reader = FakeReader()
        error = await self.refused(self.service(reader).inventory(mi.InventoryQuery()), "authority_unavailable")
        self.assertEqual(reader.calls, 0)
        self.assertEqual(error.code, ErrorCode.PROVIDER_UNAVAILABLE)
        self.assertEqual(error.details, {"reason": "authority_unavailable", "fallback_occurred": False,
                                         "core_capability": PROFILE})

    async def test_unadmitted_profile_refuses_even_with_dispatch_authority(self):
        self.core.available = False
        self.telemetry.core_dispatch_authorizer = lambda: True
        reader = FakeReader()
        await self.refused(self.service(reader).inventory(mi.InventoryQuery()), "authority_unavailable")
        self.assertEqual(reader.calls, 0)

    async def test_missing_dispatch_authority_or_observation_refuses(self):
        reader = FakeReader()
        self.telemetry.core_dispatch_authorizer = None
        await self.refused(self.service(reader).inventory(mi.InventoryQuery()), "authority_unavailable")
        self.telemetry.core_dispatch_authorizer = lambda: True
        self.core.current_observation = None
        await self.refused(self.service(reader).inventory(mi.InventoryQuery()), "authority_unavailable")
        self.assertEqual(reader.calls, 0)

    async def test_anonymous_caller_refuses(self):
        self.telemetry.caller_id = "anonymous"
        reader = FakeReader()
        await self.refused(self.service(reader).inventory(mi.InventoryQuery()), "authority_unavailable")
        self.assertEqual(reader.calls, 0)

    async def test_non_query_request_refuses(self):
        reader = FakeReader()
        await self.refused(self.service(reader).inventory({"limit": 5}), "invalid_arguments")
        self.assertEqual(reader.calls, 0)

    async def test_generation_or_admission_change_across_the_read_refuses(self):
        for change in (lambda: setattr(self.core, "generation", 8),
                       lambda: setattr(self.core, "available", False),
                       lambda: setattr(self.core.current_observation, "version", "2026.11.1")):
            self.core = AdmissionCore()
            reader = FakeReader(during=change)
            with self.subTest(change=change):
                await self.refused(self.service(reader).inventory(mi.InventoryQuery()), "authority_unavailable")

    async def test_admission_change_while_building_the_page_does_not_publish(self):
        # HAMCP-290 mutation A5: the last recheck runs after the page is built.
        original = mi.build_page
        for backend, fallback in ((mi.BACKEND_MARKETPLACE, False), (mi.BACKEND_HACS, True)):
            self.core = AdmissionCore()

            def flipping(*args, **kwargs):
                page = original(*args, **kwargs)
                self.core.generation += 1
                return page

            with self.subTest(backend=backend), mock.patch.object(mi, "build_page", flipping):
                error = await self.refused(self.service(FakeReader(backend=backend)).inventory(mi.InventoryQuery()),
                                           "authority_unavailable")
                self.assertIs(error.fallback_occurred, fallback)

    async def test_authority_loss_after_the_page_keeps_dispatch_attribution(self):
        # Codex D2 (#409): availability, authorizer or observation lost while
        # the page is built; authority() itself raises after dispatch.
        original = mi.build_page
        losses = {
            "available": lambda: setattr(self.core, "available", False),
            "authorizer": lambda: setattr(self.telemetry, "core_dispatch_authorizer", None),
            "authorizer_denies": lambda: setattr(self.telemetry, "core_dispatch_authorizer", lambda: False),
            "observation": lambda: setattr(self.core, "current_observation", None),
        }
        for backend, fallback in ((mi.BACKEND_MARKETPLACE, False), (mi.BACKEND_HACS, True)):
            for name, lose in losses.items():
                self.core = AdmissionCore()
                self.telemetry.core_dispatch_authorizer = lambda: self.core.available

                def losing(*args, **kwargs):
                    page = original(*args, **kwargs)
                    lose()
                    return page

                with self.subTest(backend=backend, loss=name), mock.patch.object(mi, "build_page", losing):
                    error = await self.refused(self.service(FakeReader(backend=backend)).inventory(
                        mi.InventoryQuery()), "authority_unavailable")
                    self.assertIs(error.fallback_occurred, fallback)
                    self.assertEqual(error.details, {"reason": "authority_unavailable", "fallback_occurred": fallback,
                                                     "core_capability": PROFILE})

    async def test_unexpected_failure_in_the_final_authority_check_is_closed(self):
        original = mi.build_page

        def raising(_requirements):
            raise RuntimeError(f"SYNTHETIC_PRIVATE {HA_TOKEN}")

        def breaking(*args, **kwargs):
            page = original(*args, **kwargs)
            self.core.route_status = raising
            return page

        with mock.patch.object(mi, "build_page", breaking), self.assertRaises(Exception) as raised:
            await self.service(FakeReader(backend=mi.BACKEND_HACS)).inventory(mi.InventoryQuery())
        error = raised.exception
        self.assertIsInstance(error, mi.MarketplaceError)
        self.assertEqual(error.reason, "provider_error")
        self.assertIs(error.fallback_occurred, True)
        self.assertNotIn("SYNTHETIC_PRIVATE", json.dumps(error.details))

    async def test_admission_asks_for_exactly_the_marketplace_profile(self):
        # HAMCP-290 mutation A8: never an empty or different requirement set.
        await self.service(FakeReader()).inventory(mi.InventoryQuery())
        self.assertEqual(mi.CORE_REQUIREMENTS, (PROFILE,))
        self.assertTrue(self.core.calls)
        self.assertEqual(set(self.core.calls), {(PROFILE,)})

    async def test_dispatch_outside_the_closed_vocabulary_is_refused(self):
        # HAMCP-290 mutation C4: defense in depth behind the constant payloads.
        class Dispatching(FakeReader):
            def __init__(self, commands):
                super().__init__()
                self.commands = commands
                self.dispatched = []

            async def read(self, *, core_version, authorize, on_dispatch=lambda _c: None, on_sent=lambda _c: None):
                self.calls += 1
                for command in self.commands:
                    authorize()
                    on_dispatch(command)
                    self.dispatched.append(command)
                    on_sent(command)
                return self.backend, deepcopy(self.result)

        for commands in (["hacs/repository/download"],
                         [mi.MARKETPLACE_LIST_COMMAND, mi.HACS_LIST_COMMAND, mi.HACS_LIST_COMMAND]):
            reader = Dispatching(commands)
            with self.subTest(commands=commands):
                await self.refused(self.service(reader).inventory(mi.InventoryQuery()), "provider_error")
                self.assertEqual(reader.dispatched, commands[:-1])

    async def test_unconfigured_runtime_fails_closed(self):
        runtime = mi.MarketplaceInventoryRuntime()
        with self.assertRaises(mi.MarketplaceError) as raised:
            runtime.require()
        self.assertEqual(raised.exception.reason, "authority_unavailable")
        runtime.configure(FakeReader(), self.core, SimpleNamespace(access_secret="a", ha_token="b"))
        self.assertIsInstance(runtime.require(), mi.MarketplaceInventoryService)


class ServiceReadTests(ServiceTestCase):
    async def test_admitted_read_returns_useful_attributed_page(self):
        page = await self.service(FakeReader()).inventory(mi.InventoryQuery(limit=2))
        self.assertEqual(page["backend"], "core_marketplace")
        self.assertFalse(page["fallback_occurred"])
        self.assertEqual(page["commands_sent"], 1)
        self.assertEqual((page["installed_total"], page["count"], page["has_more"]), (3, 2, True))
        # More installed repositories remain: this page is not the inventory.
        self.assertEqual((page["completeness"], page["partial_reasons"]), ("partial", ["more_pages"]))
        self.assertEqual((self.telemetry.completeness, self.telemetry.result_status), ("partial", "partial"))
        page = await self.service(FakeReader()).inventory(mi.InventoryQuery(limit=3))
        self.assertEqual((page["has_more"], page["completeness"]), (False, "complete"))
        self.assertEqual((self.telemetry.completeness, self.telemetry.result_status), ("complete", "success"))

    async def test_backend_is_decided_by_the_command_that_answered(self):
        page = await self.service(FakeReader(backend=mi.BACKEND_HACS)).inventory(mi.InventoryQuery())
        self.assertEqual((page["backend"], page["fallback_occurred"], page["commands_sent"]), ("hacs", True, 2))

    async def test_failures_are_closed_and_never_partial(self):
        for error, reason in (
            (mi.MarketplaceError("marketplace_not_loaded"), "marketplace_not_loaded"),
            (mi.MarketplaceError("response_too_large"), "response_too_large"),
            (RuntimeError(f"SYNTHETIC_PRIVATE {HA_TOKEN}"), "provider_error"),
        ):
            with self.subTest(reason=reason):
                raised = await self.refused(self.service(FakeReader(error=error)).inventory(mi.InventoryQuery()), reason)
                self.assertNotIn("SYNTHETIC_PRIVATE", json.dumps(raised.details))
        bad_store = store()
        bad_store[0]["full_name"] = "../escape"
        await self.refused(self.service(FakeReader(result=bad_store)).inventory(mi.InventoryQuery()),
                           "malformed_response")

    async def test_failure_attribution_follows_the_dispatch_ledger(self):
        # Codex D2 (#409): every post-dispatch failure is attributed to the
        # command that was actually sent, wherever the failure arose.
        bad_store = store()
        bad_store[0]["installed"] = "true"
        for backend, fallback in ((mi.BACKEND_MARKETPLACE, False), (mi.BACKEND_HACS, True)):
            with self.subTest(backend=backend, failure="projection"):
                error = await self.refused(self.service(FakeReader(backend=backend, result=bad_store)).inventory(
                    mi.InventoryQuery()), "malformed_response")
                self.assertIs(error.fallback_occurred, fallback)
                self.assertIs(error.details["fallback_occurred"], fallback)
            with self.subTest(backend=backend, failure="reader_local"):
                error = await self.refused(self.service(FakeReader(
                    backend=backend, error=mi.MarketplaceError("response_too_large"))).inventory(mi.InventoryQuery()),
                    "response_too_large")
                self.assertIs(error.fallback_occurred, fallback)
            with self.subTest(backend=backend, failure="unexpected"):
                error = await self.refused(self.service(FakeReader(
                    backend=backend, error=RuntimeError("synthetic"))).inventory(mi.InventoryQuery()), "provider_error")
                self.assertIs(error.fallback_occurred, fallback)
        # A reader that over-reports fallback without sending it is corrected.
        error = await self.refused(self.service(FakeReader(
            error=mi.MarketplaceError("timeout", fallback_occurred=True))).inventory(mi.InventoryQuery()), "timeout")
        self.assertIs(error.fallback_occurred, False)

    async def test_admitted_but_unwritten_fallback_is_not_attributed(self):
        # Codex D2 residual (#409): attribution follows the frame written, not
        # the command admitted; a reader over-reporting the attempt is corrected.
        for reported in (False, True):
            with self.subTest(reader_reported=reported):
                error = await self.refused(self.service(FakeReader(
                    backend=mi.BACKEND_HACS, unsent=True,
                    error=mi.MarketplaceError("transport_unavailable", fallback_occurred=reported),
                )).inventory(mi.InventoryQuery()), "transport_unavailable")
                self.assertIs(error.fallback_occurred, False)
                self.assertIs(error.details["fallback_occurred"], False)
        # Control: the same failure after the HACS frame was written.
        error = await self.refused(self.service(FakeReader(
            backend=mi.BACKEND_HACS, error=mi.MarketplaceError("transport_unavailable"),
        )).inventory(mi.InventoryQuery()), "transport_unavailable")
        self.assertIs(error.fallback_occurred, True)

    async def test_sent_ledger_accepts_only_admitted_commands_in_order(self):
        class Recording(FakeReader):
            def __init__(self, script):
                super().__init__()
                self.script = script

            async def read(self, *, core_version, authorize, on_dispatch=lambda _c: None, on_sent=lambda _c: None):
                for kind, command in self.script:
                    (on_dispatch if kind == "dispatch" else on_sent)(command)
                return self.backend, deepcopy(self.result)

        for script in (
            [("sent", mi.HACS_LIST_COMMAND)],
            [("dispatch", mi.MARKETPLACE_LIST_COMMAND), ("sent", mi.HACS_LIST_COMMAND)],
            [("dispatch", mi.MARKETPLACE_LIST_COMMAND), ("sent", mi.MARKETPLACE_LIST_COMMAND),
             ("sent", mi.MARKETPLACE_LIST_COMMAND)],
        ):
            with self.subTest(script=script):
                error = await self.refused(self.service(Recording(script)).inventory(mi.InventoryQuery()),
                                           "provider_error")
                self.assertIs(error.fallback_occurred, False)


class PublicToolTests(ServiceTestCase):
    async def test_invalid_arguments_are_never_reflected(self):
        marker = "Refused_" + "q" * 8
        for arguments in ({"query": marker, "extra": 1}, {"category": marker}, {"limit": 51},
                          {"offset": -1}, {"query": marker + "\x07"}):
            with self.subTest(arguments=arguments):
                rendered = await public.registered_tool().run(arguments)
                self.assertNotIn(marker, rendered)
                self.assertIn("invalid_arguments", rendered)

    async def test_admitted_tool_renders_attribution(self):
        with mock.patch.object(mi.MARKETPLACE_INVENTORY, "service", self.service(FakeReader(backend=mi.BACKEND_HACS))):
            rendered = json.loads(await public.registered_tool().run({"limit": 1}))
        self.assertTrue(rendered["success"], rendered)
        self.assertEqual(rendered["metadata"]["provider"], "engineering")
        self.assertEqual(rendered["metadata"]["data_providers"], ["direct_ha_api"])
        self.assertIs(rendered["metadata"]["fallback_occurred"], True)
        self.assertEqual(rendered["metadata"]["backend"], "hacs")
        self.assertEqual(rendered["data"]["count"], 1)

    async def test_truncation_only_renders_partial_with_warning(self):
        # Codex D1 (#409): through the registered tool, data, metadata,
        # warnings and telemetry agree.
        long = store()
        long[0]["description"] = "d" * (mi.DESCRIPTION_CHARS + 50)
        with mock.patch.object(mi.MARKETPLACE_INVENTORY, "service", self.service(FakeReader(result=long))):
            rendered = json.loads(await public.registered_tool().run({}))
        self.assertTrue(rendered["success"], rendered)
        self.assertEqual(rendered["data"]["completeness"], "partial")
        self.assertEqual(rendered["metadata"]["completeness"], "partial")
        self.assertIsNone(rendered["data"]["redaction"])
        self.assertEqual(rendered["data"]["truncated_field_count"], 1)
        self.assertIn(public._PARTIAL, rendered["warnings"])
        self.assertNotIn(public._MORE_PAGES, rendered["warnings"])
        self.assertEqual((self.telemetry.completeness, self.telemetry.result_status), ("partial", "partial"))

    async def test_paged_result_renders_partial_with_continuation_warning(self):
        with mock.patch.object(mi.MARKETPLACE_INVENTORY, "service", self.service(FakeReader())):
            rendered = json.loads(await public.registered_tool().run({"limit": 2}))
        self.assertTrue(rendered["success"], rendered)
        self.assertEqual((rendered["data"]["has_more"], rendered["data"]["next_offset"]), (True, 2))
        self.assertEqual((rendered["data"]["completeness"], rendered["metadata"]["completeness"]),
                         ("partial", "partial"))
        self.assertIn(public._MORE_PAGES, rendered["warnings"])
        self.assertNotIn(public._PARTIAL, rendered["warnings"])
        self.assertEqual((self.telemetry.completeness, self.telemetry.result_status), ("partial", "partial"))
        # Control: the last page is complete and carries no warning.
        with mock.patch.object(mi.MARKETPLACE_INVENTORY, "service", self.service(FakeReader())):
            rendered = json.loads(await public.registered_tool().run({"limit": 2, "offset": 2}))
        self.assertEqual((rendered["data"]["has_more"], rendered["metadata"]["completeness"]), (False, "complete"))
        self.assertEqual(rendered["warnings"], [])

    async def test_clean_inventory_renders_complete_without_warning(self):
        with mock.patch.object(mi.MARKETPLACE_INVENTORY, "service", self.service(FakeReader())):
            rendered = json.loads(await public.registered_tool().run({}))
        self.assertEqual((rendered["data"]["completeness"], rendered["metadata"]["completeness"]),
                         ("complete", "complete"))
        self.assertEqual(rendered["data"]["partial_reasons"], [])
        self.assertEqual(rendered["warnings"], [])
        self.assertEqual((self.telemetry.completeness, self.telemetry.result_status), ("complete", "success"))

    async def test_refusal_after_fallback_renders_fallback_attribution(self):
        # Codex D2 (#409): public details and metadata name the HACS fallback.
        bad_store = store()
        bad_store[0]["installed"] = "true"
        for backend, fallback in ((mi.BACKEND_MARKETPLACE, False), (mi.BACKEND_HACS, True)):
            with self.subTest(backend=backend), mock.patch.object(
                    mi.MARKETPLACE_INVENTORY, "service", self.service(FakeReader(backend=backend, result=bad_store))):
                rendered = json.loads(await public.registered_tool().run({}))
            self.assertFalse(rendered["success"])
            self.assertEqual(rendered["details"]["reason"], "malformed_response")
            self.assertIs(rendered["details"]["fallback_occurred"], fallback)
            self.assertIs(rendered["metadata"]["fallback_occurred"], fallback)

    async def test_unadmitted_tool_renders_closed_refusal(self):
        self.core.available = False
        with mock.patch.object(mi.MARKETPLACE_INVENTORY, "service", self.service(FakeReader())):
            rendered = json.loads(await public.registered_tool().run({}))
        self.assertFalse(rendered["success"])
        self.assertIn("authority_unavailable", json.dumps(rendered))


class LoopbackPeer:
    """Disposable loopback Home Assistant WebSocket peer with a command ledger."""

    def __init__(self):
        self.ledger: list[dict] = []
        self.connections = 0
        self.auth_type = "auth_ok"
        self.auth_version = CORE_VERSION
        self.replies: dict[str, str] = {}
        self.hold = False
        self.extra_frame: str | None = None
        self.greeting_pad = 0
        self.drop: str | None = None  # record this command, then close unanswered

    async def handler(self, request):
        self.connections += 1
        ws = web.WebSocketResponse(max_msg_size=0)
        await ws.prepare(request)
        try:
            await ws.send_str(json.dumps({"type": "auth_required", "ha_version": self.auth_version,
                                          **({"pad": "x" * self.greeting_pad} if self.greeting_pad else {})}))
            await ws.receive_json()
            await ws.send_json({"type": self.auth_type, "ha_version": self.auth_version})
            while True:
                message = await ws.receive()
                if message.type != web.WSMsgType.TEXT:
                    break
                command = json.loads(message.data)
                self.ledger.append(command)
                if command.get("type") == self.drop:
                    break
                if self.hold:
                    await asyncio.sleep(5)
                if self.extra_frame is not None:
                    await ws.send_str(self.extra_frame)
                reply = self.replies.get(command.get("type"), error_frame(0, "unknown_command"))
                await ws.send_str(reply.replace('"id": 0', f'"id": {command.get("id")}', 1))
        except (ConnectionError, TypeError, RuntimeError, asyncio.CancelledError):
            pass
        finally:
            await ws.close()
        return ws


def closing_transport(command: str, *, before_write: bool):
    """Close the client transport just before or just after ``command`` is written.

    The pinned aiohttp writer itself rejects a write to a closing transport;
    ``writes`` records only frames that the real ``send_json`` accepted.
    """

    import aiohttp

    real = aiohttp.ClientWebSocketResponse.send_json
    writes: list[str] = []

    async def send_json(self, data, *args, **kwargs):
        if data.get("type") != command:
            return await real(self, data, *args, **kwargs)
        if before_write:
            self._writer.transport.close()
        await real(self, data, *args, **kwargs)
        writes.append(command)
        if not before_write:
            self._writer.transport.close()

    return mock.patch.object(aiohttp.ClientWebSocketResponse, "send_json", send_json), writes


class LoopbackTestCase(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.peer = LoopbackPeer()
        app = web.Application()
        app.router.add_get("/{path:.*}", self.peer.handler)
        self.runner = web.AppRunner(app, access_log=None)
        await self.runner.setup()
        site = web.TCPSite(self.runner, "127.0.0.1", 0)
        await site.start()
        port = site._server.sockets[0].getsockname()[1]
        self.settings = SimpleNamespace(
            websocket_url=f"ws://127.0.0.1:{port}/api/websocket", ha_token=HA_TOKEN,
            access_secret=ACCESS_SECRET, ha_timeout_seconds=2.0,
        )
        self.reader = mi.MarketplaceReader(self.settings)
        self.telemetry, self.token = begin_request()
        self.telemetry.caller_id = "synthetic_authenticated_caller"
        self.telemetry.core_dispatch_authorizer = lambda: True

    async def asyncTearDown(self):
        end_request(self.token)
        await self.runner.cleanup()
        # Negative reachability on every path: no management command, ever.
        sent = {command.get("type") for command in self.peer.ledger}
        self.assertTrue(sent <= mi.COMMANDS, sent)
        self.assertFalse(sent & MANAGEMENT_COMMANDS)
        self.assertLessEqual(len(self.peer.ledger), mi.MAX_COMMANDS)
        for command in self.peer.ledger:
            self.assertEqual(set(command), {"id", "type"})

    def reply(self, command: str, frame: str) -> None:
        self.peer.replies[command] = frame

    async def read(self, authorize=lambda: None):
        return await self.reader.read(core_version=CORE_VERSION, authorize=authorize)

    async def failed(self, reason: str, *, fallback: bool = False, authorize=lambda: None):
        with self.assertRaises(mi.MarketplaceError) as raised:
            await self.read(authorize)
        self.assertEqual(raised.exception.reason, reason)
        self.assertIs(raised.exception.fallback_occurred, fallback)
        self.assertNotIn(HA_TOKEN, json.dumps(raised.exception.details))
        return raised.exception


class ReaderLoopbackTests(LoopbackTestCase):
    async def test_marketplace_success_is_one_constant_command(self):
        self.reply(mi.MARKETPLACE_LIST_COMMAND, result_frame(0, store()))
        backend, records = await self.read()
        self.assertEqual(backend, "core_marketplace")
        self.assertEqual(len(records), 5)
        self.assertEqual(self.peer.ledger, [{"id": 1, "type": "marketplace/repositories/list"}])
        self.assertEqual(self.peer.connections, 1)
        self.assertEqual(self.telemetry.ha_active_requests, 0)

    async def test_exact_unknown_command_falls_back_once_on_the_same_connection(self):
        self.reply(mi.MARKETPLACE_LIST_COMMAND, error_frame(0, "unknown_command"))
        self.reply(mi.HACS_LIST_COMMAND, result_frame(0, store(1, 0)))
        backend, records = await self.read()
        self.assertEqual(backend, "hacs")
        self.assertEqual(len(records), 1)
        self.assertEqual(self.peer.ledger, [{"id": 1, "type": "marketplace/repositories/list"},
                                            {"id": 2, "type": "hacs/repositories/list"}])
        self.assertEqual(self.peer.connections, 1)

    async def test_reader_attributes_fallback_only_after_the_hacs_frame_is_written(self):
        # Codex D2 residual (#409): selecting HACS or entering send_json is not
        # sending it; the reader's own flag follows the written frame.
        self.reply(mi.MARKETPLACE_LIST_COMMAND, error_frame(0, "unknown_command"))
        patch, writes = closing_transport(mi.HACS_LIST_COMMAND, before_write=True)
        with patch:
            await self.failed("transport_unavailable", fallback=False)
        self.assertEqual(writes, [])
        self.assertEqual([c["type"] for c in self.peer.ledger], [mi.MARKETPLACE_LIST_COMMAND])

        self.peer.ledger.clear()
        patch, writes = closing_transport(mi.HACS_LIST_COMMAND, before_write=False)
        with patch, self.assertRaises(mi.MarketplaceError) as raised:
            await self.read()
        self.assertEqual(writes, [mi.HACS_LIST_COMMAND])
        self.assertIs(raised.exception.fallback_occurred, True)

    async def test_hacs_unknown_command_is_unavailable_not_a_third_command(self):
        self.reply(mi.MARKETPLACE_LIST_COMMAND, error_frame(0, "unknown_command"))
        self.reply(mi.HACS_LIST_COMMAND, error_frame(0, "unknown_command"))
        await self.failed("marketplace_unavailable", fallback=True)
        self.assertEqual(len(self.peer.ledger), 2)

    async def test_hacs_failures_after_fallback_are_attributed(self):
        self.reply(mi.MARKETPLACE_LIST_COMMAND, error_frame(0, "unknown_command"))
        for frame, reason in ((error_frame(0, "unauthorized"), "marketplace_unauthorized"),
                              (result_frame(0, {"x": 1}), "malformed_response")):
            self.peer.ledger.clear()
            self.reply(mi.HACS_LIST_COMMAND, frame)
            with self.subTest(reason=reason):
                await self.failed(reason, fallback=True)

    async def test_no_fallback_for_any_other_marketplace_failure(self):
        for frame, reason in (
            (error_frame(0, "not_loaded", "Marketplace is not loaded"), "marketplace_not_loaded"),
            (error_frame(0, "not_found", "Unknown command."), "provider_error"),
            (error_frame(0, "unknown_error", "unknown command"), "provider_error"),
            (error_frame(0, "unauthorized"), "marketplace_unauthorized"),
            (error_frame(0, "invalid_format", "extra keys not allowed @ data['offset']"), "provider_error"),
            (json.dumps({"id": 0, "type": "result", "success": False,
                         "error": {"message": "Unknown command."}}), "malformed_response"),
            (json.dumps({"id": 0, "type": "result", "success": False, "result": [],
                         "error": {"code": "unknown_command"}}), "malformed_response"),
            ('{"id": 0, "type": "result", "success": false, "error": {"code": "unknown_command", '
             '"code": "unknown_command"}}', "malformed_response"),
        ):
            self.peer.ledger.clear()
            self.reply(mi.MARKETPLACE_LIST_COMMAND, frame)
            self.reply(mi.HACS_LIST_COMMAND, result_frame(0, store()))
            with self.subTest(reason=reason, frame=frame):
                await self.failed(reason)
                self.assertEqual([c["type"] for c in self.peer.ledger], [mi.MARKETPLACE_LIST_COMMAND])

    async def test_oversized_frame_fails_closed(self):
        self.reply(mi.MARKETPLACE_LIST_COMMAND, result_frame(0, store(40, 0)))
        with mock.patch.object(mi, "FRAME_BYTES", 4096):
            await self.failed("response_too_large")

    async def test_auth_frames_have_their_own_smaller_bound(self):
        # HAMCP-290 mutation B4: the 64 KiB auth bound, not the 8 MiB command bound.
        self.reply(mi.MARKETPLACE_LIST_COMMAND, result_frame(0, store()))
        self.peer.greeting_pad = mi.AUTH_BYTES
        await self.failed("response_too_large")
        self.assertEqual(self.peer.ledger, [])
        self.peer.greeting_pad = 1024
        backend, _records = await self.read()
        self.assertEqual(backend, "core_marketplace")

    async def test_unsolicited_frames_and_non_json_fail_closed(self):
        self.reply(mi.MARKETPLACE_LIST_COMMAND, result_frame(0, store()))
        self.peer.extra_frame = json.dumps({"id": 9, "type": "event", "event": {}})
        await self.failed("malformed_response")
        self.peer.extra_frame = None
        for frame in ('{"id": 0, "type": "result", "success": true, "result": [NaN]}',
                      '{"id": 0, "type": "result", "success": true, "result": [1e999]}',
                      '{"id": 0, "type": "result", "success": true, "result": [] , "x": 1, "x": 2}',
                      'not json'):
            self.peer.ledger.clear()
            self.reply(mi.MARKETPLACE_LIST_COMMAND, frame)
            with self.subTest(frame=frame):
                await self.failed("malformed_response")

    async def test_auth_failure_and_identity_drift_send_no_command(self):
        self.peer.auth_type = "auth_invalid"
        await self.failed("marketplace_unauthorized")
        self.peer.auth_type = "auth_ok"
        self.peer.auth_version = "2026.11.1"
        await self.failed("identity_mismatch")
        self.assertEqual(self.peer.ledger, [])

    async def test_authority_loss_before_dispatch_sends_no_command(self):
        calls = [0]

        def authorize():
            calls[0] += 1
            if calls[0] >= 2:
                raise mi.MarketplaceError("authority_unavailable")

        self.reply(mi.MARKETPLACE_LIST_COMMAND, result_frame(0, store()))
        await self.failed("authority_unavailable", authorize=authorize)
        self.assertEqual(self.peer.ledger, [])

    async def test_authority_loss_after_result_does_not_publish(self):
        calls = [0]

        def authorize():
            calls[0] += 1
            if calls[0] >= 3:
                raise mi.MarketplaceError("authority_unavailable")

        self.reply(mi.MARKETPLACE_LIST_COMMAND, result_frame(0, store()))
        await self.failed("authority_unavailable", authorize=authorize)
        self.assertEqual(len(self.peer.ledger), 1)

    async def test_deadline_is_bounded(self):
        self.settings.ha_timeout_seconds = 0.3
        self.peer.hold = True
        await self.failed("timeout")
        self.assertEqual(self.telemetry.timeout_occurred, True)
        self.settings.ha_timeout_seconds = 0
        await self.failed("timeout")

    async def test_configured_timeout_never_exceeds_the_cap(self):
        # HAMCP-290 mutation B8: a large configured timeout is capped at MAX_SECONDS.
        self.settings.ha_timeout_seconds = 600.0
        self.peer.hold = True
        started = asyncio.get_running_loop().time()
        with mock.patch.object(mi, "MAX_SECONDS", 0.3):
            await self.failed("timeout")
        self.assertLess(asyncio.get_running_loop().time() - started, 4.0)
        self.assertEqual(len(self.peer.ledger), 1)

    async def test_unreachable_peer_is_transport_unavailable(self):
        await self.runner.cleanup()
        await self.failed("transport_unavailable")


class EndToEndLoopbackTests(LoopbackTestCase):
    def service(self, core):
        return mi.MarketplaceInventoryService(self.reader, core, known_secrets=(ACCESS_SECRET, HA_TOKEN))

    async def test_admitted_service_reads_core_marketplace(self):
        self.reply(mi.MARKETPLACE_LIST_COMMAND, result_frame(0, store(4, 3)))
        page = await self.service(AdmissionCore()).inventory(mi.validate_arguments({"limit": 3}))
        self.assertEqual((page["backend"], page["installed_total"], page["count"], page["next_offset"]),
                         ("core_marketplace", 4, 3, 3))
        self.assertEqual(page["commands_sent"], 1)

    async def test_unadmitted_service_opens_no_connection(self):
        with self.assertRaises(mi.MarketplaceError) as raised:
            await self.service(AdmissionCore(available=False)).inventory(mi.InventoryQuery())
        self.assertEqual(raised.exception.reason, "authority_unavailable")
        self.assertEqual(self.peer.connections, 0)

    async def test_admission_withdrawn_between_commands_stops_the_fallback(self):
        core = AdmissionCore()
        self.reply(mi.HACS_LIST_COMMAND, result_frame(0, store()))
        original = self.peer.handler

        async def handler(request):
            return await original(request)

        self.reply(mi.MARKETPLACE_LIST_COMMAND, error_frame(0, "unknown_command"))
        service = self.service(core)
        real_read = self.reader.read

        async def read(**kwargs):
            authorize = kwargs["authorize"]
            calls = [0]

            def revoking():
                calls[0] += 1
                if calls[0] == 3:  # immediately before the fallback command
                    core.generation += 1
                authorize()

            kwargs["authorize"] = revoking
            return await real_read(**kwargs)

        with mock.patch.object(self.reader, "read", read):
            with self.assertRaises(mi.MarketplaceError) as raised:
                await service.inventory(mi.InventoryQuery())
        self.assertEqual(raised.exception.reason, "authority_unavailable")
        self.assertEqual([c["type"] for c in self.peer.ledger], [mi.MARKETPLACE_LIST_COMMAND])
        # The fallback was selected but never sent, so it is not attributed.
        self.assertIs(raised.exception.fallback_occurred, False)

    async def run_public(self, core):
        with mock.patch.object(mi.MARKETPLACE_INVENTORY, "service", self.service(core)):
            return json.loads(await public.registered_tool().run({}))

    async def test_truncation_only_over_the_real_reader_is_partial(self):
        # Codex D1 (#409), real reader and synthetic WebSocket peer.
        records = store(2, 0)
        records[0]["name"] = "n" * (mi.NAME_CHARS + 10)
        self.reply(mi.MARKETPLACE_LIST_COMMAND, result_frame(0, records))
        rendered = await self.run_public(AdmissionCore())
        self.assertTrue(rendered["success"], rendered)
        self.assertEqual((rendered["data"]["completeness"], rendered["metadata"]["completeness"]),
                         ("partial", "partial"))
        self.assertIn(public._PARTIAL, rendered["warnings"])

    async def test_paged_result_over_the_real_reader_is_partial(self):
        # More installed repositories than the default page: partial, with a
        # continuation warning; nothing was shortened.
        self.reply(mi.MARKETPLACE_LIST_COMMAND, result_frame(0, store(25, 0)))
        rendered = await self.run_public(AdmissionCore())
        self.assertTrue(rendered["success"], rendered)
        self.assertEqual((rendered["data"]["count"], rendered["data"]["has_more"], rendered["data"]["next_offset"]),
                         (20, True, 20))
        self.assertEqual((rendered["data"]["completeness"], rendered["metadata"]["completeness"]),
                         ("partial", "partial"))
        self.assertEqual(rendered["data"]["partial_reasons"], ["more_pages"])
        self.assertEqual(rendered["warnings"], [public._MORE_PAGES])

    async def test_post_fallback_failures_over_the_real_reader_are_attributed(self):
        # Codex D2 (#409): both list commands sent, then (a) an invalid
        # installed record or (b) availability withdrawn while building the page.
        bad = store(2, 0)
        bad[1]["id"] = "12a"
        self.reply(mi.MARKETPLACE_LIST_COMMAND, error_frame(0, "unknown_command"))
        self.reply(mi.HACS_LIST_COMMAND, result_frame(0, bad))
        rendered = await self.run_public(AdmissionCore())
        self.assertEqual(rendered["details"]["reason"], "malformed_response")
        self.assertIs(rendered["details"]["fallback_occurred"], True)
        self.assertIs(rendered["metadata"]["fallback_occurred"], True)

        self.peer.ledger.clear()
        self.reply(mi.HACS_LIST_COMMAND, result_frame(0, store(2, 0)))
        core = AdmissionCore()
        original = mi.build_page

        def withdrawing(*args, **kwargs):
            page = original(*args, **kwargs)
            core.available = False
            return page

        with mock.patch.object(mi, "build_page", withdrawing):
            rendered = await self.run_public(core)
        self.assertEqual(rendered["details"]["reason"], "authority_unavailable")
        self.assertIs(rendered["details"]["fallback_occurred"], True)
        self.assertIs(rendered["metadata"]["fallback_occurred"], True)
        self.assertEqual([c["type"] for c in self.peer.ledger], [mi.MARKETPLACE_LIST_COMMAND, mi.HACS_LIST_COMMAND])

    async def test_first_command_failure_is_not_attributed_to_fallback(self):
        self.reply(mi.MARKETPLACE_LIST_COMMAND, error_frame(0, "not_loaded", "Marketplace not loaded"))
        rendered = await self.run_public(AdmissionCore())
        self.assertFalse(rendered["success"])
        self.assertIs(rendered["details"]["fallback_occurred"], False)
        self.assertIs(rendered["metadata"]["fallback_occurred"], False)
        self.assertEqual([c["type"] for c in self.peer.ledger], [mi.MARKETPLACE_LIST_COMMAND])

    async def test_hacs_rejected_before_write_is_not_attributed_to_fallback(self):
        # Codex D2 residual (#409), real reader, public tool and synthetic peer:
        # Marketplace answers exact unknown_command, then the client transport
        # closes before the HACS frame is written. No HACS frame, no fallback.
        self.reply(mi.MARKETPLACE_LIST_COMMAND, error_frame(0, "unknown_command"))
        self.reply(mi.HACS_LIST_COMMAND, result_frame(0, store()))
        patch, writes = closing_transport(mi.HACS_LIST_COMMAND, before_write=True)
        with patch:
            rendered = await self.run_public(AdmissionCore())
        self.assertFalse(rendered["success"])
        self.assertEqual(rendered["details"]["reason"], "transport_unavailable")
        self.assertIs(rendered["details"]["fallback_occurred"], False)
        self.assertIs(rendered["metadata"]["fallback_occurred"], False)
        self.assertEqual(writes, [])
        self.assertEqual([c["type"] for c in self.peer.ledger], [mi.MARKETPLACE_LIST_COMMAND])

    async def test_hacs_failure_after_write_stays_attributed_to_fallback(self):
        # Positive control: the HACS frame was written, then (a) the client
        # transport closed or (b) the peer received it and dropped the reply.
        self.reply(mi.MARKETPLACE_LIST_COMMAND, error_frame(0, "unknown_command"))
        self.reply(mi.HACS_LIST_COMMAND, result_frame(0, store()))
        patch, writes = closing_transport(mi.HACS_LIST_COMMAND, before_write=False)
        with patch:
            rendered = await self.run_public(AdmissionCore())
        self.assertFalse(rendered["success"])
        self.assertEqual(writes, [mi.HACS_LIST_COMMAND])
        self.assertIs(rendered["details"]["fallback_occurred"], True)
        self.assertIs(rendered["metadata"]["fallback_occurred"], True)

        self.peer.ledger.clear()
        self.peer.drop = mi.HACS_LIST_COMMAND
        rendered = await self.run_public(AdmissionCore())
        self.assertFalse(rendered["success"])
        self.assertIs(rendered["details"]["fallback_occurred"], True)
        self.assertIs(rendered["metadata"]["fallback_occurred"], True)
        self.assertEqual([c["type"] for c in self.peer.ledger], [mi.MARKETPLACE_LIST_COMMAND, mi.HACS_LIST_COMMAND])

    async def test_reader_local_fallback_failure_stays_attributed(self):
        self.reply(mi.MARKETPLACE_LIST_COMMAND, error_frame(0, "unknown_command"))
        self.reply(mi.HACS_LIST_COMMAND, error_frame(0, "home_assistant_error", "boom"))
        rendered = await self.run_public(AdmissionCore())
        self.assertFalse(rendered["success"])
        self.assertIs(rendered["details"]["fallback_occurred"], True)
        self.assertIs(rendered["metadata"]["fallback_occurred"], True)


def _retired_entry(reviewed_schema=None, open_world=False):
    from test_readonly_upstream_gateway import policy_entry

    return policy_entry(RETIRED, reviewed_schema=reviewed_schema, open_world=open_world)


def _native_server(name: str):
    from mcp.server.fastmcp import FastMCP

    server = FastMCP(name)
    server.add_tool(public.registered_tool().fn, name=TOOL)
    return server


class GatewayRetirementTests(unittest.IsolatedAsyncioTestCase):
    """The delegated ``ha_get_hacs_info`` is retired on every reviewed release."""

    def assert_retired(self, gateway, server, *, matched=None):
        from test_readonly_upstream_gateway import registered_tools

        names = set(registered_tools(server))
        self.assertNotIn(RETIRED, names)
        self.assertFalse({name for name in names if name.endswith(RETIRED)})
        health = gateway.health_snapshot()
        withheld = {item["tool"]: item for item in health["core_withheld_tools"]}
        self.assertIn(RETIRED, withheld)
        self.assertEqual(withheld[RETIRED]["disposition"], "retired_delegated_read")
        self.assertEqual(withheld[RETIRED]["reason_codes"], ["delegated_read_retired"])
        self.assertNotIn(RETIRED, {item["upstream_name"] for item in health["collision_mappings"]})
        self.assertNotIn(RETIRED, health["exposed_tools"])
        if matched is not None:
            self.assertEqual(health["exact_matched_automatic_read_count"], matched)
            self.assertEqual(health["dynamically_exposed_count"], matched - 1)
        return health

    async def test_retired_read_is_never_exposed_without_a_native_route(self):
        from test_readonly_upstream_gateway import catalog_tool, initialize

        gateway, server, transport = await initialize([_retired_entry()], [catalog_tool(RETIRED)])
        self.assert_retired(gateway, server, matched=1)
        self.assertEqual(transport.calls, [])

    async def test_retired_read_is_never_counted_as_a_core_catalog_change(self):
        # The retired read is reported beside the Core withholdings, but it is
        # not a Core withdrawal or restoration, so no false Core telemetry.
        from test_readonly_upstream_gateway import CompositeCoreRuntime, catalog_tool, initialize

        class RecordingCoreRuntime(CompositeCoreRuntime):
            def __init__(self):
                super().__init__()
                self.catalog_changes = []

            def record_catalog_change(self, *, restored, count=1):
                self.catalog_changes.append((restored, count))

        runtime = RecordingCoreRuntime()
        gateway, server, transport = await initialize(
            [_retired_entry()], [catalog_tool(RETIRED)], core_runtime=runtime)
        self.assert_retired(gateway, server, matched=1)
        self.assertEqual(runtime.catalog_changes, [])
        self.assertEqual(runtime.acquire_calls, 0)
        self.assertEqual(transport.calls, [])

    async def test_retired_read_is_never_exposed_or_aliased_beside_the_native_route(self):
        from test_readonly_upstream_gateway import catalog_tool, initialize, registered_tools

        server = _native_server("hamcp290-native")
        original = registered_tools(server).get(TOOL)
        gateway, server, transport = await initialize(
            [_retired_entry(), ], [catalog_tool(RETIRED)], server=server)
        self.assertIs(registered_tools(server).get(TOOL), original)
        health = self.assert_retired(gateway, server, matched=1)
        self.assertEqual(health["collision_count"], 0)
        self.assertEqual(transport.calls, [])

    async def test_upstream_tool_advertising_the_native_name_is_never_exposed_in_its_place(self):
        from test_readonly_upstream_gateway import catalog_tool, initialize, policy_entry, registered_tools

        for reviewed in (False, True):
            with self.subTest(reviewed=reviewed):
                server = _native_server("hamcp290-impostor")
                original = registered_tools(server).get(TOOL)
                entries = [policy_entry("ha_get_state")]
                if reviewed:
                    # Even a reviewed upstream entry under the native name is
                    # aliased, never published in the native tool's place.
                    entries.append(policy_entry(TOOL))
                gateway, server, transport = await initialize(
                    entries, [catalog_tool("ha_get_state"), catalog_tool(TOOL)], server=server)
                self.assertIs(registered_tools(server).get(TOOL), original)
                self.assertIsNotNone(registered_tools(server).get("ha_get_state"))
                self.assertEqual(transport.calls, [])

    async def test_retired_read_is_withheld_on_the_exact_reviewed_captures(self):
        from test_readonly_upstream_gateway import registered_tools
        import test_hamcp162_yaml_admission as yaml_admission

        harness = yaml_admission.AdmissionTests()
        harness.setUp()
        self.addCleanup(harness.temporary.cleanup)
        for version in ("8.4.3", "8.5.0"):
            for label in ("A", "OFF"):
                for native in (False, True):
                    with self.subTest(version=version, label=label, native=native):
                        tools = yaml_admission.captured(version, label)
                        self.assertIn(RETIRED, {tool["name"] for tool in tools})
                        gateway, transport = await harness.gateway(version, tools)
                        server = gateway.synthetic_server
                        if native:
                            server = _native_server(f"hamcp290-{version}-{label}")
                            gateway, transport = await _gateway_on(server, version, tools)
                        health = self.assert_retired(gateway, server)
                        # Mode A also exposes the optional YAML reader, which is
                        # not an automatic read; OFF does not.
                        optional = 1 if label == "A" else 0
                        self.assertEqual(health["dynamically_exposed_count"],
                                         health["exact_matched_automatic_read_count"]
                                         - health["core_withheld_read_count"] + optional)
                        self.assertEqual(health["core_withheld_read_count"], 1)
                        if native:
                            self.assertIsNotNone(registered_tools(server).get(TOOL))
                        self.assertEqual(transport.sent, [])

    async def test_held_read_canary_never_dispatches_the_retired_read(self):
        """The evidence-only canary path does not pass the dispatch guard, so
        prove it refuses the retired read on the exact reviewed captures."""

        import test_hamcp162_yaml_admission as yaml_admission

        harness = yaml_admission.AdmissionTests()
        harness.setUp()
        self.addCleanup(harness.temporary.cleanup)
        for version in ("8.4.3", "8.5.0"):
            with self.subTest(version=version):
                gateway, transport = await harness.gateway(version, yaml_admission.captured(version, "OFF"))
                health = gateway.health_snapshot()
                self.assertEqual(health["admission_status"], "admitted_exact")
                value = json.loads(await gateway.run_held_read_canary(
                    upstream_tool_name=RETIRED,
                    expected_compatibility_entry_id=health["selected_compatibility_entry_id"],
                    arguments={},
                ))
                self.assertFalse(value["success"])
                self.assertEqual(value["details"]["reason"], "tool_not_held_for_canary")
                self.assertFalse(value["details"]["canary_evidence"]["dispatch_occurred"])
                self.assertEqual(transport.sent, [])
                self.assertNotIn(RETIRED, gateway.health_snapshot()["held_tools"])

    async def test_retired_read_is_withheld_on_the_9_1_0_capture(self):
        from test_readonly_upstream_gateway import initialize, policy_entry, registered_tools
        from ha_mcp_engineering.providers import upstream_read_gateway as gw

        capture = json.loads((ROOT / "docs/evidence/hamcp319/phase1/catalogs/full-1.json").read_text())
        tools = capture["tools"]
        by_name = {tool["name"]: tool for tool in tools}
        names = (RETIRED, "ha_get_state")

        def entries():
            return [policy_entry(name, reviewed_schema=by_name[name]["inputSchema"],
                                 open_world=bool(by_name[name]["annotations"].get("openWorldHint")))
                    for name in names]

        reviewed = {
            "version": "9.1.0", "reviewed_version": "9.1.0",
            "reviewed_descriptions": {name: by_name[name]["description"] for name in names},
            "reviewed_runtime_annotations": {name: by_name[name]["annotations"] for name in names},
            "reviewed_output_schemas": {name: by_name[name]["outputSchema"] for name in names},
        }
        # HAMCP-319 (Codex F1 on PR #406): 9.1.0 runtime admission binds each
        # used tool, not the catalog order, so this synthetic two-entry subset
        # policy reaches the retirement decision with no test-scoped lift. Its
        # observed order differs from the full-catalog evidence fingerprint,
        # which proves that order is not what admits or withholds here.
        from ha_mcp_engineering.upstream_tool_policy import schema_fingerprint

        subset_order = [tool["name"] for tool in tools if tool["name"] in names]
        self.assertNotEqual(
            schema_fingerprint(subset_order),
            gw.EXACT_RUNTIME_TOOL_ORDER_FINGERPRINTS["9.1.0"],
        )
        self.assertIn("9.1.0", gw.USED_TOOL_ADMISSION_RELEASES)
        for native in (False, True):
            with self.subTest(native=native):
                server = _native_server("hamcp290-9.1.0") if native else None
                gateway, server, transport = await initialize(entries(), tools, server=server, **reviewed)
                self.assertEqual(gateway.health_snapshot()["quarantine_reason_counts"], {})
                self.assert_retired(gateway, server, matched=2)
                self.assertIsNotNone(registered_tools(server).get("ha_get_state"))
                self.assertEqual(transport.calls, [])
        # Non-vacuous: without the retirement this exact descriptor is admitted.
        with mock.patch.object(gw, "RETIRED_DELEGATED_READS", frozenset()):
            _gateway, server, _transport = await initialize(entries(), tools, **reviewed)
        self.assertIsNotNone(registered_tools(server).get(RETIRED))

    async def test_a_previously_admitted_route_is_refused_at_dispatch(self):
        """Defence in depth: the lowest dispatch point refuses a retired read."""

        from test_readonly_upstream_gateway import catalog_tool, initialize, registered_tools
        from ha_mcp_engineering.providers import upstream_read_gateway as gw

        with mock.patch.object(gw, "RETIRED_DELEGATED_READS", frozenset()):
            gateway, server, transport = await initialize([_retired_entry()], [catalog_tool(RETIRED)])
        route = registered_tools(server).get(RETIRED)
        self.assertIsNotNone(route)
        value = json.loads(await route.run({"entity_id": "sun.sun"}))
        self.assertFalse(value["success"])
        self.assertEqual(value["error_code"], "provider_prohibited")
        self.assertEqual(transport.calls, [])
        health = gateway.health_snapshot()
        self.assertEqual(health["prohibited_delegation_attempts"], 1)
        self.assertEqual(health["fallback_count"], 0)

    async def test_direct_provider_call_to_the_retired_read_is_refused(self):
        from test_readonly_upstream_gateway import catalog_tool, initialize, schema

        entry = _retired_entry()
        gateway, _server, transport = await initialize([entry], [catalog_tool(RETIRED)])
        value = json.loads(await gateway.execute(
            exposed_name=RETIRED, arguments={"entity_id": "sun.sun"}, reviewed_schema=schema(),
            policy_entry=entry, admission_generation=gateway.health_snapshot().get("admission_generation", 0),
            contract_fingerprint="not-admitted",
        ))
        self.assertEqual(value["error_code"], "provider_prohibited")
        self.assertEqual(transport.calls, [])

    async def test_refused_native_read_never_falls_back_upstream(self):
        from test_readonly_upstream_gateway import catalog_tool, initialize, registered_tools

        server = _native_server("hamcp290-no-fallback")
        gateway, server, transport = await initialize(
            [_retired_entry()], [catalog_tool(RETIRED)], server=server)
        unconfigured = mi.MarketplaceInventoryRuntime()
        with mock.patch.object(public, "MARKETPLACE_INVENTORY", unconfigured):
            for arguments in ({}, {"query": "x"}, {"limit": 999}):
                with self.subTest(arguments=arguments):
                    rendered = await public.registered_tool().run(arguments)
                    value = json.loads(rendered)
                    self.assertFalse(value["success"])
                    self.assertIs(value["metadata"]["fallback_occurred"], False)
                    self.assertNotIn(RETIRED, rendered)
        self.assertEqual(transport.calls, [])
        self.assertIsNone(registered_tools(server).get(RETIRED))
        health = gateway.health_snapshot()
        self.assertEqual(health["fallback_count"], 0)
        self.assertEqual(health["dynamically_exposed_count"], 0)


async def _gateway_on(server, version, tools):
    """``test_hamcp162_yaml_admission.Harness.gateway`` (Mode A) on ``server``."""

    from dataclasses import replace

    import test_hamcp162_yaml_admission as yaml_admission
    import test_ha_mcp_production_readmission as signed
    from ha_mcp_engineering.providers.upstream_read_gateway import UpstreamReadGateway

    transport = yaml_admission.RaceTransport(tools, version)
    gateway = UpstreamReadGateway()
    configured = replace(
        signed._settings("synthetic-no-key"), ha_mcp_release_registry_enabled=False,
        upstream_dashboard_mcp_url="http://synthetic-provider:9583/synthetic-path-credential/mcp",
    )
    gateway.configure(configured, transport=transport)
    await gateway.initialize(server)
    return gateway, transport


class UnadmittedCompiledCoreTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        import tests.test_ha_core_2026_9_integration as core9
        from tests.test_ha_core_capability_auto_readmission import _evidence, _snapshot

        self.runtime, _source = await core9.Core20269RuntimeTests()._runtime(
            _snapshot("2026.8.1", evidence=_evidence())
        )

    async def asyncTearDown(self):
        from ha_mcp_engineering.capabilities import replace_dynamic_upstream_capabilities

        replace_dynamic_upstream_capabilities((), {})

    async def test_gateway_refuses_with_pending_admission_before_the_app(self):
        import tests.test_hamcp069a_entity_metadata_readmission as readmission

        self.assertIsNone(self.runtime.acquire(static_tool_requirements(TOOL, {})))
        gateway, app = readmission.gateway_for(self.runtime)
        for arguments in ({}, {"query": "x", "limit": 5}, {"limit": 999}):
            with self.subTest(arguments=arguments):
                body = await readmission.call(gateway, TOOL, arguments)
                self.assertIn(b"CoreCapabilityUnavailable", body)
                self.assertIn(PENDING.encode(), body)
                self.assertIn(b'"resource_type": "marketplace_inventory"', body.replace(b'\\"', b'"'))
        self.assertEqual(app.calls, [])


class SignedTestRegistryAdmissionTests(unittest.IsolatedAsyncioTestCase):
    """Test-only ephemeral CoreSigner key; nothing here is production-signed."""

    async def asyncSetUp(self):
        import tests.test_core_2026_10_device_registry as october

        await october.OctoberSignedSelectionTests.asyncSetUp(self)
        self._publish = october.OctoberSignedSelectionTests.publish

    async def publish(self, entry=None):
        return await self._publish(self, entry)

    async def asyncTearDown(self):
        from ha_mcp_engineering.capabilities import replace_dynamic_upstream_capabilities

        replace_dynamic_upstream_capabilities((), {})

    async def test_shipped_reference_set_withholds(self):
        import tests.test_hamcp069a_entity_metadata_readmission as readmission

        self.assertNotIn(PROFILE, {c["capability_id"] for c in self.entry["capabilities"]})
        await self.publish()
        self.assertIsNone(self.core.acquire((PROFILE,)))
        gateway, app = readmission.gateway_for(self.core)
        body = await readmission.call(gateway, TOOL, {})
        self.assertIn(b"CoreCapabilityUnavailable", body)
        self.assertEqual(app.calls, [])

    async def test_signed_reference_admits_and_the_read_succeeds(self):
        import tests.test_hamcp069a_entity_metadata_readmission as readmission

        entry = deepcopy(self.entry)
        entry["capabilities"] = [*self.entry["capabilities"], *references()]
        await self.publish(entry)
        self.assertIsNotNone(self.core.acquire((PROFILE,)))
        gateway, app = readmission.gateway_for(self.core)
        body = await readmission.call(gateway, TOOL, {"limit": 2})
        self.assertNotIn(b"CoreCapabilityUnavailable", body)
        self.assertEqual(len(app.calls), 1)

        # Useful success through the admitted runtime and a loopback peer.
        peer = LoopbackPeer()
        peer.auth_version = self.core.current_observation.version
        peer.replies[mi.MARKETPLACE_LIST_COMMAND] = result_frame(0, store(3, 2))
        web_app = web.Application()
        web_app.router.add_get("/{path:.*}", peer.handler)
        runner = web.AppRunner(web_app, access_log=None)
        await runner.setup()
        self.addAsyncCleanup(runner.cleanup)
        site = web.TCPSite(runner, "127.0.0.1", 0)
        await site.start()
        port = site._server.sockets[0].getsockname()[1]
        settings = SimpleNamespace(websocket_url=f"ws://127.0.0.1:{port}/api/websocket", ha_token=HA_TOKEN,
                                   access_secret=ACCESS_SECRET, ha_timeout_seconds=2.0)
        service = mi.MarketplaceInventoryService(mi.MarketplaceReader(settings), self.core,
                                                 known_secrets=(ACCESS_SECRET, HA_TOKEN))
        telemetry, token = begin_request()
        self.addCleanup(end_request, token)
        telemetry.caller_id = "synthetic_authenticated_caller"
        telemetry.core_dispatch_authorizer = lambda: True
        page = await service.inventory(mi.validate_arguments({"limit": 2}))
        self.assertEqual((page["backend"], page["installed_total"], page["count"], page["has_more"]),
                         ("core_marketplace", 3, 2, True))
        self.assertEqual([c["type"] for c in peer.ledger], [mi.MARKETPLACE_LIST_COMMAND])


if __name__ == "__main__":
    unittest.main()
