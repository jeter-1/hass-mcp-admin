"""HAMCP-288 delta evidence: regressions on the reviewed head, mutants on the fix.

Run from the repository root with the test interpreter, after committing:

    python docs/evidence/hamcp288/run_mutations.py

Everything runs in a disposable ``git clone --shared`` of HEAD under
``.artifacts/mutation-hamcp288/``; the tree under test is never mutated. All
secrets are synthetic canaries the tests generate at run time; nothing here
reads configuration, logs or a real secret path, and no network is used.

1. Regressions: the corrected test module, run against each reviewed head
   (7d8f2158 for F1-F3, fe48d80f for the round-3 F1 residual R1, 2524b2f7
   for the round-3 Low L1, a separator-only decoded secret path, and
   d4e37618 for L1R, a route-word-only decoded secret path, Josh decision
   2026-11-07), must fail each named regression test by an assertion.
2. Mutants: each mutant reopens one reviewed defect in the corrected source
   and is killed only by an assertion failure in its named test; an error,
   import failure or survival is not a kill.
3. Control: the unmutated corrected copy passes the whole module.
"""
import json
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path.cwd()
OUT = ROOT / ".artifacts/mutation-hamcp288"
REVIEWED = "7d8f21580a7d5677a3875d7c0e2f70b4073cb530"
# Round 3: the head whose review left the F1 residual (short decoded path).
REVIEWED_R3 = "fe48d80fbb95f83283a604c0395d5494f0b81bb2"
# Round 4: the head whose review left the Low L1 (separator-only decoded path).
REVIEWED_R4 = "2524b2f7b8c5458bcc6bfaa423bb2e72754c2bed"
# Round 4 follow-up: the pushed round-4 head before the route-word refusal
# (Josh decision 2026-11-07).
REVIEWED_R4B = "d4e3761815d68afe8b4fe48cc0fe5cd1ee3c5358"
MODULE = "tests.test_hamcp288_secret_path_containment"
TEST_FILE = "tests/test_hamcp288_secret_path_containment.py"
SECRET_PATHS = "hass_mcp_engineering_beta/ha_mcp_engineering/secret_paths.py"
RESPONSES = "hass_mcp_engineering_beta/ha_mcp_engineering/models/responses.py"
CONFIGURATION = "hass_mcp_engineering_beta/ha_mcp_engineering/configuration.py"
APPLICATION = "hass_mcp_engineering_beta/ha_mcp_engineering/application.py"
G = "GrammarContainmentTests."
E = "SerializedEscapeTests."
O = "StreamOverflowTests."
D = "DecodedShortPathTests."
S = "SeparatorOnlySecretPathTests."
R = "RouteOnlySecretPathTests."

shutil.rmtree(OUT, ignore_errors=True)
OUT.mkdir(parents=True)
HEAD = subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()
COPY = OUT / "copy"
subprocess.check_call(["git", "clone", "-q", "--shared", "--no-checkout", str(ROOT), str(COPY)])


def checkout(revision: str) -> None:
    subprocess.check_call(["git", "-C", str(COPY), "checkout", "-q", "--force", revision])
    subprocess.check_call(["git", "-C", str(COPY), "clean", "-q", "-fdx"])


def run(test: str, log: str) -> tuple[int, bytes]:
    for cache in COPY.rglob("__pycache__"):
        shutil.rmtree(cache)
    proc = subprocess.run([sys.executable, "-B", "-m", "unittest", f"{MODULE}.{test}" if test else MODULE],
                          cwd=COPY, capture_output=True, timeout=900)
    raw = proc.stdout + proc.stderr
    (OUT / log).write_bytes(raw)
    return proc.returncode, raw


def assertion_failure(code: int, raw: bytes) -> bool:
    return bool(code) and b"FAIL:" in raw and b"AssertionError" in raw and b"ERROR:" not in raw


