"""The reviewed HAMCP-294 public descriptor change.

HAMCP-294 adds ``get_logbook``'s optional ``start_time``, ``end_time``,
``entity_ids`` and ``name`` inputs, changes the ``hours`` default from ``12``
to ``null`` (a call without the new inputs still reads the last 12 hours) and
appends one paragraph to the description. ``entity_id`` is unchanged.
Historical descriptor pins remove exactly that change, asserting each added
part is present once and has the reviewed shape, so every other byte of the
old descriptor stays pinned.
"""

import json

HAMCP294_TOOL = "get_logbook"
HAMCP294_HISTORICAL_HOURS = {"default": 12, "title": "Hours", "type": "number"}
HAMCP294_HOURS = {"anyOf": [{"type": "number"}, {"type": "null"}], "default": None, "title": "Hours"}


def _optional(title, kind):
    return {"anyOf": [kind, {"type": "null"}], "default": None, "title": title}


HAMCP294_ADDED_PROPERTIES = {
    "start_time": _optional("Start Time", {"type": "string"}),
    "end_time": _optional("End Time", {"type": "string"}),
    "entity_ids": _optional("Entity Ids", {"items": {"type": "string"}, "type": "array"}),
    "name": _optional("Name", {"type": "string"}),
}
HAMCP294_DESCRIPTION_PARAGRAPH = (
    "\n\n"
    "    Bounded window (HAMCP-294): start_time/end_time are ISO-8601 times with an\n"
    "    explicit offset; the window is at most 168 hours, start_time must be in the\n"
    "    past and must not be combined with hours (default 12 back from end_time); a\n"
    "    future end_time is clamped to now. Filter with at most one of entity_id,\n"
    "    entity_ids (1-10 IDs) or name (1-100 characters, case-insensitive substring\n"
    "    of a current entity_id or friendly_name resolving to 1-10 entities; 0 or\n"
    "    more than 10 matches is refused with no logbook read). Any of these returns\n"
    "    one object with effective_interval, filter, completeness_notes and, when\n"
    "    truncated, a continuation window that may overlap returned rows."
)


def without_hamcp294_inputs(case, schema):
    """``schema`` (``get_logbook`` parameters) without the HAMCP-294 inputs."""

    schema = json.loads(json.dumps(schema))
    for name, expected in HAMCP294_ADDED_PROPERTIES.items():
        case.assertEqual(schema["properties"].pop(name), expected)
        case.assertNotIn(name, schema.get("required", []))
    case.assertEqual(schema["properties"]["hours"], HAMCP294_HOURS)
    schema["properties"]["hours"] = dict(HAMCP294_HISTORICAL_HOURS)
    return schema


def without_hamcp294_descriptor_change(case, descriptor):
    """The ``get_logbook`` descriptor with the reviewed HAMCP-294 change undone."""

    descriptor = json.loads(json.dumps(descriptor))
    key = "inputSchema"
    descriptor[key] = without_hamcp294_inputs(case, descriptor[key])
    description = descriptor["description"]
    case.assertEqual(description.count(HAMCP294_DESCRIPTION_PARAGRAPH), 1)
    case.assertTrue(description.endswith(HAMCP294_DESCRIPTION_PARAGRAPH))
    descriptor["description"] = description[: -len(HAMCP294_DESCRIPTION_PARAGRAPH)]
    return descriptor


def without_hamcp294_descriptor_changes(case, descriptors):
    """``descriptors`` (name -> descriptor) with the HAMCP-294 change undone where present."""

    descriptors = dict(descriptors)
    if HAMCP294_TOOL in descriptors:
        descriptors[HAMCP294_TOOL] = without_hamcp294_descriptor_change(case, descriptors[HAMCP294_TOOL])
    return descriptors
