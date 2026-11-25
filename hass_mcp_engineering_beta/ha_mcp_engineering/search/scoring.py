"""Parity scorers for native ``ha_search`` (HAMCP-481 W1, design section 3).

Pure functions over in-memory values: no I/O, no Home Assistant read, no
logging. Nothing registers, routes or dispatches through this module.

Ported from ha-mcp 9.2.0, commit 51e3aac788482a081807591cd5d58d07dc4e540b:

- ``src/ha_mcp/utils/fuzzy_search.py`` (sha256
  6759bdd4223bf3221456be84250b23e67d87dd1a783660eb2dbd3f8bee54a207): the
  tokenizer, the hidden penalty, the BM25 scorer, the BM25-primary entity
  search with its typo fallback and match types, the legacy additive name
  score and the SequenceMatcher ratios;
- ``src/ha_mcp/tools/smart_search/_scoring.py`` (sha256
  09232b092f2cc41aa2d143f5b5bacfbca6952b22d339d52984b694b83cb73682): the
  deep-match score and the exact and fuzzy configuration walks;
- ``src/ha_mcp/tools/search/entities.py:465-485``: the ``exact_match`` true
  substring score (100 equal, 80 otherwise, hidden penalty);
- ``src/ha_mcp/tools/smart_search/_graph.py:371``: ``_GRAPH_HIT_SCORE``;
- ``src/ha_mcp/tools/search/modes.py:85``: the area-plus-query threshold.

Changes from the source, none of which changes a score, a match type or an
order:

- no logging (the source logs query tokens on two configuration paths);
- typed inputs: :class:`ScoringEntity` replaces the state dicts the source
  enriches with ``_aliases`` and ``_hidden_by``, and ``hidden`` replaces
  ``hidden_by is not None``. The caller keeps the source's defaults: the
  friendly name falls back to the entity id, and non-string aliases are
  dropped before they reach this module;
- the entity search returns every match in order; paging is the caller's;
- module functions replace the searcher class and the scoring mixin, and the
  fuzzy threshold is an argument instead of ``self.settings``;
- not ported, because native search does not use them: the suggestion
  helpers, the area-from-name inference and ``extract_best_matches``.

The golden fixtures under ``docs/evidence/hamcp481/fixtures/`` were generated
by running the pinned source above, and pin this port to it.

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

from collections.abc import Sequence
from dataclasses import dataclass
from difflib import SequenceMatcher
import math
import re
from typing import Any


# Registry-hidden entities lose this many points after the threshold gate
# (fuzzy_search.py:35).
HIDDEN_SCORE_PENALTY = 20
# BM25 parameters for short entity-name documents (fuzzy_search.py:78).
BM25_K1 = 1.2
BM25_B = 0.5
# The ``fuzzy_threshold`` setting's default (smart_search/__init__.py:41) and
# the area-plus-query mode's fixed threshold (search/modes.py:85).
DEFAULT_FUZZY_THRESHOLD = 60
AREA_QUERY_FUZZY_THRESHOLD = 80
# Entity typo fallback: best token ratio, and the shortest single-token query
# it serves (fuzzy_search.py:418, :427).
TYPO_FALLBACK_THRESHOLD = 75
TYPO_FALLBACK_MIN_TOKEN_LENGTH = 4
# Configuration typo fallback (_scoring.py:109).
CONFIG_TYPO_THRESHOLD = 70
# ``exact_match`` true: entity substring scores (search/entities.py:481) and
# the configuration threshold (_scoring.py:36).
EXACT_EQUAL_SCORE = 100
EXACT_SUBSTRING_SCORE = 80
EXACT_MATCH_THRESHOLD = 100
# A reference-graph hit (smart_search/_graph.py:371).
GRAPH_HIT_SCORE = 100

MATCH_TYPES = frozenset({
    "exact_id", "exact_name", "exact_domain", "partial_id", "partial_name",
    "fuzzy_match", "alias_match", "typo_fallback", "exact_match",
})

_SPLIT_RE = re.compile(r"[._\-\s]+")

# Legacy name-score boosts (fuzzy_search.py:510-536).
_ROOM_KEYWORDS = (
    "salon", "chambre", "cuisine", "salle", "living", "bedroom", "kitchen",
)
_DEVICE_KEYWORDS = (
    "light", "switch", "sensor", "climate", "lumiere", "interrupteur",
)


@dataclass(frozen=True, slots=True)
class ScoringEntity:
    """One entity as the scorers see it."""

    entity_id: str
    friendly_name: str
    aliases: tuple[str, ...] = ()
    hidden: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.entity_id, str) or not isinstance(self.friendly_name, str):
            raise TypeError("entity_id and friendly_name must be strings")
        if not isinstance(self.aliases, tuple) or not all(
            isinstance(alias, str) for alias in self.aliases
        ):
            raise TypeError("aliases must be a tuple of strings")
        if not isinstance(self.hidden, bool):
            raise TypeError("hidden must be a bool")


@dataclass(frozen=True, slots=True)
class EntityMatch:
    """A scored entity. The caller joins state and attributes."""

    entity_id: str
    friendly_name: str
    domain: str
    score: int
    match_type: str


def apply_hidden_penalty(score: int, hidden: bool) -> int:
    """Return ``score`` less :data:`HIDDEN_SCORE_PENALTY` for a hidden entity."""
    value = int(score)
    if hidden:
        return max(0, value - HIDDEN_SCORE_PENALTY)
    return value


def tokenize(text: str) -> list[str]:
    """Split on ``.``, ``_``, ``-`` and whitespace, lowercase, drop empties."""
    return [token for token in _SPLIT_RE.split(text.lower()) if token]


def _strip_separators(text: str) -> str:
    return _SPLIT_RE.sub("", text.lower())


def entity_domain(entity_id: str) -> str:
    return entity_id.split(".")[0] if "." in entity_id else ""


def entity_order_key(match: EntityMatch) -> tuple[int, str]:
    """The 9.2.0 entity order: score descending, then entity id."""
    return (-match.score, match.entity_id)


class BM25Scorer:
    """Okapi BM25 with Robertson IDF smoothing (fuzzy_search.py:70-158)."""

    def __init__(self, k1: float | None = None, b: float | None = None) -> None:
        self.k1 = BM25_K1 if k1 is None else k1
        self.b = BM25_B if b is None else b
        self._idf: dict[str, float] = {}
        self._doc_tokens: list[list[str]] = []
        self._doc_lens: list[int] = []
        self._avgdl = 0.0

    def fit(self, corpus: list[list[str]]) -> None:
        self._doc_tokens = corpus
        count = len(corpus)
        if count == 0:
            return
        self._doc_lens = [len(doc) for doc in corpus]
        self._avgdl = sum(self._doc_lens) / count
        if self._avgdl == 0.0:
            self._avgdl = 1.0
        frequency: dict[str, int] = {}
        for doc in corpus:
            seen: set[str] = set()
            for token in doc:
                if token not in seen:
                    frequency[token] = frequency.get(token, 0) + 1
                    seen.add(token)
        self._idf = {
            token: math.log((count - freq + 0.5) / (freq + 0.5) + 1.0)
            for token, freq in frequency.items()
        }

    def score(self, query_tokens: list[str], doc_index: int) -> float:
        doc = self._doc_tokens[doc_index]
        length = self._doc_lens[doc_index]
        term_frequency: dict[str, int] = {}
        for token in doc:
            term_frequency[token] = term_frequency.get(token, 0) + 1
        total = 0.0
        for token in query_tokens:
            idf = self._idf.get(token, 0.0)
            frequency = term_frequency.get(token, 0)
            if frequency == 0:
                continue
            numerator = frequency * (self.k1 + 1)
            denominator = frequency + self.k1 * (
                1 - self.b + self.b * length / self._avgdl
            )
            total += idf * numerator / denominator
        return total

    def score_all(self, query_tokens: list[str]) -> list[float]:
        return [self.score(query_tokens, index) for index in range(len(self._doc_tokens))]

    def max_possible_score(self, query_tokens: list[str]) -> float:
        """Sum of query-token IDFs; a token absent from the corpus counts the
        corpus's highest IDF, so it cannot raise a partial match to 100."""
        if not self._idf:
            return 0.0
        highest = max(self._idf.values())
        return sum(self._idf.get(token, highest) for token in query_tokens)


