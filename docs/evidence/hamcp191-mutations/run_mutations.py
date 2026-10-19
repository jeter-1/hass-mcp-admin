"""HAMCP-191 mutation runner (offline, deterministic).

Each mutation is a set of exact textual replacements (every old fragment must
match exactly once in its file), applied to a disposable copy of the working
tree outside the repository. The named test modules then run; a mutation is
killed when they fail. Nothing in the repository is modified. The machinery
is the HAMCP-162 and HAMCP-171 runners'.

Coverage: the six mutations of the HAMCP-191 brief (``U*``), design section 5
``M1``-``M18`` (several with variants), the D11 allow-list and tolerance
mutations (``D11*``), the D3 and Finding 11 counters (``C*``), the Codex
review's probes ``R1``/``R2`` and findings ``H1*``, ``M1*`` and ``L1``, and the
owner's per-line Core log direction (``CL1``), Codex H3 (``H3``) and the
Direction A plan-persistence chokepoint (``A*``; ``A1b`` runs only the
runtime writer spy).

``M5`` as the design words it ("dedup by text instead of by type") is an
equivalent mutant here and is listed with ``equivalent`` and its argument:
a string piece that carries marker text means the original carried marker
text, so ``_scan`` returns the collision before deduplication runs. ``M5b``
is the reachable dedup mutation and must be killed.

Usage:
    python docs/evidence/hamcp191-mutations/run_mutations.py \
        --work ~/tmp/hamcp191-runs/mut --output docs/evidence/hamcp191-mutations/results.json

Optional ``--only ID [ID ...]`` reruns a subset; ``--workers N`` sets the
number of tree copies run concurrently (default 2).
"""

from __future__ import annotations

import argparse
import concurrent.futures
import json
import os
import queue
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2].parent
E = "hass_mcp_engineering_beta/ha_mcp_engineering"
SAN = f"{E}/sanitization.py"
RESOURCES = f"{E}/governance/resources.py"
GATEWAY = f"{E}/providers/upstream_read_gateway.py"
DASHBOARD = f"{E}/providers/upstream_dashboard.py"
LOGBOOK = f"{E}/clients/logbook.py"
SERVICE = f"{E}/governance/service.py"
LOGGING = f"{E}/logging_config.py"
CORE_LOGS = f"{E}/providers/core_logs.py"
STORAGE = f"{E}/governance/storage.py"
T_SHARED_FILE = "tests/test_hamcp191_shared_sanitizer.py"
T_FAMILIES_FILE = "tests/test_hamcp191_families.py"

T_SHARED = "tests.test_hamcp191_shared_sanitizer"
T_FAMILIES = "tests.test_hamcp191_families"
T_COMPAT = "tests.test_hamcp191_compatibility"
ALL = (T_SHARED, T_FAMILIES, T_COMPAT)
T_CHOKEPOINT = "tests.test_hamcp191_plan_chokepoint"
# The chokepoint module without its runtime spy, which only CHOKEPOINT_SPY runs.
CHOKEPOINT = tuple(
    f"{T_CHOKEPOINT}.{name}" for name in (
        "ApplyTimeEvidenceTests", "ChokepointUnitTests", "CoreLogSpanningTests",
        "ExemptSchemaTests", "IdentifierCreationTests", "WriterInventoryTests",
    )
)
CHOKEPOINT_SPY = (f"{T_CHOKEPOINT}.RuntimeWriterSpyTests",)
TEXT_START = (
    "        validator = self.exempt(pattern)\n"
    "        if validator is not None:\n"
)


def m(identifier, item, replacements, tests=ALL, *, equivalent=None):
    entry = {
        "id": identifier,
        "design_item": item,
        "replacements": [list(triple) for triple in replacements],
        "tests": list(tests),
    }
    if equivalent:
        entry["equivalent"] = equivalent
    return entry


SCAN_START = "        categories: set[str] = set()\n        pieces: list[Any] = [value]\n"
SCAN_COLLISION = (
    "        if not self._LINE_WINDOW and marker_like_text(value):\n"
    "            categories.add(MARKER_COLLISION_CATEGORY)\n"
    "            return MARKER_COLLISION, frozenset(categories), True\n"
)
IDENTITY_HIT = (
    "        if self.registry is not None and self.registry.output(value, self.known_secrets):\n"
    "            return self._truncate(value)\n"
)

