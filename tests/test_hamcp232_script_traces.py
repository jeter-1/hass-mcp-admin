"""Sanitized script trace reads (HAMCP-232/145 batch, design §4b).

``core.script_trace_read`` is runtime-only.  Until an owner-signed registry
references it the gateway refuses both tools with
``pending_signed_registry_admission`` before the app, and the service refuses
``authority_unavailable`` with zero Home Assistant commands.  With admission,
only ``trace/list``/``trace/get`` with ``domain: script`` and the exact
script key are sent; every result passes through the shared sanitizer before
output; a missing trace is not found, with no fallback.

Fixtures model Core 2026.10.3 ``components/trace/websocket_api.py`` and
``components/trace/models.py`` (``as_short_dict``).  Everything is synthetic;
credential-shaped values are assembled at test time.
"""

from __future__ import annotations

import ast
from copy import deepcopy
import json
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "hass_mcp_engineering_beta"), str(ROOT / "tests"), str(ROOT)]

import hamcp162_synthetic as syn  # noqa: E402
from ha_mcp_engineering import script_traces as st  # noqa: E402
from ha_mcp_engineering.capabilities import capability_for_tool  # noqa: E402
from ha_mcp_engineering.errors import (  # noqa: E402
    AuthorizationError,
    ErrorCode,
    HomeAssistantApiError,
    HomeAssistantTimeoutError,
    HomeAssistantUnavailableError,
)
from ha_mcp_engineering.ha_core_readmission.profiles import (  # noqa: E402
    CORE_CAPABILITY_PROFILES,
    CORE_NOVEMBER_BATCH_PROFILES,
    CORE_RUNTIME_CAPABILITY_PROFILES,
)
from ha_mcp_engineering.ha_core_readmission.routes import (  # noqa: E402
    PENDING_ADMISSION_TOOLS,
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
from ha_mcp_engineering.tools import script_traces as public  # noqa: E402
from ha_mcp_engineering.tools.registry import ENGINEERING_STATIC_TOOL_NAMES  # noqa: E402

import tests.test_core_2026_10_device_registry as october  # noqa: E402
import tests.test_ha_core_2026_9_integration as core9  # noqa: E402
import tests.test_hamcp069a_entity_metadata_readmission as readmission  # noqa: E402
from tests.test_ha_core_capability_auto_readmission import _evidence, _snapshot  # noqa: E402

PROFILE = "core.script_trace_read"
PENDING = "pending_signed_registry_admission"
TOOLS = ("list_script_traces", "get_script_trace")
SCRIPT_ID = "porch_scene"
RUN_ID = "0123456789abcdef0123456789abcdef"
ARGS = {"list_script_traces": {"script_id": SCRIPT_ID},
        "get_script_trace": {"script_id": SCRIPT_ID, "run_id": RUN_ID}}
HA_TOKEN = syn.needle("hatoken")
ACCESS_SECRET = syn.needle("accesssecret")
REFERENCE_KEYS = (
    "capability_id", "profile_id", "profile_version", "adapter_id", "contract_fingerprint",
)
MODULE = ROOT / "hass_mcp_engineering_beta/ha_mcp_engineering/script_traces.py"


def references() -> list[dict]:
    return [
        {key: profile.to_mapping()[key] for key in REFERENCE_KEYS}
        for profile in CORE_NOVEMBER_BATCH_PROFILES
        if profile.capability_id == PROFILE
    ]


def short_trace(run_id: str, start: str, **extra) -> dict:
    """``ActionTrace.as_short_dict`` for a script (Core 2026.10.3)."""

    return {
        "last_step": "sequence/0",
        "run_id": run_id,
        "state": "stopped",
        "script_execution": "finished",
        "timestamp": {"start": start, "finish": start},
        "domain": "script",
        "item_id": SCRIPT_ID,
        **extra,
    }


class AdmissionCore:
    def __init__(self, available: bool = True):
        self.available = available
        self.generation = 7
        self.calls: list[tuple] = []

    def route_status(self, requirements):
        self.calls.append(tuple(requirements))
        return {"available": self.available, "generation": self.generation}


class FakeClient:
    """Trace websocket fake; refuses exactly as the real client without authority."""

    def __init__(self, result=None, error: Exception | None = None, on_command=None):
        self.result = result
        self.error = error
        self.on_command = on_command
        self.commands: list[dict] = []

    async def command(self, payload):
        from core_authority_fakes import require_core_authority

        require_core_authority(self, "WS", "trace")
        self.commands.append(deepcopy(payload))
        if self.on_command is not None:
            self.on_command()
        if self.error is not None:
            raise self.error
        return deepcopy(self.result)


class ServiceTestCase(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.core = AdmissionCore()
        self.telemetry, self.token = begin_request()
        self.telemetry.caller_id = "synthetic_authenticated_caller"
        self.telemetry.core_dispatch_authorizer = lambda: self.core.available
        self.addCleanup(end_request, self.token)

    def service(self, client) -> st.ScriptTraceService:
        return st.ScriptTraceService(client, self.core, known_secrets=(ACCESS_SECRET, HA_TOKEN))

    async def refused(self, coroutine, reason: str):
        with self.assertRaises(st.ScriptTraceError) as raised:
            await coroutine
        self.assertEqual(raised.exception.reason, reason)
        self.assertNotIn(SCRIPT_ID, json.dumps(raised.exception.details))
        return raised.exception


class ContractTests(unittest.TestCase):
    def test_profile_is_runtime_only_read(self):
        self.assertIn(PROFILE, {p.capability_id for p in CORE_RUNTIME_CAPABILITY_PROFILES})
        self.assertNotIn(PROFILE, {p.capability_id for p in CORE_CAPABILITY_PROFILES})
        (profile,) = [p for p in CORE_NOVEMBER_BATCH_PROFILES if p.capability_id == PROFILE]
        self.assertFalse(profile.capability_class.mutation_capable)
        self.assertEqual(profile.to_mapping()["adapter_id"], st.PROVIDER_CONTRACT)

    def test_tools_are_registered_read_only_and_policy_bound(self):
        for tool in TOOLS:
            with self.subTest(tool=tool):
                self.assertIn(tool, ENGINEERING_STATIC_TOOL_NAMES)
                self.assertEqual(static_tool_requirements(tool, ARGS[tool]), (PROFILE,))
                self.assertEqual(PENDING_ADMISSION_TOOLS[tool], ("script_trace", PROFILE))
                self.assertEqual(TOOL_CAPABILITY_POLICY[tool], ProviderCapability.SCRIPT_TRACE)
                self.assertEqual(routing_for_tool(tool).route, CapabilityRoute.ENGINEERING_NATIVE)
                policy = ANALYTICAL_PROVIDER_POLICIES[tool]
                self.assertEqual((policy["access"], policy["writes_allowed"], policy["fallback_policy"]),
                                 ("read", "none", "none"))
                capability = capability_for_tool(tool)
                self.assertEqual(capability["core_capabilities"], [PROFILE])
                self.assertEqual(capability["core_admission_requirement"], "signed_core_registry_admission")
                self.assertEqual(capability["fallback"], "none")
                registered = getattr(public, f"registered_{tool.split('_')[0]}_tool")()
                self.assertIs(registered.annotations.readOnlyHint, True)
                self.assertIs(registered.parameters["additionalProperties"], False)

    def test_payloads_are_the_closed_vocabulary(self):
        self.assertEqual(st.list_payload(SCRIPT_ID),
                         {"type": "trace/list", "domain": "script", "item_id": SCRIPT_ID})
        self.assertEqual(st.get_payload(SCRIPT_ID, RUN_ID),
                         {"type": "trace/get", "domain": "script", "item_id": SCRIPT_ID, "run_id": RUN_ID})
        for bad in ("", "Porch", "porch-scene", "a" * 129, "script.porch", None, 1, "x\n"):
            with self.subTest(script_id=bad):
                with self.assertRaises(st.ScriptTraceError):
                    st.list_payload(bad)
        for bad in ("", "a" * 65, "../x", "a b", None, 7):
            with self.subTest(run_id=bad):
                with self.assertRaises(st.ScriptTraceError):
                    st.get_payload(SCRIPT_ID, bad)

    def test_source_builds_no_other_command(self):
        # Every string literal that looks like a websocket command type is
        # either one of the two reads or on the never-sent disclosure list.
        tree = ast.parse(MODULE.read_text(encoding="utf-8"))
        literals = {
            node.value for node in ast.walk(tree)
            if isinstance(node, ast.Constant) and isinstance(node.value, str)
            and node.value.startswith("trace/")
        }
        self.assertEqual(literals - set(st.NEVER_SENT_COMMANDS), {"trace/list", "trace/get"})


class ServiceAdmissionTests(ServiceTestCase):
    async def test_unadmitted_profile_refuses_with_zero_commands(self):
        self.core.available = False
        client = FakeClient([])
        for coroutine in (self.service(client).list_traces(SCRIPT_ID),
                          self.service(client).get_trace(SCRIPT_ID, RUN_ID)):
            error = await self.refused(coroutine, "authority_unavailable")
            self.assertEqual(error.code, ErrorCode.PROVIDER_UNAVAILABLE)
            self.assertEqual(error.details["core_capability"], PROFILE)
        self.assertEqual(client.commands, [])
        self.assertEqual(self.core.calls[0], (PROFILE,))

    async def test_missing_dispatch_authority_refuses(self):
        client = FakeClient([])
        self.telemetry.core_dispatch_authorizer = None
        await self.refused(self.service(client).list_traces(SCRIPT_ID), "authority_unavailable")
        self.telemetry.core_dispatch_authorizer = lambda: False
        await self.refused(self.service(client).list_traces(SCRIPT_ID), "authority_unavailable")
        self.assertEqual(client.commands, [])

    async def test_anonymous_caller_refuses(self):
        self.telemetry.caller_id = "anonymous"
        client = FakeClient([])
        await self.refused(self.service(client).list_traces(SCRIPT_ID), "authority_unavailable")
        self.assertEqual(client.commands, [])

    async def test_invalid_identifiers_refuse_before_admission_or_io(self):
        client = FakeClient([])
        await self.refused(self.service(client).list_traces("Bad Id"), "invalid_arguments")
        await self.refused(self.service(client).get_trace(SCRIPT_ID, "../x"), "invalid_arguments")
        self.assertEqual((client.commands, self.core.calls), ([], []))

    async def test_generation_change_across_the_read_refuses(self):
        def bump():
            self.core.generation += 1
        client = FakeClient([short_trace(RUN_ID, "2026-10-26T10:00:00+00:00")], on_command=bump)
        await self.refused(self.service(client).list_traces(SCRIPT_ID), "authority_unavailable")
        self.assertEqual(len(client.commands), 1)

    async def test_admission_withdrawn_across_the_read_refuses(self):
        def withdraw():
            self.core.available = False
        client = FakeClient({"run_id": RUN_ID}, on_command=withdraw)
        await self.refused(self.service(client).get_trace(SCRIPT_ID, RUN_ID), "authority_unavailable")


class ServiceReadTests(ServiceTestCase):
    async def test_list_is_exact_sanitized_and_normalized(self):
        older = "a" * 32
        client = FakeClient([
            short_trace(older, "2026-10-26T09:00:00+00:00"),
            short_trace(RUN_ID, "2026-10-26T10:00:00+00:00", error=f"token {HA_TOKEN} rejected"),
            {"run_id": "", "timestamp": {}},
        ])
        result = await self.service(client).list_traces(SCRIPT_ID)
        self.assertEqual(client.commands, [st.list_payload(SCRIPT_ID)])
        self.assertEqual([item["run_id"] for item in result["traces"]], [RUN_ID, older])
        self.assertEqual(result["upstream_runs_returned"], 3)
        self.assertEqual(result["malformed_entries"], 1)
        self.assertEqual(result["script_id"], SCRIPT_ID)
        rendered = json.dumps(result)
        self.assertNotIn(HA_TOKEN, rendered)
        self.assertIsNotNone(result["redaction"])

    async def test_get_is_exact_and_sanitized_before_output(self):
        trace = {
            "run_id": RUN_ID, "domain": "script", "item_id": SCRIPT_ID,
            "config": {"sequence": [{"action": "notify.x", "data": {"message": f"key {ACCESS_SECRET}"}}]},
            "trace": {"sequence/0": [{"path": "sequence/0", "result": {"params": {"api_key": "plain"}}}]},
            "context": {"id": "c" * 26, "user_id": None, "parent_id": None},
            "long": "x" * 5000,
        }
        client = FakeClient(trace)
        result = await self.service(client).get_trace(SCRIPT_ID, RUN_ID)
        self.assertEqual(client.commands, [st.get_payload(SCRIPT_ID, RUN_ID)])
        rendered = json.dumps(result)
        self.assertNotIn(ACCESS_SECRET, rendered)
        self.assertNotIn("x" * 2001, rendered)
        self.assertEqual(result["trace"]["run_id"], RUN_ID)
        self.assertIsNotNone(result["redaction"])

    async def test_not_found_is_reported_without_fallback(self):
        error = HomeAssistantApiError(details={"status": 404, "provider_response_received": True})
        client = FakeClient(error=error)
        refused = await self.refused(self.service(client).get_trace(SCRIPT_ID, RUN_ID), "trace_not_found")
        self.assertEqual(refused.code, ErrorCode.RESOURCE_NOT_FOUND)
        self.assertIs(refused.details["fallback_occurred"], False)
        self.assertEqual(len(client.commands), 1)
        # trace/list has no not-found contract; a 404 there is a provider error.
        await self.refused(self.service(FakeClient(error=error)).list_traces(SCRIPT_ID), "provider_error")

    async def test_provider_failures_map_to_closed_reasons(self):
        for error, reason in (
            (AuthorizationError(details={}), "permission_failure"),
            (HomeAssistantTimeoutError(details={}), "provider_timeout"),
            (HomeAssistantUnavailableError(details={}), "provider_unavailable"),
            (HomeAssistantApiError(details={"status": 500}), "provider_error"),
            (RuntimeError(f"secret {HA_TOKEN}"), "provider_error"),
        ):
            with self.subTest(reason=reason):
                refused = await self.refused(
                    self.service(FakeClient(error=error)).get_trace(SCRIPT_ID, RUN_ID), reason)
                self.assertNotIn(HA_TOKEN, json.dumps(refused.details))

    async def test_invalid_provider_shapes_refuse(self):
        await self.refused(self.service(FakeClient({"x": 1})).list_traces(SCRIPT_ID),
                           "invalid_provider_response")
        await self.refused(self.service(FakeClient([])).get_trace(SCRIPT_ID, RUN_ID),
                           "invalid_provider_response")


class PublicToolTests(ServiceTestCase):
    async def test_invalid_arguments_are_never_reflected(self):
        marker = "Not_A_Slug_" + "q" * 8
        for tool, arguments in (
            ("list_script_traces", {"script_id": marker}),
            ("list_script_traces", {"script_id": SCRIPT_ID, "extra": marker}),
            ("get_script_trace", {"script_id": SCRIPT_ID, "run_id": marker + "/.."}),
            ("get_script_trace", {"script_id": SCRIPT_ID}),
        ):
            with self.subTest(tool=tool, arguments=arguments):
                registered = getattr(public, f"registered_{tool.split('_')[0]}_tool")()
                rendered = await registered.run(arguments)
                self.assertNotIn(marker, rendered)
                self.assertIn("invalid_arguments", rendered)

    async def test_unconfigured_runtime_fails_closed(self):
        runtime = st.ScriptTraceRuntime()
        with self.assertRaises(st.ScriptTraceError):
            runtime.require()
        runtime.configure(FakeClient([]), self.core, SimpleNamespace(access_secret="a", ha_token="b"))
        self.assertIsInstance(runtime.require(), st.ScriptTraceService)


class UnadmittedCompiledCoreTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.runtime, _source = await core9.Core20269RuntimeTests()._runtime(
            _snapshot("2026.8.1", evidence=_evidence())
        )

    async def asyncTearDown(self):
        from ha_mcp_engineering.capabilities import replace_dynamic_upstream_capabilities

        replace_dynamic_upstream_capabilities((), {})

    async def test_gateway_refuses_with_pending_admission_before_the_app(self):
        for tool in TOOLS:
            with self.subTest(tool=tool):
                self.assertIsNone(self.runtime.acquire(static_tool_requirements(tool, ARGS[tool])))
                gateway, app = readmission.gateway_for(self.runtime)
                body = await readmission.call(gateway, tool, ARGS[tool])
                self.assertIn(b"CoreCapabilityUnavailable", body)
                self.assertIn(PENDING.encode(), body)
                self.assertIn(b'"resource_type": "script_trace"', body.replace(b'\\"', b'"'))
                self.assertEqual(app.calls, [])


class SignedTestRegistryAdmissionTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = october.OctoberSignedSelectionTests.asyncSetUp
    publish = october.OctoberSignedSelectionTests.publish

    async def asyncTearDown(self):
        from ha_mcp_engineering.capabilities import replace_dynamic_upstream_capabilities

        replace_dynamic_upstream_capabilities((), {})

    async def test_shipped_reference_set_withholds(self):
        await self.publish()
        self.assertIsNone(self.core.acquire((PROFILE,)))
        gateway, app = readmission.gateway_for(self.core)
        body = await readmission.call(gateway, "list_script_traces", ARGS["list_script_traces"])
        self.assertIn(b"CoreCapabilityUnavailable", body)
        self.assertEqual(app.calls, [])

    async def test_signed_reference_admits_the_read(self):
        entry = deepcopy(self.entry)
        entry["capabilities"] = [*self.entry["capabilities"], *references()]
        await self.publish(entry)
        # A read-only profile issues a target-less lease.
        self.assertIsNotNone(self.core.acquire((PROFILE,)))
        gateway, app = readmission.gateway_for(self.core)
        for tool in TOOLS:
            body = await readmission.call(gateway, tool, ARGS[tool])
            self.assertNotIn(b"CoreCapabilityUnavailable", body)
        self.assertEqual(len(app.calls), 2)

if __name__ == "__main__":
    unittest.main()
