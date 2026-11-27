"""HAMCP-437: read-gateway argument and wrapper guards re-pinned to ha-mcp 9.2.0.

Ported from the retired ``test_upstream_8_5_compatibility`` and
``test_upstream_8_6_compatibility`` modules. Each case guards behaviour that
ships on 9.2.0: refused arguments never dispatch, the blueprint wrapper builds
only ``list``/``get`` reads, descriptor or catalog drift withholds the read,
and malformed, oversized or error results stay bounded with no retry or
fallback. Successful reads use the committed HAMCP-371 receipts. Get mode has
no 9.2.0 receipt; its answer is the reviewed synthetic one from
``test_hamcp371_blueprint_9_2``. No endpoint is contacted.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
from datetime import timedelta
import json
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "hass_mcp_engineering_beta"), str(ROOT / "tests")]

from ha_mcp_engineering.providers.upstream_blueprint import completeness  # noqa: E402
from support import internal_search_page  # noqa: E402
from support.ha_mcp_920_receipts import receipt, with_content  # noqa: E402
from support.ha_mcp_captures import RECEIPTS_920, arguments_920, call, capture, gateway_920  # noqa: E402
import test_core_update_continuity as core_continuity  # noqa: E402
import test_hamcp371_blueprint_9_2 as blueprint_9_2  # noqa: E402

BOUND = 60000
LIST = ("ha_manage_blueprints", {"action": "list", "domain": "automation"})
PATH = blueprint_9_2.PATH


def _text_error(code: str) -> dict:
    error = {"success": False, "error": {"code": code, "message": "synthetic"}}
    return {"isError": True, "content": [{"type": "text", "text": json.dumps(error)}]}


def _get_result(component: bool) -> dict:
    """The r12 envelope carrying the reviewed synthetic get-mode answer."""

    return with_content(receipt(RECEIPTS_920["ha_get_blueprint"])[1], blueprint_9_2._get_answer(component))


class ReadGuards920Tests(unittest.IsolatedAsyncioTestCase):
    async def test_scene_lookup_cannot_become_search(self):
        g, t = await gateway_920()
        for args in ({}, {"scene_id": None}, {"scene_id": ""}, {"scene_id": "x", "query": "x"},
                     {"scene_id": "x", "limit": 2}, {"scene_id": "x", "offset": 1},
                     {"scene_id": "x", "search_in_config": True}):
            with self.subTest(args=args):
                result, _ = await call(g, "ha_config_get_scene", args)
                self.assertFalse(result["success"], result)
                self.assertFalse(result["metadata"]["upstream_dispatch_occurred"])
        self.assertEqual(t.sent, [])
        args = arguments_920("ha_config_get_scene")
        result, _ = await call(g, "ha_config_get_scene", args)
        self.assertTrue(result["success"], result)
        self.assertEqual(t.sent, [("ha_config_get_scene", args)])

    async def test_template_new_operations_and_invalid_timeout_have_zero_dispatch(self):
        g, t = await gateway_920()
        for args in ({"condition": {}}, {"template": "x", "condition": None},
                     {"template": "x", "strict": False}, {"template": "x", "variables": {}},
                     *({"template": "x", "timeout": x} for x in (0, -1, 61, True, 1.5))):
            with self.subTest(args=args):
                result, _ = await call(g, "ha_eval_template", args)
                self.assertFalse(result["success"], result)
                self.assertFalse(result["metadata"]["upstream_dispatch_occurred"])
        self.assertEqual(t.sent, [])
        template = arguments_920("ha_eval_template")["template"]
        for timeout in (1, 3, 60):
            with self.subTest(timeout=timeout):
                result, _ = await call(g, "ha_eval_template", {"template": template, "timeout": timeout})
                self.assertTrue(result["success"], result)
                self.assertEqual(t.sent[-1], ("ha_eval_template", {
                    "template": template, "timeout": timeout, "report_errors": True,
                    "condition": None, "variables": None, "strict": False}))

    async def test_skill_selector_and_traversal_are_zero_io(self):
        g, t = await gateway_920()
        skill = arguments_920("ha_get_skill_guide")["skill"]
        for args in ({}, {"skill": "automation"}, *({"skill": skill, "file": p} for p in (
                "../x.md", "/SKILL.md", "references/../x.md", "https://example.invalid/x.md",
                "x%2fy.md", "a\\b.md", "", "x" * 513 + ".md"))):
            with self.subTest(args=str(args)[:60]):
                result, _ = await call(g, "ha_get_skill_guide", args)
                self.assertFalse(result["success"], result)
                self.assertFalse(result["metadata"]["upstream_dispatch_occurred"])
        self.assertEqual(t.sent, [])
        result, _ = await call(g, "ha_get_skill_guide", arguments_920("ha_get_skill_guide"))
        self.assertTrue(result["success"], result)
        self.assertEqual(t.sent, [("ha_get_skill_guide", {"file": "SKILL.md"})])

    async def test_every_descriptor_component_drift_withholds_target_keeps_sibling(self):
        mutations = {
            "description": lambda x: x.update(description=x["description"] + " drift"),
            "title": lambda x: x.update(title="drift"),
            "schema": lambda x: x["inputSchema"].update(description="drift"),
            "annotations": lambda x: x["annotations"].update(title="drift"),
            "output": lambda x: x.update(outputSchema={"type": "string"}),
            "metadata": lambda x: x["_meta"].update(ha_mcp={"policy": {"enabled": True}}),
        }
        for key, mutate in mutations.items():
            with self.subTest(component=key):
                tools = capture("9.2.0")["tools"]
                mutate(next(x for x in tools if x["name"] == "ha_eval_template"))
                g, t = await gateway_920(tools)
                registered = g._registered_tool_registry.snapshot()
                self.assertNotIn("ha_eval_template", registered)
                self.assertIn("ha_get_state", registered)
                self.assertEqual(t.sent, [])

    async def test_unknown_identity_and_protocol_refuse(self):
        for version, protocol in (("9.2.1", None), ("9.2.0", "2099-01-01")):
            with self.subTest(version=version, protocol=protocol):
                g, t = await gateway_920(version=version, protocol=protocol)
                self.assertEqual(g._registered_tool_registry.snapshot(), {})
                self.assertEqual(t.sent, [])

    async def test_live_missing_duplicate_or_incomplete_catalog_never_dispatches(self):
        for kind in ("missing", "duplicate", "incomplete"):
            with self.subTest(kind=kind):
                g, t = await gateway_920()
                tools = list(t.catalog.tools)
                if kind == "missing":
                    tools = [x for x in tools if x["name"] != "ha_get_state"]
                if kind == "duplicate":
                    tools.append(next(x for x in tools if x["name"] == "ha_get_state"))
                t.catalog = replace(t.catalog, tools=tuple(tools), catalog_complete=kind != "incomplete")
                result, _ = await call(g, "ha_get_state", arguments_920("ha_get_state"))
                self.assertFalse(result["success"], result)
                self.assertEqual(t.sent, [])

    async def test_errors_large_and_malformed_results_are_bounded_no_retry(self):
        g, t = await gateway_920()
        raws = (_text_error("RESOURCE_NOT_FOUND"), {"isError": False, "content": []},
                {"isError": False, "structuredContent": {"success": True, "huge": "x" * 100000}})
        for raw in raws:
            with self.subTest(raw=str(raw)[:40]):
                t.override = raw
                result, _ = await call(g, "ha_get_skill_guide", arguments_920("ha_get_skill_guide"))
                self.assertFalse(result["success"], result)
                self.assertLessEqual(len(json.dumps(result).encode()), BOUND)
                self.assertFalse(result["metadata"]["fallback_occurred"])
        self.assertEqual(len(t.sent), len(raws))


class BlueprintWrapper920Tests(unittest.IsolatedAsyncioTestCase):
    async def _blueprint(self, g, args):
        return (await call(g, "ha_get_blueprint", args))[0]

    async def test_list_constructs_only_read_action(self):
        g, t = await gateway_920()
        result = await self._blueprint(g, {})
        self.assertTrue(result["success"], result)
        self.assertEqual(result["metadata"]["completeness"], "complete")
        self.assertEqual(t.sent, [LIST])

    async def test_metadata_and_full_configuration_stay_distinct(self):
        for component, expected in ((False, "partial"), (True, "complete")):
            with self.subTest(component=component):
                g, t = await gateway_920()
                t.override = _get_result(component)
                result = await self._blueprint(g, blueprint_9_2.GET_ARGS)
                self.assertTrue(result["success"], result)
                self.assertEqual(result["metadata"]["completeness"], expected, result)
                self.assertEqual(t.sent, [("ha_manage_blueprints", {"action": "get", **blueprint_9_2.GET_ARGS})])
                self.assertEqual("config" in result["data"]["data"], component)

    async def test_forbidden_arguments_never_dispatch(self):
        g, t = await gateway_920()
        for field, value in (("action", "list"), ("action", "get"), ("action", "import"),
                             ("action", "save"), ("action", "delete"), ("action", "substitute"),
                             ("url", "https://example.invalid/x"), ("yaml", "synthetic"),
                             ("config", "synthetic"), ("confirm", True), ("overwrite", True),
                             ("input", {})):
            with self.subTest(field=field, value=value):
                result = await self._blueprint(g, {field: value})
                self.assertFalse(result["success"], result)
                self.assertFalse(result["metadata"]["upstream_dispatch_occurred"])
        self.assertEqual(t.sent, [])

    async def test_invalid_paths_domains_and_result_identity_refuse(self):
        g, t = await gateway_920()
        for args in ({"path": "../x.yaml"}, {"path": "https://example.invalid/x.yaml"},
                     {"path": "x%2Fy.yaml"}, {"domain": "light"}, {"path": 42},
                     {"path": "a/../x.yaml"}):
            with self.subTest(args=args):
                result = await self._blueprint(g, args)
                self.assertFalse(result["success"], result)
                self.assertFalse(result["metadata"]["upstream_dispatch_occurred"])
        self.assertEqual(t.sent, [])
        t.override = _get_result(False)
        result = await self._blueprint(g, {"path": "other/path.yaml"})
        self.assertFalse(result["success"], result)
        self.assertEqual(result["details"]["failure_category"], "invalid_response")
        self.assertEqual(len(t.sent), 1)

    async def test_mixed_descriptor_drift_withholds_wrapper(self):
        tools = capture("9.2.0")["tools"]
        item = next(x for x in tools if x["name"] == "ha_manage_blueprints")
        item["description"] += " drift"
        g, t = await gateway_920(tools)
        registered = g._registered_tool_registry.snapshot()
        self.assertNotIn("ha_get_blueprint", registered)
        self.assertIn("ha_get_state", registered)
        self.assertEqual(t.sent, [])

    def test_source_download_never_claims_installed_completeness(self):
        data = blueprint_9_2._get_answer(True)
        data["data"]["yaml_source"] = "source_url"
        partial, warnings = completeness(data)
        self.assertTrue(partial)
        self.assertTrue(warnings)

    async def test_text_only_error_is_canonical_and_never_retried(self):
        g, t = await gateway_920()
        t.override = _text_error("RESOURCE_NOT_FOUND")
        result = await self._blueprint(g, {"path": "missing.yaml"})
        self.assertFalse(result["success"], result)
        self.assertEqual(result["details"]["failure_category"], "resource_not_found", result)
        self.assertTrue(result["metadata"]["upstream_dispatch_occurred"])
        self.assertFalse(result["metadata"]["fallback_occurred"])
        self.assertEqual(len(t.sent), 1)

    async def test_malformed_and_oversized_results_remain_bounded(self):
        g, t = await gateway_920()
        cases = (
            {"isError": False, "content": []},
            {"isError": False, "structuredContent": {"success": True, "data": {
                "domain": "automation", "count": True, "blueprints": []}}},
            {"isError": False, "structuredContent": {"success": True, "data": {
                "domain": "automation", "count": 0, "blueprints": [{}]}}},
            {"isError": False, "structuredContent": {"success": True, "data": {
                "domain": "automation", "count": 1, "blueprints": [
                    {"name": "synthetic", "many_fields": {str(i): "x" * 100 for i in range(600)}}]}}},
        )
        for raw in cases:
            with self.subTest(raw=str(raw)[:60]):
                t.override = raw
                result = await self._blueprint(g, {})
                self.assertFalse(result["success"], result)
                self.assertLessEqual(len(json.dumps(result).encode()), BOUND)
                self.assertFalse(result["metadata"]["fallback_occurred"])
        self.assertEqual(len(t.sent), len(cases))

    async def test_complete_configuration_preserves_raw_input_and_unicode(self):
        g, t = await gateway_920()
        t.override = _get_result(True)
        t.override["structuredContent"]["data"]["config"]["alias"] = "Synthetic café fixture"
        t.override["content"][0]["text"] = json.dumps(t.override["structuredContent"])
        original = deepcopy(t.override)
        result = await self._blueprint(g, blueprint_9_2.GET_ARGS)
        self.assertTrue(result["success"], result)
        self.assertEqual(result["metadata"]["completeness"], "complete")
        self.assertEqual(result["data"]["data"]["config"], original["structuredContent"]["data"]["config"])
        self.assertEqual(t.override, original)

    async def test_unknown_duplicate_missing_and_incomplete_catalogs_refuse(self):
        g, t = await gateway_920(version="9.2.1")
        self.assertEqual(g._registered_tool_registry.snapshot(), {})
        for kind in ("duplicate", "missing", "incomplete"):
            with self.subTest(kind=kind):
                g, t = await gateway_920()
                tool = g._registered_tool_registry.snapshot()["ha_get_blueprint"]
                tools = list(t.catalog.tools)
                if kind == "duplicate":
                    tools.append(next(x for x in tools if x["name"] == "ha_manage_blueprints"))
                if kind == "missing":
                    tools = [x for x in tools if x["name"] != "ha_manage_blueprints"]
                t.catalog = replace(t.catalog, tools=tuple(tools), catalog_complete=kind != "incomplete")
                result = json.loads(await tool.run({}))
                self.assertFalse(result["success"], result)
                self.assertEqual(t.sent, [], kind)


class BlueprintCore920Tests(unittest.IsolatedAsyncioTestCase):
    """Retired 8.5 Core850Tests guard on 9.2.0: the read holds core authority."""

    asyncSetUp = core_continuity.CoreContinuityTests.asyncSetUp
    runtime = core_continuity.CoreContinuityTests.runtime
    assert_admitted = core_continuity.CoreContinuityTests.assert_admitted

    async def test_blueprint_read_retains_core_authority_and_expiry_fence(self):
        core, _ = await self.runtime()
        self.assert_admitted(core)
        g, t = await gateway_920(core_runtime=core)
        original = t.execute_read

        async def observe(*args, **kwargs):
            result = await original(*args, **kwargs)
            self.assertEqual(core.health_snapshot()["active_commit_count"], 1)
            return result
        t.execute_read = observe
        result = await BlueprintWrapper920Tests._blueprint(self, g, {})
        self.assertTrue(result["success"], result)
        self.assertEqual(t.sent, [LIST])
        self.assertEqual(core.health_snapshot()["active_commit_count"], 0)
        self.clock += timedelta(days=2)
        result = await BlueprintWrapper920Tests._blueprint(self, g, {})
        self.assertFalse(result["success"], result)
        self.assertEqual(t.sent, [LIST])
        health = core.health_snapshot()
        self.assertEqual((health["issued_lease_count"], health["active_commit_count"]), (0, 0))


class Policy920Tests(unittest.TestCase):
    """Retired 8.5 guard on the 9.2.0 policy: data cannot enable an uncompiled adapter."""

    def test_policy_data_cannot_enable_an_uncompiled_adapter(self):
        from ha_mcp_engineering.upstream_tool_policy import UpstreamToolPolicyError, load_upstream_tool_policy
        path = ROOT / "hass_mcp_engineering_beta/ha_mcp_engineering/upstream_tool_policy_9_2_0.json"
        value = json.loads(path.read_text())
        binding = {"expected_version": "9.2.0", "expected_source_tag": "v9.2.0",
                   "expected_source_commit": "51e3aac788482a081807591cd5d58d07dc4e540b"}
        self.assertTrue(load_upstream_tool_policy(path, **binding).by_name["ha_manage_blueprints"].is_read_route)
        with tempfile.TemporaryDirectory() as directory:
            candidate = Path(directory) / "policy.json"
            for changes in ({"argument_restrictions": ["uncompiled-adapter"]},
                            {"classification": "automatic_read"},
                            {"exposed_name": "ha_manage_blueprints"}):
                with self.subTest(changes=changes):
                    mutated = deepcopy(value)
                    next(x for x in mutated["tools"] if x["upstream_name"] == "ha_manage_blueprints").update(changes)
                    candidate.write_text(json.dumps(mutated))
                    with self.assertRaises(UpstreamToolPolicyError):
                        load_upstream_tool_policy(candidate, **binding)



class SearchDescriptor920Tests(unittest.IsolatedAsyncioTestCase):
    """A drifted ``ha_search`` descriptor withholds search; siblings stay useful.

    HAMCP-501 W6 (G1): the delegated ha_search is internal-only, so it is
    never published; drift withholds its internal route (the native search
    sub-call route) as well.
    """

    async def test_the_stock_descriptor_admits_the_internal_route_only(self):
        g, _t = await gateway_920()
        self.assertNotIn("ha_search", g._registered_tool_registry.snapshot())
        self.assertIn("ha_search", g._internal)

    async def assert_sibling_only(self, tools):
        g, t = await gateway_920(tools)
        self.assertNotIn("ha_search", g._registered_tool_registry.snapshot())
        self.assertNotIn("ha_search", g._internal)
        self.assertIn("ha_get_state", g._registered_tool_registry.snapshot())
        result, _ = await call(g, "ha_get_state", arguments_920("ha_get_state"))
        self.assertTrue(result["success"], result)
        self.assertEqual([name for name, _ in t.sent], ["ha_get_state"])

    async def test_mutated_descriptor_surfaces_refuse_search_with_useful_sibling(self):
        mutations = {
            "description": lambda t: t.update(description=t["description"] + " drift"),
            "input": lambda t: t["inputSchema"].update(description="drift"),
            "output": lambda t: t.update(outputSchema={"type": "string"}),
            "annotations": lambda t: t["annotations"].update(readOnlyHint=False),
            "title": lambda t: t.update(title="forged"),
            "metadata": lambda t: t["_meta"].update(unreviewed=True),
            "unknown": lambda t: t.update(forged_variant="component_unified"),
        }
        for field, mutate in mutations.items():
            with self.subTest(field=field):
                tools = deepcopy(capture("9.2.0")["tools"])
                mutate(next(t for t in tools if t["name"] == "ha_search"))
                await self.assert_sibling_only(tools)

    async def test_unknown_name_keeps_whole_catalog_refusal(self):
        tools = deepcopy(capture("9.2.0")["tools"])
        next(t for t in tools if t["name"] == "ha_search")["name"] = "ha_search_forged"
        g, t = await gateway_920(tools)
        self.assertNotIn("ha_search", g._registered_tool_registry.snapshot())
        self.assertNotIn("ha_search", g._internal)
        self.assertEqual(t.sent, [])

    async def test_missing_or_duplicate_search_is_not_a_full_catalog(self):
        for kind in ("missing", "duplicate"):
            with self.subTest(kind=kind):
                tools = deepcopy(capture("9.2.0")["tools"])
                target = next(t for t in tools if t["name"] == "ha_search")
                if kind == "missing":
                    tools.remove(target)
                else:
                    tools.append(deepcopy(target))
                g, t = await gateway_920(tools)
                self.assertNotIn("ha_search", g._registered_tool_registry.snapshot())
                self.assertNotIn("ha_search", g._internal)
                self.assertEqual(t.sent, [])


class SearchCompleteness920Tests(unittest.IsolatedAsyncioTestCase):
    """Controlled ``ha_search`` replies are never complete: one call, no fallback.

    9.2.0 keeps the 9.1 design: search hides filtered entities without a
    marker, so every result is qualified as visible scope and is partial.
    Upstream partial and warning signals add their own warnings; an
    unreviewed warning is replaced, never echoed. A reply without a boolean
    ``partial`` is refused as invalid.
    """

    async def test_controlled_provider_partial_warning_error_and_unknown_completeness(self):
        from ha_mcp_engineering.providers import upstream_reads_9_1_entities as entities

        g, t = await gateway_920(url="http://qzpeer:9583/qz7synthetic9credential/mcp")
        # HAMCP-501 W6 (G1): the route is reached only as a helper sub-call
        # page, so the controlled reply is a helper page.
        base = {"success": True, "entities": [], "helpers": [
                    {"entity_id": "sensor.synthetic_flow", "helper_type": "template",
                     "name": "Synthetic flow", "score": 90}],
                "count": 1, "entity_total_matches": 0, "config_total_matches": 1,
                "has_more": False, "entity_has_more": False, "config_has_more": False,
                "partial": False, "errors": [], "warnings": []}
        unbucketed = {key: value for key, value in base.items() if key != "helpers"}
        scope, partial, warned = entities.VISIBLE_SCOPE, entities.SEARCH_PARTIAL, entities.UPSTREAM_WARNINGS
        unreviewed = "component search path failed; served via legacy path"
        cases = [
            ("exhaustive", base, [scope]),
            ("paged", {**base, "has_more": True, "next_offset": 1}, [scope]),
            ("explicit_partial", {**base, "partial": True, "partial_reason": "synthetic bounded fault"},
             [scope, partial]),
            ("unreviewed_warning", {**base, "partial": True, "warnings": [unreviewed]}, [scope, partial, warned]),
            ("error", {**base, "partial": True, "errors": ["synthetic unavailable registry"]}, [scope, partial]),
            ("missing", {"success": True, "entities": []}, None),
            # A page without its bucket is not a page (W4 D1).
            ("unbucketed", unbucketed, None),
            ("malformed", {**base, "partial": "false"}, None),
        ]
        for label, data, expected in cases:
            with self.subTest(case=label):
                t.override = {"isError": False, "structuredContent": data}
                # HAMCP-501 W6 (G1): the internal-only route, as one sub-call page.
                result, telemetry = await internal_search_page.execute_page(g, with_telemetry=True)
                if expected is None:
                    self.assertFalse(result["success"], result)
                    self.assertEqual(result["details"]["failure_category"], "invalid_response")
                else:
                    self.assertTrue(result["success"], result)
                    self.assertEqual(result["metadata"]["completeness"], "partial", result)
                    self.assertEqual(result["warnings"][:len(expected)], expected)
                    self.assertNotIn(unreviewed, json.dumps(result))
                self.assertFalse(result["metadata"]["fallback_occurred"])
                self.assertEqual(telemetry.upstream_request_count, 1)
                self.assertEqual(telemetry.upstream_active_requests, 0)
        self.assertEqual(t.calls, len(cases))


class BlueprintContextProjection920Tests(unittest.TestCase):
    """The offline blueprint count follows the compiled 9.2.0 binding without executing source."""

    RUNTIME = Path("hass_mcp_engineering_beta/ha_mcp_engineering")

    def setUp(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location("hamcp437_context", ROOT / "scripts/codex-context.py")
        self.context = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.context)

    def policy(self):
        return json.loads((ROOT / self.RUNTIME / "upstream_tool_policy_9_2_0.json").read_text())

    def count(self, policy, root=ROOT):
        return self.context.blueprint_read_projection_count(root, policy)

    def blueprint(self, policy):
        return next(t for t in policy["tools"] if t["upstream_name"] == "ha_manage_blueprints")

    def source_control(self, suffix):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = self.RUNTIME / "providers/upstream_blueprint.py"
            (root / path).parent.mkdir(parents=True)
            (root / path).write_text((ROOT / path).read_text() + suffix)
            return self.count(self.policy(), root)

    def test_exact_binding(self):
        self.assertEqual(self.count(self.policy()), 1)

    def test_wrong_source(self):
        policy = self.policy()
        policy["reviewed_source_commit"] = "0" * 40
        self.assertEqual(self.count(policy), 0)

    def test_wrong_fingerprint(self):
        policy = self.policy()
        self.blueprint(policy)["input_schema_fingerprint"] = "0" * 64
        self.assertEqual(self.count(policy), 0)

    def test_uncompiled_wrapper(self):
        policy = self.policy()
        self.blueprint(policy)["argument_restrictions"] = ["uncompiled-wrapper"]
        self.assertEqual(self.count(policy), 0)

    def test_write_annotation(self):
        policy = self.policy()
        self.blueprint(policy)["reviewed_annotations"]["destructiveHint"] = True
        self.assertEqual(self.count(policy), 0)

    def test_missing_source(self):
        with tempfile.TemporaryDirectory() as directory:
            self.assertEqual(self.count(self.policy(), Path(directory)), 0)

    def test_source_is_never_executed(self):
        self.assertEqual(self.source_control("\nraise RuntimeError('must_not_execute')\n"), 1)

    def test_ambiguous_binding_refused(self):
        policy = self.policy()
        fingerprint = self.blueprint(policy)["input_schema_fingerprint"]
        duplicate = {"duplicate": ("9.2.0", policy["reviewed_source_commit"], fingerprint)}
        self.assertEqual(self.source_control("\nADAPTER_BINDINGS = " + repr(duplicate) + "\n"), 0)

if __name__ == "__main__":
    unittest.main()