MUTATIONS = [
    # The six mutations of the brief.
    m("U1-trust-marker-by-text", "brief: trust a marker by text", [(SAN, IDENTITY_HIT, (
        "        if REDACTION_MARKER.fullmatch(value):\n"
        "            return value\n" + IDENTITY_HIT
    ))]),
    m("U2-skip-identity-check", "brief: skip the identity check (keep a string whose text was recorded)", [(
        SAN,
        "        if self.registry is not None and self.registry.output(value, self.known_secrets):\n",
        "        if self.registry is not None and any(\n"
        "            value == kept for kept, _secrets in self.registry._outputs.values()\n"
        "        ):\n",
    )]),
    m("U3-M3-collision-before-rules", "brief: collision decided before the rules; design M3", [
        (SAN, SCAN_START, (
            "        categories: set[str] = set()\n"
            "        if marker_like_text(value):\n"
            "            return MARKER_COLLISION, frozenset({MARKER_COLLISION_CATEGORY}), True\n"
            "        pieces: list[Any] = [value]\n"
        )),
    ]),
    m("U4a-M10-raw-key-written-back", "brief: the raw-key branch writes the key back; design M10 (S8)", [(
        SAN,
        "                    if safe_key is None:\n"
        "                        safe_key = self._own(SANITIZATION_FAILURE_MARKER)\n",
        "                    safe_key = str(raw_key)\n",
    )]),
    m("U4b-unscanned-key-written-back", "brief: the raw-key branch writes an unscanned key back", [(
        SAN,
        "                        safe_key = self._own(SANITIZATION_FAILURE_MARKER)\n",
        "                        safe_key = str(raw_key)\n",
    )]),
    m("U5a-gateway-complete-despite-redaction", "brief: \"complete\" despite redaction (generic upstream read)", [(
        GATEWAY,
        "                    or upstream_partial\n                    or redaction is not None\n",
        "                    or upstream_partial\n",
    )]),
    m("U5b-canary-complete-despite-redaction", "brief: \"complete\" despite redaction (held-read canary)", [(
        GATEWAY,
        "                if truncated or upstream_partial or redacted\n",
        "                if truncated or upstream_partial\n",
    )]),
    m("U5c-dashboard-list-complete-despite-redaction", "brief: \"complete\" despite redaction (dashboard list)", [(
        DASHBOARD,
        "                if truncated or malformed or redactions.applied\n",
        "                if truncated or malformed\n",
    )]),
    m("U5d-dashboard-get-complete-despite-redaction", "brief: \"complete\" despite redaction (dashboard get)", [(
        DASHBOARD,
        "                or sanitation.truncated_field_count\n                or redactions.applied\n",
        "                or sanitation.truncated_field_count\n",
    )]),
    m("U6-matrix-cell-removed", "brief: a matrix cell removed", [(
        T_SHARED_FILE,
        "        if position != \"sensitive_key_text\" or wrapper == \"folded\"\n    ]\n",
        "        if position != \"sensitive_key_text\" or wrapper == \"folded\"\n    ][1:]\n",
    )], (T_SHARED,)),
    # Design section 5.
    m("M1-marker-split-restored", "M1: restore the marker-split branch in the string path", [
        (SAN, "        pieces: list[Any] = [value]\n", (
            "        pieces: list[Any] = [\n"
            "            _Emitted(part) if REDACTION_MARKER.fullmatch(part) else part\n"
            "            for part in re.split(\"(\" + REDACTION_MARKER.pattern + \")\", value)\n"
            "            if part\n"
            "        ]\n"
        )),
        (SAN, "        if not self._LINE_WINDOW and marker_like_text(value):\n            categories.add(",
         "        if not self._LINE_WINDOW and marker_like_text(REDACTION_MARKER.sub(\"\", value)):\n"
         "            categories.add("),
    ]),
    m("M2a-identity-false-accepted", "M2: allow identity_markers_only=False again", [(
        SAN,
        "    if not identity_markers_only:\n",
        "    if False:\n",
    )]),
    m("M2b-identity-default-false", "M2: default identity_markers_only to False", [(
        SAN,
        "    identity_markers_only: bool = True,\n    segment_memo: SegmentMemo | None = None,\n    registry:",
        "    identity_markers_only: bool = False,\n    segment_memo: SegmentMemo | None = None,\n    registry:",
    )]),
    m("M4a-rule-dropped-from-list", "M4: drop one entry from the strict rule list", [(
        SAN, "    (_WEBHOOK_PROSE, \"webhook_identifier\", \"prefix\"),\n", "",
    )]),
    m("M4b-rule-skipped-by-pass", "M4: the strict pass skips one rule of the list", [(
        SAN, "        rules = [*_SEGMENT_RULES, *(", "        rules = [*_SEGMENT_RULES[:-1], *(",
    )]),
    m("M5-dedup-by-text", "M5: dedup by text instead of by type", [(
        SAN,
        "            if type(piece) is _Emitted:\n",
        "            if type(piece) is _Emitted or (type(piece) is str and REDACTION_MARKER.fullmatch(piece)):\n"
        "                piece = piece if type(piece) is _Emitted else _Emitted(piece)\n",
    )], equivalent=(
        "A str piece equal to marker text exists only when the original string held marker "
        "text; marker_like_text then returns the collision before deduplication runs."
    )),
    m("M5b-dedup-merges-different-markers", "M5: dedup merges markers of different types", [(
        SAN,
        "                if last is not None and last.text == piece.text and not \"\".join(gap).strip():\n",
        "                if last is not None and not \"\".join(gap).strip():\n",
    )]),
    m("M6a-record-accepts-equal-text", "M6: identity record accepts equal text", [(
        SAN,
        "        entry = self._outputs.get(id(value))\n        if entry is None or entry[0] is not value:\n",
        "        entry = next((item for item in self._outputs.values() if item[0] == value), None)\n"
        "        if entry is None:\n",
    )]),
    m("M6b-record-skips-secret-check", "M6/D4: identity skips a secret the producing pass lacked", [(
        SAN, "        return not any(\n            secret and secret not in entry[1]",
        "        return True or not any(\n            secret and secret not in entry[1]",
    )]),
    m("M7-record-accepts-any-str", "M7: identity record accepts any str", [(
        SAN,
        "        if type(value) is not str:\n            return False\n        entry = self._outputs.get(id(value))\n",
        "        if type(value) is str:\n            return True\n        entry = self._outputs.get(id(value))\n",
    )]),
    m("M7b-inherited-record-grows", "M7/D4: a request writes into the inherited snapshot record", [(
        SAN,
        "        self._outputs[id(value)] = (\n",
        "        if self._inherits is not None:\n"
        "            self._inherits.register_output(value, known_secrets)\n"
        "        self._outputs[id(value)] = (\n",
    )]),
    m("M8-record-shared-across-requests", "M8: identity record stored globally across requests", [(
        f"{E}/request_context.py",
        "        default_factory=ReferenceMarkerRegistry,\n",
        "        default_factory=lambda _shared=ReferenceMarkerRegistry(): _shared,\n",
    )]),
    m("M9-sensitive-key-text-unscanned", "M9: sensitive-key text not scanned (S9)", [(
        SAN,
        "                    safe_key = self._key_text(raw_key)\n",
        "                    safe_key = str(raw_key) if key_category else self._key_text(raw_key)\n",
    )]),
    m("M11-truncation-outside-record", "M11: truncation suffix appended outside the identity record", [(
        SAN,
        "        return self._own(value[: self.max_string] + TRUNCATION_SUFFIX)\n",
        "        return value[: self.max_string] + TRUNCATION_SUFFIX\n",
    )]),
    m("M12-post-pass-slice", "M12: a post-pass slice instead of the bounded truncation", [(
        SAN, IDENTITY_HIT, IDENTITY_HIT.replace(
            "            return self._truncate(value)\n",
            "            return value[: self.max_string] if self.max_string else value\n",
        ),
    )]),
    m("M13-x2-marker-text", "M13: X2 emits marker text again", [(
        f"{E}/incident/normalization.py",
        "        AUTHORIZATION_WITHHELD,\n        text,\n",
        "        \"[REDACTED:header]\",\n        text,\n",
    )]),
    m("M14-gate-ignores-collision", "M14: the persistence gate ignores the collision category", [(
        RESOURCES,
        "    if sanitized.failed_closed or sanitized.redaction_applied:\n",
        "    if sanitized.failed_closed or (\n"
        "        sanitized.redaction_applied and not sanitized.marker_collision_count\n"
        "    ):\n",
    )]),
    m("M15a-site-removed-from-table", "M15: remove a site from SANITIZER_SITES", [(
        T_SHARED_FILE,
        "    (\"clients/logbook.py\", \"project\", \"sanitize_untrusted_data\"): (1, \"logbook\"),\n",
        "",
    )], (T_SHARED,)),
    m("M15b-unlisted-call-added", "M15: add an unlisted sanitizer call", [(
        LOGBOOK,
        "def _json(value):\n",
        "def _unlisted(value):\n    return sanitize_untrusted_data(value).value\n\n\ndef _json(value):\n",
    )], (T_SHARED,)),
    m("M16a-family-injector-removed", "M16: remove a family injector", [(
        T_FAMILIES_FILE, "    FAMILY = \"logbook\"\n", "",
    )], (T_SHARED, T_FAMILIES)),
    m("M16b-position-removed", "M16: remove a position", [(
        T_SHARED_FILE,
        "POSITIONS = (\"value\", \"list_item\", \"key\", \"sensitive_key_text\", \"repr\", \"failure_key\", \"top_level\")\n",
        "POSITIONS = (\"value\", \"list_item\", \"key\", \"sensitive_key_text\", \"failure_key\", \"top_level\")\n",
    )], (T_SHARED,)),
    m("M17a-decode-rounds-weakened", "M17: weaken the decode rounds", [(
        SAN, "MAX_MARKER_DECODE_ROUNDS = 4\n", "MAX_MARKER_DECODE_ROUNDS = 1\n",
    )]),
    m("M17b-fold-without-casefold", "M17: weaken the fold (no casefold)", [(
        SAN,
        "    return folded.translate(_MARKER_FOLD).casefold().translate(_MARKER_FOLD)\n",
        "    return folded.translate(_MARKER_FOLD).translate(_MARKER_FOLD)\n",
    )]),
    m("M17c-ascii-fast-path-case-sensitive", "M17: weaken the ASCII fast path (no lowercase)", [(
        SAN, "        return text.lower()\n", "        return text\n",
    )]),
    m("M17d-fold-table-row-removed", "M17: weaken the fold (one letter row removed)", [(
        SAN, "        **dict.fromkeys(\"" + "\\u0430\\u03b1\\u0251\\u13aa\\u1d00" + "\", \"a\"),\n", "",
    )]),
    m("M17e-undecoded-text-trusted", "M17: text still changing after the rounds is trusted", [(
        SAN, "        value = decoded\n    return True\n", "        value = decoded\n    return False\n",
    )]),
    m("M17f-opener-dropped", "M17: weaken the opener set", [(
        SAN, "_MARKER_OPENERS = frozenset(\"[(<{\")\n", "_MARKER_OPENERS = frozenset(\"[<{\")\n",
    )]),
    m("M17g-fold-without-nfkc", "M17: weaken the fold (no NFKC)", [(
        SAN,
        "unicodedata.normalize(\"NFD\", unicodedata.normalize(\"NFKC\", folded))",
        "unicodedata.normalize(\"NFD\", folded)",
    )]),
    m("M18a-dispatch-metric-text-search", "M18: X1 metric back to text search (dispatcher)", [(
        f"{E}/providers/dispatch.py",
        "            if truncated:\n                METRICS.record_evidence_truncation()\n",
        "            if \"... [truncated at \" in rendered:\n                METRICS.record_evidence_truncation()\n",
    )]),
    m("M18b-helper-flag-text-search", "M18: X1 helper flag from the rendered text", [(
        f"{E}/models/responses.py",
        "    if len(output) <= limit:\n        return output, False\n",
        "    if len(output) <= limit:\n        return output, \"... [truncated at \" in output\n",
    )]),
    # D11: the stored-plan exception.
    m("D11a-allow-list-widened", "D11: a new caller of the stored-plan re-scan", [
        (LOGBOOK, "from ..sanitization import sanitize_untrusted_data\n",
         "from ..sanitization import rescan_stored_plan, sanitize_untrusted_data\n"),
        (LOGBOOK, "        sanitized = sanitize_untrusted_data(record, known_secrets=secrets)\n",
         "        sanitized = rescan_stored_plan(record, known_secrets=secrets)\n"),
        (T_SHARED_FILE,
         "    (\"clients/logbook.py\", \"project\", \"sanitize_untrusted_data\"): (1, \"logbook\"),\n",
         "    (\"clients/logbook.py\", \"project\", \"rescan_stored_plan\"): (1, \"logbook\"),\n"),
    ]),
    m("D11b-stored-plan-strict", "D11: the stored-plan re-scan loses its tolerance", [(
        SAN, "    return _result(\n        _StoredPlanSanitizer(\n", "    return _result(\n        _Sanitizer(\n",
    )]),
    m("D11c-write-time-tolerant", "D11: write-time input takes the stored-plan tolerance", [(
        RESOURCES, "    if stored_plan:\n", "    if True:\n",
    )]),
    m("D11d-stored-flag-ignored", "D11: the persisted-plan check ignores stored_plan", [(
        RESOURCES, "    if stored_plan:\n", "    if False:\n",
    )]),
    # Truthful counters (D3, Finding 11).
    m("C1-collision-counter-frozen", "D3: the collision health counter does not move", [(
        SAN,
        "        safe = sanitizer._failure()\n    if sanitizer.marker_collision_count:\n        with _COUNTER_LOCK:\n"
        "            _COUNTERS[\"marker_collisions\"] += sanitizer.marker_collision_count\n",
        "        safe = sanitizer._failure()\n    if sanitizer.marker_collision_count:\n        with _COUNTER_LOCK:\n"
        "            _COUNTERS[\"marker_collisions\"] += 0\n",
    )]),
    m("C2-refusal-counter-frozen", "D3: the refusal health counter does not move", [(
        SAN,
        "            _COUNTERS[\"marker_collision_refusals\"] += 1\n",
        "            _COUNTERS[\"marker_collision_refusals\"] += 0\n",
    )]),
    m("C3-gate-does-not-record-refusal", "D3: the persistence gate does not record its refusal", [(
        RESOURCES,
        "        record_marker_collision_refusal(sanitized)\n        errors.append(",
        "        errors.append(",
    )]),
    m("C4-collision-not-counted", "D3: a string collision is not counted", [(
        SAN,
        "            self.marker_collision_count += 1\n            return self._own(MARKER_COLLISION)\n        if emitted:\n",
        "            return self._own(MARKER_COLLISION)\n        if emitted:\n",
    )]),
    m("C5-tally-drops-collisions", "Finding 11: the redaction summary drops collision counts", [(
        SAN,
        "        self.marker_collision_count += result.marker_collision_count\n        return result\n",
        "        return result\n",
    )]),
    m("C6-gateway-redaction-metadata-dropped", "Finding 11: no redaction metadata on the generic read", [(
        GATEWAY,
        "            if redaction is not None:\n                metadata[\"redaction\"] = redaction\n",
        "",
    )]),
    # Identity-record wiring.
    m("W1-audit-ignores-registry", "D4: the audit sink drops the request's identity record", [(
        f"{E}/audit.py",
        "            registry=registry,\n        )\n        encoded = json.dumps",
        "            registry=None,\n        )\n        encoded = json.dumps",
    )]),
    m("W2-withheld-evidence-unregistered", "D4: the gateway's withheld-evidence constant is not recorded", [(
        GATEWAY,
        "    if telemetry is not None:\n"
        "        telemetry.sanitizer_registry.register_output(_REDACTED_EVIDENCE)\n"
        "    return _REDACTED_EVIDENCE\n",
        "    return _REDACTED_EVIDENCE\n",
    )]),
    # Codex review of 6212b7c, reviewer probes R1/R2 (own_mutations.py).
    m("R1-inherited-registry-drops-new-secrets", "Review R1: a child registry checks only inherited secrets", [(
        SAN,
        "value, known_secrets\n            )",
        "value, ()\n            )",
    )]),
    m("R2-collision-checked-after-truncation", "Review R2: marker text past the string limit is not detected", [(
        SAN,
        "and marker_like_text(value):",
        "and marker_like_text(value[:self.max_string]):",
    )]),
    # Codex review H1: every new plan passes the strict sanitizer.
    m("H1a-legacy-input-gate-bypassed", "H1: legacy automation planning skips the strict input gate", [(
        SERVICE,
        "        if persistence_safety_errors(\n"
        "            {\n"
        "                \"automation_id\": automation_id,\n"
        "                \"plan_title\": title,\n",
        "        if False and persistence_safety_errors(\n"
        "            {\n"
        "                \"automation_id\": automation_id,\n"
        "                \"plan_title\": title,\n",
    )]),
    m("H1b-legacy-current-gate-bypassed", "H1: the Home Assistant current automation is persisted unchecked", [(
        SERVICE,
        "        if current is not None and persistence_safety_errors(\n"
        "            current, self.sensitive_values\n"
        "        ):\n",
        "        if False and current is not None and persistence_safety_errors(\n"
        "            current, self.sensitive_values\n"
        "        ):\n",
    )]),
    m("H1c-legacy-caller-context-unfiltered", "H1: legacy caller context returns to the tolerant filter", [(
        SERVICE,
        "            caller_context=_sanitize_configuration_caller_context(\n"
        "                caller_context, known_secrets=self.sensitive_values\n"
        "            ),",
        "            caller_context=sanitize_context(caller_context, self.sensitive_values),",
    )]),
    m("H1d-dashboard-input-gate-bypassed", "H1: dashboard planning skips the strict input gate", [(
        SERVICE,
        "        if persistence_safety_errors(\n"
        "            {\n"
        "                \"plan_title\": title,\n"
        "                \"plan_description\": description,\n"
        "                \"patch_operations\"",
        "        if False and persistence_safety_errors(\n"
        "            {\n"
        "                \"plan_title\": title,\n"
        "                \"plan_description\": description,\n"
        "                \"patch_operations\"",
    )]),
    m("H1e-operational-gate-noop", "H1: operational plans skip the strict creation gate", [(
        SERVICE,
        "            unsafe = bool(persistence_safety_errors(plan.to_dict(), self.sensitive_values))\n",
        "            unsafe = False\n",
    )]),
    m("H1f-backup-caller-context-unfiltered", "H1: backup caller context returns to the tolerant filter", [(
        SERVICE,
        "                \"rollback_available\": False,\n"
        "            },\n"
        "            rollback=ChangeRollback(available=False, status=\"unavailable\"),\n"
        "            caller_context=_sanitize_configuration_caller_context(\n"
        "                caller_context,\n"
        "                known_secrets=self.sensitive_values,\n"
        "            ),\n"
        "            contract_version=OPERATIONAL_PLAN_CONTRACT_VERSION,\n"
        "            plan_family=\"operational_administration\",\n"
        "            operational=operational,\n"
        "            execution_outcome=\"not_applied\",\n"
        "        )\n"
        "        plan.policy_decision = evaluate_change_policy(plan)\n"
        "        self._bind_new_plan_policy(plan)\n"
        "        self._require_strict_new_plan(plan)\n"
        "        self._supersede_prior(plan)\n"
        "        self._record(plan, \"operational_backup_plan_created\"",
        "                \"rollback_available\": False,\n"
        "            },\n"
        "            rollback=ChangeRollback(available=False, status=\"unavailable\"),\n"
        "            caller_context=sanitize_context(caller_context),\n"
        "            contract_version=OPERATIONAL_PLAN_CONTRACT_VERSION,\n"
        "            plan_family=\"operational_administration\",\n"
        "            operational=operational,\n"
        "            execution_outcome=\"not_applied\",\n"
        "        )\n"
        "        plan.policy_decision = evaluate_change_policy(plan)\n"
        "        self._bind_new_plan_policy(plan)\n"
        "        self._require_strict_new_plan(plan)\n"
        "        self._supersede_prior(plan)\n"
        "        self._record(plan, \"operational_backup_plan_created\"",
    )]),
    # Codex review M1: any redaction in a response is reported.
    m("M1a-dashboard-warning-tally-ignored", "M1: warning and hint redaction is not counted", [(
        DASHBOARD,
        "                value, max_chars=MAX_WARNING_CHARS, redactions=redactions\n",
        "                value, max_chars=MAX_WARNING_CHARS\n",
    )]),
    m("M1b-canary-summary-dropped", "M1: the held-read canary drops the redaction summary", [(
        GATEWAY,
        "                if redaction is not None:\n                    metadata[\"redaction\"] = redaction\n",
        "",
    )]),
    # Codex review L1: structured-log counters include log_event's first pass.
    m("L1-first-pass-tally-dropped", "L1: the structured log omits log_event's first-pass counts", [(
        LOGGING,
        "    tally.add(first)\n    logger.log(",
        "    logger.log(",
    )]),
    # Owner direction (CL1) and Codex H3: two passes over the Core log window.
    m("CL1-core-log-whole-window-collision", "CL1: a marker-like line collapses the whole window", [(
        SAN,
        "        collided = [marker_like_text(line) for line in lines]\n",
        "        collided = [marker_like_text(\"\\n\".join(lines))] * len(lines)\n",
    )], tests=(*ALL, *CHOKEPOINT)),
    m("H3-core-log-per-line-detection", "H3: credential rules run per line only", [(
        SAN,
        "        text, categories, _emitted = self._scan(\"\\n\".join(lines))\n"
        "        safe = text.split(\"\\n\")\n",
        "        scanned = [self._scan(line) for line in lines]\n"
        "        safe = [item[0] for item in scanned]\n"
        "        categories = set().union(*(item[1] for item in scanned))\n",
    )], tests=(*ALL, *CHOKEPOINT)),
    # Direction A: one plan-persistence chokepoint.
    m("A1a-recovery-writer-bypasses-chokepoint", "A: restart recovery writes the record directly", [(
        STORAGE,
        "        try:\n            self.save(plan)\n        except PlanContentRefused:\n",
        "        try:\n"
        "            self._path_for_plan(plan).write_text(\n"
        "                json.dumps(plan.to_dict(), sort_keys=True, separators=(\",\", \":\")),\n"
        "                encoding=\"utf-8\",\n"
        "            )\n"
        "        except PlanContentRefused:\n",
    )], tests=CHOKEPOINT),
    m("A1b-service-writer-bypasses-chokepoint", "A: one service writer (applied plans) writes directly; runtime spy", [(
        SERVICE,
        "        try:\n            self.repository.save(plan)\n            self._update_projection_failure_index(plan)\n",
        "        try:\n"
        "            if plan.status == PlanStatus.APPLIED:\n"
        "                self.repository._path_for_plan(plan).write_text(\n"
        "                    json.dumps(plan.to_dict(), sort_keys=True, separators=(\",\", \":\")),\n"
        "                    encoding=\"utf-8\",\n"
        "                )\n"
        "            else:\n"
        "                self.repository.save(plan)\n"
        "            self._update_projection_failure_index(plan)\n",
    )], tests=CHOKEPOINT_SPY),
    m("A2a-identifier-unscanned", "A/H1g: identifier fields skip the strict scan", [(
        SAN, TEXT_START,
        "        if pattern and pattern[-1] in {\"target_id\", \"automation_id\", \"operation_id\"}:\n"
        "            return value\n" + TEXT_START,
    )], tests=CHOKEPOINT),
    m("A2b-exempt-field-unscanned", "A: a schema-exempt field is format-checked but not scanned", [(
        SAN,
        "            text, categories, _emitted = self._scan(value)\n"
        "            if not validator(value) or categories or text != value:\n",
        "            if not validator(value):\n",
    )], tests=CHOKEPOINT),
    m("A3-apply-evidence-unsanitized", "A/H2: an apply-time (verification) write is persisted unscreened", [(
        STORAGE,
        "                    self._screen_new_content(plan, path),\n",
        "                    plan.to_dict() if plan.status == PlanStatus.VERIFICATION_REQUIRED\n"
        "                    else self._screen_new_content(plan, path),\n",
    )], tests=CHOKEPOINT),
]

