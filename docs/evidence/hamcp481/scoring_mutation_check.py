"""HAMCP-481 W1: logic mutants of ``search/scoring.py`` against the parity tests.

Each mutant is one textual edit of the port, compiled in memory (the file on
disk is not changed). ``tests/test_hamcp481_scoring_parity.py`` runs against
each one, and the mutant must fail at least one test. Constant mutants are in
the test itself (``test_each_tuning_constant_is_load_bearing``).

Usage, from the repository root::

    python -I docs/evidence/hamcp481/scoring_mutation_check.py

Exit status 1 when a non-equivalent mutant survives.
"""

from __future__ import annotations

from pathlib import Path
import sys
import types
import unittest

ROOT = Path(__file__).resolve().parents[3]
sys.path[:0] = [str(ROOT / "hass_mcp_engineering_beta"), str(ROOT / "tests"), str(ROOT)]

import test_hamcp481_scoring_parity as parity  # noqa: E402

SOURCE = (ROOT / "hass_mcp_engineering_beta/ha_mcp_engineering/search/scoring.py").read_text(
    encoding="utf-8")

# name -> (original text, mutated text)
MUTANTS = {
    "penalty_before_gate": (
        "        raw_score = min(100, round(raw / theoretical_max * 100))\n",
        "        raw_score = apply_hidden_penalty(min(100, round(raw / theoretical_max * 100)),"
        " entities[index].hidden)\n"),
    "gate_inclusive_to_exclusive": (
        "        if raw_score < threshold:", "        if raw_score <= threshold:"),
    "typo_coverage_half_excluded": (
        "            if covered * 2 < len(distinct):", "            if covered * 2 <= len(distinct):"),
    "no_alias_match_type": ("        if query_token_set & alias_only[index]:", "        if False:"),
    "no_entity_id_tiebreak": (
        "    return (-match.score, match.entity_id)", "    return (-match.score, '')"),
    "fallback_despite_bm25_hit": (
        "    if not matches and not any(raw > 0 for raw in raw_scores):", "    if not matches:"),
    "no_tail_concat_token": (
        "    if tail_concat:\n        tokens.append(tail_concat)",
        "    if False:\n        tokens.append(tail_concat)"),
    "unknown_token_idf_zero": (
        "        return sum(self._idf.get(token, highest) for token in query_tokens)",
        "        return sum(self._idf.get(token, 0.0) for token in query_tokens)"),
    "exact_substring_90": ("EXACT_SUBSTRING_SCORE = 80", "EXACT_SUBSTRING_SCORE = 90"),
    "exact_config_skips_keys": (
        "                if query in str(key).lower():\n                    return 100\n", ""),
    "fuzzy_config_skips_keys": ("                out.append(str(key))\n", ""),
    "name_partial_id_elif": (
        "    if query in entity_lower:\n        score += 85",
        "    elif query in entity_lower:\n        score += 85"),
    "no_exact_domain_type": ('    if query == domain.lower():\n        return "exact_domain"\n', ""),
    "deep_exact_always_in_name": (
        "EXACT_MATCH_THRESHOLD, name_exact >= 100", "EXACT_MATCH_THRESHOLD, True"),
    "query_not_stripped": (
        "    query_lower = query.lower().strip()", "    query_lower = query.lower()"),
    "hidden_floor_removed": (
        "        return max(0, value - HIDDEN_SCORE_PENALTY)", "        return value - HIDDEN_SCORE_PENALTY"),
    "single_short_token_falls_back": (
        "    if len(distinct) == 1 and len(query_tokens[0]) < TYPO_FALLBACK_MIN_TOKEN_LENGTH:",
        "    if False:"),
    "config_bm25_uncapped": (
        "            return min(100, round(raw / max_possible * 100))",
        "            return round(raw / max_possible * 100)"),
}
# Equivalent: the early exit at 100 only saves work, since no ratio exceeds 100.
EQUIVALENT = {
    "partial_ratio_no_early_exit": ("        if best == 100:\n            break", "        pass"),
}


def failures_for(mutated: str) -> int:
    module = types.ModuleType("hamcp481_scoring_mutant")
    sys.modules[module.__name__] = module
    exec(compile(mutated, module.__name__, "exec"), module.__dict__)
    parity.scoring = module
    parity.ENTITIES = tuple(parity.scoring_entity(record) for record in parity.GOLDEN["entities"])
    result = unittest.TestResult()
    unittest.TestLoader().loadTestsFromModule(parity).run(result)
    return len(result.failures) + len(result.errors)


def main() -> int:
    survivors = []
    for group, mutants in (("mutant", MUTANTS), ("equivalent", EQUIVALENT)):
        for name, (original, mutated) in mutants.items():
            if SOURCE.count(original) != 1:
                print(f"{name}: anchor not found once", file=sys.stderr)
                return 2
            count = failures_for(SOURCE.replace(original, mutated))
            print(f"{group} {name}: failing tests={count}")
            if group == "mutant" and count == 0:
                survivors.append(name)
    print("survivors: " + (", ".join(survivors) or "none"))
    return 1 if survivors else 0


if __name__ == "__main__":
    raise SystemExit(main())
