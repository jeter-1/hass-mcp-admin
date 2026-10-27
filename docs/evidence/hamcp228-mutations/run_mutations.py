"""HAMCP-228 mutation runner (offline, deterministic).

Reuses the HAMCP-131 runner's machinery: a disposable copy of the tracked
tree outside the repository, every fragment matching exactly once before
anything runs, and fail-fast suites per mutant. The suites are
``test_hamcp228_baseline_store`` (first, so most mutants fail fast), the
capture suite and the offline comparator suite. Each run has its own
temporary directory, removed after it.

The task's classes each have mutations:
- ``S*``: the credential screen bypassed before a baseline is written or a
  comparison is returned (the default-mode identifier screen, its PEM
  header check, the compare output screen, its known secrets);
- ``P*``: prefix-only reported as verified (a prefix match counted as
  ``UNCHANGED``, a differing prefix not ``CHANGED``, the stored/stored
  ``PREFIX_MATCH`` count dropped, the partial flag ignoring withheld values,
  rows needing attention not first);
- ``R*``: the resume record filter widened or narrowed (every unreadable
  record retried, timeout outcomes not retried, carried records re-read,
  ``resume_not_needed`` skipped, the resumed count, the base's start and
  outcomes not inherited);
- ``F*``: the resume inventory fence weakened (installation, authority
  generation, declared count, the mapping compared by configuration id only);
- ``C*``: the capacity boundary (off by one, checked only after the reads,
  the retention sweep boundary, the quarantine bound);
- ``A*``: the caller check (another caller's baseline, anonymous compare,
  a cursor from another caller);
- ``D*``: the stored digest or validator check skipped;
- ``V*``/``T*``: v2 ``timed_out`` and ``resume`` validation and the
  comparator's ``timed_out`` reason;
- ``O*``: public output (the compare tool's read-only annotation and the
  page's ``resumed_from``).

Review 1 (HAMCP-226/228) adds:
- ``H1-*``: a registry, state or response field returned unscreened (each
  inventory screen, the withheld-row rule, the dependency target state, the
  final-envelope backstop's value and key screens and its partial flags, in
  every tool that carries one);
- ``H2-*``: a missing or unreadable index deleting or dropping committed
  baselines (emptied instead of rebuilt, stubs, persistence, binding,
  expiry, the reported store status);
- ``M1-*``: a resume not bound to the base's Core version;
- ``M2-*``: compare output truncated, or its cursor not bound to the
  arguments, the caller or the compare domain, or its page row cap;
- ``M3-*``: over-bound accounting (the old cap before deduplication, the
  total, the listed bound, the overflow count).

Runs: ``run1.log`` stopped at its baseline (a wrong suite name);
``results-run2.json`` and ``results-run3.json`` keep runs 2 and 3;
``results-run4.json`` is run 4 (44 of 44 killed); ``results-run5.json`` and
``run5.log`` are the first review 1 run (71 of 75 killed);
``results-run6.json`` and ``run6.log`` are run 6, after the run 5
corrections below; ``run7.log`` is the first review 2 run, stopped at 80 of
111 once ``SB-05`` survived (no results file); ``results-run8.json`` and
``run8.log`` are run 8, after the ``SB-05`` correction (108 of 111 killed);
``results.json`` and ``run9.log`` are run 9, after the run 8 corrections.

Run 7 showed ``SB-05`` (the store's guard called without the known secrets)
masked by the capture's own storage screen and a credential-shaped probe;
the review 2 suite now patches that screen out and plants a known secret,
which only the store's guard, given the known secrets, can refuse.

Run 8 showed three suite gaps, now closed: ``SB-11`` (a stubbed record's
outcome kept: it never reaches a page, so the suite now reads the stored
report, which resume reads), ``W-10`` (the listed stubs unbounded: every
stub came from the projector, already bounded, so the suite now stubs 17
records at the storage screen) and ``W-15`` (the registry's withheld unique
id ignored: the suite withheld both copies, so it now withholds the
registry's alone).

Review 2 (HAMCP-226/228) adds:
- ``SB-*`` (finding R1): a record or diagnostic reaching the hash or the
  store unscreened (the capture's storage screen, its known secrets, the
  store's own guard, each part of the report, the header refusal);
- ``W-*``: the owner decision (Josh, 2026-10-27) broken: a hit refusing the
  capture instead of a stub, or a withheld field or identity without its
  partial marks, counts, listed stubs or warning, at the screen, the
  projector or the tool;
- ``IX-*`` (finding R2): an unreadable index or namespace refused instead of
  rebuilt, a rebuild on a namespace it cannot commit to, an unreadable record
  quarantined, or the ``store_condition`` detail lost or widened.

Equivalent mutants, analysed and left out after run 2:
- the resumed baseline inheriting the base's ``unestablished`` identity or a
  drifted authority: the fence requires the base's ``installation_id`` to
  equal the current identity, and a baseline this code writes has
  ``installation_id`` ``null`` exactly when it is unestablished and an
  ``authority_drift`` other than ``none_observed_at_capture_fences`` only
  then, so no resumable base reaches either inheritance;
- the ordered mapping compared as an unordered set: both sides are built
  from ``sorted(states.items())``, so equal sets are equal sequences.

Equivalent mutant, analysed and left out after run 5:
- ``H2-03``, the rebuild's explicit over-capacity refusal removed: the rebuilt
  index is committed only through ``_write_index``, whose ``validate_index``
  refuses more than ``STORE_BASELINES`` baselines (``store_invalid``) before
  the index is replaced and before any damaged file is removed, so nothing
  observable changes. The explicit refusal stays as the first, readable guard.

Run 5 also showed three suite gaps, now closed: ``D3`` (the record's own
binding must be the indexed entry; the suite now rewrites only the index to
another caller), ``H1-12`` and ``H1-13`` (the capture and compare final
backstops were masked by the inner screens; the suite now returns a field the
inner screens never see).

Run 3 showed ``A3`` (both cursor bindings removed) masked by the store's own
caller check; the HAMCP-228 suite now requires the cursor itself to refuse
(``invalid_cursor``, store not consulted), including a token signed for the
other caller, which also makes the stored-caller check alone (``A4``)
observable.

Usage:
    python docs/evidence/hamcp228-mutations/run_mutations.py \\
        --work /var/tmp/hamcp228-mut --output docs/evidence/hamcp228-mutations/results.json
"""