_FAILURE_LINE = re.compile(r"^(FAIL|ERROR): (\S+) \(([^)]+)\)", re.MULTILINE)


def apply(tree: Path, mutation: dict) -> dict[Path, str]:
    originals: dict[Path, str] = {}
    for path, old, new in mutation["replacements"]:
        target = tree / path
        if target not in originals:
            originals[target] = target.read_text(encoding="utf-8")
        text = target.read_text(encoding="utf-8")
        count = text.count(old)
        if count != 1:
            for restore, original in originals.items():
                restore.write_text(original, encoding="utf-8")
            raise SystemExit(f"{mutation['id']}: fragment matched {count} times in {path}: {old!r}")
        target.write_text(text.replace(old, new), encoding="utf-8")
    return originals


def run_tests(tree: Path, modules, timeout: int, *, failfast: bool) -> tuple[int, str, float]:
    environment = dict(os.environ, PYTHONDONTWRITEBYTECODE="1", PYTHONPATH="tests")
    command = [sys.executable, "-m", "unittest"] + (["-f"] if failfast else []) + list(modules)
    started = time.monotonic()
    try:
        completed = subprocess.run(
            command, cwd=tree, env=environment, capture_output=True, text=True, timeout=timeout,
        )
        code, output = completed.returncode, completed.stdout + completed.stderr
    except subprocess.TimeoutExpired:
        code, output = -1, "TIMEOUT"
    return code, output, time.monotonic() - started


