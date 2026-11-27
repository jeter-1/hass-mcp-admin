"""HAMCP-501 W6b: logic mutants of the component-route entity search.

The mutated modules are ``search/scoring.py`` (the tier scorer, the
configuration name and body scores, the match types and the helpers) and the
W6b logic of ``search/entities.py`` (the composed name, the scored texts, the
filters before paging, the order and the reads they need). Each mutant is one
or more textual edits of one module, served by an in-memory import hook (the
files on disk are not changed). For each mutant the search modules and the
three suites below are imported afresh, so every module and suite see the
same classes, and the suites together must fail at least one test:

- ``tests/test_hamcp501_w6b_component_parity.py``;
- ``tests/test_hamcp481_native_search_engine.py``;
- ``tests/test_hamcp481_scoring_parity.py``.

The tuning constants are mutated by the parity suite itself
(``test_each_tuning_constant_is_load_bearing``). The AST and pin tests read
the files on disk, so a mutant does not reach them. ``EQUIVALENT`` holds the
edits that change no observable outcome, each with its reason; they are run
and reported, not required to fail.

This replaces the W1 check of the retired BM25 scorer, whose anchors are
gone; the W1 golden stays as history (``test_w1_history_is_unchanged``).

Usage, from the repository root::

    python -I docs/evidence/hamcp481/scoring_mutation_check.py

Exit status 1 when a non-equivalent mutant survives, 2 when the baseline
fails or an anchor is not found exactly once.
"""

from __future__ import annotations

import importlib
import importlib.abc
import importlib.util
from pathlib import Path
import sys
import time
import unittest

ROOT = Path(__file__).resolve().parents[3]
sys.path[:0] = [str(ROOT / "hass_mcp_engineering_beta"), str(ROOT)]

PACKAGE = "ha_mcp_engineering.search"
SUITES = (
    "tests.test_hamcp501_w6b_component_parity",
    "tests.test_hamcp481_native_search_engine",
    "tests.test_hamcp481_scoring_parity",
)
SEARCH = ROOT / "hass_mcp_engineering_beta/ha_mcp_engineering/search"
# Every module that imports scoring or entities, so none keeps a stale copy.
MODULES = ("scoring", "reply", "subcalls", "entities", "configs", "engine")
SOURCES = {name: (SEARCH / f"{name}.py").read_text(encoding="utf-8") for name in MODULES}