from __future__ import annotations

import os
from pathlib import Path
import shutil
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "hamcp131-mutations"))

import run_mutations as base  # noqa: E402

m = base.m
E = base.E
AB = f"{E}/audit_baseline"
PROV = f"{AB}/capture_provider.py"
SERV = f"{AB}/capture_service.py"
STORE = f"{AB}/store.py"
COMP = f"{AB}/comparison.py"
VAL = f"{AB}/validation.py"
TOOL = f"{E}/tools/audit_baseline.py"
SS = f"{AB}/storage_screen.py"
CONTRACTS = f"{AB}/capture_contracts.py"
UT = f"{E}/untrusted_text.py"
INTEG = f"{E}/integrity/provider.py"
DEPP = f"{E}/dependency/provider.py"
SCRIPTS = f"{E}/dependency/script_sources.py"
BOUNDED = f"{E}/dependency/bounded_sources.py"
HELPER = f"{E}/governance/helper_dependency.py"
ANALYSIS = f"{E}/tools/analysis.py"
SCREEN = "the credential screen bypassed before a write or a returned comparison"
PREFIX = "prefix-only evidence reported as verified or complete"
RESUME = "the resume record filter widened or narrowed"
FENCE = "the resume inventory fence weakened"
CAPACITY = "the store capacity or retention boundary moved"
CALLER = "the caller binding skipped"
DIGEST = "a stored baseline's integrity check skipped"
SCHEMA = "v2 timed_out or resume validation weakened"
OUTPUT = "a public output field or annotation changed"
# Review 1 (HAMCP-226/228) classes.
H1 = "a registry, state or response field returned unscreened"
H2 = "a missing or unreadable index deleting or dropping committed baselines"
M1 = "a resume not bound to the base capture's Core version"
M2 = "compare output truncated or its cursor unbound"
M3 = "over-bound accounting inexact at the production limits"
# Review 2 (HAMCP-226/228) classes.
SB = "a record or diagnostic reaching the hash or the store unscreened"
W = "a credential hit refusing a capture, or withheld without its stub, count or partial"
IX = "an unreadable index or namespace deleting baselines or misreported"
FENCE_HEAD = '                if (prior["installation"]["installation_id"] != first["identity"]\n'
GENERATION = '                        or prior["authority"]["generation"] != authority[0]\n'
DECLARED = ('                        or prior["inventory"]["declared_count"] != len(states)'
            ' + first["states"]["invalid_count"]\n')
