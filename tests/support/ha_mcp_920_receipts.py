"""Reviewed ha-mcp 9.2.0 upstream read results for offline tests (HAMCP-437).

ha-mcp 9.2.0 reads are rebuilt from a reviewed output-key contract
(``tests/fixtures/ha_mcp_9_2_0/output-key-contract.json``), so a generic
synthetic payload such as ``{"value": "ok"}`` is refused as an invalid
response. Tests that need a successful 9.2.0 read take its upstream result from
the committed HAMCP-371 receipt set instead. Every receipt is checked against
``SHA256SUMS.json`` before use, and callers get deep copies.
"""

from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
FIXTURES = ROOT / "tests/fixtures/ha_mcp_9_2_0"
DEFAULT_SET = "A/9.2.0"
# 9.2.0 is admitted only through the strict full-catalog endpoint form. The
# literal lives here because test_hamcp319_endpoint_selector keeps strict
# values out of the general test corpus (support is exempt by design).
STRICT_CANARY_URL = "http://upstream.invalid/synthetic-canary/mcp/catalog/full"


def _json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def _receipt(relative: str) -> dict:
    sums = _json(FIXTURES / "SHA256SUMS.json")
    full = ROOT / relative
    if hashlib.sha256(full.read_bytes()).hexdigest() != sums[relative]:
        raise AssertionError(f"receipt hash drift: {relative}")
    return _json(full)


def record(call_id: str, set_name: str = DEFAULT_SET, sub: str = "calls") -> dict:
    """One committed receipt record (``sub`` names the receipt directory)."""

    base = _json(FIXTURES / "output-key-contract.json")["receipt_sets"][set_name] + f"/{sub}"
    for call in _receipt(f"{base}/_index.json")["calls"]:
        if call["id"] == call_id:
            return deepcopy(_receipt(f"{base}/{call['file']}"))
    raise KeyError(call_id)


def receipt(call_id: str, set_name: str = DEFAULT_SET, sub: str = "calls") -> tuple[dict, dict]:
    """``(public arguments, upstream call result)`` of one committed receipt."""

    found = record(call_id, set_name, sub)
    return found["public_arguments"], found["exchange"]["response"]["result"]


def result(call_id: str, set_name: str = DEFAULT_SET) -> dict:
    """The upstream call result of one committed receipt."""

    return receipt(call_id, set_name)[1]


def with_content(call_result: dict, content: dict) -> dict:
    """A copy of ``call_result`` whose structured and text content is ``content``."""

    copy = deepcopy(call_result)
    copy["structuredContent"] = deepcopy(content)
    copy["content"] = [{"type": "text", "text": json.dumps(content)}]
    return copy


def yaml_catalog(version: str = "9.2.0") -> list:
    """The exact filesystem-on tools/list the 9.2.0 YAML reader binds.

    The path and digest come from ``upstream_yaml_reader._CAPTURES``; the
    stock 9.2.0 catalog (``ha_mcp_captures.capture``) omits the YAML tool.
    """

    from ha_mcp_engineering.providers import upstream_yaml_reader as y

    _commit, resource, digest = y._CAPTURES[version]
    raw = (ROOT / resource).read_bytes()
    if hashlib.sha256(raw).hexdigest() != digest:
        raise AssertionError(f"capture hash drift: {resource}")
    return json.loads(raw)["tools"]
