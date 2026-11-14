"""The reviewed HAMCP-379 public descriptor change.

HAMCP-379 makes ``get_execution_task`` a pure read and replaces the sentence of
``control_fan``'s description that said it retrieves and reconciles the task;
the input schema and annotations are unchanged. Historical descriptor pins
restore exactly that sentence, asserting the new text is present once, so
every other byte of the old descriptor stays pinned.
"""

import json

HAMCP379_TOOL = "control_fan"
HAMCP379_OLD = "get_execution_task retrieves/reconciles its task."
HAMCP379_NEW = "get_execution_task reads its stored task; recovery runs in the background."


def without_hamcp379_descriptor_changes(case, descriptors):
    """``descriptors`` (name -> descriptor) with the HAMCP-379 change undone where present."""

    descriptors = dict(descriptors)
    if HAMCP379_TOOL in descriptors:
        descriptor = json.loads(json.dumps(descriptors[HAMCP379_TOOL]))
        case.assertEqual(descriptor["description"].count(HAMCP379_NEW), 1)
        descriptor["description"] = descriptor["description"].replace(HAMCP379_NEW, HAMCP379_OLD)
        descriptors[HAMCP379_TOOL] = descriptor
    return descriptors