# name -> (module, ((original text, mutated text), ...))
MUTANTS = {
    # scoring.py: the entity tier (search_score.py 49-100).
    "substring_is_exact": ("scoring", ((
        "    if query_lower in text_lower:\n        return SUBSTRING_TIER, 0",
        "    if query_lower in text_lower:\n        return EXACT_TIER, 0"),)),
    "no_separator_exact": ("scoring", ((
        "    if text_norm and query_norm == text_norm:\n        return EXACT_TIER, 0\n", ""),)),
    "no_separator_substring": ("scoring", ((
        "    if text_norm and query_norm in text_norm:\n        return SUBSTRING_TIER, 0\n", ""),)),
    "ratio_threshold_exclusive": ("scoring", ((
        "    if fuzzy and best_ratio >= FUZZY_THRESHOLD:", "    if fuzzy and best_ratio > FUZZY_THRESHOLD:"),)),
    "no_exact_early_return": ("scoring", ((
        "        if tier == EXACT_TIER:\n            return EXACT_TIER\n        if tier == SUBSTRING_TIER:",
        "        if tier == SUBSTRING_TIER:"),)),
    "substring_dropped": ("scoring", ((
        "            best_substring = SUBSTRING_TIER\n", "            pass\n"),)),
    "ratio_over_substring": ("scoring", ((
        "    if best_substring is not None:\n        return best_substring\n",
        "    if best_substring is not None and best_ratio <= best_substring:\n        return best_substring\n"),)),
    "falsy_text_scored": ("scoring", ((
        "        if not text:\n            continue\n        tier, text_ratio = _tier_one_text",
        "        tier, text_ratio = _tier_one_text"),)),
    "text_not_lowered": ("scoring", (("str(text).lower(), fuzzy)", "str(text), fuzzy)"),)),
    "exact_mode_separator": ("scoring", (
        ('    query_norm = sep_normalized(query_lower) if fuzzy else ""',
         "    query_norm = sep_normalized(query_lower)"),
        ('    text_norm = sep_normalized(text_lower) if fuzzy and query_norm else ""',
         '    text_norm = sep_normalized(text_lower) if query_norm else ""'))),
    "exact_mode_ratio": ("scoring", (
        ("    if fuzzy:\n        return None, ratio(query_lower, text_lower)\n    return None, 0",
         "    return None, ratio(query_lower, text_lower)"),
        ("    if fuzzy and best_ratio >= FUZZY_THRESHOLD:", "    if best_ratio >= FUZZY_THRESHOLD:"))),
    # scoring.py: the configuration name tier (search_score.py 103-125).
    "name_substring_80": ("scoring", ((
        "        if query_lower in text_lower:\n            return 100",
        "        if query_lower in text_lower:\n            return 80"),)),
    "name_no_separator": ("scoring", ((
        "            if query_norm and query_norm in sep_normalized(text_lower):\n                return 100\n", ""),)),
    "name_ratio_exclusive": ("scoring", ((
        "    if not exact and best_ratio >= FUZZY_THRESHOLD:", "    if not exact and best_ratio > FUZZY_THRESHOLD:"),)),
    "name_falsy_scored": ("scoring", ((
        "        if not text:\n            continue\n        text_lower = str(text).lower()",
        "        text_lower = str(text).lower()"),)),
    "entity_name_exact": ("scoring", ((
        "    return name_tier(query, (entity_id, friendly_name), exact=False) or 0",
        "    return name_tier(query, (entity_id, friendly_name), exact=True) or 0"),)),
    "entity_name_skips_id": ("scoring", ((
        "    return name_tier(query, (entity_id, friendly_name), exact=False) or 0",
        "    return name_tier(query, (friendly_name,), exact=False) or 0"),)),
    # scoring.py: the match types (search.py 395-472).
    "no_exact_match_type": ("scoring", (('    if exact:\n        return "exact_match"\n', ""),)),
    "no_alias_match_type": ("scoring", ((
        '    if is_alias_driven(query_lower, entity_id, names, aliases):\n        return "alias_match"\n', ""),)),
    "id_tokens_dropped": ("scoring", ((
        "    id_name_tokens = set(tokenize(entity_id))", "    id_name_tokens = set()"),)),
    "id_tail_concat_dropped": ("scoring", ((
        '    id_name_tokens.add(_SPLIT_RE.sub("", id_tail.lower()))\n', ""),)),
    "alias_ignores_name_tokens": ("scoring", (("        id_name_tokens.update(tokenize(text))\n", ""),)),
    "name_concat_dropped": ("scoring", ((
        '        id_name_tokens.add(_SPLIT_RE.sub("", text.lower()))\n', ""),)),
    "alias_tokens_dropped": ("scoring", ((
        "            if token not in id_name_tokens:\n                alias_only.add(token)", "            pass"),)),
    "alias_concat_dropped": ("scoring", ((
        "        if alias_concat and alias_concat not in id_name_tokens:\n            alias_only.add(alias_concat)\n",
        ""),)),
    "alias_query_untokenized": ("scoring", ((
        "    return bool(set(tokenize(query_lower)) & alias_only)", "    return query_lower in alias_only"),)),
    "names_not_lowered": ("scoring", ((
        "    lowered = [str(name).lower() for name in names]", "    lowered = [str(name) for name in names]"),)),
    "no_exact_id": ("scoring", (('    if query_lower == entity_lower:\n        return "exact_id"\n', ""),)),
    "no_exact_name": ("scoring", (('    if query_lower in lowered:\n        return "exact_name"\n', ""),)),
    "no_exact_domain": ("scoring", (('    if query_lower == domain.lower():\n        return "exact_domain"\n', ""),)),
    "no_partial_id": ("scoring", (('    if query_lower in entity_lower:\n        return "partial_id"\n', ""),)),
    "no_partial_name": ("scoring", ((
        '    if any(query_lower in name for name in lowered):\n        return "partial_name"\n', ""),)),
    # scoring.py: the configuration record score (search_score.py 128-151).
    "deep_exact_threshold_fuzzy": ("scoring", ((
        "return max(name_exact, config_score), EXACT_MATCH_THRESHOLD, name_exact >= 100",
        "return max(name_exact, config_score), fuzzy_threshold, name_exact >= 100"),)),
    "deep_exact_in_name_true": ("scoring", ((
        "return max(name_exact, config_score), EXACT_MATCH_THRESHOLD, name_exact >= 100",
        "return max(name_exact, config_score), EXACT_MATCH_THRESHOLD, True"),)),
    "deep_exact_ignores_config": ("scoring", ((
        "return max(name_exact, config_score), EXACT_MATCH_THRESHOLD,", "return name_exact, EXACT_MATCH_THRESHOLD,"),)),
    "deep_exact_name_fuzzy": ("scoring", ((
        "name_exact = name_tier(query_lower, (entity_id, friendly_name), exact=True) or 0",
        "name_exact = name_tier(query_lower, (entity_id, friendly_name), exact=False) or 0"),)),
    "deep_exact_name_skips_id": ("scoring", ((
        "name_exact = name_tier(query_lower, (entity_id, friendly_name), exact=True) or 0",
        "name_exact = name_tier(query_lower, (friendly_name,), exact=True) or 0"),)),
    "deep_fuzzy_in_name_true": ("scoring", (("        fuzzy_name_score >= fuzzy_threshold,\n", "        True,\n"),)),
    "deep_fuzzy_ignores_config": ("scoring", ((
        "        max(fuzzy_name_score, config_score),\n", "        fuzzy_name_score,\n"),)),
    # scoring.py: the configuration body score (search_score.py 154-249).
    "config_exact_skipped": ("scoring", ((
        "    if _config_exact_score(data, query) >= 100:\n        return 100\n", ""),)),
    "config_exact_mode_fuzzy": ("scoring", (("    if exact_match:\n        return 0\n    query_tokens", "    query_tokens"),)),
    "config_ratio_exclusive": ("scoring", ((
        "    return best if best >= FUZZY_THRESHOLD else 0", "    return best if best > FUZZY_THRESHOLD else 0"),)),
    "exact_config_skips_keys": ("scoring", ((
        "                if query in str(key).lower():\n                    return 100\n", ""),)),
    "exact_config_key_case": ("scoring", ((
        "                if query in str(key).lower():", "                if query in str(key):"),)),
    "exact_config_leaf_case": ("scoring", ((
        "        elif node is not None and query in str(node).lower():",
        "        elif node is not None and query in str(node):"),)),
    "config_none_leaf_scored": ("scoring", ((
        "        elif node is not None and query in str(node).lower():",
        "        elif query in str(node).lower():"),)),
    "fuzzy_config_skips_keys": ("scoring", (("                out.append(str(key))\n", ""),)),
    "leaf_none_collected": ("scoring", ((
        "        elif node is not None:\n            out.append(str(node))", "        else:\n            out.append(str(node))"),)),
    "leaf_scalars_skipped": ("scoring", ((
        "        elif node is not None:\n            out.append(str(node))", "        elif False:\n            out.append(str(node))"),)),
    # scoring.py: the helpers.
    "hidden_floor_removed": ("scoring", ((
        "        return max(0, value - HIDDEN_SCORE_PENALTY)", "        return value - HIDDEN_SCORE_PENALTY"),)),
    "ratio_rounded": ("scoring", ((
        "    return int(SequenceMatcher(None, query, value, autojunk=False).ratio() * 100)",
        "    return round(SequenceMatcher(None, query, value, autojunk=False).ratio() * 100)"),)),
    "ratio_autojunk": ("scoring", (("autojunk=False).ratio() * 100)", "autojunk=True).ratio() * 100)"),)),
    "partial_ratio_short_window": ("scoring", ((
        "    for start in range(len(longer) - window + 1):", "    for start in range(len(longer) - window):"),)),
    "partial_ratio_empty_scored": ("scoring", (("    if not query or not value:\n        return 0\n", ""),)),
    "tokenize_keeps_empties": ("scoring", ((
        "    return [token for token in _SPLIT_RE.split(text.lower()) if token]",
        "    return _SPLIT_RE.split(text.lower())"),)),
    "tokenize_keeps_case": ("scoring", ((
        "    return [token for token in _SPLIT_RE.split(text.lower()) if token]",
        "    return [token for token in _SPLIT_RE.split(text) if token]"),)),
    "sep_normalized_join_empty": ("scoring", (('    return " ".join(tokenize(text))', '    return "".join(tokenize(text))'),)),
    # entities.py: the next name parts (Core entity_registry.py 252-260,
    # device_registry.py 735-738 and 884-889).
    "entity_part_device_first": ("entities", ((
        '    if row["area_id"] is not None:\n        return "area"\n'
        '    return "device" if row["device_id"] is not None else None',
        '    if row["device_id"] is not None:\n        return "device"\n'
        '    return "area" if row["area_id"] is not None else None'),)),
    "entity_part_ignores_area": ("entities", ((
        '    if row["area_id"] is not None:\n        return "area"\n    return "device"', '    return "device"'),)),
    "device_part_ignores_area": ("entities", ((
        '    if row["area_id"] is not None:\n        return "area"\n    return None if row["parent_device_id"]',
        '    return None if row["parent_device_id"]'),)),
    "device_part_child_none": ("entities", ((
        '    return None if row["parent_device_id"] is None else "parent_device"', "    return None"),)),
    "entity_parts_unchecked": ("entities", ((
        '    if not _parts_agree(registry, _entity_part):\n'
        '        raise SearchError("source_unavailable", source="entity_registry")\n', ""),)),
    "device_parts_unchecked": ("entities", ((
        "    if _usable(result, fields) and _parts_agree(result.value, _device_part):",
        "    if _usable(result, fields):"),)),
    "parts_agree_always": ("entities", ((
        '    return all("next_name_part" not in row or row["next_name_part"] == derive(row)\n'
        "               for row in rows.values())", "    return True"),)),
    "absent_part_is_drift": ("entities", ((
        '"next_name_part" not in row or row["next_name_part"] == derive(row)',
        'row.get("next_name_part") == derive(row)'),)),
    # entities.py: the device rows the name and the texts read.
    "naming_devices_optional": ("entities", ((
        '    if required:\n        raise SearchError("source_unavailable", source="device_registry")\n    return None',
        "    return None"),)),
    "naming_devices_always_required": ("entities", ((
        '    if required:\n        raise SearchError("source_unavailable", source="device_registry")',
        '    if True:\n        raise SearchError("source_unavailable", source="device_registry")'),)),
    "listing_reads_text_fields": ("entities", ((
        "_DEVICE_TEXT_FIELDS if query_mode else _DEVICE_NAME_FIELDS,", "_DEVICE_TEXT_FIELDS,"),)),
    "query_reads_name_fields": ("entities", ((
        "_DEVICE_TEXT_FIELDS if query_mode else _DEVICE_NAME_FIELDS,", "_DEVICE_NAME_FIELDS,"),)),
    "text_fields_drop_model": ("entities", ((
        '_DEVICE_TEXT_FIELDS = (*_DEVICE_NAME_FIELDS, "labels", "manufacturer", "model")',
        '_DEVICE_TEXT_FIELDS = (*_DEVICE_NAME_FIELDS, "labels", "manufacturer")'),)),
    "text_fields_drop_manufacturer": ("entities", ((
        '_DEVICE_TEXT_FIELDS = (*_DEVICE_NAME_FIELDS, "labels", "manufacturer", "model")',
        '_DEVICE_TEXT_FIELDS = (*_DEVICE_NAME_FIELDS, "labels", "model")'),)),
    "text_fields_drop_labels": ("entities", ((
        '_DEVICE_TEXT_FIELDS = (*_DEVICE_NAME_FIELDS, "labels", "manufacturer", "model")',
        '_DEVICE_TEXT_FIELDS = (*_DEVICE_NAME_FIELDS, "manufacturer", "model")'),)),
    "device_name_fields_drop_name_by_user": ("entities", ((
        '_DEVICE_NAME_FIELDS = ("name", "name_by_user", "area_id", "parent_device_id", "next_name_part")',
        '_DEVICE_NAME_FIELDS = ("name", "area_id", "parent_device_id", "next_name_part")'),)),
    "entity_name_fields_drop_original_name": ("entities", ((
        '_ENTITY_NAME_FIELDS = ("name", "original_name", "area_id", "device_id", "next_name_part")',
        '_ENTITY_NAME_FIELDS = ("name", "area_id", "device_id", "next_name_part")'),)),
    # entities.py: the composed name (Core ``_async_get_full_entity_name``).
    "empty_name_falls_back": ("entities", ((
        '    entity_name = entry["name"] if entry["name"] is not None else entry["original_name"]',
        '    entity_name = entry["name"] or entry["original_name"]'),)),
    "original_name_ignored": ("entities", ((
        '    entity_name = entry["name"] if entry["name"] is not None else entry["original_name"]',
        '    entity_name = entry["name"]'),)),
    "device_part_unconditional": ("entities", ((
        '    if _entity_part(entry) == "device":', '    if entry["device_id"] is not None:'),)),
    "device_name_by_user_ignored": ("entities", ((
        '            device_name = device["name_by_user"] or device["name"]', '            device_name = device["name"]'),)),
    "child_part_unconditional": ("entities", ((
        '            if _device_part(device) == "parent_device":', '            if device["parent_device_id"] is not None:'),)),
    "grandparent_part_kept": ("entities", ((
        '                if parent is not None and parent["parent_device_id"] is None:',
        "                if parent is not None:"),)),
    "parent_part_dropped": ("entities", ((
        '                    parent_name = parent["name_by_user"] or parent["name"]', "                    pass"),)),
    "parent_name_by_user_ignored": ("entities", ((
        '                    parent_name = parent["name_by_user"] or parent["name"]',
        '                    parent_name = parent["name"]'),)),
    "parts_order_reversed": ("entities", ((
        "(parent_name, device_name, entity_name) if part)", "(entity_name, device_name, parent_name) if part)"),)),
    "empty_parts_joined": ("entities", ((
        "(parent_name, device_name, entity_name) if part)",
        "(parent_name, device_name, entity_name) if part is not None)"),)),
    # entities.py: the record name.
    "no_friendly_fallback": ("entities", ((
        "        return composed_name(registry.get(entity_id), devices) or friendly_name",
        "        return composed_name(registry.get(entity_id), devices)"),)),
    "listing_name_without_devices": ("entities", ((
        "        if devices is None:\n            return None\n        return composed_name",
        "        if devices is None:\n            return friendly_name\n        return composed_name"),)),
    # entities.py: the scored texts (search.py 475-564).
    "entity_id_not_scored": ("entities", ((
        "        texts = [entity_id, name, friendly_name, *sorted(aliases),",
        "        texts = [name, friendly_name, *sorted(aliases),"),)),
    "name_not_scored": ("entities", ((
        "        texts = [entity_id, name, friendly_name, *sorted(aliases),",
        "        texts = [entity_id, friendly_name, *sorted(aliases),"),)),
    "friendly_name_not_scored": ("entities", ((
        "        texts = [entity_id, name, friendly_name, *sorted(aliases),",
        "        texts = [entity_id, name, *sorted(aliases),"),)),
    "aliases_not_scored": ("entities", ((
        "        texts = [entity_id, name, friendly_name, *sorted(aliases),",
        "        texts = [entity_id, name, friendly_name,"),)),
    "domain_scored": ("entities", ((
        "        texts = [entity_id, name, friendly_name, *sorted(aliases),",
        "        texts = [entity_id, scoring.entity_domain(entity_id), name, friendly_name, *sorted(aliases),"),)),
    "labels_scored_by_id": ("entities", ((
        '                 *((self.labels.get(label_id) or {}).get("name") or label_id\n'
        "                   for label_id in sorted(label_ids)),",
        "                 *sorted(label_ids),"),)),
    "orphan_label_not_scored": ("entities", ((
        '(self.labels.get(label_id) or {}).get("name") or label_id\n                   for label_id',
        '(self.labels.get(label_id) or {}).get("name")\n                   for label_id'),)),
    "entity_labels_not_scored": ("entities", ((
        '            label_ids = set(entry["labels"])\n            device_id', "            device_id"),)),
    "device_labels_not_scored": ("entities", ((
        '                label_ids |= set(device["labels"])\n                # A child', "                # A child"),)),
    "device_texts_not_scored": ("entities", (("                 *(text for text in device_texts if text)]", "                 ]"),)),
    "device_name_by_user_not_scored": ("entities", ((
        '("name_by_user", "name", "manufacturer", "model")]', '("name", "manufacturer", "model")]'),)),
    "device_name_not_scored": ("entities", ((
        '("name_by_user", "name", "manufacturer", "model")]', '("name_by_user", "manufacturer", "model")]'),)),
    "manufacturer_not_scored": ("entities", ((
        '("name_by_user", "name", "manufacturer", "model")]', '("name_by_user", "name", "model")]'),)),
    "model_not_scored": ("entities", ((
        '("name_by_user", "name", "manufacturer", "model")]', '("name_by_user", "name", "manufacturer")]'),)),
    "entity_area_ignored": ("entities", ((
        "            area_id = effective_entity_area_id(entry, self.device_areas)", '            area_id = entry["area_id"]'),)),
    "area_not_scored": ("entities", ((
        '        if area is not None and area["name"]:\n            texts.append(area["name"])\n', ""),)),
    "floor_not_scored": ("entities", ((
        '        if floor is not None and floor["name"]:\n            texts.append(floor["name"])\n', ""),)),
    "area_aliases_scored": ("entities", ((
        '            texts.append(area["name"])\n', '            texts.extend((area["name"], *area["aliases"]))\n'),)),
    "floor_aliases_scored": ("entities", ((
        '            texts.append(floor["name"])\n', '            texts.extend((floor["name"], *floor["aliases"]))\n'),)),
    # entities.py: the filters before paging and the order (search.py 176-189, 340-392).
    "hidden_not_filtered": ("entities", ((
        "        return (entity_id not in denied\n                and (domain",
        "        return (True\n                and (domain"),)),
    "domain_filter_dropped": ("entities", ((
        "                and (domain is None or scoring.entity_domain(entity_id) == domain)\n", ""),)),
    "state_filter_dropped": ("entities", ((
        '                and (state_filter is None or (state or "").lower() == state_filter))', "                )"),)),
    "state_case_kept": ("entities", ((
        '(state or "").lower() == state_filter))', '(state or "") == state_filter))'),)),
    "order_tiebreak_dropped": ("entities", ((
        '    return sorted(records, key=lambda record: (-record["score"], record["entity_id"]))',
        '    return sorted(records, key=lambda record: -record["score"])'),)),
    "order_ascending": ("entities", ((
        '    return sorted(records, key=lambda record: (-record["score"], record["entity_id"]))',
        '    return sorted(records, key=lambda record: (record["score"], record["entity_id"]))'),)),
    # entities.py: the query scoring and the listing records.
    "query_not_lowered": ("entities", ((
        "        query_lower = request.query.strip().lower()", "        query_lower = request.query.strip()"),)),
    "exact_flag_inverted": ("entities", (("fuzzy=not request.exact_match)", "fuzzy=request.exact_match)"),)),
    "match_type_exact_inverted": ("entities", ((
        "                exact=request.exact_match)", "                exact=not request.exact_match)"),)),
    "texts_skip_composed_name": ("entities", ((
        "joins.texts(entity_id, name, row.friendly_name, known),",
        "joins.texts(entity_id, row.friendly_name, row.friendly_name, known),"),)),
    "match_names_friendly_only": ("entities", ((
        "query_lower, entity_id, (name, row.friendly_name), entity_domain, known,",
        "query_lower, entity_id, (row.friendly_name,), entity_domain, known,"),)),
    "match_names_composed_only": ("entities", ((
        "query_lower, entity_id, (name, row.friendly_name), entity_domain, known,",
        "query_lower, entity_id, (name,), entity_domain, known,"),)),
    "query_hidden_unpenalized": ("entities", ((
        "scoring.apply_hidden_penalty(tier, entity_id in hidden),", "tier,"),)),
    "listing_hidden_unpenalized": ("entities", ((
        "row.state, scoring.apply_hidden_penalty(100, entity_id in hidden),", "row.state, 100,"),)),
    "listing_label_mode": ("entities", ((
        '        label = "area_match" if current == AREA_ONLY else current', "        label = current"),)),
    # entities.py: the reads the query texts need.
    "entity_labels_unchecked": ("entities", ((
        '    if mode(request) in QUERY_MODES or "labels" in _requested(request, ENRICHMENT_FIELDS):',
        '    if "labels" in _requested(request, ENRICHMENT_FIELDS):'),)),
    "query_label_registry_unread": ("entities", ((
        '        names |= {"area_registry", "floor_registry", "label_registry"}',
        '        names |= {"area_registry", "floor_registry"}'),)),
    "joins_area_name_unchecked": ("entities", ((
        'required_rows(sources, "area_registry", ("name", "floor_id")),',
        'required_rows(sources, "area_registry", ("floor_id",)),'),)),
    "joins_floor_id_unchecked": ("entities", ((
        'required_rows(sources, "area_registry", ("name", "floor_id")),',
        'required_rows(sources, "area_registry", ("name",)),'),)),
    "joins_floor_name_unchecked": ("entities", ((
        'required_rows(sources, "floor_registry", ("name",)),', 'required_rows(sources, "floor_registry", ()),'),)),
    "joins_label_name_unchecked": ("entities", ((
        'required_rows(sources, "label_registry", ("name",)))', 'required_rows(sources, "label_registry", ()))'),)),
}
# Run and reported, but not required to fail a test.
EQUIVALENT = {
    # Once ``best`` is 100 no later window can raise it; the break only
    # saves work.
    "partial_ratio_no_early_exit": ("scoring", (("        if best == 100:\n            break\n", ""),)),
    # ``EntityRequest`` keeps a stripped query (its own validation), so a
    # second strip changes nothing.
    "query_not_stripped": ("entities", ((
        "        query_lower = request.query.strip().lower()", "        query_lower = request.query.lower()"),)),
}


