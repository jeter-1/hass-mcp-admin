"""The reviewed HAMCP-226 and HAMCP-228 public descriptor changes.

Historical descriptor pins compare the current catalog with earlier
baselines. These helpers remove exactly the reviewed additions, asserting
each is present once, so every other byte of the old descriptor stays
pinned:

- HAMCP-226 adds ``unavailable_entity_reference`` to the
  ``configuration_integrity_analysis`` ``finding_types`` enum (additive).
- HAMCP-228 adds ``capture_automation_baseline``'s optional
  ``resume_capture_id``, rewrites its description (durable store, resume,
  compare) and clears ``readOnlyHint``: a capture now writes one
  Engineering-local stored baseline; Home Assistant access stays read-only.
"""

import hashlib
import json

HAMCP226_FINDING_TYPE = "unavailable_entity_reference"
HAMCP228_RESUME_PROPERTY = {"default": "", "maxLength": 32, "title": "Resume Capture Id", "type": "string"}
# sha256 of the reviewed HAMCP-228 capture description.
HAMCP228_CAPTURE_DESCRIPTION_SHA256 = "d666090bc00fc4889cc527d5d927d759ff6639c1ddf97c435d3faae459851f39"


def without_hamcp226_finding_type(case, schema):
    """``schema`` (``configuration_integrity_analysis`` parameters) without the HAMCP-226 value."""

    schema = json.loads(json.dumps(schema))
    values = schema["properties"]["finding_types"]["items"]["enum"]
    case.assertEqual(values.count(HAMCP226_FINDING_TYPE), 1)
    values.remove(HAMCP226_FINDING_TYPE)
    return schema


def without_hamcp226_descriptor_change(case, descriptor):
    descriptor = json.loads(json.dumps(descriptor))
    descriptor["inputSchema"] = without_hamcp226_finding_type(case, descriptor["inputSchema"])
    return descriptor


def without_hamcp228_capture_changes(case, descriptor, historical_description):
    """The capture descriptor with the reviewed HAMCP-228 changes undone."""

    descriptor = json.loads(json.dumps(descriptor))
    case.assertIs(descriptor["annotations"]["readOnlyHint"], False)
    descriptor["annotations"]["readOnlyHint"] = True
    case.assertEqual(descriptor["inputSchema"]["properties"].pop("resume_capture_id"), HAMCP228_RESUME_PROPERTY)
    case.assertEqual(hashlib.sha256(descriptor["description"].encode()).hexdigest(),
                     HAMCP228_CAPTURE_DESCRIPTION_SHA256)
    descriptor["description"] = historical_description
    return descriptor
