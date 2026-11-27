"""The reviewed HAMCP-425 (PC-1) and HAMCP-492 public descriptor changes.

HAMCP-425 adds an optional ``cursor`` input (default empty, meaning the first
page; at most 2048 characters) to ``search_entities``, ``list_automations``,
``list_devices``, ``list_entity_registry`` and ``list_execution_tasks``, and
appends four lines to each description naming the order and the walk. Their
other inputs are unchanged; the helper asserts that.
HAMCP-492 adds two lines to ``configuration_integrity_analysis``'s
description, after the HAMCP-473 lines, naming the compact page budget and the
limit-1 read; its inputs are unchanged.
Historical descriptor pins remove exactly these changes, asserting each added
part is present once and has the reviewed shape, so every other byte of the
old descriptor stays pinned. Undo this before HAMCP-508, whose lines these
follow.
"""

import json

HAMCP425_CURSOR_PROPERTY = {"default": "", "maxLength": 2048, "title": "Cursor", "type": "string"}


def _walk_lines(order, binding):
    return (
        "\n"
        f"    Pages in {order} order: pass next_cursor back as cursor, with the same\n"
        f"    {binding}, while has_more is true. Each page reflects its own read; a changed\n"
        "    match set or a server restart refuses the cursor, so start again without\n"
        "    one. A row too large for any page is named in omitted_ids."
    )


HAMCP425_APPENDED_LINES = {
    "search_entities": (
        "\n"
        "    Pages in entity_id order: pass next_cursor back as cursor, with the same\n"
        "    query and domain, while has_more is true. Each page reflects its own read;\n"
        "    a changed match set or a server restart refuses the cursor, so start again\n"
        "    without one. A row too large for any page is named in omitted_ids."
    ),
    "list_automations": _walk_lines("entity_id", "query"),
    "list_devices": _walk_lines("device id", "query"),
    "list_entity_registry": _walk_lines("entity_id", "query"),
    "list_execution_tasks": (
        "\n"
        "    Pages newest first: pass next_cursor back as cursor, with the same\n"
        "    filters, while has_more is true. Each page reflects its own read; a\n"
        "    changed match set or a server restart refuses the cursor, so start again\n"
        "    without one. A row too large for any page is named in omitted_ids."
    ),
}
HAMCP425_UNCHANGED_INPUTS = {
    "search_entities": {"domain", "limit", "query"},
    "list_automations": {"limit", "query"},
    "list_devices": {"limit", "query"},
    "list_entity_registry": {"limit", "query"},
    "list_execution_tasks": {"limit", "plan_id", "state", "terminal_outcome"},
}
HAMCP425_TOOLS = tuple(HAMCP425_APPENDED_LINES)

HAMCP492_TOOL = "configuration_integrity_analysis"
HAMCP492_DESCRIPTION_LINES = (
    "    A compact page holds at most 24000 characters. For the whole-analysis\n"
    "    counts and coverage with one finding, use detail_level compact with limit 1.\n"
)


def without_hamcp425_inputs(case, name, schema):
    """Tool ``name``'s parameters schema without the HAMCP-425 ``cursor`` input."""

    schema = json.loads(json.dumps(schema))
    case.assertEqual(schema["properties"].pop("cursor"), HAMCP425_CURSOR_PROPERTY)
    case.assertNotIn("cursor", schema.get("required", []))
    case.assertEqual(set(schema["properties"]), HAMCP425_UNCHANGED_INPUTS[name])
    return schema


def without_hamcp425_schema_changes(case, schemas):
    """``schemas`` (name -> parameters) with the HAMCP-425 input undone where present."""

    schemas = dict(schemas)
    for name in HAMCP425_TOOLS:
        if name in schemas:
            schemas[name] = without_hamcp425_inputs(case, name, schemas[name])
    return schemas


def without_hamcp425_descriptor_change(case, name, descriptor):
    """Tool ``name``'s descriptor with the reviewed HAMCP-425 change undone."""

    descriptor = json.loads(json.dumps(descriptor))
    descriptor["inputSchema"] = without_hamcp425_inputs(case, name, descriptor["inputSchema"])
    lines = HAMCP425_APPENDED_LINES[name]
    description = descriptor["description"]
    case.assertEqual(description.count(lines), 1)
    case.assertTrue(description.endswith(lines))
    descriptor["description"] = description[: -len(lines)]
    return descriptor


def without_hamcp492_descriptor_change(case, descriptor):
    """The integrity descriptor with the reviewed HAMCP-492 lines undone."""

    descriptor = json.loads(json.dumps(descriptor))
    case.assertEqual(descriptor["description"].count(HAMCP492_DESCRIPTION_LINES), 1)
    descriptor["description"] = descriptor["description"].replace(HAMCP492_DESCRIPTION_LINES, "")
    return descriptor


def without_hamcp425_descriptor_changes(case, descriptors):
    """``descriptors`` (name -> descriptor) with the HAMCP-425 and HAMCP-492 changes undone where present."""

    descriptors = dict(descriptors)
    for name in HAMCP425_TOOLS:
        if name in descriptors:
            descriptors[name] = without_hamcp425_descriptor_change(case, name, descriptors[name])
    if HAMCP492_TOOL in descriptors:
        descriptors[HAMCP492_TOOL] = without_hamcp492_descriptor_change(case, descriptors[HAMCP492_TOOL])
    return descriptors
