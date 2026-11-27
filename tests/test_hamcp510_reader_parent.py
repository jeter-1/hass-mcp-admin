"""HAMCP-510: the fixed gateway readers refuse without inherited authority.

The script, YAML (every purpose) and history readers start a child request
that inherits the parent's Core dispatch authorizer. With no parent request,
or a parent with no authorizer, there is nothing to inherit, and the
request-context default (no authorizer authorizes) must not stand in for it.
Each reader refuses before a request starts, as the search sub-call page
readers do: zero dispatch, zero audit, and the caller's context unchanged.
"""

from __future__ import annotations

import json
from pathlib import Path
import sys
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "hass_mcp_engineering_beta"), str(ROOT), str(ROOT / "tests")]

from support import ha_mcp_captures as captures  # noqa: E402
from support.parent_request import authorized_parent  # noqa: E402
import test_hamcp162_yaml_admission as yaml_admission  # noqa: E402
from ha_mcp_engineering.providers import upstream_read_gateway as gw  # noqa: E402
from ha_mcp_engineering.request_context import begin_request, current_telemetry, end_request  # noqa: E402

REFUSED = {"success": False, "details": {"failure_category": "provider_authority_changed"}}
TIME = "2026-01-01T00:00:00.000000+00:00"


class ReaderParentAuthorityTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.gateway, _ = await captures.gateway_920(tools=yaml_admission.captured("9.2.0"))
        self.sent = []

        async def execute(**kwargs):
            self.sent.append(kwargs)
            return json.dumps({"success": True})

        self.enterContext(patch.object(self.gateway, "execute", execute))

    def readers(self):
        """Each fixed reader, with one argument set it accepts."""

        audit = Mock()
        readers = {
            "script": (self.gateway.script_dependency_reader(audit=audit), ("script.synthetic",)),
            "history": (self.gateway.history_reader(audit=audit), ("sensor.synthetic", TIME, TIME, 0, 10)),
        }
        for purpose, (paths, _event) in gw.YAML_READER_PURPOSES.items():
            readers[f"yaml:{purpose}"] = (
                self.gateway.yaml_config_reader(audit=audit, purpose=purpose), (sorted(paths)[0],))
        for name, (reader, _arguments) in readers.items():
            self.assertIsNotNone(reader, name)
        return readers, audit

    async def test_no_parent_request_refuses_before_any_request(self):
        readers, audit = self.readers()
        self.assertIsNone(current_telemetry())
        for name, (reader, arguments) in readers.items():
            with self.subTest(reader=name):
                self.assertEqual(await reader.read(*arguments), REFUSED)
                self.assertIsNone(current_telemetry())
        self.assertEqual(self.sent, [])
        audit.write.assert_not_called()

    async def test_parent_without_an_authorizer_refuses_and_is_left_unchanged(self):
        readers, audit = self.readers()
        parent, token = begin_request()
        parent.caller_id = "synthetic_authenticated_caller"
        try:
            for name, (reader, arguments) in readers.items():
                with self.subTest(reader=name):
                    self.assertEqual(await reader.read(*arguments), REFUSED)
                    self.assertIs(current_telemetry(), parent)
                    self.assertIsNone(parent.core_dispatch_authorizer)
                    self.assertNotIn("parent_request_id", parent.audit_context)
        finally:
            end_request(token)
        self.assertEqual(self.sent, [])
        audit.write.assert_not_called()

    async def test_parent_with_an_authorizer_dispatches_once_per_read(self):
        # The control: the same readers and arguments do dispatch when the
        # parent carries authority, so the refusals above are not vacuous.
        readers, _audit = self.readers()
        with authorized_parent() as parent:
            for name, (reader, arguments) in readers.items():
                with self.subTest(reader=name):
                    self.sent.clear()
                    self.assertIs((await reader.read(*arguments))["success"], True)
                    self.assertEqual(len(self.sent), 1)
                    self.assertIs(current_telemetry(), parent)

    async def test_parent_whose_authorizer_refuses_dispatches_nothing(self):
        readers, _audit = self.readers()
        with authorized_parent(lambda: False) as parent:
            for name, (reader, arguments) in readers.items():
                with self.subTest(reader=name):
                    self.assertIs((await reader.read(*arguments))["success"], False)
                    self.assertIs(current_telemetry(), parent)
        self.assertEqual(self.sent, [])


if __name__ == "__main__":
    unittest.main()