def fuzzy_entity_matches(
    entities: Sequence[ScoringEntity],
    query: str,
    threshold: int = DEFAULT_FUZZY_THRESHOLD,
) -> tuple[EntityMatch, ...]:
    """``exact_match`` false entity search: every match, in 9.2.0 order.

    BM25 over entity id, friendly name and aliases; when BM25 scores every
    document at zero, the token-ratio typo fallback (fuzzy_search.py:173-235).
    """
    if not query or not entities:
        return ()
    query_lower = query.lower().strip()
    query_tokens = tokenize(query_lower)
    if not query_tokens:
        return ()
    docs: list[list[str]] = []
    alias_only: list[set[str]] = []
    for entity in entities:
        tokens, alias_tokens = _entity_document(entity)
        docs.append(tokens)
        alias_only.append(alias_tokens)
    scorer = BM25Scorer()
    scorer.fit(docs)
    raw_scores = scorer.score_all(query_tokens)
    theoretical_max = scorer.max_possible_score(query_tokens)
    matches = _bm25_matches(
        entities, raw_scores, theoretical_max, query_tokens, query_lower,
        alias_only, threshold,
    )
    if not matches and not any(raw > 0 for raw in raw_scores):
        matches = _typo_fallback_matches(entities, docs, query_tokens)
    matches.sort(key=entity_order_key)
    return tuple(matches)


