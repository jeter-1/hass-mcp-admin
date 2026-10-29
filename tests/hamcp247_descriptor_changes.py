"""The reviewed HAMCP-247 public descriptor change.

HAMCP-247 adds ``list_capabilities``'s optional ``detail_section`` and
``detail_cursor`` inputs (additive; the defaults keep every existing argument
valid) and rewrites its description for the bounded summary, the section
selector, section paging and the size statement. Historical descriptor pins remove exactly that addition, asserting
it is present once, and restore the historical description only after
checking the reviewed one, so every other byte of the old descriptor stays
pinned.
"""

import hashlib
import json

HAMCP247_DETAIL_SECTION_PROPERTY = {"default": "", "title": "Detail Section", "type": "string"}
HAMCP247_DETAIL_CURSOR_PROPERTY = {"default": "", "title": "Detail Cursor", "type": "string"}
# sha256 of the reviewed HAMCP-247 list_capabilities description.
HAMCP247_DESCRIPTION_SHA256 = "1a221e591b7b194d1869b6563f279891d5eeec05a8883d1798634c21e6c31e82"
# The list_capabilities description every release before HAMCP-247 served.
HAMCP247_HISTORICAL_DESCRIPTION = (
    "List this server's tool capabilities and lifecycle classification.\n\n"
    "    Optional exact filters:\n"
    "    - status: native, transitional, delegated, deprecated\n"
    "    - category: foundation, evidence, verification, observability, discovery,\n"
    "      configuration, or execution\n\n"
    "    Planned engineering capabilities are returned separately.\n"
    "    "
)


def without_hamcp247_detail_section(case, schema):
    """``schema`` (``list_capabilities`` parameters) without the HAMCP-247 inputs."""

    schema = json.loads(json.dumps(schema))
    for name, expected in (
        ("detail_section", HAMCP247_DETAIL_SECTION_PROPERTY),
        ("detail_cursor", HAMCP247_DETAIL_CURSOR_PROPERTY),
    ):
        case.assertEqual(schema["properties"].pop(name), expected)
        case.assertNotIn(name, schema.get("required", []))
    return schema


def without_hamcp247_descriptor_change(case, descriptor):
    """The ``list_capabilities`` descriptor with the reviewed HAMCP-247 changes undone."""

    descriptor = json.loads(json.dumps(descriptor))
    descriptor["inputSchema"] = without_hamcp247_detail_section(case, descriptor["inputSchema"])
    case.assertEqual(hashlib.sha256(descriptor["description"].encode()).hexdigest(),
                     HAMCP247_DESCRIPTION_SHA256)
    descriptor["description"] = HAMCP247_HISTORICAL_DESCRIPTION
    return descriptor
