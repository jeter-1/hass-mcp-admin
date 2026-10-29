"""The reviewed HAMCP-247 public descriptor change.

HAMCP-247 adds ``list_capabilities``'s optional ``detail_section`` input
(additive; the default keeps every existing argument valid) and rewrites its
description for the bounded summary, the section selector and the size
statement. Historical descriptor pins remove exactly that addition, asserting
it is present once, and restore the historical description only after
checking the reviewed one, so every other byte of the old descriptor stays
pinned.
"""

import hashlib
import json

HAMCP247_DETAIL_SECTION_PROPERTY = {"default": "", "title": "Detail Section", "type": "string"}
# sha256 of the reviewed HAMCP-247 list_capabilities description.
HAMCP247_DESCRIPTION_SHA256 = "3c5b1ffda55be6424bbc0b9974f406b083786206678acd0eaf610c674dac178e"
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
    """``schema`` (``list_capabilities`` parameters) without the HAMCP-247 input."""

    schema = json.loads(json.dumps(schema))
    case.assertEqual(schema["properties"].pop("detail_section"), HAMCP247_DETAIL_SECTION_PROPERTY)
    case.assertNotIn("detail_section", schema.get("required", []))
    return schema


def without_hamcp247_descriptor_change(case, descriptor):
    """The ``list_capabilities`` descriptor with the reviewed HAMCP-247 changes undone."""

    descriptor = json.loads(json.dumps(descriptor))
    descriptor["inputSchema"] = without_hamcp247_detail_section(case, descriptor["inputSchema"])
    case.assertEqual(hashlib.sha256(descriptor["description"].encode()).hexdigest(),
                     HAMCP247_DESCRIPTION_SHA256)
    descriptor["description"] = HAMCP247_HISTORICAL_DESCRIPTION
    return descriptor