def _entity_document(entity: ScoringEntity) -> tuple[list[str], set[str]]:
    """The BM25 document and its alias-only tokens (fuzzy_search.py:239-337)."""
    entity_id = entity.entity_id
    friendly_name = entity.friendly_name
    id_tokens = tokenize(entity_id)
    name_tokens = tokenize(friendly_name)
    tokens = list(id_tokens + name_tokens)
    tail = entity_id.split(".", 1)[1] if "." in entity_id else entity_id
    tail_concat = _strip_separators(tail)
    if tail_concat:
        tokens.append(tail_concat)
    name_concat = _strip_separators(friendly_name)
    if name_concat and name_concat != tail_concat:
        tokens.append(name_concat)
    id_name_tokens = set(id_tokens) | set(name_tokens)
    id_name_tokens.add(tail_concat)
    id_name_tokens.add(name_concat)
    alias_only: set[str] = set()
    for alias in entity.aliases:
        alias_tokens = tokenize(alias)
        tokens.extend(alias_tokens)
        for token in alias_tokens:
            if token not in id_name_tokens:
                alias_only.add(token)
        alias_concat = _strip_separators(alias)
        if alias_concat:
            tokens.append(alias_concat)
            if alias_concat not in id_name_tokens:
                alias_only.add(alias_concat)
    return tokens, alias_only


def _bm25_matches(
    entities: Sequence[ScoringEntity],
    raw_scores: list[float],
    theoretical_max: float,
    query_tokens: list[str],
    query_lower: str,
    alias_only: list[set[str]],
    threshold: int,
) -> list[EntityMatch]:
    """The threshold gates the raw score; the hidden penalty applies after the
    gate, so it re-ranks a hidden match but never hides it."""
    matches: list[EntityMatch] = []
    if theoretical_max <= 0:
        return matches
    query_token_set = set(query_tokens)
    for index, raw in enumerate(raw_scores):
        if raw <= 0:
            continue
        raw_score = min(100, round(raw / theoretical_max * 100))
        if raw_score < threshold:
            continue
        entity = entities[index]
        domain = entity_domain(entity.entity_id)
        if query_token_set & alias_only[index]:
            match_type = "alias_match"
        else:
            match_type = _match_type(
                entity.entity_id, entity.friendly_name, domain, query_lower,
            )
        matches.append(EntityMatch(
            entity.entity_id, entity.friendly_name, domain,
            apply_hidden_penalty(raw_score, entity.hidden), match_type,
        ))
    return matches


