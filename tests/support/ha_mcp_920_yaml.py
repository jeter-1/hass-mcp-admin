"""The HAMCP-162 YAML admission harness, bound to ha-mcp 9.2.0 (HAMCP-437).

``test_hamcp162_yaml_admission.Harness`` builds gateways from the 8.x D1
captures. These helpers bind it to the exact 9.2.0 filesystem-on capture
instead, and give the synthetic peer's YAML replies each match's node text, as
the committed 9.2.0 receipts carry it (``include_content`` is true on 9.2.0,
and a reply without it leaves the tag scan unavailable, so partial).
"""

from __future__ import annotations

import json
from unittest.mock import patch

import yaml

import test_hamcp162_yaml_admission as yaml_admission
from support import ha_mcp_920_receipts as receipts
from support.ha_mcp_captures import capture

VERSION = "9.2.0"
_YAML_RESPONSE = yaml_admission.yaml_response


def stock_catalog():
    """The reviewed stock 9.2.0 tools/list: no YAML reader tool."""

    return capture(VERSION)["tools"]


def yaml_response(path="recorder"):
    response = _YAML_RESPONSE(path)
    body = json.loads(response["content"][0]["text"])
    for match in body["matches"]:
        match["content"] = yaml.safe_dump(match["parsed"], sort_keys=False)
    response["content"][0]["text"] = json.dumps(body)
    response["structuredContent"] = body
    return response


def patch_replies(case):
    """Give ``case``'s synthetic peers 9.2.0-shaped YAML replies until cleanup."""

    patcher = patch.object(yaml_admission, "yaml_response", yaml_response)
    patcher.start()
    case.addCleanup(patcher.stop)


async def gateway(case, version=VERSION, tools=None, **kwargs):
    """``yaml_admission.Harness.gateway`` on the 9.2.0 filesystem-on capture.

    ``case`` need not be a ``Harness``: it runs ``Harness.setUp`` first, as
    callers composing the harness into another test case do.
    """

    tools = receipts.yaml_catalog(version) if tools is None else tools
    return await yaml_admission.Harness.gateway(case, version, tools, **kwargs)


class Harness(yaml_admission.Harness):
    def setUp(self):
        super().setUp()
        patch_replies(self)

    async def gateway(self, version=VERSION, tools=None, **kwargs):
        return await gateway(self, version, tools, **kwargs)
