"""Parity scorers for native ``ha_search`` (HAMCP-481 W1; HAMCP-501 W6b).

Pure functions over in-memory values: no I/O, no Home Assistant read, no
logging. Nothing registers, routes or dispatches through this module.

The parity basis is the route live 9.2.0 serves: the ``ha_mcp_tools``
custom component's in-process search. Ported from ha-mcp 9.2.0, commit
51e3aac788482a081807591cd5d58d07dc4e540b:

- ``custom_components/ha_mcp_tools/websocket_api/search_score.py`` (sha256
  ba034030b149091fba1e2777ac6c0c4d9c442fa33148b7d92441cba3feca43de): the
  hidden penalty, the ratio, the tokenizer, the separator normalization, the
  entity text tier, the configuration name tier, and the configuration body
  score with its exact and leaf walks;
- ``custom_components/ha_mcp_tools/websocket_api/search.py`` (sha256
  5f1a1e312884494245f71c4720c5ddd3ac3d4211f9c8257e9be9460be36781a8): the
  entity match types (``_entity_match_type``, ``_is_alias_driven`` and
  ``_get_match_type_tier``);
- ``custom_components/ha_mcp_tools/websocket_api/constants.py`` (sha256
  672c13bb636c77c549cb27a72cf27c304d8ac1a61cc833348e4531ad40cbfa71):
  ``FUZZY_THRESHOLD``, ``HIDDEN_SCORE_PENALTY`` and ``_SPLIT_RE``;
- ``custom_components/ha_mcp_tools/search_locations.py`` (sha256
  4567798ee26475efd88e9ed14f9261262e84132d4f453bd6f32789c038c30fd3): the
  area-query ratios and their threshold (``_ratio``, ``_partial_ratio``,
  ``_best_matches`` and ``_resolve_area_query``);
- ``src/ha_mcp/tools/smart_search/_graph.py:371``: ``_GRAPH_HIT_SCORE``.

W1 ported the server's legacy scorers (BM25 with a typo fallback, the
additive name score), which live 9.2.0 reaches only when the component is
missing or fails. W6b retires them for the component's tier scorer. The W1
golden fixtures under ``docs/evidence/hamcp481/fixtures/`` stay as history;
``component-scoring-golden.json`` there pins this port.

Changes from the source, none of which changes a score, a tier or a match
type:

- no secret scrub (``secret_values``): native search holds no secret value,
  and W6's S1 (HAMCP-501) takes the automation, script and scene body matches
  from the component's own scrubbed sub-call. The bodies scored here are the
  ones the configuration surface passes in;
- the exact and leaf walks are iterative, so any depth is read;
- module names drop the leading underscore, and ``entity_name_score``,
  ``config_match_score`` and ``deep_match_score`` keep the W1 signatures the
  configuration surface calls.

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

from collections.abc import Iterable
from difflib import SequenceMatcher
import re
from typing import Any


# The fuzzy floor of the entity and configuration tiers (constants.py:276).
FUZZY_THRESHOLD = 70
# Registry-hidden entities lose this many points after the tier gate
# (constants.py:277).
HIDDEN_SCORE_PENALTY = 20
# The entity tiers: equal, then substring (search_score.py:49-100).
EXACT_TIER = 100
SUBSTRING_TIER = 80
# ``exact_match`` true: the configuration threshold (search_score.py:147).
EXACT_MATCH_THRESHOLD = 100
# The area-query ratio floor (search_locations.py:168, :216).
AREA_QUERY_FUZZY_THRESHOLD = 80
# A reference-graph hit (smart_search/_graph.py:371).
GRAPH_HIT_SCORE = 100

MATCH_TYPES = frozenset({
    "exact_id", "exact_name", "exact_domain", "partial_id", "partial_name",
    "fuzzy_match", "alias_match", "exact_match",
})

_SPLIT_RE = re.compile(r"[._\-\s]+")


def apply_hidden_penalty(score: int, hidden: bool) -> int:
    """Return ``score`` less :data:`HIDDEN_SCORE_PENALTY` for a hidden entity."""
    value = int(score)
    if hidden:
        return max(0, value - HIDDEN_SCORE_PENALTY)
    return value


def ratio(query: str, value: str) -> int:
    """SequenceMatcher similarity, 0-100, truncated (search_score.py:24-26)."""
    return int(SequenceMatcher(None, query, value, autojunk=False).ratio() * 100)


def partial_ratio(query: str, value: str) -> int:
    """Best ratio of the shorter string against any equal-length window
    (search_locations.py:146-153)."""
    if not query or not value:
        return 0
    shorter, longer = (query, value) if len(query) <= len(value) else (value, query)
    window = len(shorter)
    best = 0
    for start in range(len(longer) - window + 1):
        best = max(best, ratio(shorter, longer[start:start + window]))
        if best == 100:
            break
    return best


def tokenize(text: str) -> list[str]:
    """Split on ``.``, ``_``, ``-`` and whitespace, lowercase, drop empties."""
    return [token for token in _SPLIT_RE.split(text.lower()) if token]


def sep_normalized(text: str) -> str:
    """Separator runs collapsed to single spaces (search_score.py:37-46)."""
    return " ".join(tokenize(text))


def entity_domain(entity_id: str) -> str:
    return entity_id.split(".")[0] if "." in entity_id else ""


def text_tier(query_lower: str, texts: Iterable[Any], *, fuzzy: bool) -> int | None:
    """The entity tier over the record's match texts (search_score.py:49-77).

    100 when the query equals a text, 80 when it is a substring of one; in
    fuzzy mode the separator-normalized forms count too, and otherwise the best
    whole-string ratio when it reaches :data:`FUZZY_THRESHOLD`. None means no
    match. Falsy texts are skipped. The caller passes the stripped, lowercased
    query.
    """
    query_norm = sep_normalized(query_lower) if fuzzy else ""
    best_substring: int | None = None
    best_ratio = 0
    for text in texts:
        if not text:
            continue
        tier, text_ratio = _tier_one_text(query_lower, query_norm, str(text).lower(), fuzzy)
        if tier == EXACT_TIER:
            return EXACT_TIER
        if tier == SUBSTRING_TIER:
            best_substring = SUBSTRING_TIER
        elif text_ratio > best_ratio:
            best_ratio = text_ratio
    if best_substring is not None:
        return best_substring
    if fuzzy and best_ratio >= FUZZY_THRESHOLD:
        return best_ratio
    return None


def _tier_one_text(query_lower: str, query_norm: str, text_lower: str,
                   fuzzy: bool) -> tuple[int | None, int]:
    """``(tier, ratio)`` of one text (search_score.py:80-100)."""
    if query_lower == text_lower:
        return EXACT_TIER, 0
    text_norm = sep_normalized(text_lower) if fuzzy and query_norm else ""
    if text_norm and query_norm == text_norm:
        return EXACT_TIER, 0
    if query_lower in text_lower:
        return SUBSTRING_TIER, 0
    if text_norm and query_norm in text_norm:
        return SUBSTRING_TIER, 0
    if fuzzy:
        return None, ratio(query_lower, text_lower)
    return None, 0


def name_tier(query_lower: str, texts: Iterable[Any], *, exact: bool) -> int | None:
    """The configuration name tier (search_score.py:103-125): a substring is
    100, not 80; in fuzzy mode a separator-normalized substring is 100 too,
    else the best ratio when it reaches :data:`FUZZY_THRESHOLD`."""
    query_norm = "" if exact else sep_normalized(query_lower)
    best_ratio = 0
    for text in texts:
        if not text:
            continue
        text_lower = str(text).lower()
        if query_lower in text_lower:
            return 100
        if not exact:
            if query_norm and query_norm in sep_normalized(text_lower):
                return 100
            best_ratio = max(best_ratio, ratio(query_lower, text_lower))
    if not exact and best_ratio >= FUZZY_THRESHOLD:
        return best_ratio
    return None


def entity_match_type(query_lower: str, entity_id: str, names: tuple[Any, ...], domain: str,
                      aliases: Iterable[str], *, exact: bool) -> str:
    """The match type of a scored entity (search.py:395-425)."""
    if exact:
        return "exact_match"
    if is_alias_driven(query_lower, entity_id, names, aliases):
        return "alias_match"
    return match_type_tier(query_lower, entity_id, names, domain)


def is_alias_driven(query_lower: str, entity_id: str, names: tuple[Any, ...],
                    aliases: Iterable[str]) -> bool:
    """True when a query token is an alias-only token (search.py:428-453):
    a token, or a separator-stripped alias, absent from the entity id and the
    names."""
    id_tail = entity_id.split(".", 1)[1] if "." in entity_id else entity_id
    id_name_tokens = set(tokenize(entity_id))
    id_name_tokens.add(_SPLIT_RE.sub("", id_tail.lower()))
    for name in names:
        text = str(name)
        id_name_tokens.update(tokenize(text))
        id_name_tokens.add(_SPLIT_RE.sub("", text.lower()))
    alias_only: set[str] = set()
    for alias in aliases:
        alias_lower = str(alias).lower()
        for token in tokenize(alias_lower):
            if token not in id_name_tokens:
                alias_only.add(token)
        alias_concat = _SPLIT_RE.sub("", alias_lower)
        if alias_concat and alias_concat not in id_name_tokens:
            alias_only.add(alias_concat)
    return bool(set(tokenize(query_lower)) & alias_only)


def match_type_tier(query_lower: str, entity_id: str, names: tuple[Any, ...],
                    domain: str) -> str:
    """search.py:456-472."""
    entity_lower = entity_id.lower()
    lowered = [str(name).lower() for name in names]
    if query_lower == entity_lower:
        return "exact_id"
    if query_lower in lowered:
        return "exact_name"
    if query_lower == domain.lower():
        return "exact_domain"
    if query_lower in entity_lower:
        return "partial_id"
    if any(query_lower in name for name in lowered):
        return "partial_name"
    return "fuzzy_match"


def entity_name_score(entity_id: str, friendly_name: str, domain: str, query: str) -> int:
    """The fuzzy configuration name score: :func:`name_tier` over the entity id
    and the name, or 0 (search_score.py:143). ``domain`` is not scored; the
    argument keeps the configuration surface's call."""
    return name_tier(query, (entity_id, friendly_name), exact=False) or 0