MAPPING = ('                        or [(r["configuration_id"], r["entity_id"]) for r in prior["records"]]\n'
           '                        != [(r["configuration_id"], r["entity_id"]) for r in records]):\n')

MUTATIONS = [
    # -- S: the credential screen
    m("S1-identifier-default-screen-removed", SCREEN,
      (PROV, "        return value if untrusted_text.screen(value, tuple(self.known_secrets)) is None else None",
       "        return value")),
    m("S2-identifier-known-secrets-dropped", SCREEN,
      (PROV, "return value if untrusted_text.screen(value, tuple(self.known_secrets))",
       "return value if untrusted_text.screen(value, ())")),
    m("S3-pem-header-check-removed", SCREEN,
      (UT, "    if marker is None and credentials.private_key_header(value):", "    if False:")),
    m("S4-compare-output-unscreened", SCREEN,
      (SERV, "        value, withheld = untrusted_text.screen_tree(value, self.known_secrets())", "        withheld = 0")),
    m("S5-compare-screen-without-known-secrets", SCREEN,
      (SERV, "untrusted_text.screen_tree(value, self.known_secrets())", "untrusted_text.screen_tree(value, ())")),
    # -- P: prefix-only reporting
    m("P1-prefix-match-counted-unchanged", PREFIX,
      (COMP, "            elif len(recorded) == len(digest):", "            elif True:")),
    m("P2-differing-prefix-not-changed", PREFIX,
      (COMP, "            if not digest.startswith(recorded):", "            if False:")),
    m("P3-prefix-match-reason-dropped", PREFIX,
      (COMP, '                reasons.append(f"prefix_match:{len(recorded)}")\n', "")),
    m("P4-stored-pair-prefix-count-dropped", PREFIX,
      (SERV, '            value["counts"] = {**value["counts"], PREFIX_MATCH: 0}\n', "")),
    m("P5-partial-ignores-withheld", PREFIX,
      (TOOL, 'partial = result["screened_withheld_count"] > 0', "partial = False")),
    m("P6-attention-rows-not-first", PREFIX,
      (SERV, 'row["classification"] in {"UNCHANGED", PREFIX_MATCH},', "False,")),
    m("P7-one-side-only-not-unknown", PREFIX,
      (COMP, '        elif recorded is None:\n            reasons.append("absent_from_recorded_fingerprints")\n',
       '        elif recorded is None:\n            pass\n')),
    m("P8-fingerprint-timed-out-reason-dropped", PREFIX,
      (COMP, '            if record.configuration_status == "timed_out":\n                reasons.append("timed_out")\n',
       "")),
    # -- R: the resume record filter
    m("R1-every-unreadable-retried", RESUME,
      (PROV, '                or (row["configuration"]["status"] == "unreadable"\n'
             '                    and outcomes.get(row["configuration_id"]) == "timeout")}',
       '                or row["configuration"]["status"] == "unreadable"}')),
    m("R2-timeout-outcome-not-retried", RESUME,
      (PROV, '                or (row["configuration"]["status"] == "unreadable"\n'
             '                    and outcomes.get(row["configuration_id"]) == "timeout")}',
       "                or False}")),
    m("R2-carried-records-re-read", RESUME,
      (PROV, '                pending = [index for index, row in enumerate(records) if row["configuration_id"] in retry]',
       "                pending = list(range(len(records)))")),
    m("R4-carried-records-not-copied", RESUME,
      (PROV, "                        records[index] = copy.deepcopy(row)", "                        pass")),
    m("R5-resume-not-needed-skipped", RESUME,
      (SERV, '                if not self.provider.retry_ids(base["baseline"], base["diagnostics"]["record_outcomes"]):',
       "                if False:")),
    m("R6-resumed-count-all-records", RESUME,
      (PROV, '"resumed_count": len(pending)}', '"resumed_count": len(records)}')),
    m("R8-base-start-not-inherited", RESUME,
      (PROV, '            started_at = prior["capture"]["started_at"]\n', "")),
    m("R10-base-outcomes-not-carried", RESUME,
      (PROV, "                outcomes.update({key: value for key, value in prior_outcomes.items() if key not in retry})\n",
       "")),
    # -- F: the inventory fence
    m("F1-installation-not-fenced", FENCE,
      (PROV, FENCE_HEAD + GENERATION, '                if (prior["authority"]["generation"] != authority[0]\n')),
    m("F2-generation-not-fenced", FENCE, (PROV, GENERATION, "")),
    m("F3-declared-count-not-fenced", FENCE, (PROV, DECLARED, "")),
    m("F4-mapping-by-configuration-id-only", FENCE,
      (PROV, MAPPING,
       '                        or [r["configuration_id"] for r in prior["records"]]\n'
       '                        != [r["configuration_id"] for r in records]):\n')),
    # -- C: capacity and retention
    m("C1-capacity-off-by-one", CAPACITY,
      (STORE, '        if len(index["baselines"]) >= c.STORE_BASELINES:', '        if len(index["baselines"]) > c.STORE_BASELINES:')),
    m("C2-capacity-checked-after-reads", CAPACITY,
      (SERV, '            await self.stored("check_capacity", self.now_ms())\n', "")),
    m("C3-retention-boundary-inclusive", CAPACITY,
      (STORE, 'if entry["created_at_ms"] > horizon]', 'if entry["created_at_ms"] >= horizon]')),
    m("C4-retention-not-swept", CAPACITY,
      (STORE, 'if entry["created_at_ms"] > horizon]', "]")),
    m("C5-quarantine-unbounded", CAPACITY,
      (STORE, "        if len(stubs) > c.QUARANTINE_MAX:", "        if False:")),
    m("C6-full-store-age-dropped", CAPACITY,
      (STORE, '            raise StoreError("store_full", max(0, (now_ms - oldest) // 1000))',
       '            raise StoreError("store_full")')),
    # -- A: the caller binding
    m("A1-other-caller-baseline-returned", CALLER,
      (STORE, '            if entry is None or entry["caller"] != caller:', "            if entry is None:")),
    m("A2-anonymous-compare-allowed", CALLER,
      (SERV, '        caller = current_caller_id()\n        if caller == "anonymous":\n'
             '            raise c.CaptureError("access_denied")\n        request =',
       "        caller = current_caller_id()\n        request =")),
    m("A3-cursor-other-caller-accepted", CALLER,
      (SERV, '(value + ":" + caller).encode()', '(value + ":").encode()'),
      (SERV, "        if cursor[1] != caller:", "        if False:")),
    m("A4-cursor-stored-caller-unchecked", CALLER,
      (SERV, "        if cursor[1] != caller:", "        if False:")),
    # -- D: stored integrity
    m("D1-stored-digest-not-checked", DIGEST,
      (STORE, '                    if len(raw) != entry["bytes"] or observed["observed_sha256"] != entry["sha256"]:',
       "                    if False:")),
    m("D2-stored-report-not-validated", DIGEST,
      (STORE, "        report = validate_report(report_bytes, entry)", "        report = json.loads(report_bytes)")),
    m("D3-stored-binding-not-the-indexed-one", DIGEST,
      (STORE, "                        if verified is None or verified[1] != entry:",
       "                        if verified is None:")),
    # -- V/T: v2 validation and the comparator
    m("V1-v1-accepts-timed-out", SCHEMA,
      (VAL, "    if schema == BASELINE_SCHEMA_V2:\n        # HAMCP-228: a record",
       "    if True:\n        # HAMCP-228: a record")),
    m("V2-timed-out-digest-accepted", SCHEMA,
      (VAL, '    if config_status == "timed_out" and digest is not None:', "    if False:")),
    m("V3-resumed-count-zero-accepted", SCHEMA,
      (VAL, '_require_int(resume["resumed_count"], "resumed_count_invalid", minimum=1)',
       '_require_int(resume["resumed_count"], "resumed_count_invalid", minimum=0)')),
    m("T1-comparator-timed-out-reason-dropped", SCHEMA,
      (COMP, '    if "timed_out" in (earlier.configuration_status, later.configuration_status):\n'
             '        reasons.append("timed_out")\n', "")),
    m("T2-unscheduled-record-unreadable", SCHEMA,
      (PROV, '                        config["status"] = "timed_out"', '                        config["status"] = "unreadable"')),
    # -- O: public output
    m("O1-compare-not-read-only", OUTPUT,
      (TOOL, "    return _tool(compare_automation_baselines, read_only=True)",
       "    return _tool(compare_automation_baselines, read_only=False)")),
    m("O2-page-resumed-from-dropped", OUTPUT,
      (SERV, '"resumed_from": resumed_from, "provider_reads_for_continuation": 0,',
       '"resumed_from": None, "provider_reads_for_continuation": 0,')),
    # -- H1: every returned string screened, with a final-envelope backstop
    m("H1-01-registry-inventory-unscreened", H1,
      (INTEG, "        value, withheld_rows, withheld_text = self._screen_inventory(\n"
              "            value, REGISTRY_TEXT_KEYS\n        )\n", "        withheld_rows, withheld_text = 0, 0\n")),
    m("H1-02-registry-platform-unscreened", H1,
      (INTEG, 'REGISTRY_TEXT_KEYS = ("platform", "disabled_by")', 'REGISTRY_TEXT_KEYS = ("disabled_by",)')),
    m("H1-03-state-inventory-unscreened", H1,
      (INTEG, "        value, withheld_rows, withheld_text = self._screen_inventory(\n"
              "            value, STATE_TEXT_KEYS\n        )\n", "        withheld_rows, withheld_text = 0, 0\n")),
    m("H1-04-withheld-entity-id-row-kept", H1,
      (INTEG, "            if withheld_ids:\n                withheld_rows += 1\n                continue\n", "")),
    m("H1-05-dependency-target-state-unscreened", H1,
      (DEPP, '                    screen(state["state"], (self.secret,) if self.secret else ())\n'
             '                    if isinstance(state.get("state"), str) else None\n', "                    None\n")),
    m("H1-06-envelope-value-screen-removed", H1,
      (UT, "            return value, 0\n        marker = screen(value, known_secrets)\n"
           "        return (value, 0) if marker is None else (marker, 1)\n",
       "            return value, 0\n        return value, 0\n")),
    m("H1-07-envelope-key-screen-removed", H1,
      (UT, "if isinstance(key, str) and key not in MARKERS and screen(key, known_secrets) is not None:",
       "if False:")),
    m("H1-08-analysis-backstop-data-unscreened", H1,
      (ANALYSIS, "        data, data_hits = screen_envelope(output.data, known)\n",
       "        data, data_hits = output.data, 0\n")),
    m("H1-09-integrity-backstop-not-partial", H1,
      (ANALYSIS, "            # Envelope withholds of the accepted page make it partial too.\n"
                 "            partial = output.partial or screened > 0\n",
       "            partial = output.partial\n")),
    m("H1-10-dependency-backstop-not-partial", H1,
      (ANALYSIS, "        if telemetry:\n            partial = output.partial or screened > 0\n",
       "        if telemetry:\n            partial = output.partial\n")),
    m("H1-11-helper-bounded-sources-backstop-removed", H1,
      (HELPER, "    return screen_envelope(public, known_secrets)[0]", "    return public")),
    m("H1-12-capture-backstop-removed", H1,
      (TOOL, "        if _final_screen(service, result)[1]:", "        if False:")),
    m("H1-13-compare-backstop-removed", H1,
      (TOOL, "        result, hits = _final_screen(service, result)\n", "        hits = 0\n")),
    # -- H2: a lost index never deletes committed baselines
    m("H2-01-missing-index-emptied", H2,
      (STORE, "            index = self._rebuild(reason, int(time.time() * 1000) if now_ms is None else now_ms)",
       "            index = empty_index()")),
    m("H2-02-rebuild-quarantine-without-stub", H2,
      (STORE, '            stubs.append({"capture_id": capture_id, "quarantined_at_ms": now_ms, **damage})\n',
       "            pass\n")),
    m("H2-04-rebuild-not-committed", H2,
      (STORE, "        self._write_index(index)\n        for capture_id, _ in damaged:",
       "        for capture_id, _ in damaged:")),
    m("H2-05-rebuild-binding-not-checked", H2,
      (STORE, ' or binding["capture_id"] != capture_id:', ":")),
    m("H2-06-rebuild-reindexes-expired", H2,
      (STORE, '                    if verified[1]["created_at_ms"] > horizon:\n', "                    if True:\n")),
    m("H2-07-store-status-not-reported", H2,
      (SERV, '        value["store_status"] = self.store_status()\n', "")),
    # -- M1: the resume binds the base's Core version
    m("M1-01-resume-core-version-unbound", M1,
      (PROV, '                if prior["authority"]["home_assistant_core"] != authority[1]:', "                if False:")),
    # -- M2: compare fully paged, cursor bound
    m("M2-01-compare-truncated-reported-complete", M2,
      (SERV, "        more = offset + count < total", "        more = False")),
    m("M2-02-compare-cursor-not-bound-to-arguments", M2,
      (SERV, "        if key != request or not hmac.compare_digest(token, self._compare_token(request, offset, caller)):",
       "        if not hmac.compare_digest(token, self._compare_token(key, offset, caller)):")),
    m("M2-03-compare-cursor-not-bound-to-caller", M2,
      (SERV, '("compare:" + value + ":" + caller).encode()', '("compare:" + value + ":").encode()')),
    m("M2-04-compare-cursor-not-domain-separated", M2,
      (SERV, '("compare:" + value + ":" + caller)', '(value + ":" + caller)')),
    m("M2-05-compare-page-row-cap-removed", M2,
      (SERV, "records[offset:offset + c.COMPARE_PAGE_ROWS]", "records[offset:]")),
    # -- M3: exact over-bound accounting
    m("M3-01-script-terminals-capped-before-dedupe", M3,
      (SCRIPTS, 'pair = (item.source_type, item.source_id, item.source_entity_id or "", item.reason_code)',
       'pair = (item.source_type, item.source_id, item.source_entity_id or "", item.reason_code, item.evidence_id)'),
      (SCRIPTS, "                if pair not in bounded or item.evidence_id < bounded[pair].evidence_id:\n"
                "                    bounded[pair] = item\n",
       "                if pair not in bounded or item.evidence_id < bounded[pair].evidence_id:\n"
       "                    bounded[pair] = item\n"
       "            while len(bounded) > MAX_SCRIPT_BOUNDED_OBLIGATIONS:\n"
       "                bounded_overflow[0] = True\n"
       "                bounded.pop(max(bounded))\n")),
    m("M3-02-bounded-total-excludes-withheld", M3,
      (BOUNDED, "listed, len(entries) - len(listed), withheld, len(entries) + withheld",
       "listed, len(entries) - len(listed), withheld, len(entries)")),
    m("M3-03-bounded-listed-off-by-one", M3,
      (BOUNDED, "listed = tuple(entries[:MAX_BOUNDED_SOURCES])", "listed = tuple(entries[:MAX_BOUNDED_SOURCES - 1])")),
    m("M3-04-bounded-overflow-not-counted", M3,
      (BOUNDED, "listed, len(entries) - len(listed), withheld,", "listed, 0, withheld,")),
    # -- SB (review 2 R1): the storage boundary screens every record and diagnostic
    m("SB-01-service-storage-screen-removed", SB,
      (SERV, "            report = await c.worker(storage_screen.screen_report, report, self.known_secrets())\n", "")),
    m("SB-02-service-storage-screen-without-known-secrets", SB,
      (SERV, "storage_screen.screen_report, report, self.known_secrets())", "storage_screen.screen_report, report, ())")),
    m("SB-03-store-put-guard-removed", SB,
      (STORE, '        if hits:\n            raise StoreError("unscreened_content")\n', "")),
    m("SB-04-store-put-guard-ignores-withheld-key", SB,
      (STORE, "            hits = 1\n", "            hits = 0\n")),
    m("SB-05-store-put-without-known-secrets", SB,
      (SERV, "now_ms=self.now_ms(), known_secrets=self.known_secrets())", "now_ms=self.now_ms())")),
    m("SB-06-records-not-screened", SB,
      (SS, "        if _hits(row, known)[1] == 0:\n", "        if True:\n")),
    m("SB-07-diagnostics-not-screened", SB,
      (SS, '    rest, hits = _tree({key: value for key, value in diagnostics.items() if key != "record_outcomes"}, known)\n',
       '    rest, hits = {key: value for key, value in diagnostics.items() if key != "record_outcomes"}, 0\n')),
    m("SB-08-record-outcomes-not-screened", SB,
      (SS, "    outcomes, hits = _tree(outcomes, known)\n", "    hits = 0\n")),
    m("SB-09-withheld-diagnostic-key-kept", SB,
      (SS, "                count += 1\n                continue\n", "                pass\n")),
    m("SB-10-header-hit-not-refused", SB,
      (SS, '    if _hits(header, known)[1] != 0:\n        raise c.CaptureError("redacted_identity")\n', "")),
    m("SB-11-stubbed-record-outcome-kept", SB,
      (SS, "outcomes.items() if key not in removed}", "outcomes.items()}")),
    # -- W: withheld, not refused (owner decision, Josh, 2026-10-27)
    m("W-01-stub-refuses-capture", W,
      (SS, "        if screened is None:\n", '        if screened is None:\n            raise c.CaptureError("redacted_identity")\n')),
    m("W-02-stub-inventory-not-partial", W,
      (SS, 'dict(baseline["inventory"], completeness="partial")', 'dict(baseline["inventory"])')),
    m("W-03-stub-omitted-count-not-raised", W,
      (SS, '        inventory["omitted_count"] = (inventory["omitted_count"] or 0) + len(stubs)\n', "")),
    m("W-04-partial-record-not-redacted", W,
      (SS, '        coverage["redacted"] = True\n', "")),
    m("W-05-partial-record-warning-not-counted", W,
      (SS, '        coverage["warning_count"] += 1\n', "")),
    m("W-06-partial-record-complete", W,
      (SS, '            coverage["completeness"] = "partial"\n', "            pass\n")),
    m("W-07-withheld-entity-id-kept", W,
      (SS, '            screened["entity_id"] = None\n            screened["mapping_status"] = "missing"\n', "            pass\n")),
    m("W-08-stub-record-count-dropped", W,
      (SS, '"withheld_record_count": prior_records + len(stubs)', '"withheld_record_count": prior_records')),
    m("W-09-withheld-field-count-dropped", W,
      (SS, "        records.append(screened)\n        fields += hits\n", "        records.append(screened)\n")),
    m("W-10-listed-stubs-unbounded", W,
      (SS, '"withheld_records": (prior + stubs)[:LISTED_STUBS]}', '"withheld_records": prior + stubs}')),
    m("W-11-prior-summary-dropped", W,
      (SS, '    prior, prior_records, prior_fields = _prior_summary(rest.pop("credential_screen", None))\n',
       '    rest.pop("credential_screen", None)\n    prior, prior_records, prior_fields = [], 0, 0\n')),
    m("W-12-tool-not-partial-for-withheld", W,
      (TOOL, "                   or withheld_records > 0 or withheld_fields > 0)", ")")),
    m("W-13-tool-warning-dropped", W,
      (TOOL, "        if withheld_records or withheld_fields:\n", "        if False:\n")),
    m("W-14-projector-identity-stub-dropped", W,
      (PROV, '                    if state["id_withheld"] or (mapped and mapped["id_withheld"]):', "                    if False:")),
    m("W-15-projector-registry-withheld-ignored", W,
      (PROV, '(mapped and mapped["id_withheld"])', "False")),
    m("W-16-projector-entity-withheld-not-counted", W,
      (PROV, "                withheld += entity_withheld\n", "")),
    m("W-17-projector-withheld-flag-false", W,
      (PROV, "        return identifier, (identifier is None and type(value) is str",
       "        return identifier, (False and type(value) is str")),
    m("W-18-projector-summary-not-reported", W,
      (PROV, '            "final_fence": "complete" if last else "unavailable",\n            "credential_screen": storage_screen.summary(withheld)}}',
       '            "final_fence": "complete" if last else "unavailable"}}')),
    # -- IX (review 2 R2): an unreadable index or namespace never deletes baselines
    m("IX-01-unreadable-index-refused", IX,
      (STORE, '            # R2): rebuilt from the records, never treated as an empty store.\n            index, reason = None, "index_unreadable"\n',
       '            raise StoreError("store_unavailable") from None\n')),
    m("IX-02-namespace-writability-unchecked", IX,
      (STORE, "        if not (os.access(self.root, os.W_OK | os.X_OK)\n"
              "                and os.access(self.records, os.R_OK | os.W_OK | os.X_OK)):\n",
       "        if False:\n")),
    m("IX-03-records-directory-writability-unchecked", IX,
      (STORE, "\n                and os.access(self.records, os.R_OK | os.W_OK | os.X_OK)):\n", "):\n")),
    m("IX-04-unreadable-record-quarantined", IX,
      (STORE, '                # (that would remove it): the rebuild stops with nothing changed.\n'
              '                raise StoreError("store_unavailable", condition="record_unreadable") from None\n',
       '                # (that would remove it): the rebuild stops with nothing changed.\n'
       '                damage = {"reason": "unreadable", "observed_bytes": None, "observed_sha256": None}\n')),
    m("IX-05-unlistable-namespace-condition-dropped", IX,
      (STORE, 'raise StoreError("store_unavailable", condition="namespace_unreadable")', 'raise StoreError("store_unavailable")')),
    m("IX-06-store-condition-detail-dropped", IX,
      (CONTRACTS, '            details["store_condition"] = store_condition\n', "            pass\n")),
    m("IX-07-store-condition-not-passed", IX,
      (SERV, ", store_condition=exc.condition) from None", ") from None")),
    m("IX-08-store-condition-on-every-reason", IX,
      (CONTRACTS, 'if reason == "store_unavailable" and store_condition in STORE_CONDITIONS:', "if store_condition is not None:")),
]