def copy_tree(destination: Path) -> None:
    if destination.exists():
        shutil.rmtree(destination)
    shutil.copytree(
        ROOT, destination,
        ignore=shutil.ignore_patterns(".git", ".artifacts", "__pycache__", "*.pyc"),
        symlinks=True,
    )
    # The copy has no .git. Suites that read exact historical sources with
    # ``git show <sha>:path`` get a throwaway repository whose only link to
    # the real one is a read-only alternate object store.
    objects = subprocess.run(
        ["git", "rev-parse", "--path-format=absolute", "--git-path", "objects"],
        cwd=ROOT, check=True, capture_output=True, text=True,
    ).stdout.strip()
    subprocess.run(["git", "init", "-q", str(destination)], check=True)
    alternates = destination / ".git" / "objects" / "info" / "alternates"
    alternates.write_text(objects + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--work", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--workers", type=int, default=2)
    parser.add_argument("--timeout", type=int, default=900)
    parser.add_argument("--only", nargs="*")
    arguments = parser.parse_args()
    work = arguments.work.expanduser().resolve()
    if work == ROOT or ROOT in work.parents:
        raise SystemExit("--work must be outside the repository")
    log_dir = work / "mutation-logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    if len({item["id"] for item in MUTATIONS}) != len(MUTATIONS):
        raise SystemExit("duplicate mutation id")
    selected = [item for item in MUTATIONS if not arguments.only or item["id"] in arguments.only]
    trees: "queue.Queue[Path]" = queue.Queue()
    for index in range(arguments.workers):
        tree = work / f"tree-{index}"
        copy_tree(tree)
        trees.put(tree)
    # Every fragment must match once before anything runs.
    probe = trees.get()
    for mutation in selected:
        for path, text in apply(probe, mutation).items():
            path.write_text(text, encoding="utf-8")
    trees.put(probe)
    # The unmutated baseline must pass every module the mutations use.
    baseline_modules = sorted({module for item in selected for module in item["tests"]})
    tree = trees.get()
    code, output, seconds = run_tests(tree, baseline_modules, arguments.timeout, failfast=False)
    trees.put(tree)
    baseline_log = log_dir / "baseline.log"
    baseline_log.write_text(output, encoding="utf-8")
    baseline = {"log_path": str(baseline_log), "modules": baseline_modules, "returncode": code,
                "seconds": round(seconds, 1),
                "summary": output.strip().splitlines()[-1] if output.strip() else ""}
    print(f"baseline rc={code} {baseline['summary']} ({seconds:.0f}s)", flush=True)
    if code != 0:
        print(output[-4000:])
        return 2

    def one(mutation: dict) -> dict:
        tree = trees.get()
        try:
            originals = apply(tree, mutation)
            try:
                code, output, seconds = run_tests(
                    tree, mutation["tests"], arguments.timeout, failfast=True
                )
            finally:
                for path, text in originals.items():
                    path.write_text(text, encoding="utf-8")
        finally:
            trees.put(tree)
        failures = [f"{kind} {name} ({where})" for kind, name, where in _FAILURE_LINE.findall(output)]
        import_error = "ImportError" in output or "SyntaxError" in output or "Failed to import" in output
        log_path = log_dir / f"{mutation['id']}.log"
        log_path.write_text(output, encoding="utf-8")
        result = {
            **mutation,
            "log_path": str(log_path),
            "returncode": code,
            "killed": code != 0,
            "killed_by_import_or_syntax_error": import_error,
            "first_failures": failures[:3],
            "seconds": round(seconds, 1),
        }
        state = "KILLED" if result["killed"] else ("EQUIV" if mutation.get("equivalent") else "SURVIVED")
        print(f"{state:8} {mutation['id']:44} {seconds:5.0f}s {failures[:1]}", flush=True)
        return result

    with concurrent.futures.ThreadPoolExecutor(max_workers=arguments.workers) as pool:
        results = list(pool.map(one, selected))
    survived = [item["id"] for item in results if not item["killed"] and not item.get("equivalent")]
    equivalent = [item["id"] for item in results if not item["killed"] and item.get("equivalent")]
    report = {
        "baseline": baseline,
        "mutation_count": len(results),
        "killed": sum(item["killed"] for item in results),
        "survived": survived,
        "survived_equivalent": equivalent,
        "python": sys.version.split()[0],
        "results": results,
    }
    output_path = arguments.output.expanduser()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(report, indent=2, ensure_ascii=True) + "\n", encoding="utf-8")
    print(f"{report['killed']}/{len(results)} killed; survived: {survived}; equivalent: {equivalent}")
    return 0 if not survived else 1


if __name__ == "__main__":
    raise SystemExit(main())
