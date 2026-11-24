"""The reviewed HAMCP-475 public descriptor change.

HAMCP-475 adds ``get_audit_log``'s optional ``cursor`` input (default empty,
meaning the newest page) and appends two lines to the description naming the
page trailer and the walk. ``lines`` (default ``50``) and ``event`` are
unchanged; the helper asserts that.
Historical descriptor pins remove exactly that change, asserting each added
part is present once and has the reviewed shape, so every other byte of the
old descriptor stays pinned.
"""

import json

HAMCP475_TOOL = "get_audit_log"
HAMCP475_CURSOR_PROPERTY = {"default": "", "title": "Cursor", "type": "string"}
HAMCP475_UNCHANGED_PROPERTIES = {
    "event": {"default": "", "title": "Event", "type": "string"},
    "lines": {"default": 50, "title": "Lines", "type": "integer"},
}
HAMCP475_DESCRIPTION_LINES = (
    "\n"
    "    Pages fit the response limit; the last line is always a _page trailer.\n"
    "    To read older entries, pass its next_cursor until complete is true."
)


def without_hamcp475_inputs(case, schema):
    """``schema`` (``get_audit_log`` parameters) without the HAMCP-475 input."""

    schema = json.loads(json.dumps(schema))
    case.assertEqual(schema["properties"].pop("cursor"), HAMCP475_CURSOR_PROPERTY)
    case.assertNotIn("cursor", schema.get("required", []))
    for name, expected in HAMCP475_UNCHANGED_PROPERTIES.items():
        case.assertEqual(schema["properties"][name], expected)
    return schema


def without_hamcp475_descriptor_change(case, descriptor):
    """The ``get_audit_log`` descriptor with the reviewed HAMCP-475 change undone."""

    descriptor = json.loads(json.dumps(descriptor))
    descriptor["inputSchema"] = without_hamcp475_inputs(case, descriptor["inputSchema"])
    description = descriptor["description"]
    case.assertEqual(description.count(HAMCP475_DESCRIPTION_LINES), 1)
    case.assertTrue(description.endswith(HAMCP475_DESCRIPTION_LINES))
    descriptor["description"] = description[: -len(HAMCP475_DESCRIPTION_LINES)]
    return descriptor


def without_hamcp475_descriptor_changes(case, descriptors):
    """``descriptors`` (name -> descriptor) with the HAMCP-475 change undone where present."""

    descriptors = dict(descriptors)
    if HAMCP475_TOOL in descriptors:
        descriptors[HAMCP475_TOOL] = without_hamcp475_descriptor_change(case, descriptors[HAMCP475_TOOL])
    return descriptors
