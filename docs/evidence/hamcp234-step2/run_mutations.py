"""HAMCP-234 step 2 mutation runner (offline, deterministic).

Reuses the HAMCP-131 runner's machinery: a disposable copy of the tracked
tree outside the repository, with every fragment matching exactly once. It
also reuses the HAMCP-234 step 1 runner's per-run temporary directory.
Unlike those runners, each mutant runs its own module set, without
fail-fast, and is killed only when a named test fails. That proves the
suites the task names, not merely any test:

- ``S*``: ``user_pass_host`` still read in the squashed form in the journal
  (``S1`` the journal's option off, ``S2`` the squashed form always on,
  ``S3`` the form's gate ignored). The live-shaped benign tests in
  ``test_hamcp234_step2_journal_relaxation`` must fail.
- ``L*``: the relaxation leaking into the definitions or trace paths (``L1``
  into the default mode the definitions read, ``L2`` into every labelled
  read, ``L3`` the trace path opting in, ``L4`` everywhere). Only the
  HAMCP-132 (``test_hamcp132_configuration_definitions``, ``test_pr294_*``)
  or HAMCP-225 (``test_hamcp225_*``) suites run first, unchanged; whether one
  of them fails is recorded as ``killed_by_legacy_suites``. A second run of
  the step 2 suite must fail its cross-path boundary test. A mutant the
  legacy suites miss is reported, not hidden: the HAMCP-225 suites hold no
  text that only the squashed ``user_pass_host`` withholds.
- ``D*`` (correction batch, review of aaf1fe8e): contiguity judged only
  after decoding (``D1``, the reviewer's proxy reproduction), the text as
  given but no intermediate decode round (``D2``), or the rounds read without
  the normal form's NFKC, casefold and confusable fold (``D3``). The
  percent-encoded tests in ``test_hamcp234_step2_journal_relaxation`` must
  fail; for ``D1`` that includes the final authenticated gateway response.
  ``D4`` (correction D320-H1, review of c0789386): the pre-fix helper, the
  whole value read as given and after each round with no raw-token
  boundaries and no decoded whitespace kept in the token. The encoded
  separator tests must fail, including the final response and audit test
  and the ported reviewer regression.
- ``T1`` (correction batch): the reviewer's identity-only opt-in, the trace
  identity and name fields reading ``user_pass_host`` as one token
  (``contiguous_user_pass_host=not labelled``). As for ``L*``, the unchanged
  HAMCP-225 suites run first and are recorded; then the step 2 trace
  identity boundary tests must fail.
- ``C*``: ``calibration`` missing from the served ``get_incident_logs``
  schema (``C1`` removed from the advertised properties, ``C2`` removed from
  the tool). The served-schema test must fail.

Usage:
    python docs/evidence/hamcp234-step2/run_mutations.py \\
        --work /var/tmp/hamcp234s2-mut --output docs/evidence/hamcp234-step2/mutations.json
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import platform
import re
import shutil
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "hamcp131-mutations"))

import run_mutations as base  # noqa: E402

E = base.E
CRED = f"{E}/configuration_definitions/credentials.py"
HJ = f"{E}/incident_logs/host_journal.py"
TRACE = f"{E}/trace_normalization.py"
TOOL = f"{E}/tools/incident_logs.py"

STEP2 = "test_hamcp234_step2_journal_relaxation"
DEFINITIONS = ("test_hamcp132_configuration_definitions", "test_pr294_delta_review", "test_pr294_corr2_extra",
               "test_pr294_final32_review")
TRACES = ("test_hamcp225_trace_snapshots", "test_hamcp225_cross_path_credentials",
          "test_hamcp225_review1_reviewer_probes", "test_hamcp225_review1_store_probes",
          "test_hamcp225_review1_wire_probes", "test_hamcp225_review2_delta_extra")
LIVE_SHAPED = ("test_live_shaped_matter_js_lines_are_shown",
               "test_the_read_shows_the_live_shaped_lines_and_withholds_every_credential",
               "test_the_calibration_counts_the_live_shaped_lines_shown")
SERVED = ("test_the_served_schema_offers_calibration",)
BOUNDARY = ("test_definitions_and_trace_paths_still_withhold_the_live_shaped_lines",)
PROXY = ("test_the_reviewer_proxy_reproduction_is_withheld",
         "test_percent_encoded_whitespace_inside_a_credential_is_withheld",
         "test_the_proxy_credential_is_withheld_and_matter_js_shown_over_the_wire")
ENCODED_SEPARATORS = ("test_encoded_separators_decoded_with_whitespace_are_withheld",
                      "test_urlencoded_proxy_value_with_space_is_withheld_over_wire",
                      "test_every_encoded_separator_case_is_withheld_in_the_response_and_the_audit")
IDENTITY_BOUNDARY = ("test_the_named_fields_are_identity_keys_read_in_the_default_mode",
                     "test_capture_store_and_read_back_withhold_squashed_only_identity_credentials",
                     "test_get_automation_trace_withholds_squashed_only_identity_credentials")

SQUASHED_CALL = "                                      user_pass_host=not contiguous_user_pass_host)\n"
SCREEN_CALL = "            decided[(text, labelled)] = credentials.screen(text, known_secrets, labelled=labelled)\n"


def m(identifier: str, item: str, modules: tuple[str, ...], must_fail: tuple[str, ...], *replacements):
    """``must_fail``: test names, one of which must fail; empty means any failing test of ``modules``."""

    return {**base.m(identifier, item, *replacements), "modules": list(modules), "must_fail": list(must_fail)}


def leak(identifier: str, item: str, modules: tuple[str, ...], *replacements,
         boundary: tuple[str, ...] = BOUNDARY):
    """A leak mutant: the unchanged legacy suites first, then the step 2 boundary test."""

    return {**m(identifier, item, modules, (), *replacements), "boundary_modules": [STEP2],
            "boundary_must_fail": list(boundary)}


MUTATIONS = [
    # -- S: the journal reads user_pass_host as one contiguous token, normal form only
    m("S1-journal-option-off", "user_pass_host still on the squashed form in the journal", (STEP2,), LIVE_SHAPED,
      (HJ, '_JOURNAL_SCREEN = MappingProxyType({"labelled": True, "contiguous_user_pass_host": True})\n',
       '_JOURNAL_SCREEN = MappingProxyType({"labelled": True, "contiguous_user_pass_host": False})\n')),
    m("S2-squashed-form-always-on", "user_pass_host still on the squashed form in the journal", (STEP2,),
      LIVE_SHAPED, (CRED, SQUASHED_CALL, "                                      user_pass_host=True)\n")),
    m("S3-form-gate-ignored", "user_pass_host still on the squashed form in the journal", (STEP2,), LIVE_SHAPED,
      (CRED, "    if user_pass_host and _USER_PASS_HOST.search(text) is not None:\n",
       "    if _USER_PASS_HOST.search(text) is not None:\n")),
    # -- L: the definitions (HAMCP-132) and trace (HAMCP-225) paths are unchanged
    leak("L1-leak-into-default-mode", "the relaxation leaking into the definitions path", DEFINITIONS,
      (CRED, SQUASHED_CALL,
       "                                      user_pass_host=labelled and not contiguous_user_pass_host)\n")),
    leak("L2-leak-into-labelled-mode", "the relaxation leaking into the trace path", TRACES,
      (CRED, SQUASHED_CALL,
       "                                      user_pass_host=not (labelled or contiguous_user_pass_host))\n")),
    leak("L3-trace-path-opts-in", "the relaxation leaking into the trace path", TRACES,
      (TRACE, SCREEN_CALL,
       "            decided[(text, labelled)] = credentials.screen(text, known_secrets, labelled=labelled,\n"
       "                                                           contiguous_user_pass_host=True)\n")),
    leak("L4-leak-everywhere", "the relaxation leaking into the definitions and trace paths", DEFINITIONS + TRACES,
      (CRED, SQUASHED_CALL, "                                      user_pass_host=False)\n")),
    # -- D: contiguity judged before decoding too (correction batch, owner clarification 2026-10-28)
    m("D1-contiguity-only-after-decoding", "a percent-encoded split credential shown in the journal", (STEP2,),
      PROXY, (CRED, "    if contiguous_user_pass_host and _undecoded_user_pass_host(value):\n",
              "    if False:\n")),
    m("D2-no-intermediate-decode-round", "a doubly encoded split credential shown in the journal", (STEP2,),
      ("test_an_intermediate_decode_round_is_read",),
      (CRED, '            stage = unquote_to_bytes(stage).decode("utf-8", "strict")\n',
       "            return False\n")),
    m("D3-rounds-not-normalized", "a look-alike split credential shown in the journal", (STEP2,),
      ("test_each_round_is_normalized_and_folded_as_the_normal_form",),
      (CRED, '    text = fold_confusables(unicodedata.normalize("NFKC", squash(stage)).casefold())\n',
       "    text = squash(stage)\n")),
    m("D4-separator-only-stage-check", "an encoded separator decoded with the password whitespace shown",
      (STEP2,), ENCODED_SEPARATORS,
      (CRED, "    return any(_ESCAPE.search(token) and _decoded_token_user_pass_host(token)"
             " for token in value.split())\n",
       "    stage = value\n"
       "    for _round in range(MAX_DECODE_ROUNDS):\n"
       "        if not _ESCAPE.search(stage):\n"
       "            return False\n"
       '        if _USER_PASS_HOST.search(fold_confusables(unicodedata.normalize("NFKC", stage).casefold()))'
       " is not None:\n"
       "            return True\n"
       "        try:\n"
       '            stage = unquote_to_bytes(stage).decode("utf-8", "strict")\n'
       "        except (UnicodeError, ValueError):\n"
       "            return True\n"
       "    return False\n")),
    # -- T: the trace identity and name fields keep the squashed shape (correction batch)
    leak("T1-trace-identity-opts-in", "the relaxation leaking into the trace identity and name fields", TRACES,
         (TRACE, SCREEN_CALL,
          "            decided[(text, labelled)] = credentials.screen(text, known_secrets, labelled=labelled,\n"
          "                                                           contiguous_user_pass_host=not labelled)\n"),
         boundary=IDENTITY_BOUNDARY),
    # -- C: calibration is in the served schema
    m("C1-calibration-not-advertised", "calibration missing from the served schema", (STEP2,), SERVED,
      (TOOL, '    tool.parameters["additionalProperties"] = False\n',
       '    tool.parameters["additionalProperties"] = False\n'
       '    tool.parameters["properties"].pop("calibration", None)\n')),
    m("C2-calibration-removed", "calibration missing from the served schema", (STEP2,), SERVED,
      (TOOL, "    calibration: Annotated[bool, Field(strict=True)] = False,\n", ""),
      (TOOL, '"cursor": cursor, "calibration": calibration}\n', '"cursor": cursor}\n')),
]

_FAILED_TEST = re.compile(r"^(?:FAIL|ERROR): \w+ \(([\w.]+)\)", re.MULTILINE)


def outcome(output: str, code: int, required: list[str]) -> tuple[bool, list[str], set[str]]:
    failed = sorted(set(_FAILED_TEST.findall(output)))
    names = {item.rsplit(".", 1)[1] for item in failed}
    return code != 0 and bool(failed) and (not required or bool(names & set(required))), failed, names


def run(tree: Path, modules: list[str], timeout: int) -> tuple[int, str, float]:
    # Each run gets its own temporary directory, removed after it.
    scratch = tempfile.mkdtemp(prefix="hamcp234s2-mutant-")
    previous = os.environ.get("TMPDIR")
    os.environ["TMPDIR"] = scratch
    base.MODULES = tuple(modules)
    try:
        return base.run_tests(tree, timeout, failfast=False)
    finally:
        if previous is None:
            del os.environ["TMPDIR"]
        else:
            os.environ["TMPDIR"] = previous
        shutil.rmtree(scratch, ignore_errors=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--work", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--timeout", type=int, default=1800)
    arguments = parser.parse_args()
    work = arguments.work.expanduser().resolve()
    if work == base.ROOT or base.ROOT in work.parents:
        raise SystemExit("--work must be outside the repository")
    tree, log_dir = work / "tree", work / "mutation-logs"
    if tree.exists():
        shutil.rmtree(tree)
    top = base.tracked_top_level()
    common = shutil.ignore_patterns(".git", ".artifacts", "__pycache__", "*.pyc")

    def ignore(directory: str, names: list[str]) -> set[str]:
        skipped = set(common(directory, names))
        if Path(directory) == base.ROOT:  # untracked top-level entries stay out
            skipped |= {name for name in names if name not in top}
        return skipped

    shutil.copytree(base.ROOT, tree, symlinks=True, ignore=ignore)
    log_dir.mkdir(parents=True, exist_ok=True)
    if len({item["id"] for item in MUTATIONS}) != len(MUTATIONS):
        raise SystemExit("duplicate mutation id")
    for mutation in MUTATIONS:  # every fragment matches once before anything runs
        for path, text in base.apply(tree, mutation).items():
            path.write_text(text, encoding="utf-8")

    modules = list(dict.fromkeys(name for item in MUTATIONS
                                 for name in (*item["modules"], *item.get("boundary_modules", ()))))
    code, output, seconds = run(tree, modules, arguments.timeout)
    baseline_log = log_dir / "baseline.log"
    baseline_log.write_text(output, encoding="utf-8")
    summary = output.strip().splitlines()[-1] if output.strip() else ""
    ran = re.search(r"^Ran (\d+) tests?", output, re.MULTILINE)
    baseline = {"log_path": baseline_log.relative_to(work).as_posix(), "modules": modules, "returncode": code,
                "tests": int(ran.group(1)) if ran else None, "seconds": round(seconds, 1), "summary": summary}
    print(f"baseline rc={code} {summary} ({seconds:.0f}s)", flush=True)
    if code != 0:
        print(output[-4000:])
        return 2

    results = []
    for mutation in MUTATIONS:
        originals = base.apply(tree, mutation)
        boundary = None
        try:
            code, output, seconds = run(tree, mutation["modules"], arguments.timeout)
            if "boundary_modules" in mutation:
                boundary = run(tree, mutation["boundary_modules"], arguments.timeout)
        finally:
            for path, text in originals.items():
                path.write_text(text, encoding="utf-8")
        required = mutation["must_fail"]
        killed, failed, names = outcome(output, code, required)
        log_path = log_dir / f"{mutation['id']}.log"
        log_path.write_text(output, encoding="utf-8")
        result = {
            **mutation, "log_path": log_path.relative_to(work).as_posix(), "returncode": code, "killed": killed,
            "killed_by_import_or_syntax_error": any(
                marker in output for marker in ("ImportError", "SyntaxError", "Failed to import")),
            "required_failed": sorted(names & set(required)), "failed_tests": failed[:40],
            "failed_test_count": len(failed), "seconds": round(seconds, 1),
        }
        if boundary is not None:
            b_code, b_output, b_seconds = boundary
            b_required = mutation["boundary_must_fail"]
            b_killed, b_failed, b_names = outcome(b_output, b_code, b_required)
            b_log = log_dir / f"{mutation['id']}-boundary.log"
            b_log.write_text(b_output, encoding="utf-8")
            result.update({
                "killed_by_legacy_suites": killed, "killed": killed or b_killed,
                "boundary": {"log_path": b_log.relative_to(work).as_posix(), "returncode": b_code,
                             "killed": b_killed, "required_failed": sorted(b_names & set(b_required)),
                             "failed_tests": b_failed[:40], "seconds": round(b_seconds, 1)},
            })
        results.append(result)
        legacy = "" if boundary is None else (
            f" legacy={'KILLED' if killed else 'SURVIVED'} boundary={'KILLED' if result['boundary']['killed'] else 'SURVIVED'}")
        print(f"{'KILLED' if result['killed'] else 'SURVIVED':8} {mutation['id']:34} {len(failed)} failed "
              f"{sorted(names & set(required)) or failed[:1]}{legacy}", flush=True)

    survived = [item["id"] for item in results if not item["killed"]]
    legacy_missed = [item["id"] for item in results if item.get("killed_by_legacy_suites") is False]
    document = {"baseline": baseline, "mutation_count": len(results),
                "killed": sum(item["killed"] for item in results), "survived": survived,
                "missed_by_unchanged_legacy_suites": legacy_missed,
                "python": platform.python_version(), "results": results}
    arguments.output.write_text(json.dumps(document, indent=1) + "\n", encoding="utf-8")
    return 1 if survived else 0


if __name__ == "__main__":
    raise SystemExit(main())
