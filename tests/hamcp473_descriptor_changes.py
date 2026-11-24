"""The reviewed HAMCP-473 public descriptor change.

HAMCP-473 adds two optional inputs to ``configuration_integrity_analysis``
(both additive; existing calls behave as before):

- the ``compact`` value of the ``detail_level`` enum, a short record per
  finding so a page of up to 100 findings fits the response limit; and
- the ``severities`` filter (default empty, meaning every severity).

It also appends two lines to the description naming that walk. Historical
descriptor pins remove exactly these additions, asserting each is present
once, so every other byte of the old descriptor stays pinned.
"""

import json

HAMCP473_DETAIL_LEVEL = "compact"
HAMCP473_SEVERITIES_PROPERTY = {
    "default": [],
    "items": {"enum": ["high", "medium", "low", "info"], "type": "string"},
    "title": "Severities",
    "type": "array",
}
HAMCP473_DESCRIPTION_LINES = (
    "    To walk every finding, use detail_level compact with limit 100 and follow\n"
    "    next_cursor; severities and finding_types narrow the walk.\n"
)


def without_hamcp473_inputs(case, schema):
    """``schema`` (``configuration_integrity_analysis`` parameters) without the HAMCP-473 inputs."""

    schema = json.loads(json.dumps(schema))
    values = schema["properties"]["detail_level"]["enum"]
    case.assertEqual(values.count(HAMCP473_DETAIL_LEVEL), 1)
    values.remove(HAMCP473_DETAIL_LEVEL)
    case.assertEqual(schema["properties"].pop("severities"), HAMCP473_SEVERITIES_PROPERTY)
    return schema


def without_hamcp473_descriptor_change(case, descriptor):
    descriptor = json.loads(json.dumps(descriptor))
    descriptor["inputSchema"] = without_hamcp473_inputs(case, descriptor["inputSchema"])
    case.assertEqual(descriptor["description"].count(HAMCP473_DESCRIPTION_LINES), 1)
    descriptor["description"] = descriptor["description"].replace(HAMCP473_DESCRIPTION_LINES, "")
    return descriptor