regressions = [
    ("F1", G + "test_every_accepted_custom_path_is_configured_and_contained_on_every_surface"),
    ("F1", G + "test_selector_words_are_credentials_only_in_credential_position"),
    ("F1", G + "test_the_process_streams_contain_each_edge_value"),
    ("F1", G + "test_refused_values_never_reach_an_output_and_unparseable_ones_fail_closed"),
    ("F2", E + "test_every_serialization_depth_is_recognized"),
    ("F2", E + "test_responses_streams_and_listeners_never_reconstruct_the_path"),
    ("F2", E + "test_the_renderer_screens_values_before_serialization"),
    ("F3", O + "test_the_reviewed_alignment_is_redacted"),
    ("F3", O + "test_every_split_across_the_bound_in_every_write_pattern"),
]
regressions_r3 = [
    ("R1", G + "test_every_accepted_custom_path_is_configured_and_contained_on_every_surface"),
    ("R1", G + "test_selector_words_are_credentials_only_in_credential_position"),
    ("R1", D + "test_the_complete_decoded_path_is_contained_on_every_surface"),
    ("R1", D + "test_split_writes_never_reassemble_the_decoded_path"),
    ("R1", D + "test_the_process_streams_contain_the_decoded_path"),
]
regressions_r4 = [
    ("L1", S + "test_the_secret_path_is_refused_with_a_reason_naming_the_option"),
    ("L1", S + "test_the_operator_sees_the_reason_at_startup_and_nothing_starts"),
    ("L1", S + "test_configured_anyway_the_endpoint_suffix_is_never_an_anchor"),
]
regressions_r4b = [
    ("L1R", R + "test_the_secret_path_is_refused_with_a_reason_naming_the_option"),
    ("L1R", R + "test_the_operator_sees_the_reason_at_startup_and_nothing_starts"),
    ("L1R", R + "test_configured_anyway_the_endpoint_suffix_is_never_an_anchor"),
    ("L1R", G + "test_every_accepted_custom_path_is_configured_and_contained_on_every_surface"),
    ("L1R", G + "test_selector_words_are_credentials_only_in_credential_position"),
]
ROUTE_REFUSAL = "        raise RouteOnlySecretPathError(ROUTE_ONLY_SECRET_PATH_REASON)\n"
GUARD = "    unanchorable = _separator_only(raw_parts) or _route_only(raw_parts)\n"
ROUTE_WORD = "    return part in SELECTOR_WORDS or part.lower() in ROUTE_SEGMENTS\n"
EXCEPT = "    except (SeparatorOnlySecretPathError, RouteOnlySecretPathError) as exc:\n"
REFUSAL = "        raise SeparatorOnlySecretPathError(SEPARATOR_ONLY_SECRET_PATH_REASON)\n"
DERIVED = "    return _longest_first(kept + added), credentials, tuple(whole_paths)\n"
WHOLE = "            + [_sequence(path) + _WHOLE_PATH_END for path in whole_paths]\n"
CREDENTIAL = '        credential.append("/".join(base))\n        credential.extend(base)\n'
OLD_STRIP = ('        base = [item for item in base if item.lower() not in {"readonly", "catalog", "full", "search"}]\n'
             '        credential.append("/".join(base))\n        credential.extend(base)\n')
OVERFLOW = "            while len(self._pending) > self.MAX_PENDING:\n                self._emit_bounded()\n"
FLUSH_ALL = ('            if len(self._pending) > self.MAX_PENDING:\n'
             '                ready, self._pending = self._pending, ""\n'
             '                self._target.write(redact_lines(ready))\n')
HOLD = "        hold = min(state.tail_window, self.MAX_PENDING // 2) if state.text is not None else 0\n"
mutants = [
    ("F1", "an accepted value without a long credential installs no guard", SECRET_PATHS, DERIVED,
     "    return (_longest_first(kept + added) if credentials else ()), credentials, tuple(whole_paths)\n",
     G + "test_every_accepted_custom_path_is_configured_and_contained_on_every_surface"),
    ("F1", "selector words dropped from credential position", SECRET_PATHS, CREDENTIAL, OLD_STRIP,
     G + "test_selector_words_are_credentials_only_in_credential_position"),
    ("F2", "single-backslash Unicode escape only", SECRET_PATHS, '    return r"\\\\+u" + "".join(',
     '    return r"\\\\u" + "".join(', E + "test_every_serialization_depth_is_recognized"),
    ("F2", "renderer screens only on a serialized match", RESPONSES,
     "    if secret_paths.configured():\n        # HAMCP-288",
     "    if secret_paths.configured() and secret_paths.contains(json.dumps(data, indent=2, default=str)):\n"
     "        # HAMCP-288", E + "test_the_renderer_screens_values_before_serialization"),
    ("F3", "overflow flushes the whole buffer and forgets the cut", SECRET_PATHS, OVERFLOW, FLUSH_ALL,
     O + "test_the_reviewed_alignment_is_redacted"),
    ("F3", "no held window at the bound", SECRET_PATHS, HOLD, "        hold = 0\n",
     O + "test_every_split_across_the_bound_in_every_write_pattern"),
    ("F3", "the stream's own cut is not evidence", SECRET_PATHS,
     "                self._target.write(redact_lines(ready, cut_before=self._cut))\n",
     "                self._target.write(redact_lines(ready))\n",
     O + "test_a_held_tail_after_a_bounded_write_keeps_its_cut_evidence"),
    ("R1", "the complete decoded path filtered by the fragment threshold again", SECRET_PATHS, WHOLE,
     "            + [_sequence(path) + _WHOLE_PATH_END for path in whole_paths if len(path) >= MIN_FRAGMENT]\n",
     D + "test_the_complete_decoded_path_is_contained_on_every_surface"),
    ("R1", "the short whole path matched without its end boundary", SECRET_PATHS,
     '_WHOLE_PATH_END = r"(?![\\w\\-])"\n', '_WHOLE_PATH_END = ""\n',
     D + "test_ordinary_text_hosts_and_bare_words_are_kept"),
    ("L1", "a separator-only secret path accepted by validation again", CONFIGURATION,
     "    if secret_paths.separator_only_secret_path(candidate):\n", "    if False:\n",
     S + "test_the_secret_path_is_refused_with_a_reason_naming_the_option"),
    ("L1", "the endpoint suffix installed as an anchor when configured anyway", SECRET_PATHS, GUARD,
     "    unanchorable = _route_only(raw_parts)\n",
     S + "test_configured_anyway_the_endpoint_suffix_is_never_an_anchor"),
    ("L1", "the operator sees only the generic reason", APPLICATION, EXCEPT,
     "    except RouteOnlySecretPathError as exc:\n",
     S + "test_the_operator_sees_the_reason_at_startup_and_nothing_starts"),
    ("L1", "the reason carries the configured value", CONFIGURATION, REFUSAL,
     '        raise SeparatorOnlySecretPathError(f"{SEPARATOR_ONLY_SECRET_PATH_REASON}: {candidate}")\n',
     S + "test_the_secret_path_is_refused_with_a_reason_naming_the_option"),
    ("L1R", "a route-word-only secret path accepted by validation again", CONFIGURATION,
     "    if secret_paths.route_only_secret_path(candidate):\n", "    if False:\n",
     R + "test_the_secret_path_is_refused_with_a_reason_naming_the_option"),
    ("L1R", "the guard not symmetric: a route-only span installs its decoded base", SECRET_PATHS, GUARD,
     "    unanchorable = _separator_only(raw_parts)\n",
     R + "test_configured_anyway_the_endpoint_suffix_is_never_an_anchor"),
    ("L1R", "selector words are not route words (route set hard-coded to mcp/sse)", SECRET_PATHS, ROUTE_WORD,
     "    return part.lower() in ROUTE_SEGMENTS\n",
     R + "test_the_secret_path_is_refused_with_a_reason_naming_the_option"),
    ("L1R", "route segments compared in exact case", SECRET_PATHS, ROUTE_WORD,
     "    return part in SELECTOR_WORDS or part in ROUTE_SEGMENTS\n",
     R + "test_the_secret_path_is_refused_with_a_reason_naming_the_option"),
    ("L1R", "a route word with other content refused (substring match)", SECRET_PATHS, ROUTE_WORD,
     "    return any(word in part.lower() for word in SELECTOR_WORDS | ROUTE_SEGMENTS)\n",
     R + "test_paths_with_other_content_are_accepted_and_unchanged"),
    ("L1R", "the operator sees only the generic reason", APPLICATION, EXCEPT,
     "    except SeparatorOnlySecretPathError as exc:\n",
     R + "test_the_operator_sees_the_reason_at_startup_and_nothing_starts"),
    ("L1R", "the reason carries the configured value", CONFIGURATION, ROUTE_REFUSAL,
     '        raise RouteOnlySecretPathError(f"{ROUTE_ONLY_SECRET_PATH_REASON}: {candidate}")\n',
     R + "test_the_secret_path_is_refused_with_a_reason_naming_the_option"),
]

