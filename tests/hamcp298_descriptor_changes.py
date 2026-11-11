"""The reviewed HAMCP-298 public descriptor change.

HAMCP-298 replaces the sentence of ``rollback_change``'s description that said
creation rollback was unavailable (automation, script, helper, scene and mixed
plans); the input schema is unchanged. Historical
descriptor pins restore exactly that sentence, asserting the new text is present
once, so every other byte of the old descriptor stays pinned.
"""

import json

HAMCP298_TOOL = "rollback_change"
HAMCP298_OLD = (
    " Creation and\n"
    "    operational rollback remain unavailable because deletion and generalized\n"
    "    compensation are outside this milestone."
)
HAMCP298_NEW = (
    " A verified\n"
    "    governed automation or script create rolls back as a separately approved\n"
    "    configuration delete plan of the created object, using the identity\n"
    "    Engineering captured at creation; a registry entry modified since creation\n"
    "    needs elevated acknowledgement. A verified governed helper or scene create\n"
    "    rolls back the same way once a signed registry admits its delete. A plan\n"
    "    that created and updated objects rolls back as one plan in strict reverse\n"
    "    order. Operational rollback remains unavailable."
)


def without_hamcp298_descriptor_changes(case, descriptors):
    """``descriptors`` (name -> descriptor) with the HAMCP-298 change undone where present."""

    descriptors = dict(descriptors)
    if HAMCP298_TOOL in descriptors:
        descriptor = json.loads(json.dumps(descriptors[HAMCP298_TOOL]))
        case.assertEqual(descriptor["description"].count(HAMCP298_NEW), 1)
        descriptor["description"] = descriptor["description"].replace(HAMCP298_NEW, HAMCP298_OLD)
        descriptors[HAMCP298_TOOL] = descriptor
    return descriptors
