"""Native dashboard search for ``ha_search`` (HAMCP-447, 2.11.0 Lane B2).

Approved by Josh as relayed by the architect, 2026-11-27: dashboards are
searched natively, YAML dashboards are skipped with a closed code, and the
default dashboard is read and matched (d1, disclosed). Core 2026.11.3 has no
read within the signed profile that reports the default dashboard's mode:
``lovelace/info`` reports only the resource mode.

The engine reads ``lovelace/dashboards/list`` as an inventory. This module
chooses which configurations are read, and
:meth:`.core_reads.CoreReadSession.dashboard_configs` scores each body as it
is read and then drops it. A record carries the dashboard's ``url_path`` and
``title``, its score and the match flags, never its body. Under
``include_config`` the reply discloses each matched body as withheld, as it
does for automations.

Which dashboards are read:

- a listed ``storage`` dashboard whose ``url_path`` fits
  :data:`.core_contracts.URL_PATH`, in Core's order, up to
  :data:`.core_contracts.MAX_DASHBOARDS`;
- never a listed ``yaml`` dashboard. Core resolves ``!secret`` tags when it
  loads one, so matching it would test for those values. Each is counted as
  ``not_searched`` ``yaml_dashboards_skipped``;
- the default dashboard (``lovelace/config`` with no ``url_path``), first,
  only when the list was read with no dropped row and lists no ``lovelace``
  dashboard. Core serves a listed ``lovelace`` dashboard in the default's
  place, and that one may be YAML (the legacy ``lovelace: mode: yaml``). Core
  sets YAML dashboards up only when it starts, so a whole list without
  ``lovelace`` rules that out until Core restarts. A listed ``lovelace``
  dashboard in storage mode is read by its own ``url_path``;
- when the default is read, a listed dashboard at ``default`` is not, so no
  two records share a path.

Accounting, all ``not_searched`` for the dashboard bucket. An unreadable list
is ``read_failed`` with no count. Each dropped row makes the count unknown,
since the default is then not read. A listed row that is neither YAML nor a
readable storage row, a row past the bound, and each configuration read that
failed or did not start within :data:`.subcalls.SCAN_DEADLINE_SECONDS` counts
one ``read_failed``. Core's ``config_not_found`` (a dashboard with no saved
configuration, or one removed since the list was read) is a clean no-match.

Matching is :func:`.scoring.config_match_score` over the body. Under
``exact_match`` (the default) it is a case-insensitive substring of any key or
non-null leaf, score 100; otherwise the fuzzy leaf score also counts, at
:data:`.scoring.FUZZY_THRESHOLD`.

Ported from ha-mcp 9.2.0, commit 51e3aac788482a081807591cd5d58d07dc4e540b:

- ``src/ha_mcp/tools/smart_search/_deep.py`` (sha256
  10c57f9fd6186deb31d88329f3d61bdd63bf6cb525fafbf455eda0bfd44482ff): the walk
  over the default and the listed dashboards, the default's ``default`` path
  and "Default Dashboard" title, the title fallback, the record and the clean
  no-stored-configuration skip;
- ``custom_components/ha_mcp_tools/websocket_api/dashboards.py`` (sha256
  9fa69546e108ee72cf39d76b96e716a3d841d4b0fb93cc759b78b990e95608fb): the
  YAML skip.

Changes from the source:

- YAML dashboards are never read. 9.2.0's component skips them, but its
  legacy walk, which runs whenever the component skipped one, reads them;
- the default is read only under the rule above. The source always reads it,
  and with a ``lovelace`` dashboard listed it reports that dashboard twice;
- both ``config_not_found`` causes are a clean no-match: a batch reply carries
  the code only. The legacy walk counts an unknown ``url_path`` as a failure;
- the fuzzy mode uses the component's leaf scorer at 70, not the server's
  ``_search_in_dict`` at its ``fuzzy_threshold`` setting (default 60);
- a record never carries ``config``;
- the reads are pipelined in chunks of :data:`.core_contracts.CONFIG_CHUNK`
  under one deadline, not concurrent reads each with its own timeout;
- an unusable or over-bound row, a failed read and an unreadable list are
  counted under ``read_failed``.

A dashboard removed or replaced between the list and its read is read as Core
then holds it; one removed is a clean no-match.

The ported code carries the source's license notice:

    MIT License

    Copyright (c) 2024 Julien

    Permission is hereby granted, free of charge, to any person obtaining a copy
    of this software and associated documentation files (the "Software"), to deal
    in the Software without restriction, including without limitation the rights
    to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
    copies of the Software, and to permit persons to whom the Software is
    furnished to do so, subject to the following conditions:

    The above copyright notice and this permission notice shall be included in all
    copies or substantial portions of the Software.

    THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
    IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
    FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
    AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
    LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
    OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
    SOFTWARE.
"""

