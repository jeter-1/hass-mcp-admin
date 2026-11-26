"""The reviewed HAMCP-508 public descriptor changes.

HAMCP-508 (SI-1) appends two lines to the descriptions of ``list_automations``,
``list_devices`` and ``list_entity_registry`` naming the new ``total`` and
``truncated`` output keys. Their inputs (``query`` and ``limit``, default
``100``) are unchanged. SI-5 appends three lines to ``list_automation_traces``
naming its object reply; its ``automation_id`` input is unchanged.
Historical descriptor pins remove exactly that change, asserting it is present
once at the end of each description, so every other byte stays pinned.
"""

import json

HAMCP508_DESCRIPTION_LINES = (
    "\n"
    "    Returns at most limit rows; total counts every match and truncated is\n"
    "    true when more matched than were returned."
)
HAMCP508_TRACE_DESCRIPTION_LINES = (
    "\n"
    "    Returns an object: traces, returned, source_truncated and retention. Core\n"
    "    keeps only each automation's newest traces (default 5), so older runs are\n"
    "    not listed."
)
HAMCP508_APPENDED_LINES = {
    "list_automations": HAMCP508_DESCRIPTION_LINES,
    "list_devices": HAMCP508_DESCRIPTION_LINES,
    "list_entity_registry": HAMCP508_DESCRIPTION_LINES,
    "list_automation_traces": HAMCP508_TRACE_DESCRIPTION_LINES,
}
HAMCP508_TOOLS = tuple(HAMCP508_APPENDED_LINES)


def without_hamcp508_descriptor_change(case, name, descriptor):
    """Tool ``name``'s descriptor with the reviewed HAMCP-508 change undone."""

    descriptor = json.loads(json.dumps(descriptor))
    lines = HAMCP508_APPENDED_LINES[name]
    description = descriptor["description"]
    case.assertEqual(description.count(lines), 1)
    case.assertTrue(description.endswith(lines))
    descriptor["description"] = description[: -len(lines)]
    return descriptor


def without_hamcp508_descriptor_changes(case, descriptors):
    """``descriptors`` (name -> descriptor) with the HAMCP-508 change undone where present."""

    descriptors = dict(descriptors)
    for name in HAMCP508_TOOLS:
        if name in descriptors:
            descriptors[name] = without_hamcp508_descriptor_change(case, name, descriptors[name])
    return descriptors