def _typo_fallback_matches(
    entities: Sequence[ScoringEntity],
    docs: list[list[str]],
    query_tokens: list[str],
) -> list[EntityMatch]:
    """Token-level ratio fallback (fuzzy_search.py:391-460).

    A short single-token query never falls back. A multi-token query needs at
    least half of its distinct tokens to ratio-match some document token.
    """
    results: list[EntityMatch] = []
    distinct = list(dict.fromkeys(query_tokens))
    if len(distinct) == 1 and len(query_tokens[0]) < TYPO_FALLBACK_MIN_TOKEN_LENGTH:
        return results
    for index, doc_tokens in enumerate(docs):
        best = 0
        for query_token in query_tokens:
            for doc_token in doc_tokens:
                best = max(best, ratio(query_token, doc_token))
        if best < TYPO_FALLBACK_THRESHOLD:
            continue
        if len(distinct) > 1:
            covered = sum(
                1 for query_token in distinct
                if any(
                    ratio(query_token, doc_token) >= TYPO_FALLBACK_THRESHOLD
                    for doc_token in doc_tokens
                )
            )
            if covered * 2 < len(distinct):
                continue
        entity = entities[index]
        results.append(EntityMatch(
            entity.entity_id, entity.friendly_name, entity_domain(entity.entity_id),
            apply_hidden_penalty(best, entity.hidden), "typo_fallback",
        ))
    return results


def _match_type(entity_id: str, friendly_name: str, domain: str, query: str) -> str:
    """fuzzy_search.py:540-555."""
    if query == entity_id.lower():
        return "exact_id"
    if query == friendly_name.lower():
        return "exact_name"
    if query == domain.lower():
        return "exact_domain"
    if query in entity_id.lower():
        return "partial_id"
    if query in friendly_name.lower():
        return "partial_name"
    return "fuzzy_match"


def exact_entity_match(entity: ScoringEntity, query_lower: str) -> EntityMatch | None:
    """``exact_match`` true: a substring of the entity id or friendly name.

    100 on an equal match, else 80, then the hidden penalty
    (search/entities.py:465-485). Domain, visibility and ``include_hidden``
    exclusion are the caller's.
    """
    entity_id = entity.entity_id
    friendly_name = entity.friendly_name
    if query_lower not in entity_id.lower() and query_lower not in friendly_name.lower():
        return None
    equal = query_lower == entity_id.lower() or query_lower == friendly_name.lower()
    score = EXACT_EQUAL_SCORE if equal else EXACT_SUBSTRING_SCORE
    return EntityMatch(
        entity_id, friendly_name, entity_domain(entity_id),
        apply_hidden_penalty(score, entity.hidden), "exact_match",
    )


def entity_name_score(entity_id: str, friendly_name: str, domain: str, query: str) -> int:
    """The legacy additive name score of configuration records
    (fuzzy_search.py:462-538). It is not capped at 100."""
    score = 0
    entity_lower = entity_id.lower()
    name_lower = friendly_name.lower()
    domain_lower = domain.lower()
    if query == entity_lower:
        score += 100
    elif query == name_lower:
        score += 95
    elif query == domain_lower:
        score += 90
    if query in entity_lower:
        score += 85
    if query in name_lower:
        score += 80
    weighted = (
        max(
            ratio(query, entity_lower),
            partial_ratio(query, entity_lower),
            token_sort_ratio(query, entity_lower),
        ) * 0.7
        + max(
            ratio(query, name_lower),
            partial_ratio(query, name_lower),
            token_sort_ratio(query, name_lower),
        ) * 0.8
        + ratio(query, domain_lower) * 0.6
    )
    score += int(weighted)
    for keyword in _ROOM_KEYWORDS:
        if keyword in query and keyword in name_lower:
            score += 15
    for keyword in _DEVICE_KEYWORDS:
        if keyword in query and (keyword in domain or keyword in name_lower):
            score += 10
    return score


