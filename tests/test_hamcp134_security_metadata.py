"""Security and effective-permission metadata read (HAMCP-134, Release 2.6.0).

Design: ``docs/HAMCP_134_SECURITY_METADATA_FEASIBILITY.md`` (#392).
``core.security_metadata_read`` is runtime-only.  Until an owner-signed
registry references it the service refuses ``authority_unavailable`` with
zero Home Assistant commands.  With admission, only constant payloads exist:
``auth/current_user`` always, ``config/auth/list`` only for ``users``, then
one command per remaining section command, each at most once.  Every
section 6 command, every ``supervisor/`` and ``hassio/`` type and every write
is unreachable.

Fixtures model Core 2026.11.0 (74cf5a49):
``components/config/auth.py`` ``_user_info``, ``components/auth/__init__.py``
``websocket_current_user``, ``components/http/config.py`` ``ConfData``,
``components/homeassistant/exposed_entities.py``,
``components/assist_pipeline/pipeline.py``, ``components/conversation/http.py``
and ``components/frontend/__init__.py`` ``get_panels``.  Every value is
synthetic; credential-shaped values are assembled at test time.
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
from ha_mcp_engineering import configuration  # noqa: E402
from ha_mcp_engineering import security_metadata as sm  # noqa: E402
from ha_mcp_engineering.errors import ErrorCode  # noqa: E402
from ha_mcp_engineering.ha_core_readmission import profiles as core_profiles  # noqa: E402
from ha_mcp_engineering.ha_core_readmission.models import CoreCapabilityClass  # noqa: E402
from ha_mcp_engineering.ha_core_readmission.profiles import (  # noqa: E402
    CORE_CAPABILITY_PROFILES,
    CORE_RUNTIME_CAPABILITY_PROFILES,
    CORE_SECURITY_METADATA_PROFILES,
)
from ha_mcp_engineering.ha_core_readmission.routes import (  # noqa: E402
    DELEGATED_CORE_REQUIREMENTS,
    F3_ENTRY_ADMISSION_PROFILES,
    PENDING_ADMISSION_TOOLS,
    SECURITY_METADATA_PROFILE,
    SECURITY_METADATA_RESOURCE_TYPE,
    SECURITY_METADATA_TOOL,
)
from ha_mcp_engineering.providers.models import ProviderCapability  # noqa: E402
from ha_mcp_engineering.providers.routing import ANALYTICAL_PROVIDER_POLICIES  # noqa: E402
from ha_mcp_engineering.request_context import begin_request, end_request  # noqa: E402
from ha_mcp_engineering.tools import security_metadata as public  # noqa: E402

PROFILE = "core.security_metadata_read"
TOOL = "get_security_metadata"
HA_TOKEN = syn.needle("hatoken")
ACCESS_SECRET = syn.needle("accesssecret")
CORE_VERSION = "2026.11.0"
MODULE = ROOT / "hass_mcp_engineering_beta/ha_mcp_engineering/security_metadata.py"
TOOL_MODULE = ROOT / "hass_mcp_engineering_beta/ha_mcp_engineering/tools/security_metadata.py"
PINNED_FINGERPRINT = "sha256:9aa788b50f6d9544808bd8b89fd50c00ffd32e7ebcdd940b9dddace18b2e9c55"
SUPERVISOR = ("default", "supervisor_token")
ALTERNATIVE = ("ha_url_env", "ha_token")
ALL = sm.SecurityMetadataRequest()
# Raw upstream values that must never appear in any output.
SECRET_TEXT = (
    "u-raw-owner-0001", "u-raw-member-0002", "u-raw-super-0003", "SynthOwnerName", "synthowner",
    "SynthMemberName", "/ssl/fullchain.pem", "/ssl/privkey.pem", "/ssl/client-ca.pem",
    "172.30.33.0/24", "93.184.216.34", "https://synth-origin.example", "synthhome.ui.nabu.casa",
    "192.168.77.10", "SynthErrorMessageText", "SynthLatitude", "synth-voice", "synth-wake-id",
    "SynthPanelIcon", "synth-config-secret",
)


def user(raw_id, *, name="SynthMemberName", username="synthmember", owner=False, active=True,
         local_only=False, system=False, groups=("system-users",), credentials=("homeassistant",)):
    return {"id": raw_id, "username": username, "name": name, "is_owner": owner, "is_active": active,
            "local_only": local_only, "system_generated": system, "group_ids": list(groups),
            "credentials": [{"type": kind} for kind in credentials]}


def auth_list():
    return [
        user("u-raw-owner-0001", name="SynthOwnerName", username="synthowner", owner=True,
             groups=("system-admin",), credentials=("homeassistant", "trusted_networks")),
        user("u-raw-member-0002", groups=("system-users", "synth-custom-group")),
        user("u-raw-super-0003", name="Supervisor", username=None, system=True,
             groups=("system-admin",), credentials=()),
    ]


def current_user(*, supervisor=True, enabled=True):
    if supervisor:
        return {"id": "u-raw-super-0003", "name": "Supervisor", "is_owner": False, "is_admin": True,
                "credentials": [], "mfa_modules": [{"id": "totp", "name": "Authenticator app",
                                                    "enabled": enabled}]}
    return {"id": "u-raw-member-0002", "name": "SynthMemberName", "is_owner": False, "is_admin": False,
            "credentials": [{"auth_provider_type": "homeassistant", "auth_provider_id": None}],
            "mfa_modules": [{"id": "totp", "name": "Authenticator app", "enabled": enabled}]}


def slot(**overrides):
    record = {
        "server_port": 8123, "ssl_certificate": "/ssl/fullchain.pem", "ssl_key": "/ssl/privkey.pem",
        "ssl_peer_certificate": "/ssl/client-ca.pem", "cors_allowed_origins": ["https://synth-origin.example"],
        "use_x_forwarded_for": True, "trusted_proxies": ["172.30.33.0/24", "127.0.0.1", "93.184.216.34"],
        "login_attempts_threshold": -1, "ip_ban_enabled": True, "ssl_profile": "modern",
        "use_x_frame_options": True, "server_host": ["0.0.0.0", "192.168.77.10"],
        "created_at": "2026-11-01T00:00:00+00:00", "error": None, "error_message": "SynthErrorMessageText",
    }
    record.update(overrides)
    return {key: value for key, value in record.items() if value is not ...}


def default_slot():
    return {"server_port": 8123, "cors_allowed_origins": ["https://cast.home-assistant.io"],
            "login_attempts_threshold": -1, "ip_ban_enabled": True, "ssl_profile": "modern",
            "use_x_frame_options": True}


def http_config(active="stable", **overrides):
    record = {"stable": slot(), "pending": None, "revert_at": None, "active_config_type": active,
              "default": default_slot()}
    record.update(overrides)
    return record


def get_config(**overrides):
    record = {"external_url": "https://synthhome.ui.nabu.casa", "internal_url": "http://192.168.77.10:8123",
              "latitude": "SynthLatitude", "location_name": "SynthOwnerName", "version": CORE_VERSION}
    record.update(overrides)
    return {key: value for key, value in record.items() if value is not ...}


def pipelines():
    return {"pipelines": [{
        "conversation_engine": "conversation.home_assistant", "conversation_language": "en", "id": "01synthpipe",
        "language": "en", "name": "Synth Assist", "stt_engine": "stt.synth", "stt_language": "en",
        "tts_engine": "tts.synth", "tts_language": "en", "tts_voice": "synth-voice",
        "wake_word_entity": None, "wake_word_id": "synth-wake-id", "prefer_local_intents": False,
    }], "preferred_pipeline": "01synthpipe"}


def agents():
    return {"agents": [{"id": "conversation.home_assistant", "name": "Home Assistant",
                        "supported_languages": ["en", "de"]}]}


def panels():
    return {
        "config": {"component_name": "config", "icon": "SynthPanelIcon", "title": None, "default_visible": True,
                   "config": {"synth": "synth-config-secret"}, "url_path": "config", "require_admin": True,
                   "config_panel_domain": None, "show_in_sidebar": False},
        "synth_app": {"component_name": "app", "icon": "SynthPanelIcon", "title": "Synth App",
                      "default_visible": True, "config": {"addon": "synth_addon", "other": "synth-config-secret"},
                      "url_path": "synth_app", "require_admin": True, "config_panel_domain": None,
                      "show_in_sidebar": True},
        "energy": {"component_name": "energy", "icon": "SynthPanelIcon", "title": "energy",
                   "default_visible": True, "config": None, "url_path": "energy", "require_admin": False,
                   "config_panel_domain": None, "show_in_sidebar": True},
    }


def results(*, supervisor=True):
    return {
        sm.CURRENT_USER: current_user(supervisor=supervisor),
        sm.AUTH_LIST: auth_list(),
        sm.HTTP_CONFIG: http_config(),
        sm.GET_CONFIG: get_config(),
        sm.EXPOSE_NEW_GET: {"expose_new": False},
        sm.PIPELINE_LIST: pipelines(),
        sm.AGENT_LIST: agents(),
        sm.GET_PANELS: panels(),
    }


def replies_for(request, overrides=None):
    data = results()
    data.update(overrides or {})
    out = []
    for payload in sm.read_plan(request):
        value = data[payload["type"]]
        if isinstance(value, sm.Reply):
            out.append(sm.Reply(payload["type"], result=value.result, reason=value.reason))
        else:
            out.append(sm.Reply(payload["type"], result=deepcopy(value)))
    return out


def report(request=ALL, overrides=None, topology=SUPERVISOR, secrets=(ACCESS_SECRET, HA_TOKEN)):
    return sm.build_report(request, CORE_VERSION, replies_for(request, overrides), topology, secrets,
                           observed_at="2026-11-09T00:00:00+00:00")


def failure(reason):
    return sm.Reply("ignored", reason=reason)


def result_frame(command_id: int, result) -> str:
    return json.dumps({"id": command_id, "type": "result", "success": True, "result": result})


def error_frame(command_id: int, code, message: str = "Unknown command.") -> str:
    return json.dumps({"id": command_id, "type": "result", "success": False,
                       "error": {"code": code, "message": message}})


def profile_contract() -> dict:
    """The response contract material is part of the fingerprint; re-derive it."""

    return profile_keyword("response_contract")


def profile_keyword(name: str):

    import inspect

    source = inspect.getsource(core_profiles)
    start = source.index("CORE_SECURITY_METADATA_PROFILES = (")
    end = source.index("CORE_RUNTIME_CAPABILITY_PROFILES = (")
    tree = ast.parse(source[start:end])
    call = tree.body[0].value.elts[0]
    for keyword in call.keywords:
        if keyword.arg == name:
            return ast.literal_eval(keyword.value)
    raise AssertionError(f"{name} missing")


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
    def __init__(self, overrides=None, error=None, during=None):
        self.overrides = overrides
        self.error = error
        self.during = during
        self.calls = 0
        self.plans: list = []

    async def read(self, *, core_version, plan, authorize, on_dispatch=lambda _c: None, on_sent=lambda _c: None):
        self.calls += 1
        self.plans.append(plan)
        authorize()
        for payload in plan:
            on_dispatch(payload["type"])
            on_sent(payload["type"])
        if self.during is not None:
            self.during()
        authorize()
        if self.error is not None:
            raise self.error
        request = sm.SecurityMetadataRequest(tuple(
            name for name in sm.SECTIONS
            if any(p in plan for p in sm.SECTION_PAYLOADS[name]) or (name == "mfa" and self.mfa)))
        return replies_for(request, self.overrides)[:len(plan)] if False else self._replies(plan)

    mfa = False

    def _replies(self, plan):
        data = results()
        data.update(self.overrides or {})
        out = []
        for payload in plan:
            value = data[payload["type"]]
            if isinstance(value, sm.Reply):
                out.append(sm.Reply(payload["type"], reason=value.reason))
            else:
                out.append(sm.Reply(payload["type"], result=deepcopy(value)))
        return out


class ServiceTestCase(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.core = AdmissionCore()
        self.telemetry, self.token = begin_request()
        self.telemetry.caller_id = "synthetic_authenticated_caller"
        self.telemetry.core_dispatch_authorizer = lambda: self.core.available
        self.addCleanup(end_request, self.token)

    def service(self, reader, topology=SUPERVISOR) -> sm.SecurityMetadataService:
        return sm.SecurityMetadataService(reader, self.core, topology=topology,
                                          known_secrets=(ACCESS_SECRET, HA_TOKEN))

    async def refused(self, coroutine, reason: str) -> sm.SecurityMetadataError:
        with self.assertRaises(sm.SecurityMetadataError) as raised:
            await coroutine
        self.assertEqual(raised.exception.reason, reason)
        return raised.exception


def dumps(value) -> str:
    return json.dumps(value, sort_keys=True, default=str)


class ContractTests(unittest.TestCase):
    def test_profile_is_runtime_only_read_never_compiled_for_any_release(self):
        self.assertIn(PROFILE, {p.capability_id for p in CORE_RUNTIME_CAPABILITY_PROFILES})
        self.assertNotIn(PROFILE, {p.capability_id for p in CORE_CAPABILITY_PROFILES})
        (profile,) = CORE_SECURITY_METADATA_PROFILES
        self.assertEqual(profile.capability_id, PROFILE)
        self.assertEqual(profile.profile_id, "core_security_metadata_read_v1")
        self.assertIs(profile.capability_class, CoreCapabilityClass.SECURITY_METADATA_READ)
        self.assertFalse(profile.capability_class.mutation_capable)
        self.assertTrue(profile.capability_class.semantic)
        self.assertEqual(profile.adapter_id, sm.PROVIDER_CONTRACT)
        self.assertEqual(profile_keyword("provider_boundary"), "direct_ha_api")
        self.assertEqual(profile.contract_fingerprint, PINNED_FINGERPRINT)
        # Not compiled for any shipped release (2026.10.x included): only an
        # owner-signed registry reference can admit it.
        for version, _commit, _digest in core_profiles.SUPPORTED_CORE_RELEASES:
            with self.subTest(version=version):
                self.assertNotIn(PROFILE, {
                    item.capability_ids[0] for item in core_profiles.compiled_exact_authority(version)})

    def test_existing_profile_fingerprints_are_unchanged(self):
        others = {p.capability_id: p.contract_fingerprint for p in CORE_RUNTIME_CAPABILITY_PROFILES
                  if p.capability_id != PROFILE}
        self.assertEqual(len(others), 33)
        import test_hamcp290_marketplace_inventory as marketplace
        self.assertEqual(others["core.marketplace_read"], marketplace.PINNED_FINGERPRINT)

    def test_contract_matches_the_module_vocabulary_and_bounds(self):
        contract = profile_contract()
        self.assertEqual(set(contract["commands"]), sm.COMMANDS)
        self.assertEqual(contract["expose_new_entities_assistants"], list(sm.KNOWN_ASSISTANTS))
        self.assertEqual(contract["max_commands"], sm.MAX_COMMANDS)
        self.assertEqual(len(sm.PAYLOADS), sm.MAX_COMMANDS)
        self.assertEqual(contract["sections"], list(sm.SECTIONS))
        self.assertEqual(contract["always_sent"], [sm.CURRENT_USER])
        self.assertEqual(contract["users_only"], [sm.AUTH_LIST])
        self.assertEqual(contract["never_sent"],
                         [*sm.NEVER_SENT_COMMANDS, *(prefix + "*" for prefix in sm.NEVER_SENT_PREFIXES)])
        self.assertEqual(contract["bounds"], {
            "frame_bytes": sm.FRAME_BYTES, "auth_bytes": sm.AUTH_BYTES, "max_frames": sm.MAX_FRAMES,
            "max_users": sm.MAX_USERS, "max_list_items": sm.MAX_LIST_ITEMS,
            "output_bytes": sm.OUTPUT_BYTES, "max_seconds": sm.MAX_SECONDS})
        self.assertEqual(contract["never_called"], ["async_should_expose"])
        self.assertEqual((contract["supervisor_requests"], contract["write"]), ("none", "none"))
        from ha_mcp_engineering.approval_identity import MAX_USERS
        self.assertEqual(sm.MAX_USERS, MAX_USERS)

    def test_payloads_are_constant_and_closed(self):
        self.assertEqual([dict(p) for p in sm.PAYLOADS], [
            {"type": "auth/current_user"}, {"type": "config/auth/list"}, {"type": "http/config"},
            {"type": "get_config"},
            {"type": "homeassistant/expose_new_entities/get", "assistant": "cloud.alexa"},
            {"type": "homeassistant/expose_new_entities/get", "assistant": "cloud.google_assistant"},
            {"type": "homeassistant/expose_new_entities/get", "assistant": "conversation"},
            {"type": "assist_pipeline/pipeline/list"}, {"type": "conversation/agent/list"},
            {"type": "get_panels"},
        ])
        with self.assertRaises(TypeError):
            sm.AGENT_LIST_PAYLOAD["language"] = "en"  # type: ignore[index]
        self.assertFalse(sm.COMMANDS & set(sm.NEVER_SENT_COMMANDS))
        self.assertFalse({c for c in sm.COMMANDS if c.startswith(sm.NEVER_SENT_PREFIXES)})

    def test_admission_gate_accepts_only_the_constant_objects(self):
        for payload in sm.PAYLOADS:
            self.assertTrue(sm.admitted_payload(payload))
            # An equal copy is not the constant and is refused.
            self.assertFalse(sm.admitted_payload(dict(payload)))
        for command in (*sm.NEVER_SENT_COMMANDS, "supervisor/api", "hassio/addon/info", "call_service"):
            with self.subTest(command=command):
                self.assertFalse(sm.admitted_payload({"type": command}))

    def test_source_builds_no_other_command_and_no_supervisor_request(self):
        for path in (MODULE, TOOL_MODULE):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            literals = {node.value for node in ast.walk(tree)
                        if isinstance(node, ast.Constant) and isinstance(node.value, str)}
            commandish = {value for value in literals if "/" in value and " " not in value
                          and not value.startswith(("http", "/", "ws"))}
            with self.subTest(path=path.name):
                self.assertTrue(commandish - set(sm.NEVER_SENT_COMMANDS) - set(sm.NEVER_SENT_PREFIXES)
                                <= sm.COMMANDS, commandish)
                # No callable named async_should_expose, no HTTP verb on a session.
                calls = {node.func.attr for node in ast.walk(tree)
                         if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)}
                self.assertNotIn("async_should_expose", calls)
                self.assertFalse(calls & {"get", "post", "put", "patch", "delete", "request"} - {"get"})
                self.assertNotIn("async_should_expose", {node.id for node in ast.walk(tree)
                                                         if isinstance(node, ast.Name)})
        source = MODULE.read_text(encoding="utf-8")
        for forbidden in ("http://supervisor", "SUPERVISOR_TOKEN", "aiohttp.ClientSession().get"):
            self.assertNotIn(forbidden, source)

    def test_routing_and_public_schema(self):
        self.assertIn(PROFILE, F3_ENTRY_ADMISSION_PROFILES)
        self.assertEqual(SECURITY_METADATA_PROFILE, PROFILE)
        self.assertEqual((SECURITY_METADATA_TOOL, SECURITY_METADATA_RESOURCE_TYPE), (TOOL, "security_metadata"))
        self.assertEqual(ProviderCapability.SECURITY_METADATA.value, "security_metadata")
        policy = ANALYTICAL_PROVIDER_POLICIES[TOOL]
        self.assertEqual((policy["access"], policy["writes_allowed"], policy["fallback_policy"]),
                         ("read", "none", "none"))
        self.assertFalse(TOOL.startswith("ha_"))
        self.assertNotIn(TOOL, DELEGATED_CORE_REQUIREMENTS)
        # Registration is pending wiring (application.py / capabilities.py frozen).
        self.assertNotIn(TOOL, PENDING_ADMISSION_TOOLS)
        registered = public.registered_tool()
        self.assertIs(registered.annotations.readOnlyHint, True)
        self.assertIs(registered.annotations.destructiveHint, False)
        self.assertIs(registered.annotations.openWorldHint, False)
        self.assertIs(registered.parameters.get("additionalProperties"), False)
        self.assertEqual(set(registered.parameters["properties"]), {"sections"})
        self.assertEqual(registered.parameters.get("required", []), [])
        enum = registered.parameters["properties"]["sections"]["anyOf"][0]["items"]["enum"]
        self.assertEqual(enum, list(sm.SECTIONS))

    def test_omissions_name_every_x_area_without_session_defaults(self):
        self.assertEqual(set(sm.OMISSIONS), {
            "per_user_mfa_enrolment",
            "per_user_session_token_policy_and_long_lived_token_existence",
            "complete_effective_exposure",
            "conversation_agent_llm_api_and_tool_scope",
            "satellite_user_authority",
            "device_tablet_kiosk_acting_user",
            "complete_add_on_inventory_and_other_add_on_options",
        })
        self.assertEqual(set(sm.OMISSIONS.values()), {"not_provided_by_api"})
        fixed = dumps([dict(sm.OMISSIONS), dict(sm.PERMISSION_LIMITS), dict(sm.RELATED_TOOLS)])
        self.assertFalse(any(ch.isdigit() for ch in fixed), "no session-default constant")
        self.assertEqual(set(sm.PERMISSION_LIMITS), {
            "mfa.connection_user_only", "panels.listing_not_enforcement",
            "http.default_legacy_port_not_returned"})
        self.assertEqual(set(sm.RELATED_TOOLS), {"ha_get_system_health", "ha_get_entity_exposure",
                                                 "ha_get_integration"})


class ArgumentTests(unittest.TestCase):
    def test_defaults_and_canonical_order(self):
        self.assertEqual(sm.validate_arguments({}).sections, sm.SECTIONS)
        self.assertEqual(sm.validate_arguments({"sections": None}).sections, sm.SECTIONS)
        self.assertEqual(sm.validate_arguments({"sections": ["panels", "users"]}).sections, ("users", "panels"))

    def test_refused_values_are_closed_and_never_reflected(self):
        marker = "Refused_" + "q" * 8
        for arguments in (None, [], {"sections": marker}, {"sections": [marker]}, {"sections": []},
                          {"sections": ["users", "users"]}, {"sections": ["users"], "extra": marker},
                          {"sections": [1]}, {marker: 1}, {"sections": ("users",)}):
            with self.subTest(arguments=arguments):
                with self.assertRaises(sm.SecurityMetadataError) as raised:
                    sm.validate_arguments(arguments)
                self.assertEqual(raised.exception.reason, "invalid_arguments")
                self.assertNotIn(marker, dumps(raised.exception.details))
                self.assertNotIn(marker, str(raised.exception))

    def test_unknown_reason_collapses_to_provider_error(self):
        error = sm.SecurityMetadataError("SynthUnknownReason")
        self.assertEqual(error.reason, "provider_error")
        self.assertNotIn("SynthUnknownReason", dumps(error.details))


class ReadPlanTests(unittest.TestCase):
    """The exact read set: no optional read, nothing beyond the requested sections."""

    def types(self, *sections):
        request = sm.SecurityMetadataRequest(tuple(sections)) if sections else ALL
        return [(p["type"], p.get("assistant")) for p in sm.read_plan(request)]

    def test_exact_plan_per_section(self):
        cu = (sm.CURRENT_USER, None)
        self.assertEqual(self.types("mfa"), [cu])
        self.assertEqual(self.types("users"), [cu, (sm.AUTH_LIST, None)])
        self.assertEqual(self.types("http_boundary"), [cu, (sm.HTTP_CONFIG, None)])
        self.assertEqual(self.types("urls"), [cu, (sm.GET_CONFIG, None)])
        self.assertEqual(self.types("panels"), [cu, (sm.GET_PANELS, None)])
        self.assertEqual(self.types("assist"), [cu, *((sm.EXPOSE_NEW_GET, a) for a in sm.KNOWN_ASSISTANTS),
                                                (sm.PIPELINE_LIST, None), (sm.AGENT_LIST, None)])
        everything = self.types()
        self.assertEqual(len(everything), sm.MAX_COMMANDS)
        self.assertEqual(len(set(everything)), len(everything))

    def test_auth_list_is_sent_only_for_users(self):
        for sections in (("mfa",), ("http_boundary", "urls", "assist", "panels"), ("mfa", "panels")):
            with self.subTest(sections=sections):
                self.assertNotIn(sm.AUTH_LIST, [t for t, _a in self.types(*sections)])

    def test_no_optional_read_is_ever_planned(self):
        planned = {t for t, _a in self.types()}
        self.assertNotIn("homeassistant/expose_entity/list", planned)
        self.assertNotIn("assist_satellite/get_configuration", planned)
        self.assertFalse({t for t in planned if t.startswith(("supervisor/", "hassio/"))})


class UsersProjectionTests(unittest.TestCase):
    def test_pseudonymous_privacy_projection(self):
        value = report()
        users = value["sections"]["users"]
        self.assertEqual(users["user_count"], 3)
        self.assertEqual([u["pseudonym"] for u in users["users"]], ["user-1", "user-2", "user-3"])
        owner, member, service = users["users"]
        self.assertEqual(owner, {"pseudonym": "user-1", "is_owner": True, "is_admin": True, "is_active": True,
                                 "local_only": False, "system_generated": False,
                                 "groups": {"admin": True, "user": False, "read_only": False,
                                            "other_group_count": 0},
                                 "credential_types": ["homeassistant", "trusted_networks"]})
        self.assertEqual(member["groups"], {"admin": False, "user": True, "read_only": False,
                                            "other_group_count": 1})
        self.assertIs(member["is_admin"], False)
        self.assertIs(service["is_admin"], True)
        self.assertEqual(service["credential_types"], [])
        text = dumps(value)
        for raw in SECRET_TEXT + ("synth-custom-group", "synthmember"):
            self.assertNotIn(raw, text)
        self.assertEqual(value["coverage"]["users"], {"status": "observed", "commands": [sm.AUTH_LIST],
                                                      "reason_code": None})

    def test_pseudonyms_follow_core_order_not_identity(self):
        reordered = list(reversed(auth_list()))
        value = report(overrides={sm.AUTH_LIST: reordered})
        self.assertIs(value["sections"]["users"]["users"][0]["system_generated"], True)
        self.assertEqual(value["sections"]["users"]["users"][0]["pseudonym"], "user-1")

    def test_every_missing_kept_field_is_not_provided(self):
        for key, output in (("is_owner", "is_owner"), ("is_active", "is_active"), ("local_only", "local_only"),
                            ("system_generated", "system_generated"), ("group_ids", "groups"),
                            ("credentials", "credential_types")):
            with self.subTest(key=key):
                records = auth_list()
                del records[1][key]
                value = report(overrides={sm.AUTH_LIST: records})
                self.assertEqual(value["sections"]["users"]["users"][1][output], sm.NOT_PROVIDED)
                self.assertEqual(value["coverage"]["users"]["status"], "observed")

    def test_is_admin_is_never_guessed(self):
        for key in ("is_owner", "is_active", "group_ids"):
            with self.subTest(key=key):
                records = auth_list()
                del records[1][key]
                value = report(overrides={sm.AUTH_LIST: records})
                self.assertEqual(value["sections"]["users"]["users"][1]["is_admin"], sm.NOT_PROVIDED)
        records = auth_list()
        records[0]["is_active"] = False
        del records[0]["group_ids"]
        # Owners are admin by Core's rule regardless of the other inputs.
        self.assertIs(report(overrides={sm.AUTH_LIST: records})["sections"]["users"]["users"][0]["is_admin"], True)
        records = auth_list()
        records[1]["groups"] = None
        records[1]["group_ids"] = ["system-admin"]
        records[1]["is_active"] = False
        self.assertIs(report(overrides={sm.AUTH_LIST: records})["sections"]["users"]["users"][1]["is_admin"], False)

    def test_drift_fails_the_section_closed(self):
        cases = {
            "not_a_list": {"users": "x"},
            "bool_type": [dict(auth_list()[0], is_owner="true")],
            "missing_id": [{k: v for k, v in auth_list()[0].items() if k != "id"}],
            "duplicate_id": [auth_list()[0], auth_list()[0]],
            "group_type": [dict(auth_list()[0], group_ids="system-admin")],
            "credential_type": [dict(auth_list()[0], credentials=[{"type": 1}])],
        }
        for name, records in cases.items():
            with self.subTest(case=name):
                value = report(overrides={sm.AUTH_LIST: records})
                self.assertIsNone(value["sections"]["users"])
                self.assertEqual(value["coverage"]["users"]["status"], "failed")
                self.assertEqual(value["coverage"]["users"]["reason_code"], "unexpected_shape")
                self.assertEqual(value["completeness"], "partial")
                self.assertNotIn("u-raw-owner-0001", dumps(value))

    def test_more_than_max_users_fails_closed(self):
        records = [user(f"u-raw-{i:05d}") for i in range(sm.MAX_USERS + 1)]
        value = report(sm.SecurityMetadataRequest(("users",)), overrides={sm.AUTH_LIST: records})
        self.assertEqual(value["coverage"]["users"]["reason_code"], "too_many_users")
        self.assertIsNone(value["sections"]["users"])

    def test_core_error_codes_map_to_fixed_reasons(self):
        for code, reason in (("not_admin", "not_admin"), ("command_unavailable", "command_unavailable"),
                             ("core_error", "core_error")):
            with self.subTest(reason=reason):
                value = report(overrides={sm.AUTH_LIST: failure(reason)})
                self.assertEqual(value["coverage"]["users"], {"status": "failed", "commands": [sm.AUTH_LIST],
                                                              "reason_code": reason})


class IdentityTests(unittest.TestCase):
    def identity(self, topology=SUPERVISOR, request=ALL, overrides=None):
        return report(request, overrides=overrides, topology=topology)["connection_identity"]

    def test_supervisor_corroborated(self):
        self.assertEqual(self.identity(), {"kind": "supervisor_system_user", "is_admin": True,
                                           "basis": "supervisor_topology_and_core_corroboration"})
        # Without users, is_admin plus empty credentials corroborate.
        self.assertEqual(self.identity(request=sm.SecurityMetadataRequest(("mfa",)))["kind"],
                         "supervisor_system_user")

    def test_supervisor_corroboration_failure_is_unknown(self):
        for overrides in ({sm.CURRENT_USER: current_user(supervisor=False)},
                          {sm.CURRENT_USER: dict(current_user(), credentials=[{"auth_provider_type": "x"}])},
                          {sm.AUTH_LIST: [user("u-raw-super-0003", system=False)]},
                          {sm.AUTH_LIST: failure("not_admin")},
                          {sm.AUTH_LIST: [user("u-raw-other-0009", system=True)]}):
            with self.subTest(overrides=list(overrides)):
                identity = self.identity(overrides=overrides)
                self.assertEqual((identity["kind"], identity["basis"]),
                                 ("unknown", "supervisor_corroboration_failed"))

    def test_alternative_connection_is_other_user(self):
        for topology in (ALTERNATIVE, ("ha_url_env", "supervisor_token"), ("default", "ha_token")):
            with self.subTest(topology=topology):
                identity = self.identity(topology, overrides={sm.CURRENT_USER: current_user(supervisor=False)})
                self.assertEqual(identity, {"kind": "other_connection_user", "is_admin": False,
                                            "basis": "alternative_connection_configuration"})
        # An admin personal token matching every Supervisor signal is still not Supervisor.
        identity = self.identity(ALTERNATIVE)
        self.assertEqual((identity["kind"], identity["is_admin"]), ("other_connection_user", True))

    def test_current_user_failure_is_unknown(self):
        for overrides in ({sm.CURRENT_USER: failure("core_error")},
                          {sm.CURRENT_USER: {"id": "u-raw-super-0003"}}):
            with self.subTest(overrides=overrides):
                identity = self.identity(overrides=overrides)
                self.assertEqual(identity, {"kind": "unknown", "is_admin": sm.NOT_PROVIDED,
                                            "basis": "current_user_failed"})

    def test_unknown_token_source_is_unknown(self):
        for topology in (("unknown", "unknown"), ("default", "none"), ("unknown", "supervisor_token")):
            with self.subTest(topology=topology):
                identity = self.identity(topology)
                self.assertEqual((identity["kind"], identity["basis"]), ("unknown", "token_source_unknown"))

    def test_identity_carries_no_id_or_name(self):
        text = dumps(report()["connection_identity"])
        self.assertNotIn("u-raw-super-0003", text)
        self.assertNotIn("Supervisor\"", text)

    def test_settings_record_token_source_without_value(self):
        cases = (
            ({"SUPERVISOR_TOKEN": HA_TOKEN}, ("default", "supervisor_token")),
            ({"HA_TOKEN": HA_TOKEN}, ("default", "ha_token")),
            ({"HA_URL": "http://synth.invalid:8123", "SUPERVISOR_TOKEN": HA_TOKEN}, ("ha_url_env", "supervisor_token")),
            ({}, ("default", "none")),
        )
        for env, expected in cases:
            with self.subTest(env=sorted(env)), mock.patch.dict("os.environ", env, clear=True):
                settings = configuration.load_settings()
                self.assertEqual((settings.ha_url_source, settings.ha_token_source), expected)
        self.assertEqual((configuration.Settings.ha_url_source, configuration.Settings.ha_token_source),
                         ("unknown", "unknown"))

    def test_non_admin_connection_fails_admin_only_sections_with_not_admin(self):
        """Design section 9 (finding M3): non-admin user, admin-only sections not_admin."""

        overrides = {sm.CURRENT_USER: dict(current_user(supervisor=False), is_admin=False),
                     sm.AUTH_LIST: failure("not_admin"), sm.HTTP_CONFIG: failure("not_admin")}
        value = report(overrides=overrides, topology=ALTERNATIVE)
        self.assertEqual(value["connection_identity"], {
            "kind": "other_connection_user", "is_admin": False,
            "basis": "alternative_connection_configuration"})
        for name in ("users", "http_boundary"):
            with self.subTest(section=name):
                self.assertIsNone(value["sections"][name])
                self.assertEqual((value["coverage"][name]["status"], value["coverage"][name]["reason_code"]),
                                 ("failed", "not_admin"))
        # Non-admin-only sections still report; MFA enrolment is never reported
        # for an identity other than the Supervisor system user.
        self.assertEqual(value["coverage"]["urls"]["status"], "observed")
        self.assertEqual(value["coverage"]["panels"]["status"], "observed")
        self.assertNotIn("service_user_enrolled", dumps(value["sections"]["mfa"]))


class MfaTests(unittest.TestCase):
    def test_supervisor_enrolment_is_service_user_only(self):
        value = report()
        self.assertEqual(value["sections"]["mfa"], {
            "modules": [{"id": "totp", "name": "Authenticator app", "service_user_enrolled": True}],
            "enrolment_scope": "supervisor_system_user"})
        self.assertEqual(value["coverage"]["mfa"]["status"], "observed")

    def test_other_identity_enrolment_is_withheld(self):
        for topology, overrides in ((ALTERNATIVE, None), (SUPERVISOR, {sm.CURRENT_USER: current_user(supervisor=False)})):
            with self.subTest(topology=topology):
                value = report(overrides=overrides, topology=topology)
                mfa = value["sections"]["mfa"]
                self.assertEqual(mfa["modules"], [{"id": "totp", "name": "Authenticator app"}])
                self.assertEqual(mfa["connection_user_enrolment"], "not_reported")
                self.assertNotIn("enabled", dumps(mfa))
                self.assertNotIn("service_user_enrolled", dumps(mfa))
                self.assertEqual(value["coverage"]["mfa"]["status"], "partial")
                self.assertEqual(value["coverage"]["mfa"]["commands"], [sm.CURRENT_USER])

    def test_current_user_failure_fails_mfa(self):
        value = report(overrides={sm.CURRENT_USER: failure("core_error")})
        self.assertEqual(value["coverage"]["mfa"]["status"], "failed")
        self.assertIsNone(value["sections"]["mfa"])


class HttpBoundaryTests(unittest.TestCase):
    def http(self, record):
        value = report(sm.SecurityMetadataRequest(("http_boundary",)), overrides={sm.HTTP_CONFIG: record})
        return value["sections"]["http_boundary"], value["coverage"]["http_boundary"], value

    def test_stable_active_projection(self):
        section, coverage, value = self.http(http_config())
        self.assertEqual(coverage["status"], "observed")
        self.assertEqual(section["active_config_type"], "stable")
        self.assertEqual(section["active"], {
            "server_port": 8123,
            "server_host": {"bind": "explicit", "explicit_bind_count": 2, "explicit_bind_includes_wildcard": True},
            "tls_configured": True, "client_cert_required": True, "ssl_profile": "modern",
            "use_x_forwarded_for": True,
            "trusted_proxies": {"count": 3, "includes_loopback": True, "includes_private": True,
                                "includes_public": True},
            "cors_allowed_origins_count": 1, "ip_ban_enabled": True, "login_attempts_threshold": -1,
            "use_x_frame_options": True, "error": "none",
        })
        self.assertEqual(section["configured"]["label"], "configured_not_necessarily_active")
        self.assertIsNone(section["configured"]["pending"])
        self.assertEqual((section["pending_trial_revert_at"], section["pending_trial_active"]), (None, False))
        text = dumps(value)
        for raw in SECRET_TEXT:
            self.assertNotIn(raw, text)
        self.assertNotIn("created_at", text)

    def test_default_slot_only_when_default_is_active(self):
        section, coverage, value = self.http(http_config("default"))
        self.assertEqual(section["active"]["server_host"], {"bind": "default"})
        self.assertIs(section["active"]["tls_configured"], False)
        for key in ("use_x_forwarded_for", "trusted_proxies", "error"):
            self.assertEqual(section["active"][key], sm.NOT_PROVIDED)
        self.assertNotIn("cast.home-assistant.io", dumps(value))
        section, _c, _v = self.http(http_config())
        self.assertNotIn("default", section["configured"])

    def test_pending_trial_active(self):
        record = http_config("pending", pending=slot(server_port=8443, error=None),
                             revert_at="2026-11-09T06:00:00+00:00")
        section, coverage, _value = self.http(record)
        self.assertEqual(coverage["status"], "observed")
        self.assertEqual(section["active"]["server_port"], 8443)
        self.assertEqual(section["configured"]["pending"]["server_port"], 8443)
        self.assertEqual(section["pending_trial_revert_at"], "2026-11-09T06:00:00+00:00")
        self.assertIs(section["pending_trial_active"], True)

    def test_active_slot_is_never_substituted(self):
        cases = (
            (http_config("default_legacy_port"), "default_legacy_port", "active_slot_not_returned"),
            (http_config("synth_future_type"), "unknown", "active_config_type_unrecognized"),
            ({k: v for k, v in http_config("pending").items() if k != "pending"}, "pending", "active_slot_missing"),
            (http_config("pending", pending=slot(error="apply_failed")), "pending", "active_slot_inconsistent"),
            (http_config("pending", pending=None), "pending", "active_slot_inconsistent"),
        )
        for record, active_type, reason in cases:
            with self.subTest(reason=reason, active_type=active_type):
                section, coverage, _value = self.http(record)
                self.assertEqual(section["active"], "unknown")
                self.assertEqual(section["active_config_type"], active_type)
                self.assertEqual(section["active_reason"], reason)
                self.assertEqual((coverage["status"], coverage["reason_code"]), ("partial", reason))
                # The configured stable slot is still reported, labelled.
                self.assertEqual(section["configured"]["stable"]["server_port"], 8123)

    def test_error_mapping_drops_text(self):
        for raw, mapped in ((None, "none"), ("apply_failed", "apply_failed"), ("not_promoted", "not_promoted"),
                            ("SynthErrorMessageText", "other")):
            with self.subTest(raw=raw):
                section, _c, value = self.http(http_config(stable=slot(error=raw)))
                self.assertEqual(section["active"]["error"], mapped)
                self.assertNotIn("SynthErrorMessageText", dumps(value))

    def test_absent_fields_are_not_provided_and_absent_tls_is_false(self):
        minimal = {"server_port": 8123}
        section, coverage, _value = self.http(http_config(stable=minimal))
        active = section["active"]
        self.assertEqual(coverage["status"], "observed")
        self.assertEqual((active["tls_configured"], active["client_cert_required"]), (False, False))
        self.assertEqual(active["server_host"], {"bind": "default"})
        for key in ("ssl_profile", "use_x_forwarded_for", "trusted_proxies", "cors_allowed_origins_count",
                    "ip_ban_enabled", "login_attempts_threshold", "use_x_frame_options", "error"):
            self.assertEqual(active[key], sm.NOT_PROVIDED, key)
        for key in ("pending", "revert_at"):
            record = http_config()
            del record[key]
            section, _c, _v = self.http(record)
            self.assertEqual(section["configured"]["pending"] if key == "pending"
                             else section["pending_trial_revert_at"], sm.NOT_PROVIDED)

    def test_proxy_classes(self):
        for proxies, expected in ((["127.0.0.1"], (1, True, False, False)),
                                  (["10.0.0.0/8", "::1"], (2, True, True, False)),
                                  (["93.184.216.34"], (1, False, False, True)), ([], (0, False, False, False))):
            with self.subTest(proxies=proxies):
                section, _c, _v = self.http(http_config(stable=slot(trusted_proxies=proxies)))
                p = section["active"]["trusted_proxies"]
                self.assertEqual((p["count"], p["includes_loopback"], p["includes_private"], p["includes_public"]),
                                 expected)

    def test_drift_fails_the_section_closed(self):
        for record in ("x", http_config(stable=slot(server_port="8123")),
                       http_config(stable=slot(trusted_proxies=["synth-not-an-ip"])),
                       http_config(stable=slot(server_host="0.0.0.0")),
                       http_config(stable=slot(error=3)),
                       http_config(revert_at="SynthNotADate"), http_config(revert_at="2026-11-09T06:00:00"),
                       http_config(stable=slot(ssl_certificate=True))):
            with self.subTest(record=str(record)[:60]):
                section, coverage, value = self.http(record)
                self.assertIsNone(section)
                self.assertEqual((coverage["status"], coverage["reason_code"]), ("failed", "unexpected_shape"))
                self.assertNotIn("synth-not-an-ip", dumps(value))
                self.assertNotIn("SynthNotADate", dumps(value))

    def test_design_section_9_active_slot_cases(self):
        """Design section 9 (finding M2) slot fixtures: active is only the selected slot."""

        path_text = "/config/synth-secret-dir/fullchain.pem"
        trial_stable = slot(ssl_certificate=..., ssl_peer_certificate=..., trusted_proxies=["127.0.0.1"])
        trial_pending = slot(trusted_proxies=["10.0.0.0/8", "93.184.216.34"], error=None)
        cases = {
            "stable_with_apply_failed_pending": (
                http_config("stable", pending=slot(server_port=8443, error="apply_failed",
                                                   error_message="Failed: " + path_text)),
                {"server_port": 8123, "error": "none"}, {"server_port": 8443, "error": "apply_failed"}),
            "stable_with_not_promoted_pending": (
                http_config("stable", pending=slot(server_port=8443, error="not_promoted")),
                {"server_port": 8123, "error": "none"}, {"server_port": 8443, "error": "not_promoted"}),
            "stable_with_error_free_pending_recovery": (
                http_config("stable", pending=slot(server_port=8443, error=None)),
                {"server_port": 8123, "error": "none"}, {"server_port": 8443, "error": "none"}),
        }
        for name, (record, active, pending) in cases.items():
            with self.subTest(case=name):
                section, coverage, value = self.http(record)
                self.assertEqual(coverage["status"], "observed")
                self.assertEqual(section["active_config_type"], "stable")
                self.assertEqual({k: section["active"][k] for k in active}, active)
                self.assertEqual({k: section["configured"]["pending"][k] for k in pending}, pending)
                self.assertEqual(section["configured"]["label"], "configured_not_necessarily_active")
                text = dumps(value)
                for raw in (path_text, "synth-secret-dir", "SynthErrorMessageText", "error_message"):
                    self.assertNotIn(raw, text)
        # Default active after stable failed: the in-memory stable error is a
        # configured observation; the default slot carries no error key.
        section, coverage, value = self.http(http_config(
            "default", stable=slot(error="apply_failed", error_message="Failed: " + path_text)))
        self.assertEqual(coverage["status"], "observed")
        self.assertEqual(section["active"]["error"], sm.NOT_PROVIDED)
        self.assertIs(section["active"]["tls_configured"], False)
        self.assertEqual(section["active"]["server_host"], {"bind": "default"})
        self.assertEqual(section["configured"]["stable"]["error"], "apply_failed")
        self.assertNotIn(path_text, dumps(value))
        # Trial: stable and pending differ in TLS and proxies; active shows pending.
        section, coverage, value = self.http(http_config(
            "pending", stable=trial_stable, pending=trial_pending, revert_at="2026-11-09T06:00:00+00:00"))
        self.assertEqual(coverage["status"], "observed")
        self.assertIs(section["active"]["tls_configured"], True)
        self.assertIs(section["active"]["client_cert_required"], True)
        self.assertEqual(section["active"]["trusted_proxies"], {
            "count": 2, "includes_loopback": False, "includes_private": True, "includes_public": True})
        self.assertIs(section["configured"]["stable"]["tls_configured"], False)
        self.assertEqual(section["configured"]["stable"]["trusted_proxies"]["count"], 1)
        self.assertIs(section["pending_trial_active"], True)
        for raw in ("10.0.0.0/8", "93.184.216.34", "127.0.0.1"):
            self.assertNotIn(raw, dumps(value))

    def test_selected_slot_of_the_wrong_type_is_never_substituted(self):
        # The selected default slot (not otherwise projected) is a non-mapping:
        # active is unknown with a fixed reason; stable is not borrowed.
        section, coverage, value = self.http(http_config("default", default=["SynthWrongTypeSlot"]))
        self.assertEqual((section["active"], section["active_reason"]), ("unknown", "active_slot_inconsistent"))
        self.assertEqual((coverage["status"], coverage["reason_code"]), ("partial", "active_slot_inconsistent"))
        self.assertNotIn("SynthWrongTypeSlot", dumps(value))
        # A selected stable or pending slot of the wrong type is also drifted as
        # a configured observation, so the whole section fails closed.
        for record in (http_config("stable", stable="SynthWrongTypeSlot"),
                       http_config("pending", pending=["SynthWrongTypeSlot"])):
            with self.subTest(active=record["active_config_type"]):
                section, coverage, value = self.http(record)
                self.assertIsNone(section)
                self.assertEqual((coverage["status"], coverage["reason_code"]), ("failed", "unexpected_shape"))
                self.assertNotIn("SynthWrongTypeSlot", dumps(value))


class UrlTests(unittest.TestCase):
    def urls(self, record):
        value = report(sm.SecurityMetadataRequest(("urls",)), overrides={sm.GET_CONFIG: record})
        return value["sections"]["urls"], value["coverage"]["urls"], value

    def test_url_posture_without_hosts(self):
        section, coverage, value = self.urls(get_config())
        self.assertEqual(coverage["status"], "observed")
        self.assertEqual(section["external_url"], {"present": True, "scheme": "https", "https": True,
                                                   "is_nabu_casa_domain": True, "uses_default_port": True})
        self.assertEqual(section["internal_url"], {"present": True, "scheme": "http", "https": False,
                                                   "is_nabu_casa_domain": False, "uses_default_port": False})
        text = dumps(value)
        for raw in SECRET_TEXT:
            self.assertNotIn(raw, text)

    def test_absent_external_url_is_unknown_local_only(self):
        section, coverage, _v = self.urls(get_config(external_url=...))
        self.assertEqual(section["external_url"], {"status": "unknown", "reason": "withheld_for_local_only_user"})
        self.assertEqual((coverage["status"], coverage["reason_code"]), ("partial", "withheld_for_local_only_user"))

    def test_absent_internal_url_is_not_provided_and_null_is_not_present(self):
        section, coverage, _v = self.urls(get_config(internal_url=..., external_url=None))
        self.assertEqual(section["internal_url"], sm.NOT_PROVIDED)
        self.assertEqual(section["external_url"], {"present": False})
        self.assertEqual(coverage["status"], "observed")
        section, _c, _v = self.urls(get_config(external_url=""))
        self.assertEqual(section["external_url"], {"present": False})

    def test_scheme_and_lookalike_domains(self):
        for url, expected in (("ftp://synth.example", ("other", False)),
                              ("https://ui.nabu.casa", ("https", False)),
                              ("https://synthhome.ui.nabu.casa.evil.example", ("https", False)),
                              ("https://synthhome.ui.nabu.casa:443/x", ("https", True))):
            with self.subTest(url=url):
                section, _c, value = self.urls(get_config(external_url=url))
                self.assertEqual((section["external_url"]["scheme"], section["external_url"]["is_nabu_casa_domain"]),
                                 expected)
                self.assertNotIn("evil", dumps(value))

    def test_drift_fails_closed(self):
        for record in ("x", get_config(external_url=5), get_config(internal_url="http://synth:notaport")):
            with self.subTest(record=str(record)[:50]):
                section, coverage, _v = self.urls(record)
                self.assertIsNone(section)
                self.assertEqual((coverage["status"], coverage["reason_code"]), ("failed", "unexpected_shape"))


class AssistTests(unittest.TestCase):
    def assist(self, overrides=None):
        value = report(sm.SecurityMetadataRequest(("assist",)), overrides=overrides)
        return value["sections"]["assist"], value["coverage"]["assist"], value

    def test_assist_projection(self):
        section, coverage, value = self.assist()
        self.assertEqual(coverage, {"status": "observed",
                                    "commands": [sm.EXPOSE_NEW_GET, sm.PIPELINE_LIST, sm.AGENT_LIST],
                                    "reason_code": None})
        self.assertEqual(section["expose_new_entities"], {a: False for a in sm.KNOWN_ASSISTANTS})
        self.assertEqual(section["pipelines"], [{
            "id": "01synthpipe", "name": "Synth Assist", "conversation_engine": "conversation.home_assistant",
            "stt_engine": "stt.synth", "tts_engine": "tts.synth", "wake_word_entity": None, "language": "en",
            "prefer_local_intents": False}])
        self.assertEqual(section["preferred_pipeline"], "01synthpipe")
        self.assertEqual(section["agents"], [{"id": "conversation.home_assistant", "name": "Home Assistant"}])
        text = dumps(value)
        for raw in ("synth-voice", "synth-wake-id", "supported_languages", "tts_language"):
            self.assertNotIn(raw, text)

    def test_component_failures(self):
        section, coverage, _v = self.assist({sm.AGENT_LIST: failure("command_unavailable")})
        self.assertEqual((coverage["status"], coverage["reason_code"]), ("partial", "command_unavailable"))
        self.assertEqual(section["agents"], [])
        self.assertEqual(section["agents_status"], {"status": "failed", "reason_code": "command_unavailable"})
        self.assertEqual(len(section["pipelines"]), 1)
        section, coverage, _v = self.assist({sm.EXPOSE_NEW_GET: {"expose_new": "no"}})
        self.assertEqual(section["expose_new_entities"]["conversation"],
                         {"status": "failed", "reason_code": "unexpected_shape"})
        self.assertEqual(coverage["status"], "partial")
        section, coverage, _v = self.assist({sm.PIPELINE_LIST: {"pipelines": [{"name": "no id"}]}})
        self.assertEqual(section["preferred_pipeline"], sm.NOT_PROVIDED)
        self.assertEqual(coverage["status"], "partial")

    def test_all_components_failed(self):
        overrides = {c: failure("not_admin") for c in (sm.EXPOSE_NEW_GET, sm.PIPELINE_LIST, sm.AGENT_LIST)}
        section, coverage, _v = self.assist(overrides)
        self.assertIsNone(section)
        self.assertEqual((coverage["status"], coverage["reason_code"]), ("failed", "not_admin"))
        overrides[sm.AGENT_LIST] = failure("core_error")
        _s, coverage, _v = self.assist(overrides)
        self.assertEqual(coverage["reason_code"], "component_failed")

    def test_missing_pipeline_fields_are_not_provided(self):
        record = pipelines()
        for key in ("name", "stt_engine", "wake_word_entity", "prefer_local_intents"):
            del record["pipelines"][0][key]
        del record["preferred_pipeline"]
        section, coverage, _v = self.assist({sm.PIPELINE_LIST: record})
        pipe = section["pipelines"][0]
        for key in ("name", "stt_engine", "wake_word_entity", "prefer_local_intents"):
            self.assertEqual(pipe[key], sm.NOT_PROVIDED, key)
        self.assertEqual(section["preferred_pipeline"], sm.NOT_PROVIDED)
        self.assertEqual(coverage["status"], "observed")


class PanelTests(unittest.TestCase):
    def test_panel_projection(self):
        value = report(sm.SecurityMetadataRequest(("panels",)))
        section = value["sections"]["panels"]
        self.assertEqual(section["panel_count"], 3)
        self.assertEqual([p["url_path"] for p in section["panels"]], ["config", "energy", "synth_app"])
        config, _energy, app = section["panels"]
        self.assertEqual(config, {"url_path": "config", "component_name": "config",
                                  "panel_listing_require_admin": True, "show_in_sidebar": False, "title": None})
        self.assertEqual(app["addon"], "synth_addon")
        self.assertNotIn("addon", config)
        text = dumps(value)
        for raw in ("SynthPanelIcon", "synth-config-secret", "default_visible", "config_panel_domain"):
            self.assertNotIn(raw, text)

    def test_missing_panel_fields_and_drift(self):
        record = panels()
        del record["energy"]["require_admin"]
        del record["synth_app"]["config"]
        section = report(sm.SecurityMetadataRequest(("panels",)), overrides={sm.GET_PANELS: record})["sections"]["panels"]
        self.assertEqual(section["panels"][1]["panel_listing_require_admin"], sm.NOT_PROVIDED)
        self.assertEqual(section["panels"][2]["addon"], sm.NOT_PROVIDED)
        record = panels()
        record["energy"]["show_in_sidebar"] = "yes"
        value = report(sm.SecurityMetadataRequest(("panels",)), overrides={sm.GET_PANELS: record})
        self.assertEqual(value["coverage"]["panels"]["status"], "failed")


class SanitizationAndBoundsTests(unittest.TestCase):
    def test_secrets_bearing_text_is_redacted(self):
        record = pipelines()
        record["pipelines"][0]["name"] = "Synth " + HA_TOKEN
        agent_list = {"agents": [{"id": "conversation.synth", "name": syn.jwt("agentjwt")}]}
        table = panels()
        table["energy"]["title"] = "mail synth.owner@example.invalid now"
        value = report(overrides={sm.PIPELINE_LIST: record, sm.AGENT_LIST: agent_list, sm.GET_PANELS: table})
        text = dumps(value)
        self.assertNotIn(HA_TOKEN, text)
        self.assertNotIn(syn.needle("agentjwt"), text)
        self.assertNotIn("synth.owner@example.invalid", text)
        self.assertIsNotNone(value["redaction"])
        self.assertEqual(value["completeness"], "partial")
        self.assertFalse(value["sanitization_failed_closed"])

    def test_capped_strings_count_as_truncated(self):
        record = pipelines()
        record["pipelines"][0]["name"] = "n" * (sm.TITLE_CAP + 20)
        value = report(sm.SecurityMetadataRequest(("assist",)), overrides={sm.PIPELINE_LIST: record})
        self.assertEqual(len(value["sections"]["assist"]["pipelines"][0]["name"]), sm.TITLE_CAP)
        self.assertEqual(value["truncated_field_count"], 1)
        self.assertEqual(value["completeness"], "partial")

    def test_clean_report_is_complete_except_declared_partials(self):
        value = report(sm.SecurityMetadataRequest(("users", "mfa", "http_boundary", "urls", "assist", "panels")))
        self.assertEqual({k: v["status"] for k, v in value["coverage"].items()},
                         {k: "observed" for k in sm.SECTIONS})
        self.assertEqual(value["completeness"], "complete")
        self.assertIsNone(value["redaction"])
        self.assertEqual(value["omissions"], dict(sm.OMISSIONS))

    def test_not_requested_sections_are_not_provided(self):
        value = report(sm.SecurityMetadataRequest(("mfa",)))
        for name in sm.SECTIONS:
            if name != "mfa":
                self.assertIsNone(value["sections"][name])
                self.assertEqual(value["coverage"][name], {"status": "not_provided", "commands": [],
                                                           "reason_code": "not_requested"})
        self.assertEqual(value["completeness"], "complete")

    def test_output_cap_removes_whole_items_lowest_priority_first(self):
        table = {f"p{i:03d}": {"component_name": "iframe", "url_path": f"p{i:03d}", "require_admin": False,
                               "show_in_sidebar": True, "title": "t" * 100} for i in range(400)}
        value = report(overrides={sm.GET_PANELS: table})
        self.assertLessEqual(len(json.dumps(value, sort_keys=True, separators=(",", ":"))), sm.OUTPUT_BYTES + 64)
        coverage = value["coverage"]["panels"]
        self.assertEqual((coverage["status"], coverage["reason_code"]), ("partial", "output_cap"))
        kept = len(value["sections"]["panels"]["panels"])
        self.assertEqual(kept + coverage["removed_items"], 400)
        self.assertEqual(value["sections"]["panels"]["panels"][-1]["title"], "t" * 100)  # no string cut
        self.assertEqual(value["coverage"]["users"]["status"], "observed")
        self.assertEqual(value["completeness"], "partial")

    def test_output_cap_moves_on_and_finally_refuses(self):
        built = report()
        tiny = deepcopy(built)
        sm.apply_output_cap(tiny, cap=len(json.dumps(tiny, sort_keys=True, separators=(",", ":"))) - 50)
        self.assertEqual(tiny["coverage"]["panels"]["reason_code"], "output_cap")
        with self.assertRaises(sm.SecurityMetadataError) as raised:
            sm.apply_output_cap(deepcopy(built), cap=500)
        self.assertEqual(raised.exception.reason, "response_too_large")

    def test_list_bound(self):
        record = {"pipelines": [dict(pipelines()["pipelines"][0], id=f"p{i}") for i in range(sm.MAX_LIST_ITEMS + 1)],
                  "preferred_pipeline": None}
        value = report(sm.SecurityMetadataRequest(("assist",)), overrides={sm.PIPELINE_LIST: record})
        self.assertEqual(value["sections"]["assist"]["pipelines_status"]["reason_code"], "list_exceeds_bound")

    def test_reply_ledger_mismatch_is_provider_error(self):
        replies = replies_for(ALL)
        for broken in (replies[:-1], [*replies[:-1], sm.Reply("get_config", result={})]):
            with self.assertRaises(sm.SecurityMetadataError) as raised:
                sm.build_report(ALL, CORE_VERSION, broken, SUPERVISOR)
            self.assertEqual(raised.exception.reason, "provider_error")


class ClassifyReplyTests(unittest.TestCase):
    def test_success_and_error_codes(self):
        self.assertEqual(sm.classify_reply(json.loads(result_frame(1, [])), 1, sm.AUTH_LIST).result, [])
        for code, reason in (("unauthorized", "not_admin"), ("unknown_command", "command_unavailable"),
                             ("home_assistant_error", "core_error"), ("SynthCode", "core_error")):
            with self.subTest(code=code):
                reply = sm.classify_reply(json.loads(error_frame(1, code, "SynthErrorMessageText")), 1, sm.AUTH_LIST)
                self.assertEqual(reply.reason, reason)
                self.assertNotIn("SynthErrorMessageText", repr(reply))

    def test_malformed_envelopes(self):
        for message in ([], {"id": 1, "type": "event"}, {"id": 2, "type": "result", "success": True, "result": 1},
                        {"id": 1, "type": "result", "success": "yes"}, {"id": 1, "type": "result", "success": True},
                        {"id": 1, "type": "result", "success": False, "error": {"code": 3}},
                        {"id": 1, "type": "result", "success": False, "result": 1, "error": {"code": "x"}},
                        {"id": True, "type": "result", "success": True, "result": 1}):
            with self.subTest(message=message):
                with self.assertRaises(sm.SecurityMetadataError) as raised:
                    sm.classify_reply(message, 1, sm.AUTH_LIST)
                self.assertEqual(raised.exception.reason, "malformed_response")


class ServiceAdmissionTests(ServiceTestCase):
    async def test_unadmitted_profile_refuses_with_zero_reads(self):
        self.core.available = False
        reader = FakeReader()
        error = await self.refused(self.service(reader).metadata(ALL), "authority_unavailable")
        self.assertEqual(reader.calls, 0)
        self.assertEqual(error.code, ErrorCode.PROVIDER_UNAVAILABLE)
        self.assertEqual(error.details, {"reason": "authority_unavailable", "fallback_occurred": False,
                                         "core_capability": PROFILE})
        self.assertEqual(self.core.calls, [(PROFILE,)])
        summary = self.telemetry.audit_context["security_metadata_summary"]
        self.assertEqual((summary["result"], summary["reason_code"], summary["command_count"]),
                         ("failed", "authority_unavailable", 0))

    async def test_unadmitted_profile_refuses_even_with_dispatch_authority(self):
        self.core.available = False
        self.telemetry.core_dispatch_authorizer = lambda: True
        reader = FakeReader()
        await self.refused(self.service(reader).metadata(ALL), "authority_unavailable")
        self.assertEqual(reader.calls, 0)

    async def test_missing_dispatch_authority_or_observation_refuses(self):
        reader = FakeReader()
        self.telemetry.core_dispatch_authorizer = None
        await self.refused(self.service(reader).metadata(ALL), "authority_unavailable")
        self.telemetry.core_dispatch_authorizer = lambda: True
        self.core.current_observation = None
        await self.refused(self.service(reader).metadata(ALL), "authority_unavailable")
        self.assertEqual(reader.calls, 0)

    async def test_anonymous_caller_and_non_request_refuse(self):
        reader = FakeReader()
        await self.refused(self.service(reader).metadata({"sections": ["users"]}), "invalid_arguments")
        self.telemetry.caller_id = "anonymous"
        await self.refused(self.service(reader).metadata(ALL), "authority_unavailable")
        self.assertEqual(reader.calls, 0)

    async def test_authority_change_across_the_read_refuses(self):
        for change in (lambda: setattr(self.core, "generation", 8), lambda: setattr(self.core, "available", False)):
            with self.subTest(change=change):
                self.core = AdmissionCore()
                self.telemetry.core_dispatch_authorizer = lambda: self.core.available
                await self.refused(self.service(FakeReader(during=change)).metadata(ALL), "authority_unavailable")

    async def test_unexpected_failure_is_closed_and_unechoed(self):
        reader = FakeReader(error=RuntimeError("SynthErrorMessageText"))
        error = await self.refused(self.service(reader).metadata(ALL), "provider_error")
        self.assertNotIn("SynthErrorMessageText", dumps(error.details))
        self.assertNotIn("SynthErrorMessageText", dumps(self.telemetry.audit_context))

    async def test_unconfigured_runtime_fails_closed(self):
        with self.assertRaises(sm.SecurityMetadataError) as raised:
            sm.SecurityMetadataRuntime().require()
        self.assertEqual(raised.exception.reason, "authority_unavailable")

    async def test_runtime_reads_topology_and_secrets_from_settings(self):
        runtime = sm.SecurityMetadataRuntime()
        settings = SimpleNamespace(access_secret=ACCESS_SECRET, ha_token=HA_TOKEN,
                                   ha_url_source="default", ha_token_source="supervisor_token")
        runtime.configure(FakeReader(), self.core, settings)
        self.assertEqual(runtime.require().topology, SUPERVISOR)
        self.assertEqual(runtime.require().known_secrets, (ACCESS_SECRET, HA_TOKEN))
        runtime.configure(FakeReader(), self.core, SimpleNamespace(access_secret="", ha_token=HA_TOKEN))
        self.assertEqual(runtime.require().topology, ("unknown", "unknown"))


class ServiceReadTests(ServiceTestCase):
    async def test_admitted_read_returns_useful_attributed_report(self):
        reader = FakeReader()
        value = await self.service(reader).metadata(ALL)
        self.assertEqual(reader.plans, [sm.read_plan(ALL)])
        self.assertEqual(value["completeness"], "complete")
        self.assertEqual(value["commands_sent"], sm.MAX_COMMANDS)
        self.assertEqual(value["core_version"], CORE_VERSION)
        self.assertEqual(value["connection_identity"]["kind"], "supervisor_system_user")
        self.assertEqual((self.telemetry.completeness, self.telemetry.result_status), ("complete", "success"))

    async def test_exact_read_set_for_mfa_only(self):
        reader = FakeReader()
        value = await self.service(reader).metadata(sm.SecurityMetadataRequest(("mfa",)))
        self.assertEqual([p["type"] for p in reader.plans[0]], [sm.CURRENT_USER])
        self.assertEqual(value["commands_sent"], 1)

    async def test_audit_summary_carries_no_identity_or_values(self):
        reader = FakeReader()
        await self.service(reader).metadata(ALL)
        summary = self.telemetry.audit_context["security_metadata_summary"]
        self.assertEqual(set(summary), {"requested_sections", "commands_sent", "command_count", "result",
                                        "section_statuses", "section_reason_codes", "item_counts"})
        self.assertEqual(summary["commands_sent"], [p["type"] for p in sm.read_plan(ALL)])
        self.assertEqual(summary["item_counts"], {"users": 3, "mfa_modules": 1, "pipelines": 1, "agents": 1,
                                                  "panels": 3})
        text = dumps(summary)
        for raw in SECRET_TEXT + ("user-1", "supervisor_system_user", "totp", "synth_addon", "01synthpipe"):
            self.assertNotIn(raw, text)

    async def test_partial_section_renders_partial(self):
        reader = FakeReader(overrides={sm.HTTP_CONFIG: failure("not_admin")})
        value = await self.service(reader).metadata(ALL)
        self.assertEqual(value["completeness"], "partial")
        self.assertEqual(value["coverage"]["http_boundary"]["reason_code"], "not_admin")
        self.assertEqual((self.telemetry.completeness, self.telemetry.result_status), ("partial", "partial"))


class PublicToolTests(ServiceTestCase):
    async def test_invalid_arguments_are_never_reflected(self):
        marker = "Refused_" + "q" * 8
        for arguments in ({"sections": [marker]}, {"sections": ["users"], "extra": marker},
                          {"sections": marker}, {"sections": []}):
            with self.subTest(arguments=arguments):
                rendered = await public.registered_tool().run(arguments)
                self.assertNotIn(marker, rendered)
                self.assertIn("invalid_arguments", rendered)

    async def test_admitted_tool_renders_attribution(self):
        with mock.patch.object(sm.SECURITY_METADATA, "service", self.service(FakeReader())):
            rendered = json.loads(await public.registered_tool().run({"sections": ["panels", "mfa"]}))
        self.assertTrue(rendered["success"], rendered)
        self.assertEqual(rendered["metadata"]["provider"], "engineering")
        self.assertEqual(rendered["metadata"]["data_providers"], ["direct_ha_api"])
        self.assertIs(rendered["metadata"]["fallback_occurred"], False)
        self.assertEqual(rendered["metadata"]["completeness"], "complete")
        self.assertEqual(rendered["data"]["requested_sections"], ["mfa", "panels"])
        self.assertEqual(rendered["warnings"], [])

    async def test_partial_renders_warning(self):
        reader = FakeReader(overrides={sm.AUTH_LIST: failure("not_admin")})
        with mock.patch.object(sm.SECURITY_METADATA, "service", self.service(reader)):
            rendered = json.loads(await public.registered_tool().run({}))
        self.assertEqual(rendered["metadata"]["completeness"], "partial")
        self.assertIn(public._PARTIAL, rendered["warnings"])

    async def test_unadmitted_tool_renders_closed_refusal(self):
        self.core.available = False
        reader = FakeReader()
        with mock.patch.object(sm.SECURITY_METADATA, "service", self.service(reader)):
            rendered = json.loads(await public.registered_tool().run({}))
        self.assertFalse(rendered["success"])
        self.assertIn("authority_unavailable", json.dumps(rendered))
        self.assertEqual(reader.calls, 0)


class LoopbackPeer:
    """Disposable loopback Home Assistant WebSocket peer with a command ledger."""

    def __init__(self):
        self.ledger: list[dict] = []
        self.connections_ledger: list[list[dict]] = []
        self.connections = 0
        self.auth_type = "auth_ok"
        self.auth_version = CORE_VERSION
        self.replies: dict[str, str] = {}
        self.hold = False
        self.extra_frame: str | None = None
        self.greeting_pad = 0

    async def handler(self, request):
        self.connections += 1
        connection: list[dict] = []
        self.connections_ledger.append(connection)
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
                connection.append(command)
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


class LoopbackTestCase(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.peer = LoopbackPeer()
        for command, value in results().items():
            self.peer.replies[command] = result_frame(0, value)
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
            ha_url_source="default", ha_token_source="supervisor_token",
        )
        self.reader = sm.SecurityMetadataReader(self.settings)
        self.telemetry, self.token = begin_request()
        self.telemetry.caller_id = "synthetic_authenticated_caller"
        self.telemetry.core_dispatch_authorizer = lambda: True

    async def asyncTearDown(self):
        end_request(self.token)
        await self.runner.cleanup()
        # Negative reachability on every path: no write, no never-sent command,
        # no Supervisor or hassio type, no caller argument, ever.
        sent = [command.get("type") for command in self.peer.ledger]
        self.assertTrue(set(sent) <= sm.COMMANDS, sent)
        self.assertFalse(set(sent) & set(sm.NEVER_SENT_COMMANDS))
        self.assertFalse([t for t in sent if t.startswith(sm.NEVER_SENT_PREFIXES)])
        for connection in self.peer.connections_ledger:
            self.assertLessEqual(len(connection), sm.MAX_COMMANDS)
        constants = [dict(p) for p in sm.PAYLOADS]
        for command in self.peer.ledger:
            body = {k: v for k, v in command.items() if k != "id"}
            self.assertIn(body, constants)
        # Within one connection each constant is sent at most once.
        for connection in self.peer.connections_ledger:
            bodies = [json.dumps({k: v for k, v in c.items() if k != "id"}, sort_keys=True) for c in connection]
            self.assertEqual(len(set(bodies)), len(bodies))

    def reply(self, command: str, frame: str) -> None:
        self.peer.replies[command] = frame

    async def read(self, request=ALL, authorize=lambda: None):
        return await self.reader.read(core_version=CORE_VERSION, plan=sm.read_plan(request), authorize=authorize)

    async def failed(self, reason: str, *, request=ALL, authorize=lambda: None):
        with self.assertRaises(sm.SecurityMetadataError) as raised:
            await self.read(request, authorize)
        self.assertEqual(raised.exception.reason, reason)
        self.assertNotIn(HA_TOKEN, dumps(raised.exception.details))
        return raised.exception


class ReaderLoopbackTests(LoopbackTestCase):
    async def test_all_sections_send_exactly_the_constant_plan_in_order(self):
        replies = await self.read()
        self.assertEqual([{k: v for k, v in c.items() if k != "id"} for c in self.peer.ledger],
                         [dict(p) for p in sm.read_plan(ALL)])
        self.assertEqual([c["id"] for c in self.peer.ledger], list(range(1, sm.MAX_COMMANDS + 1)))
        self.assertTrue(all(reply.ok for reply in replies))
        self.assertEqual(self.peer.connections, 1)

    async def test_mfa_only_sends_only_current_user(self):
        await self.read(sm.SecurityMetadataRequest(("mfa",)))
        self.assertEqual([c["type"] for c in self.peer.ledger], [sm.CURRENT_USER])

    async def test_core_error_text_is_never_returned(self):
        self.reply(sm.AUTH_LIST, error_frame(0, "unauthorized", "SynthErrorMessageText u-raw-owner-0001"))
        replies = await self.read(sm.SecurityMetadataRequest(("users",)))
        self.assertEqual(replies[1].reason, "not_admin")
        self.assertNotIn("SynthErrorMessageText", repr(replies))

    async def test_non_constant_or_forbidden_plans_open_no_connection(self):
        forbidden = (
            (dict(sm.CURRENT_USER_PAYLOAD),),
            (sm.CURRENT_USER_PAYLOAD, {"type": "auth/refresh_tokens"}),
            (sm.CURRENT_USER_PAYLOAD, {"type": "supervisor/api", "endpoint": "/addons"}),
            (sm.AUTH_LIST_PAYLOAD,),
            (sm.CURRENT_USER_PAYLOAD, sm.CURRENT_USER_PAYLOAD),
            (sm.CURRENT_USER_PAYLOAD, *sm.PAYLOADS[1:], sm.GET_PANELS_PAYLOAD),
            (),
            [sm.CURRENT_USER_PAYLOAD],
        )
        for plan in forbidden:
            with self.subTest(plan=str(plan)[:80]):
                with self.assertRaises(sm.SecurityMetadataError) as raised:
                    await self.reader.read(core_version=CORE_VERSION, plan=plan, authorize=lambda: None)
                self.assertEqual(raised.exception.reason, "provider_error")
        self.assertEqual(self.peer.connections, 0)

    async def test_oversized_frame_fails_closed(self):
        self.reply(sm.GET_PANELS, result_frame(0, {"x": "y" * (sm.FRAME_BYTES + 10)}))
        await self.failed("response_too_large", request=sm.SecurityMetadataRequest(("panels",)))

    async def test_auth_frames_have_their_own_smaller_bound(self):
        self.peer.greeting_pad = sm.AUTH_BYTES
        await self.failed("response_too_large")
        self.assertEqual(self.peer.ledger, [])

    async def test_unsolicited_frames_and_non_json_fail_closed(self):
        self.peer.extra_frame = json.dumps({"id": 99, "type": "event", "event": {}})
        await self.failed("malformed_response")
        self.peer.extra_frame = "not json"
        await self.failed("malformed_response")

    async def test_auth_failure_and_identity_drift_send_no_command(self):
        self.peer.auth_type = "auth_invalid"
        await self.failed("unauthorized")
        self.peer.auth_type = "auth_ok"
        self.peer.auth_version = "2026.10.3"
        await self.failed("identity_mismatch")
        self.assertEqual(self.peer.ledger, [])

    async def test_authority_loss_before_dispatch_sends_no_command(self):
        def authorize():
            raise sm.SecurityMetadataError("authority_unavailable")
        await self.failed("authority_unavailable", authorize=authorize)
        self.assertEqual(self.peer.connections, 0)

    async def test_authority_loss_mid_plan_stops_the_plan(self):
        calls = {"n": 0}

        def authorize():
            calls["n"] += 1
            if calls["n"] > 3:
                raise sm.SecurityMetadataError("authority_unavailable")
        await self.failed("authority_unavailable", authorize=authorize)
        self.assertEqual([c["type"] for c in self.peer.ledger], [sm.CURRENT_USER, sm.AUTH_LIST])

    async def test_deadline_is_bounded(self):
        self.peer.hold = True
        self.settings.ha_timeout_seconds = 0.3
        await self.failed("timeout", request=sm.SecurityMetadataRequest(("mfa",)))

    async def test_unreachable_peer_is_transport_unavailable(self):
        self.settings.websocket_url = "ws://127.0.0.1:9/api/websocket"
        await self.failed("transport_unavailable")

    async def test_transport_exception_text_is_never_echoed_or_logged(self):
        import logging

        raw = "synth-internal-host.example:8123 /config/synth-secret-dir/token.txt"
        records: list[logging.LogRecord] = []

        class Capture(logging.Handler):
            def emit(self, record):
                records.append(record)

        handler = Capture(level=logging.DEBUG)
        root = logging.getLogger()
        previous = root.level
        root.addHandler(handler)
        root.setLevel(logging.DEBUG)
        try:
            for error in (OSError(raw), ConnectionResetError(raw)):
                with self.subTest(error=type(error).__name__), mock.patch(
                        "aiohttp.ClientSession.ws_connect", side_effect=error):
                    failed = await self.failed("transport_unavailable")
                    self.assertTrue(failed.__suppress_context__)
                    self.assertIsNone(failed.__cause__)
                    self.assertNotIn("synth-internal-host", repr(failed) + dumps(failed.details))
        finally:
            root.removeHandler(handler)
            root.setLevel(previous)
        for record in records:
            text = record.getMessage() + repr(record.args) + repr(record.exc_info)
            self.assertNotIn("synth-internal-host", text)
            self.assertNotIn("synth-secret-dir", text)
        self.assertEqual(self.peer.ledger, [])


class EndToEndLoopbackTests(LoopbackTestCase):
    def service(self, core):
        runtime = sm.SecurityMetadataRuntime()
        runtime.configure(self.reader, core, self.settings)
        return runtime.require()

    async def test_admitted_service_reads_core(self):
        value = await self.service(AdmissionCore()).metadata(ALL)
        self.assertEqual(value["completeness"], "complete")
        self.assertEqual(value["connection_identity"]["kind"], "supervisor_system_user")
        self.assertEqual(len(self.peer.ledger), sm.MAX_COMMANDS)
        text = dumps(value)
        for raw in SECRET_TEXT + (HA_TOKEN, ACCESS_SECRET):
            self.assertNotIn(raw, text)

    async def test_unadmitted_service_opens_no_connection(self):
        with self.assertRaises(sm.SecurityMetadataError):
            await self.service(AdmissionCore(available=False)).metadata(ALL)
        self.assertEqual(self.peer.connections, 0)

    async def test_october_core_is_refused_before_any_command(self):
        # A 2026.10.x observation can never match an auth_ok of another
        # version; with the profile unselected it never connects at all.
        for version in ("2026.10.0", "2026.10.1", "2026.10.3"):
            with self.subTest(version=version):
                self.peer.auth_version = version
                core = AdmissionCore(available=False, version=version)
                with self.assertRaises(sm.SecurityMetadataError) as raised:
                    await self.service(core).metadata(ALL)
                self.assertEqual(raised.exception.reason, "authority_unavailable")
        self.assertEqual(self.peer.connections, 0)

    async def test_public_tool_over_the_real_reader(self):
        with mock.patch.object(sm.SECURITY_METADATA, "service", self.service(AdmissionCore())):
            rendered = json.loads(await public.registered_tool().run({"sections": ["urls"]}))
        self.assertTrue(rendered["success"], rendered)
        self.assertEqual([c["type"] for c in self.peer.ledger], [sm.CURRENT_USER, sm.GET_CONFIG])
        self.assertNotIn("synthhome", json.dumps(rendered))


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

    def references(self) -> list[dict]:
        keys = ("capability_id", "profile_id", "profile_version", "adapter_id", "contract_fingerprint")
        return [{key: profile.to_mapping()[key] for key in keys} for profile in CORE_SECURITY_METADATA_PROFILES]

    async def service_read(self):
        telemetry, token = begin_request()
        self.addCleanup(end_request, token)
        telemetry.caller_id = "synthetic_authenticated_caller"
        telemetry.core_dispatch_authorizer = lambda: True
        reader = FakeReader()
        service = sm.SecurityMetadataService(reader, self.core, topology=SUPERVISOR,
                                             known_secrets=(ACCESS_SECRET, HA_TOKEN))
        return reader, service

    async def test_shipped_reference_set_withholds_with_zero_reads(self):
        self.assertNotIn(PROFILE, {c["capability_id"] for c in self.entry["capabilities"]})
        await self.publish()
        self.assertIsNone(self.core.acquire((PROFILE,)))
        reader, service = await self.service_read()
        with self.assertRaises(sm.SecurityMetadataError) as raised:
            await service.metadata(ALL)
        self.assertEqual(raised.exception.reason, "authority_unavailable")
        self.assertEqual(reader.calls, 0)

    async def test_signed_reference_admits_and_the_read_succeeds(self):
        entry = deepcopy(self.entry)
        entry["capabilities"] = [*self.entry["capabilities"], *self.references()]
        await self.publish(entry)
        self.assertIsNotNone(self.core.acquire((PROFILE,)))
        reader, service = await self.service_read()
        version = self.core.current_observation.version
        value = await service.metadata(sm.SecurityMetadataRequest(("mfa", "panels")))
        self.assertEqual(reader.calls, 1)
        self.assertEqual(value["core_version"], version)
        self.assertEqual([p["type"] for p in reader.plans[0]], [sm.CURRENT_USER, sm.GET_PANELS])
        self.assertEqual(value["completeness"], "complete")

    async def test_a_tampered_reference_does_not_admit(self):
        entry = deepcopy(self.entry)
        (reference,) = self.references()
        reference["contract_fingerprint"] = "sha256:" + "0" * 64
        entry["capabilities"] = [*self.entry["capabilities"], reference]
        try:
            await self.publish(entry)
        except AssertionError:
            pass
        self.assertIsNone(self.core.acquire((PROFILE,)))


if __name__ == "__main__":
    unittest.main()