class _Mutant(importlib.abc.MetaPathFinder, importlib.abc.Loader):
    """Serves one module from mutated source text; writes no bytecode."""

    def __init__(self, module: str, source: str) -> None:
        self.fullname = f"{PACKAGE}.{module}"
        self.path = str(SEARCH / f"{module}.py")
        self.source = source

    def find_spec(self, fullname, path, target=None):
        if fullname != self.fullname:
            return None
        return importlib.util.spec_from_file_location(fullname, self.path, loader=self)

    def create_module(self, spec):
        return None

    def exec_module(self, module) -> None:
        exec(compile(self.source, self.path, "exec"), module.__dict__)


def fresh_suites(target: str | None, source: str | None) -> list:
    """Import the search modules and the suites afresh, ``target`` mutated."""

    package = importlib.import_module(PACKAGE)
    for name in MODULES:
        sys.modules.pop(f"{PACKAGE}.{name}", None)
        if name in vars(package):
            delattr(package, name)
    tests = sys.modules.get("tests")
    for suite_name in SUITES:
        sys.modules.pop(suite_name, None)
        if tests is not None and suite_name.rpartition(".")[2] in vars(tests):
            delattr(tests, suite_name.rpartition(".")[2])
    finder = _Mutant(target, source) if target is not None else None
    path = list(sys.path)
    if finder is not None:
        sys.meta_path.insert(0, finder)
    try:
        suites = [importlib.import_module(suite_name) for suite_name in SUITES]
    finally:
        sys.path[:] = path
        if finder is not None:
            sys.meta_path.remove(finder)
    if finder is not None:
        module = sys.modules[finder.fullname]
        # Each suite binds both mutated modules at import.
        if module.__spec__.loader is not finder or any(
                getattr(suite, target, module) is not module for suite in suites):
            raise RuntimeError("mutant not loaded")
    return suites