def ratio(query: str, value: str) -> int:
    """SequenceMatcher similarity, 0-100, truncated (fuzzy_search.py:655-657)."""
    return int(SequenceMatcher(None, query, value, autojunk=False).ratio() * 100)


def partial_ratio(query: str, value: str) -> int:
    """Best ratio of the shorter string against any equal-length window."""
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


def token_sort_ratio(query: str, value: str) -> int:
    return ratio(" ".join(sorted(query.split())), " ".join(sorted(value.split())))


def deep_match_score(
    entity_id: str,
    friendly_name: str,
    fuzzy_name_score: int,
    config_score: int,
    query_lower: str,
    exact_match: bool,
    fuzzy_threshold: int = DEFAULT_FUZZY_THRESHOLD,
) -> tuple[int, int, bool]:
    """``(total_score, threshold, match_in_name)`` of a configuration record
    (_scoring.py:15-40). A record matches when ``total_score >= threshold``."""
    if exact_match:
        name_exact = (
            100
            if query_lower in entity_id.lower() or query_lower in friendly_name.lower()
            else 0
        )
        return max(name_exact, config_score), EXACT_MATCH_THRESHOLD, name_exact >= 100
    return (
        max(fuzzy_name_score, config_score),
        fuzzy_threshold,
        fuzzy_name_score >= fuzzy_threshold,
    )


def config_match_score(data: Any, query: str, exact_match: bool) -> int:
    """Score ``query`` against the keys and leaves of a configuration body.

    ``exact_match`` true: 100 when the query is a substring of any key or leaf,
    else 0. False: one BM25 document of every leaf token, normalized to 0-100,
    with a token-ratio fallback at 70 (_scoring.py:42-109). The caller passes
    the lowercased query, as 9.2.0 does.
    """
    if exact_match:
        return _config_exact_score(data, query)
    leaves: list[str] = []
    _collect_string_leaves(data, leaves)
    if not leaves:
        return 0
    query_tokens = tokenize(query)
    if not query_tokens:
        return 0
    doc_tokens: list[str] = []
    for leaf in leaves:
        doc_tokens.extend(tokenize(leaf))
    if not doc_tokens:
        return 0
    scorer = BM25Scorer()
    scorer.fit([doc_tokens])
    raw = scorer.score(query_tokens, 0)
    if raw > 0:
        max_possible = scorer.max_possible_score(query_tokens)
        if max_possible > 0:
            return min(100, round(raw / max_possible * 100))
        return 100
    best = 0
    unique_tokens = set(doc_tokens)
    for query_token in query_tokens:
        for doc_token in unique_tokens:
            best = max(best, ratio(query_token, doc_token))
    return best if best >= CONFIG_TYPO_THRESHOLD else 0


def _collect_string_leaves(data: Any, out: list[str]) -> None:
    """Every key and non-null leaf as a string; iterative, so any depth is read
    (_scoring.py:112-133)."""
    stack = [data]
    while stack:
        node = stack.pop()
        if isinstance(node, dict):
            for key, value in node.items():
                out.append(str(key))
                stack.append(value)
        elif isinstance(node, list):
            stack.extend(node)
        elif isinstance(node, str):
            out.append(node)
        elif node is not None:
            out.append(str(node))


def _config_exact_score(data: Any, query: str) -> int:
    """_scoring.py:135-155."""
    stack = [data]
    while stack:
        node = stack.pop()
        if isinstance(node, dict):
            for key, value in node.items():
                if query in str(key).lower():
                    return 100
                stack.append(value)
        elif isinstance(node, list):
            stack.extend(node)
        elif node is not None and query in str(node).lower():
            return 100
    return 0