results = {"head": HEAD, "reviewed_head": REVIEWED, "reviewed_head_r3": REVIEWED_R3,
           "reviewed_head_r4": REVIEWED_R4, "reviewed_head_r4b": REVIEWED_R4B,
           "python": sys.version.split()[0], "regressions": [], "mutants": [], "control": None}
corrected_tests = subprocess.check_output(["git", "-C", str(ROOT), "show", f"{HEAD}:{TEST_FILE}"])
index = 0
for revision, tests in ((REVIEWED, regressions), (REVIEWED_R3, regressions_r3), (REVIEWED_R4, regressions_r4),
                         (REVIEWED_R4B, regressions_r4b)):
    checkout(revision)
    (COPY / TEST_FILE).write_bytes(corrected_tests)
    for finding, test in tests:
        index += 1
        code, raw = run(test, f"regression-{index:02d}.log")
        classification = "assertion" if assertion_failure(code, raw) else ("invalid" if code else "passed")
        results["regressions"].append({"id": index, "finding": finding, "revision": revision, "test": test,
                                       "classification": classification, "exit_code": code})
        print("regression", index, finding, revision[:8], classification, flush=True)

checkout(HEAD)
for index, (finding, name, file, before, after, test) in enumerate(mutants, 1):
    path = COPY / file
    original = path.read_text()
    assert original.count(before) == 1, (name, original.count(before))
    path.write_text(original.replace(before, after))
    code, raw = run(test, f"mutant-{index:02d}.log")
    classification = "assertion" if assertion_failure(code, raw) else ("invalid" if code else "survived")
    results["mutants"].append({"id": index, "finding": finding, "name": name, "file": file, "test": test,
                               "classification": classification, "exit_code": code})
    path.write_text(original)
    print("mutant", index, finding, name, classification, flush=True)

code, raw = run("", "control.log")
ran = [line for line in raw.decode(errors="replace").splitlines() if line.startswith("Ran ")]
results["control"] = {"name": "control (unmutated corrected copy, whole module)",
                      "classification": "pass" if code == 0 else "fail", "exit_code": code,
                      "ran": ran[-1] if ran else None}
(OUT / "results.json").write_text(json.dumps(results, indent=2) + "\n")
print(json.dumps(results["control"]), flush=True)
if (code or any(item["classification"] != "assertion" for item in results["regressions"])
        or any(item["classification"] != "assertion" for item in results["mutants"])):
    sys.exit(1)