def failures_for(target: str | None, mutated: str | None) -> int:
    result = unittest.TestResult()
    loader = unittest.TestLoader()
    for suite in fresh_suites(target, mutated):
        loader.loadTestsFromModule(suite).run(result)
    return len(result.failures) + len(result.errors)


def mutated_source(name: str, target: str, edits: tuple) -> str | None:
    source = SOURCES[target]
    for original, mutated in edits:
        if source.count(original) != 1:
            print(f"{name}: anchor not found once: {original!r}", file=sys.stderr)
            return None
        source = source.replace(original, mutated)
    return source


def main() -> int:
    started = time.monotonic()
    baseline = failures_for(None, None)
    print(f"baseline: failing tests={baseline} ({time.monotonic() - started:.1f}s)")
    if baseline:
        return 2
    survivors = []
    for group, mutants in (("mutant", MUTANTS), ("equivalent", EQUIVALENT)):
        for name, (target, edits) in mutants.items():
            source = mutated_source(name, target, edits)
            if source is None:
                return 2
            started = time.monotonic()
            count = failures_for(target, source)
            print(f"{group} {name}: failing tests={count} ({time.monotonic() - started:.1f}s)")
            if group == "mutant" and count == 0:
                survivors.append(name)
    print(f"mutants: {len(MUTANTS)}, equivalent: {len(EQUIVALENT)}")
    print("survivors: " + (", ".join(survivors) or "none"))
    return 1 if survivors else 0


if __name__ == "__main__":
    raise SystemExit(main())
