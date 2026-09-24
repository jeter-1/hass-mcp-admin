"""Exact Supervisor security/HTTP-handler slices, with synthetic host boundaries.

This is offline integration of source fragments, not a booted Supervisor or HAOS.
The journal decoder, installed-app inventory, and process objects are stubs.
"""

from contextlib import asynccontextmanager
from dataclasses import dataclass
from functools import partial
import hashlib
import json
import logging
from pathlib import Path
import re
from types import SimpleNamespace
import unittest

from aiohttp import ClientPayloadError, web
from aiohttp.client_exceptions import ClientConnectionResetError
from aiohttp.test_utils import make_mocked_request
import yaml

from tests import test_core_log_history as reader_tests


FIXTURE = Path(__file__).parent / "fixtures/supervisor_core_logs_2026_09_1"


def exact_contract():
    ns = dict(
        re=re, dataclass=dataclass, web=web, partial=partial,
        middleware=lambda fn: fn, _LOGGER=logging.getLogger("synthetic-supervisor"),
        RE_SLUG=r"[a-z0-9_]+", REQUEST_FROM="synthetic_request_from",
        HTTPForbidden=web.HTTPForbidden, HTTPUnauthorized=web.HTTPUnauthorized,
        HEADER_TOKEN="X-Supervisor-Token", HEADER_TOKEN_OLD="X-Hassio-Key",
        AUTHORIZATION="Authorization", ACCEPT="Accept", RANGE="Range",
        CONTENT_TYPE_TEXT="text/plain", CONTENT_TYPE_X_LOG="text/x-log",
        PARAM_SYSLOG_IDENTIFIER="SYSLOG_IDENTIFIER", PARAM_BOOT_ID="_BOOT_ID",
        PARAM_FOLLOW="follow", BOOTID="bootid", IDENTIFIER="identifier",
        DEFAULT_LINES=100, SYSTEMD_JOURNAL_GATEWAYD_LINES_MAX=10000,
        LogFormatter=SimpleNamespace(PLAIN="plain", VERBOSE="verbose"),
        LogFormat=SimpleNamespace(JOURNAL="journal"), APIError=RuntimeError,
        ClientConnectionResetError=ClientConnectionResetError,
        ClientPayloadError=ClientPayloadError,
    )
    for role in ("DEFAULT", "HOMEASSISTANT", "BACKUP", "MANAGER", "ADMIN"):
        ns["ROLE_" + role] = role.lower()
    provenance = json.loads((FIXTURE / "provenance.json").read_text())
    for fragment in provenance["fragments"]:
        data = (FIXTURE / fragment["file"]).read_bytes()
        if hashlib.sha256(data).hexdigest() != fragment["sha256"]:
            raise AssertionError("Supervisor contract fragment changed")
        exec(compile("from __future__ import annotations\n" + data.decode(),
                     fragment["path"], "exec"), ns)
    ns["_get_app_security_patterns"] = lambda request: ns["_V1_PATTERNS"]
    return ns


