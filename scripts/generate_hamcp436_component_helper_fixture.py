"""HAMCP-436: the ha_config_set_helper descriptor the REAL ha-mcp 9.2.0 transform emits.

With the ``ha_mcp_tools`` component installed, ha-mcp 9.2.0's
``ComponentHelperSchemaTransform`` (``src/ha_mcp/transforms/component_helpers.py``
at 51e3aac788482a081807591cd5d58d07dc4e540b) rewrites the ``config``
description of ``ha_config_set_helper`` from Core's own helper schemas. This
generator runs that exact upstream code on the committed reviewed 9.2.0 stock
descriptor and a committed synthetic set of Core helper schemas, and writes
the rewritten descriptor with its provenance.

Run it with the Python of an environment where ha-mcp is installed from a
clean checkout of that exact commit (ha-mcp needs Python >= 3.13), from
outside that checkout::

    <ha-mcp-venv>/bin/python -I scripts/generate_hamcp436_component_helper_fixture.py \
        --ha-mcp-checkout <clean-51e3aac7-checkout> \
        --output tests/fixtures/hamcp436_component_helper_9_2_0.json

``--check`` regenerates in memory and fails when the committed file differs.
Only synthetic data is used; nothing contacts Home Assistant.
"""

from __future__ import annotations

import argparse
import asyncio
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import subprocess
import sys
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SOURCE_COMMIT = "51e3aac788482a081807591cd5d58d07dc4e540b"
TRANSFORM_PATH = "src/ha_mcp/transforms/component_helpers.py"
CAPTURE = "docs/evidence/upstream-read-compatibility/ha-mcp-9.2.0.json"
TOOL = "ha_config_set_helper"

# Synthetic Core helper create schemas, in Core's
# ``{helper_type: {"create": [...], "update": [...]}}`` shape. Every simple
# type is present, so the transform's completeness gate passes; one field has
# more choices than the transform lists inline, to exercise that branch.
CORE_HELPER_SCHEMAS: dict[str, Any] = {
    "counter": {"create": [
        {"name": "name", "type": "string", "required": True},
        {"name": "icon", "type": "icon"},
        {"name": "initial", "type": "positive_int", "default": 0},
        {"name": "step", "type": "positive_int", "default": 1},
        {"name": "minimum", "type": "integer"},
        {"name": "maximum", "type": "integer"},
        {"name": "restore", "type": "boolean", "default": True},
    ], "update": []},
    "input_boolean": {"create": [
        {"name": "name", "type": "string", "required": True},
        {"name": "icon", "type": "icon"},
        {"name": "initial", "type": "boolean"},
    ], "update": []},
    "input_button": {"create": [
        {"name": "name", "type": "string", "required": True},
        {"name": "icon", "type": "icon"},
    ], "update": []},
    "input_datetime": {"create": [
        {"name": "name", "type": "string", "required": True},
        {"name": "has_date", "type": "boolean", "default": False},
        {"name": "has_time", "type": "boolean", "default": False},
        {"name": "initial", "type": "string"},
    ], "update": []},
    "input_number": {"create": [
        {"name": "name", "type": "string", "required": True},
        {"name": "min", "type": "float", "required": True},
        {"name": "max", "type": "float", "required": True},
        {"name": "step", "type": "float", "default": 1},
        {"name": "mode", "options": ["box", "slider"], "default": "slider"},
        {"name": "unit_of_measurement", "type": "string"},
    ], "update": []},
    "input_select": {"create": [
        {"name": "name", "type": "string", "required": True},
        {"name": "options", "type": "string_list", "required": True},
        {"name": "initial", "type": "string"},
    ], "update": []},
    "input_text": {"create": [
        {"name": "name", "type": "string", "required": True},
        {"name": "min", "type": "positive_int", "default": 0},
        {"name": "max", "type": "positive_int", "default": 100},
        {"name": "mode", "options": [["text", "Text"], ["password", "Password"]], "default": "text"},
        {"name": "pattern", "type": "string"},
    ], "update": []},
    "person": {"create": [
        {"name": "name", "type": "string", "required": True},
        {"name": "user_id", "type": "string"},
        {"name": "device_trackers", "type": "entity_id_list"},
    ], "update": []},
    "schedule": {"create": [
        {"name": "name", "type": "string", "required": True},
        {"name": "monday", "type": "time_ranges"},
        {"name": "tuesday", "type": "time_ranges"},
    ], "update": []},
    "tag": {"create": [
        {"name": "name", "type": "string"},
        {"name": "tag_id", "type": "string"},
    ], "update": []},
    "timer": {"create": [
        {"name": "name", "type": "string", "required": True},
        {"name": "duration", "type": "time_period", "default": "0:00:00"},
        {"name": "restore", "type": "boolean", "default": False},
    ], "update": []},
    "zone": {"create": [
        {"name": "name", "type": "string", "required": True},
        {"name": "latitude", "type": "latitude", "required": True},
        {"name": "longitude", "type": "longitude", "required": True},
        {"name": "radius", "type": "float", "default": 100},
        {"name": "passive", "type": "boolean", "default": False},
        {"name": "unit", "options": [f"unit_{index:02d}" for index in range(14)]},
    ], "update": []},
}


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _checkout_transform_sha256(checkout: Path) -> str:
    head = subprocess.run(
        ["git", "-C", str(checkout), "rev-parse", "HEAD"],
        check=True, capture_output=True, text=True,
    ).stdout.strip()
    if head != SOURCE_COMMIT:
        raise SystemExit(f"ha-mcp checkout is at {head}, not {SOURCE_COMMIT}")
    dirty = subprocess.run(
        ["git", "-C", str(checkout), "status", "--porcelain"],
        check=True, capture_output=True, text=True,
    ).stdout
    if dirty.strip():
        raise SystemExit("ha-mcp checkout is not clean")
    blob = subprocess.run(
        ["git", "-C", str(checkout), "show", f"{SOURCE_COMMIT}:{TRANSFORM_PATH}"],
        check=True, capture_output=True,
    ).stdout
    return _sha256(blob)


