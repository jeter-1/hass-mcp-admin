"""The reviewed HAMCP-518 public descriptor change.

HAMCP-518 adds ``get_audit_log``'s optional ``summary`` input (default
``false``, meaning a page) and appends one line to the description naming it.
``lines``, ``event`` and ``cursor`` are unchanged; the helper asserts that.
Historical descriptor pins remove this change before the HAMCP-475 one,
asserting each added part is present once and has the reviewed shape, so every
other byte of the old descriptor stays pinned.
"""

import json

try:  # imported as ``tests.hamcp518_...`` or, with tests/ on the path, bare
    from tests.hamcp475_descriptor_changes import (
        HAMCP475_CURSOR_PROPERTY,
        HAMCP475_DESCRIPTION_LINES,
        HAMCP475_UNCHANGED_PROPERTIES,
        without_hamcp475_descriptor_changes,
        without_hamcp475_inputs,
    )
except ImportError:
    from hamcp475_descriptor_changes import (
        HAMCP475_CURSOR_PROPERTY,
        HAMCP475_DESCRIPTION_LINES,
        HAMCP475_UNCHANGED_PROPERTIES,
        without_hamcp475_descriptor_changes,
        without_hamcp475_inputs,
    )

HAMCP518_TOOL = "get_audit_log"
HAMCP518_SUMMARY_PROPERTY = {"default": False, "title": "Summary", "type": "boolean"}
HAMCP518_UNCHANGED_PROPERTIES = {**HAMCP475_UNCHANGED_PROPERTIES, "cursor": HAMCP475_CURSOR_PROPERTY}
HAMCP518_DESCRIPTION_LINE = (
    "\n"
    "    summary=true returns per-event and per-tool counts for the whole log instead."
)


def without_hamcp518_inputs(case, schema):
    """``schema`` (``get_audit_log`` parameters) without the HAMCP-518 input."""

    schema = json.loads(json.dumps(schema))
    case.assertEqual(schema["properties"].pop("summary"), HAMCP518_SUMMARY_PROPERTY)
    case.assertNotIn("summary", schema.get("required", []))
    for name, expected in HAMCP518_UNCHANGED_PROPERTIES.items():
        case.assertEqual(schema["properties"][name], expected)
    return schema


def without_hamcp518_descriptor_change(case, descriptor):
    """The ``get_audit_log`` descriptor with the reviewed HAMCP-518 change undone."""

    descriptor = json.loads(json.dumps(descriptor))
    descriptor["inputSchema"] = without_hamcp518_inputs(case, descriptor["inputSchema"])
    description = descriptor["description"]
    case.assertEqual(description.count(HAMCP518_DESCRIPTION_LINE), 1)
    case.assertTrue(description.endswith(HAMCP475_DESCRIPTION_LINES + HAMCP518_DESCRIPTION_LINE))
    descriptor["description"] = description[: -len(HAMCP518_DESCRIPTION_LINE)]
    return descriptor


def without_hamcp518_descriptor_changes(case, descriptors):
    """``descriptors`` (name -> descriptor) with the HAMCP-518 change undone where present."""

    descriptors = dict(descriptors)
    if HAMCP518_TOOL in descriptors:
        descriptors[HAMCP518_TOOL] = without_hamcp518_descriptor_change(case, descriptors[HAMCP518_TOOL])
    return descriptors


def without_hamcp518_and_475_inputs(case, schema):
    """``get_audit_log`` parameters with both reviewed input additions undone."""

    return without_hamcp475_inputs(case, without_hamcp518_inputs(case, schema))


def without_hamcp518_and_475_descriptor_changes(case, descriptors):
    """``descriptors`` with the HAMCP-518 then the HAMCP-475 change undone."""

    return without_hamcp475_descriptor_changes(case, without_hamcp518_descriptor_changes(case, descriptors))
