"""9.2.0 dashboard getter: default-withhold counts and warning rule (HAMCP-371 S10).

The shared ``upstream_dashboard.py`` getter already returns a closed rebuild:
it names each returned key, and every other upstream key is dropped without a
count. On ha-mcp 9.2.0 only, one gate in that getter calls ``apply`` (22:59
record, item 6; N30), which adds what items 7 to 12 of the 21:36 record
require:

- ``fields_withheld`` counts the distinct upstream names outside the reviewed
  get-mode key set; ``config`` is open (item 11) and is not walked.
  ``fields_dropped_by_policy`` is 0: the getter has no policy drops. Reviewed
  keys the rebuild does not return (for example ``render_paths``) are admitted,
  as in the other 9.2.0 rows. Both counts are capped at 999 and sit in ``data``
  (D1).
- An upstream warning or hint passes only when it fully matches a reviewed
  template. Every other one, including one the provider's 20-entry bound or
  its sanitizer removed, is replaced: ``upstream_warning_replaced`` is
  appended once, ``upstream_warnings_replaced`` counts per occurrence, and the
  read becomes partial (item 12).

Reviewed source: ``_get_dashboard_get_mode`` and ``_finalize_get_result`` in
``tools_config_dashboards.py`` and ``dashboard_render_paths`` in
``dashboard_screenshot/paths.py:175-233`` at 51e3aac7. Both blobs are the same
at 9.1.2 (bab615df), so the 9.2.0 rows equal 9.1.2's because the source is the
same, not by analogy. Engineering sends no ``view_path``, ``entity_id`` or
screenshot, so the view-path and screenshot texts cannot occur and are not
reviewed.
"""
from __future__ import annotations

import re
from typing import Any

from .upstream_reads_9_2_keys import (
    COUNT_CAP,
    REPLACED_PARTIAL,
    REPLACED_WARNING,
    KeySet,
    _names,
)

RELEASES = frozenset({"9.2.0"})

ROW = KeySet(
    admitted=frozenset({
        # tools_config_dashboards.py, get mode at 51e3aac7.
        "success", "action", "url_path", "config", "config_hash",
        "config_size_bytes", "render_paths", "hint", "resolved_from",
        "stored_config", "message", "warnings",
        # paths.py:157-167, one render_paths row.
        "view_index", "view_path", "title", "render_path", "stable",
        "strategy_dashboard", "ambiguous_path", "invalid_path",
    }),
    open=frozenset({"config"}),
)

REVIEWED_WARNINGS = (
    # paths.py:196-197.
    re.compile(re.escape(
        "Strategy dashboard views are generated at runtime; only the dashboard "
        "base render path is available.")),
    # paths.py:224-227.
    re.compile(r"[1-9][0-9]* " + re.escape(
        "dashboard view(s) have no usable stable path; their render paths use "
        "fragile numeric indexes.")),
    # paths.py:229-232.
    re.compile(r"[1-9][0-9]* " + re.escape(
        "dashboard view(s) share a duplicate configured path; numeric render "
        "paths are returned for those ambiguous views.")),
    # tools_config_dashboards.py:2319-2326 (config_size >= 10000).
    re.compile(r"Large config \([1-9][0-9]{0,2}(?:,[0-9]{3})+ bytes\)\. " + re.escape(
        "Pass view_path=... to fetch a single view, or use "
        "ha_config_get_dashboard(entity_id=...) to find card positions, then "
        "ha_config_set_dashboard(python_transform=...) instead of full config "
        "replacement.")),
)


def _upstream_count(payload: dict) -> int:
    """Upstream warning and hint values, before the provider's bound."""

    warnings = payload.get("warnings")
    count = len(warnings) if isinstance(warnings, list) else int(isinstance(warnings, str))
    return count + int(isinstance(payload.get("hint"), str))


def apply(
    payload: dict[str, Any],
    data: dict[str, Any],
    metadata: dict[str, Any],
    warnings: list[str],
    completeness: str,
) -> tuple[list[str], str]:
    """Add the 9.2.0 counts to ``data``; return the warnings and completeness.

    ``warnings`` is the provider's sanitized upstream list, followed by the
    provider's own partial sentence exactly when ``completeness`` is partial.
    ``payload`` is the upstream dict and ``data`` the provider's fixed rebuild,
    which never carries the count names.
    """

    own = warnings[-1:] if completeness == "partial" else []
    upstream = warnings[: len(warnings) - len(own)]
    kept = [
        text for text in upstream
        if type(text) is str and any(rule.fullmatch(text) for rule in REVIEWED_WARNINGS)
    ]
    replaced = _upstream_count(payload) - len(kept)
    names = _names(payload, ROW, set())
    data["fields_withheld"] = min(len(names - ROW.admitted), COUNT_CAP)
    data["fields_dropped_by_policy"] = 0
    data["upstream_warnings_replaced"] = min(replaced, COUNT_CAP)
    result = kept + ([REPLACED_WARNING] if replaced else []) + own
    if replaced:
        completeness = "partial"
        metadata["completeness"] = completeness
        result.append(REPLACED_PARTIAL)
    return result, completeness