class SupervisorCoreLogContractTests(unittest.IsolatedAsyncioTestCase):
    result = reader_tests.CoreLogHistoryTests.result

    async def asyncSetUp(self):
        await reader_tests.CoreLogHistoryTests.asyncSetUp(self)
        self.ns = exact_contract()
        self.app_identity = SimpleNamespace(
            slug="synthetic_engineering", access_hassio_api=True,
            hassio_role="homeassistant",
        )
        self.security = SimpleNamespace(
            sys_homeassistant=SimpleNamespace(supervisor_token="synthetic-core-only"),
            sys_plugins=SimpleNamespace(
                cli=SimpleNamespace(supervisor_token="synthetic-cli-only"),
                observer=SimpleNamespace(supervisor_token="synthetic-observer-only"),
            ),
            sys_apps=SimpleNamespace(from_token=lambda value: self.app_identity
                if value == "synthetic-supervisor-token" else None),
        )
        self.journal_calls = []
        self.lines = [("synthetic-cursor", "2026-08-01 retained Core error"),
                      (None, "  retained traceback line")]
        self.stream_fault = False

        @asynccontextmanager
        async def journal(**kwargs):
            self.journal_calls.append(kwargs)
            yield object()

        async def decode(response, formatter, no_colors):
            for cursor, line in self.lines:
                yield cursor, line
            if self.stream_fault:
                raise ClientPayloadError("synthetic journal ended unexpectedly")

        self.ns["journal_logs_reader"] = decode
        host = SimpleNamespace(sys_host=SimpleNamespace(logs=SimpleNamespace(
            journald_logs=journal)))
        self.log_handler = partial(self.ns["advanced_logs_handler"], host,
                                   identifier="homeassistant")

        async def secured(request):
            return await self.ns["token_validation"](
                self.security, request, self.log_handler)
        self.handler = secured

    async def asyncTearDown(self):
        await reader_tests.CoreLogHistoryTests.asyncTearDown(self)

    async def test_authorized_reader_crosses_actual_security_and_http_handler(self):
        result = await self.result(limit=20, offset=100)
        self.assertTrue(result["success"])
        self.assertEqual(result["data"]["log"],
            "2026-08-01 retained Core error\n  retained traceback line\n")
        self.assertEqual(self.journal_calls, [{
            "params": {"SYSLOG_IDENTIFIER": "homeassistant"},
            "range_header": "entries=:-119:20", "accept": "journal",
        }])
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(result["metadata"]["provider"], "supervisor_core_logs")

    async def test_permission_and_role_denials_do_not_reach_journal(self):
        for enabled, role in ((False, "homeassistant"), (True, "default")):
            with self.subTest(enabled=enabled, role=role):
                self.app_identity.access_hassio_api = enabled
                self.app_identity.hassio_role = role
                result = await self.result()
                self.assertEqual(result["error_code"], "authorization_failure")
                self.assertEqual(self.journal_calls, [])

    async def test_late_supervisor_stream_failure_cannot_be_claimed_complete(self):
        self.stream_fault = True
        result = await self.result()
        self.assertTrue(result["success"])
        self.assertTrue(result["data"]["download_complete"])
        self.assertEqual(result["data"]["window_completeness"], "unknown")
        self.assertEqual(result["data"]["completeness"], "partial")
        self.assertIsNone(result["data"]["has_more"])

    async def test_empty_supervisor_stream_does_not_establish_history_exhaustion(self):
        self.lines = []
        result = await self.result()
        self.assertTrue(result["success"])
        self.assertEqual(result["data"]["log"], "")
        self.assertIsNone(result["data"]["has_more"])

    async def test_source_route_registration_selects_only_homeassistant_journal(self):
        app = web.Application()
        owner = SimpleNamespace(_api_host=SimpleNamespace(advanced_logs=self.log_handler))
        self.ns["_register_advanced_logs"](owner, "/core", "homeassistant", app)
        matches = [r for r in app.router.routes()
                   if r.method == "GET" and r.resource.canonical == "/core/logs"]
        self.assertEqual(len(matches), 1)
        self.assertEqual(matches[0].handler.keywords,
                         {"identifier": "homeassistant", "default_verbose": False})

    async def test_permission_expansion_is_visible_and_not_misrepresented_as_read_only(self):
        root = Path(__file__).resolve().parents[1]
        manifest = yaml.safe_load((root / "hass_mcp_engineering_beta/config.yaml").read_text())
        self.assertIs(manifest["hassio_api"], True)
        self.assertEqual(manifest["hassio_role"], "homeassistant")
        observed = []
        async def harmless(request):
            observed.append(request.path)
            return web.Response()
        for path in ("/core/restart", "/core/stop", "/core/update"):
            request = make_mocked_request("POST", path,
                headers={"Authorization": "Bearer synthetic-supervisor-token"})
            await self.ns["token_validation"](self.security, request, harmless)
        self.assertEqual(len(observed), 3)
        for path in ("/host/reboot", "/addons/other/restart", "/backups/new/full",
                     "/core/api/hassio/core/logs"):
            with self.assertRaises(web.HTTPForbidden):
                await self.ns["token_validation"](self.security,
                    make_mocked_request("POST", path,
                        headers={"Authorization": "Bearer synthetic-supervisor-token"}),
                    harmless)
        self.assertEqual(len(observed), 3)


if __name__ == "__main__":
    unittest.main()