def deep_match_score(
    entity_id: str,
    friendly_name: str,
    fuzzy_name_score: int,
    config_score: int,
    query_lower: str,
    exact_match: bool,
    fuzzy_threshold: int = FUZZY_THRESHOLD,
) -> tuple[int, int, bool]:
    """``(total_score, threshold, match_in_name)`` of a configuration record
    (search_score.py:128-151). A record matches when
    ``total_score >= threshold``; the threshold is 100 in exact mode."""
    if exact_match:
        name_exact = name_tier(query_lower, (entity_id, friendly_name), exact=True) or 0
        return max(name_exact, config_score), EXACT_MATCH_THRESHOLD, name_exact >= 100
    return (
        max(fuzzy_name_score, config_score),
        fuzzy_threshold,
        fuzzy_name_score >= fuzzy_threshold,
    )


def config_match_score(data: Any, query: str, exact_match: bool) -> int:
    """Score ``query`` against a configuration body (search_score.py:154-183).

    100 when the query is a substring of any key or leaf. Otherwise, in exact
    mode 0; in fuzzy mode the best ratio of a query token against a leaf
    token when it reaches :data:`FUZZY_THRESHOLD`, else 0. The caller passes
    the lowercased query.
    """
    if data is None:
        return 0
    if _config_exact_score(data, query) >= 100:
        return 100
    if exact_match:
        return 0
    query_tokens = tokenize(query)
    if not query_tokens:
        return 0
    leaves: list[str] = []
    _collect_string_leaves(data, leaves)
    doc_tokens = {token for leaf in leaves for token in tokenize(leaf)}
    best = 0
    for query_token in query_tokens:
        for doc_token in doc_tokens:
            best = max(best, ratio(query_token, doc_token))
    return best if best >= FUZZY_THRESHOLD else 0


def _config_exact_score(data: Any, query: str) -> int:
    """100 when ``query`` is a substring of a key or a non-null leaf
    (search_score.py:186-226)."""
    stack = [data]
    while stack:
        node = stack.pop()
        if isinstance(node, dict):
            for key, value in node.items():
                if query in str(key).lower():
                    return 100
                stack.append(value)
        elif isinstance(node, (list, tuple)):
            stack.extend(node)
        elif node is not None and query in str(node).lower():
            return 100
    return 0


def _collect_string_leaves(data: Any, out: list[str]) -> None:
    """Every key and non-null leaf as a string (search_score.py:229-249)."""
    stack = [data]
    while stack:
        node = stack.pop()
        if isinstance(node, dict):
            for key, value in node.items():
                out.append(str(key))
                stack.append(value)
        elif isinstance(node, (list, tuple)):
            stack.extend(node)
        elif isinstance(node, str):
            out.append(node)
        elif node is not None:
            out.append(str(node))