from __future__ import annotations

import time
from typing import Any, Callable, Mapping

from . import core_contracts as c
from . import scoring
from . import subcalls
from .core_reads import INVALID, ReadResult
from .reply import Reasons

DEFAULT_URL_PATH = "default"
DEFAULT_TITLE = "Default Dashboard"
# Core serves this listed dashboard for ``lovelace/config`` with no
# ``url_path`` (``lovelace/websocket.py``).
SERVED_AS_DEFAULT = "lovelace"
STORAGE_MODE = "storage"
YAML_MODE = "yaml"


def plan(listing: ReadResult, reasons: Reasons) -> list:
    """The dashboards to read, as ``(url_path, title)`` pairs in read order
    (``None`` is the default dashboard); the rest are counted in
    ``reasons``."""

    if (type(listing) is not ReadResult or listing.status not in (c.COMPLETE, c.PARTIAL)
            or not isinstance(listing.value, Mapping)):
        reasons.add("not_searched", "dashboard", "read_failed", None)
        return []
    rows = listing.value
    read_default = not listing.dropped and SERVED_AS_DEFAULT not in rows
    if listing.dropped:
        # A dropped row may be the one Core would serve as the default.
        reasons.add("not_searched", "dashboard", "read_failed", None)
    targets = [(None, DEFAULT_TITLE)] if read_default else []
    listed = yaml = failed = 0
    for url_path, row in rows.items():
        mode = row.get("mode")
        if mode == YAML_MODE:
            yaml += 1
            continue
        title = row.get("title")
        if (mode != STORAGE_MODE or type(title) is not str or title is INVALID
                or type(url_path) is not str or c.URL_PATH.fullmatch(url_path) is None
                or (read_default and url_path == DEFAULT_URL_PATH)
                or listed >= c.MAX_DASHBOARDS):
            failed += 1
            continue
        listed += 1
        targets.append((url_path, title or url_path))
    reasons.add("not_searched", "dashboard", "yaml_dashboards_skipped", yaml)
    reasons.add("not_searched", "dashboard", "read_failed", failed)
    return targets


async def search_dashboards(query_lower: str, exact_match: bool, listing: ReadResult,
                            reader: Any, reasons: Reasons, *,
                            monotonic: Callable[[], float] = time.monotonic) -> list:
    """The dashboards whose configuration matches, as ``(record, True)``
    pairs in read order: each body was read and matched, and is withheld."""

    if type(query_lower) is not str or not query_lower or type(exact_match) is not bool:
        raise ValueError("invalid dashboard search")
    targets = plan(listing, reasons)
    if not targets:
        return []
    threshold = scoring.EXACT_MATCH_THRESHOLD if exact_match else scoring.FUZZY_THRESHOLD
    result = await reader.dashboard_configs(
        [target for target, _ in targets],
        lambda body: scoring.config_match_score(body, query_lower, exact_match),
        deadline=monotonic() + subcalls.SCAN_DEADLINE_SECONDS, monotonic=monotonic)
    if (type(result) is not ReadResult or result.status not in (c.COMPLETE, c.PARTIAL)
            or not isinstance(result.value, Mapping)):
        reasons.add("not_searched", "dashboard", "read_failed", len(targets))
        return []
    records = []
    failed = 0
    for target, title in targets:
        outcome = result.items.get(target)
        if outcome not in ("read", "not_found"):
            failed += 1
            continue
        score = result.value.get(target)
        if outcome == "read" and type(score) is int and score >= threshold:
            records.append(({"url_path": DEFAULT_URL_PATH if target is None else target,
                             "title": title, "score": score, "match_in_config": True,
                             "match_in_references": False}, True))
    reasons.add("not_searched", "dashboard", "read_failed", failed)
    return records