def generate(checkout: Path) -> dict[str, Any]:
    from ha_mcp._vendor.fastmcp.tools import Tool
    import ha_mcp.transforms.component_helpers as transform_module
    from ha_mcp.transforms.component_helpers import ComponentHelperSchemaTransform

    expected_transform = _checkout_transform_sha256(checkout)
    imported_transform = _sha256(Path(transform_module.__file__).read_bytes())
    if imported_transform != expected_transform:
        raise SystemExit("imported transform differs from the checkout's exact source")

    capture = json.loads((ROOT / CAPTURE).read_text(encoding="utf-8"))
    stock = next(tool for tool in capture["tools"] if tool["name"] == TOOL)

    class _Caps:
        capabilities = frozenset(("helper_schemas", "helper_item", "helper_write"))

    async def _caps(_client: Any) -> Any:
        return _Caps()

    async def _schemas(_client: Any) -> dict[str, Any]:
        return deepcopy(CORE_HELPER_SCHEMAS)

    # The component's two WS reads are replaced by synthetic answers; the
    # capability gate, completeness gate and rewrite are the upstream code.
    transform_module.get_component_caps = _caps
    transform_module.fetch_helper_schemas = _schemas
    tool = Tool(
        name=stock["name"],
        title=stock.get("title"),
        description=stock.get("description"),
        parameters=deepcopy(stock["inputSchema"]),
        output_schema=deepcopy(stock.get("outputSchema")),
        annotations=deepcopy(stock.get("annotations")),
    )
    (rewritten,) = asyncio.run(
        ComponentHelperSchemaTransform(object()).list_tools([tool])
    )
    if rewritten.parameters == stock["inputSchema"]:
        raise SystemExit("the transform did not rewrite the descriptor")
    # The listed inputSchema is the tool's parameters (Tool.to_mcp_tool), so
    # the rewritten descriptor is the stock one with those parameters.
    descriptor = deepcopy(stock)
    descriptor["inputSchema"] = deepcopy(rewritten.parameters)
    return {
        "schema_version": 1,
        "provenance": {
            "task": "HAMCP-436",
            "upstream_repository": "homeassistant-ai/ha-mcp",
            "upstream_source_commit": SOURCE_COMMIT,
            "upstream_transform_path": TRANSFORM_PATH,
            "upstream_transform_sha256": expected_transform,
            "upstream_transform_class": "ComponentHelperSchemaTransform",
            "stock_descriptor_source": CAPTURE,
            "stock_descriptor_sha256": _sha256(_canonical(stock).encode("utf-8")),
            "generator": "scripts/generate_hamcp436_component_helper_fixture.py",
            "generator_sha256": _sha256(Path(__file__).read_bytes()),
            "generator_python": ".".join(map(str, sys.version_info[:3])),
            "synthetic_inputs": (
                "Core helper schemas are synthetic (CORE_HELPER_SCHEMAS in the "
                "generator); the component capability probe and schema fetch "
                "are replaced by synthetic answers. No Home Assistant was "
                "contacted."
            ),
        },
        "core_helper_schemas": deepcopy(CORE_HELPER_SCHEMAS),
        "descriptor": descriptor,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--ha-mcp-checkout", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    text = json.dumps(generate(args.ha_mcp_checkout), indent=2, sort_keys=True) + "\n"
    output = args.output if args.output.is_absolute() else ROOT / args.output
    if args.check:
        if output.read_text(encoding="utf-8") != text:
            print("committed fixture differs from the regenerated one", file=sys.stderr)
            return 1
        print("committed fixture matches the regenerated one")
        return 0
    output.write_text(text, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
