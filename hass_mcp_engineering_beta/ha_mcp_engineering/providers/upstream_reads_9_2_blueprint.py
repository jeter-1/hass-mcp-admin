"""9.2.0 blueprint read: refuse unreviewed keys under ``data`` (HAMCP-371 D5-B).

The shared ``upstream_blueprint.validate_result`` checks the identity and shape
of an ``ha_manage_blueprints`` list or get answer but does not close ``data``:
any other key under it passes through (HAMCP-402, every admitted version). On
ha-mcp 9.2.0 only, one gate in ``validate_result`` calls ``validate`` (the D5-B
ruling of 2026-11-15 14:45), which refuses any key directly under ``data``
outside the reviewed set for the mode. The refusal is the existing path: a
``ValueError``, which the gateway reports as ``invalid_response``. The payload
is never changed. Values are not inspected here: ``config`` and ``yaml`` stay
open as user content, and the shared body's type checks still apply. 8.x and
9.1.x never reach this module.

Both sets apply with and without the ha-mcp component; the component only
changes where ``config`` and ``yaml`` come from (``yaml_source``).

- ``LIST_KEYS`` is the 9.2.0 capture: every ``r12-blueprints`` receipt, profile
  A (no component) and profile B (component 9.2.0), has exactly these keys
  under ``data``. It matches ``_format_blueprint_list``
  (``src/ha_mcp/tools/tools_blueprints.py:123-127`` at ``51e3aac7``).
- ``GET_KEYS`` is a source-review key set, not a capture: the lane has no
  9.2.0 get-mode receipt. ``_get_blueprint`` and ``_merge_blueprint_body`` in
  ``src/ha_mcp/tools/tools_blueprints.py`` at ``51e3aac7`` (the file is
  byte-identical to 9.1.2 ``bab615df``) write ``path``, ``domain`` and
  ``name`` (:449-455), ``metadata`` (:461), ``inputs`` (:470), ``used_by``
  (:482), ``config`` (:696), ``yaml`` and ``yaml_source`` (:698-699). SR must
  read get mode on a default Core blueprint with the component.
"""
from __future__ import annotations

from typing import Any

RELEASES = frozenset({"9.2.0"})

LIST_KEYS = frozenset({"domain", "count", "blueprints"})
GET_KEYS = frozenset({
    "path", "domain", "name", "metadata", "inputs", "used_by",
    "config", "yaml", "yaml_source",
})


def validate(payload: dict[str, Any], action: str) -> None:
    """Refuse a key under ``data`` outside the reviewed set for ``action``."""

    allowed = GET_KEYS if action == "get" else LIST_KEYS
    if not set(payload["data"]) <= allowed:
        raise ValueError("blueprint_read_response_key_unreviewed")
