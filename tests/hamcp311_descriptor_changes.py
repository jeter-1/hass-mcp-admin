"""The reviewed HAMCP-311 public descriptor change.

HAMCP-311 adds ``control_helper`` and appends one two-line note to the
``create_helper_state_plan`` and ``create_input_select_option_plan``
descriptions: value changes are superseded by ``control_helper`` and the
plan tools remain available and unchanged. Their input schemas and
annotations are untouched. Historical descriptor pins remove exactly that
note, asserting it is present once, so every other byte of the old
descriptor stays pinned.
"""

import json

HAMCP311_SUPERSEDED_TOOLS = ("create_helper_state_plan", "create_input_select_option_plan")
HAMCP311_SUPERSEDED_LINES = (
    "    Superseded for value changes by control_helper (no plan or approval);\n"
    "    this tool remains available and unchanged.\n"
)


def without_hamcp311_superseded_note(case, descriptor):
    """The descriptor with the reviewed HAMCP-311 note removed."""

    descriptor = json.loads(json.dumps(descriptor))
    case.assertEqual(descriptor["description"].count(HAMCP311_SUPERSEDED_LINES), 1)
    descriptor["description"] = descriptor["description"].replace(HAMCP311_SUPERSEDED_LINES, "")
    return descriptor


def without_hamcp311_descriptor_changes(case, descriptors):
    """``descriptors`` (name -> descriptor) with the HAMCP-311 note removed where present."""

    descriptors = dict(descriptors)
    for name in HAMCP311_SUPERSEDED_TOOLS:
        if name in descriptors:
            descriptors[name] = without_hamcp311_superseded_note(case, descriptors[name])
    return descriptors