# Review 1 adds the HAMCP-226 suites and the review 1 corrections and probes
# (the probes last: they run in child interpreters and are the slowest).
# Review 2 adds its corrections and the reviewer's unchanged delta tests.
base.MODULES = ("test_hamcp228_baseline_store", "test_hamcp226_228_review1_corrections",
                "test_hamcp226_228_review2_corrections", "test_hamcp226_228_review2_delta_extra",
                "test_automation_baseline_capture", "test_automation_audit_baseline",
                "test_hamcp226_named_sources", "test_configuration_integrity_analysis",
                "test_entity_dependency_analysis", "test_hamcp226_228_review1_probes")
base.MUTATIONS = MUTATIONS

_run_tests = base.run_tests


def run_tests(tree: Path, timeout: int, *, failfast: bool) -> tuple[int, str, float]:
    scratch = tempfile.mkdtemp(prefix="hamcp228-mutant-")
    previous = os.environ.get("TMPDIR")
    os.environ["TMPDIR"] = scratch
    try:
        return _run_tests(tree, timeout, failfast=failfast)
    finally:
        if previous is None:
            del os.environ["TMPDIR"]
        else:
            os.environ["TMPDIR"] = previous
        shutil.rmtree(scratch, ignore_errors=True)


base.run_tests = run_tests


if __name__ == "__main__":
    raise SystemExit(base.main())
