"""The reviewed HAMCP-508 public descriptor change.

HAMCP-508 (SI-1) appends two lines to the descriptions of ``list_automations``,
``list_devices`` and ``list_entity_registry`` naming the new ``total`` and
``truncated`` output keys. Their inputs (``query`` and ``limit``, default
``100``) are unchanged.
Historical descriptor pins remove exactly that change, asserting it is present
once at the end of each description, so every other byte stays pinned.
"""

import json

HAMCP508_TOOLS = ("list_automations", "list_devices", "list_entity_registry")
HAMCP508_DESCRIPTION_LINES = (
    "\n"
    "    Returns at most limit rows; total counts every match and truncated is\n"
    "    true when more matched than were returned."
)


def without_hamcp508_descriptor_change(case, descriptor):
    """One list descriptor with the reviewed HAMCP-508 change undone."""

    descriptor = json.loads(json.dumps(descriptor))
    description = descriptor["description"]
    case.assertEqual(description.count(HAMCP508_DESCRIPTION_LINES), 1)
    case.assertTrue(description.endswith(HAMCP508_DESCRIPTION_LINES))
    descriptor["description"] = description[: -len(HAMCP508_DESCRIPTION_LINES)]
    return descriptor


def without_hamcp508_descriptor_changes(case, descriptors):
    """``descriptors`` (name -> descriptor) with the HAMCP-508 change undone where present."""

    descriptors = dict(descriptors)
    for name in HAMCP508_TOOLS:
        if name in descriptors:
            descriptors[name] = without_hamcp508_descriptor_change(case, descriptors[name])
    return descriptors
