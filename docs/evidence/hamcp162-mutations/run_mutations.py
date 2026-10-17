"""HAMCP-162 design section 8.2 mutation runner (offline, deterministic).

Each mutation is an exact textual replacement in shipped source (every old
fragment must match exactly once), applied to a disposable copy of the
working tree outside the repository. The named test modules then run; a
mutation is killed when they fail. Nothing in the repository is modified.

Usage:
    python docs/evidence/hamcp162-mutations/run_mutations.py \
        --work ~/tmp/hamcp162-runs/mut --output ~/tmp/hamcp162-runs/mut/results.json

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

ROOT = Path(__file__).resolve().parents[3]
E = "hass_mcp_engineering_beta/ha_mcp_engineering"
READER = f"{E}/providers/upstream_yaml_reader.py"
GATEWAY = f"{E}/providers/upstream_read_gateway.py"
SANITIZATION = f"{E}/sanitization.py"
POLICY = f"{E}/upstream_tool_policy.py"
HA_MCP = f"{E}/ha_mcp_readmission/ha_mcp.py"
COORDINATOR = f"{E}/ha_mcp_readmission/coordinator.py"
CORE_ROUTES = f"{E}/ha_core_readmission/routes.py"

T_READER = "tests.test_hamcp162_yaml_reader"
T_ADMISSION = "tests.test_hamcp162_yaml_admission"
T_SANITIZATION = "tests.test_beta11_sanitization"
BOTH = (T_READER, T_ADMISSION)

CREDENTIAL_WORDS = (
    "pwd", "passw", "secret", "token", "apikey", "api_key", "api-key", "auth",
    "credential", "private", "bearer", "cookie", "session",
)


def m(identifier, item, path, replacements, tests=(T_READER,)):
    return {
        "id": identifier,
        "design_item": item,
        "path": path,
        "replacements": [list(pair) for pair in replacements],
        "tests": list(tests),
    }


MUTATIONS = [
    # L1-L3 and the upstream argument shape.
    m("L1-drop", "dropping L1", READER, [(
        "if not l1_allows(file_value) or l2_denies(file_value):",
        "if l2_denies(file_value):",
    )]),
    m("L2-drop", "dropping L2 against a widened L1", READER, [(
        "if not l1_allows(file_value) or l2_denies(file_value):",
        "if not l1_allows(file_value):",
    )]),
    m("file-forward-caller", "forwarding the caller's file instead of the constant", GATEWAY, [(
        "dispatch_arguments = yaml_reader.upstream_arguments(\n"
        "                    yaml_reader.validate_arguments(arguments)\n"
        "                )",
        "dispatch_arguments = dict(yaml_reader.upstream_arguments(\n"
        "                    yaml_reader.validate_arguments(arguments)\n"
        "                ), file=arguments.get(\"file\"))",
    )], BOTH),
    m("L1-yml-and-case", "accepting configuration.yml or a case variant", READER, [(
        "return type(file_value) is str and file_value == ALLOWED_FILE",
        "return type(file_value) is str and file_value.lower() in (ALLOWED_FILE, \"configuration.yml\")",
    )]),
    m("L3-drop", "dropping L3", READER, [(
        "if any(is_credential_key(segment.lower()) for segment in yaml_path.split(\".\")):",
        "if False:",
    )]),
    m("include-content-true", "forwarding include_content: true", READER, [(
        "\"include_content\": False,", "\"include_content\": True,",
    )], BOTH),
    # L4 path context.
    m("L4-node-only", "computing L4 key context from the returned node only", READER, [(
        "walker.redact(kept, list(prefix), [segment.lower() for segment in prefix], 1)",
        "walker.redact(kept, list(prefix), [], 1)",
    )]),
    m("L4-walk-credential-container", "walking into a container under a sensitive key", READER, [(
        "        if first_credential is not None:\n            if first_credential",
        "        if first_credential is not None and type(value) not in (dict, list):\n            if first_credential",
    )]),
    # Rule A.
    m("A-decode-once", "rule A: decoding only once", READER, [(
        "for count in range(MAX_DECODE_ROUNDS):", "for count in range(1):",
    )]),
    m("A-bound-3", "rule A: a bound of 3", READER, [(
        "MAX_DECODE_ROUNDS = 4\n", "MAX_DECODE_ROUNDS = 3\n",
    )]),
    m("A-bound-5", "rule A: a bound of 5", READER, [(
        "MAX_DECODE_ROUNDS = 4\n", "MAX_DECODE_ROUNDS = 5\n",
    )]),
    m("A-no-final-escape", "rule A: no final escape check", READER, [(
        "    if _ESCAPE.search(text):\n        raise _EncodingRefused(\"non_converging\")",
        "    if False:\n        raise _EncodingRefused(\"non_converging\")",
    )]),
    m("A-no-nfkc", "rule A: dropping NFKC", READER, [(
        "decoded = unicodedata.normalize(\"NFKC\", value)", "decoded = value",
    )]),
    m("A-no-casefold", "rule A: dropping casefold", READER, [(
        "text = decoded.casefold()", "text = decoded",
    )]),
    m("A-judge-raw", "rule A: judging the raw form instead of the canonical text", READER, [(
        "        if credential_text(text):\n            replacement, category = redaction_form(text, self.known_secrets)",
        "        if credential_text(value):\n            replacement, category = redaction_form(text, self.known_secrets)",
    ), (
        "            if not sqlite_visible(value, text, rounds):",
        "            if not sqlite_visible(value, value, 0):",
    )]),
    m("A-emit-canonical", "rule A: emitting the canonical text instead of the raw string", READER, [(
        "# Visible values are emitted raw, never in canonical form.\n        return value",
        "# Visible values are emitted raw, never in canonical form.\n        return text",
    )]),
    # Rule B.
    m("B-drop", "rule B: dropping B", READER, [(
        "connection = any(is_connection_key(key) for key in cpath)", "connection = False",
    )]),
    m("B-exact-names", "rule B: limiting B to exact key names", READER, [(
        "connection = any(is_connection_key(key) for key in cpath)",
        "connection = any(key in _CONNECTION_WORDS for key in cpath)",
    )]),
    m("B-raw-keys", "rule B / R10: matching raw instead of canonical key names", READER, [(
        "key: self.redact(child, display + [str(key)], cpath + [canonical_key], depth + 1)",
        "key: self.redact(child, display + [str(key)], cpath + [str(key)], depth + 1)",
    )]),
    m("B-envelope-key", "rule B: key context from the envelope key, not the absolute path", READER, [(
        "connection = any(is_connection_key(key) for key in cpath)",
        "connection = any(is_connection_key(key) for key in cpath[getattr(self, 'prefix_len', 0):])",
    ), (
        "        walker = _L4(known_secrets, registry)\n",
        "        walker = _L4(known_secrets, registry)\n        walker.prefix_len = len(prefix)\n",
    )]),
    m("B-walk-connection-dict", "rule B: walking into a container under a connection key", READER, [(
        "            if connection:\n                self.account.record(\"connection_string\")\n"
        "                return self._mark(CONNECTION_STRING_MARKER)\n            canonical_keys = []",
        "            canonical_keys = []",
    )]),
    m("B-walk-connection-list", "rule B: walking into a list under a connection key", READER, [(
        "        if type(value) is list:\n            if connection:\n"
        "                self.account.record(\"connection_string\")\n"
        "                return self._mark(CONNECTION_STRING_MARKER)\n",
        "        if type(value) is list:\n",
    )]),
    # R11.
    m("R11-match-dollar", "R11: re.match with $ instead of fullmatch", READER, [(
        "if not _SQLITE.fullmatch(canonical_text) or",
        "if not re.match(_SQLITE.pattern + \"$\", canonical_text) or",
    )]),
    m("R11-no-dotdot", "R11: dropping the .. segment check", READER, [(
        "    if \"..\" in canonical_text.split(\"/\"):\n        return False",
        "    if False:\n        return False",
    )]),
    m("R11-no-raw-equals", "R11: dropping raw-equals-canonical", READER, [(
        "return rounds == 0 and canonical_text == raw", "return True",
    )]),
    # Rule C'.
    m("C1-drop", "rule C': dropping C'1", READER, [(
        "        _userinfo(canonical_text)\n        or any(word in canonical_text",
        "        False\n        or any(word in canonical_text",
    )]),
    m("C2-drop", "rule C': dropping C'2", READER, [(
        "        or any(word in canonical_text for word in CREDENTIAL_WORDS)\n", "",
    )]),
    *[
        m(f"C2-word-{word}", f"rule C': dropping the word {word!r}", READER, [(
            "\n_COMPACT_WORDS = ",
            f"\nCREDENTIAL_WORDS = tuple(w for w in CREDENTIAL_WORDS if w != {word!r})"
            "\n_COMPACT_WORDS = ",
        )])
        for word in CREDENTIAL_WORDS
    ],
    m("C-rev4-double-equals", "rule C': restoring revision 4's == exemption", READER, [(
        "        or any(word in canonical_text for word in CREDENTIAL_WORDS)\n",
        "        or any(word in canonical_text and \"==\" not in canonical_text for word in CREDENTIAL_WORDS)\n",
    )]),
    m("C-partial-redaction", "rule C': redacting only the matching part", READER, [(
        "        if credential_text(text):\n            replacement, category = redaction_form(text, self.known_secrets)\n"
        "            self.account.record(category)\n            return self._mark(replacement)",
        "        if credential_text(text):\n            replacement, category = redaction_form(text, self.known_secrets)\n"
        "            self.account.record(category)\n"
        "            return re.sub(\"|\".join(map(re.escape, CREDENTIAL_WORDS)) + r\"\\S*\", \"[REDACTED]\", value, flags=re.I)",
    )]),
    m("C-keep-credential-scheme", "rule C': keeping a scheme that holds a credential word", READER, [(
        "if match and not credential_text(match[1]) and", "if match and",
    )]),
    m("C3-drop", "rule C': dropping C'3", READER, [(
        "_DELIMITED_TOKEN = re.compile(r\"\\b(pass|key)\\s*[=:]\")",
        "_DELIMITED_TOKEN = re.compile(r\"(?!x)x\")",
    )]),
    m("C3-no-word-boundary", "rule C': C'3 without \\b", READER, [(
        "_DELIMITED_TOKEN = re.compile(r\"\\b(pass|key)\\s*[=:]\")",
        "_DELIMITED_TOKEN = re.compile(r\"(pass|key)\\s*[=:]\")",
    )]),
    m("C3-plain-substring", "rule C': C'3 as a plain substring", READER, [(
        "        or any(word in canonical_text for word in CREDENTIAL_WORDS)\n",
        "        or any(word in canonical_text for word in CREDENTIAL_WORDS + (\"pass\", \"key\"))\n",
    )]),
    # R10 / K1 and key matching.
    m("K1-drop", "R10: dropping K1", READER, [(
        "                if key_text_unsafe(canonical_key):", "                if False:",
    )]),
    m("key-separator-sensitive", "making key matching separator-sensitive", READER, [(
        "normalized = re.sub(\"[^a-z0-9]+\", \"_\", canonical_key).strip(\"_\")",
        "normalized = canonical_key",
    ), (
        "def _compact(text: str) -> str:\n    return re.sub(\"[^a-z0-9]\", \"\", text)",
        "def _compact(text: str) -> str:\n    return text",
    )]),
    m("key-case-sensitive", "making key matching case-sensitive", READER, [(
        "                    canonical_key = canonical(str(key))[0]",
        "                    canonical(str(key))\n                    canonical_key = str(key)",
    ), (
        "[segment.lower() for segment in prefix]", "list(prefix)",
    ), (
        "if any(is_credential_key(segment.lower()) for segment in yaml_path.split(\".\")):",
        "if any(is_credential_key(segment) for segment in yaml_path.split(\".\")):",
    )]),
    # Reference markers and L5.
    m("L5-isinstance", "exempting references at L5 by isinstance instead of exact type", SANITIZATION, [(
        "        if type(value) is ReferenceMarker:\n            return self._reference_marker(value)",
        "        if isinstance(value, ReferenceMarker):\n            return self._reference_marker(value)",
    ), (
        "        return self.minted_markers is not None and self.minted_markers.minted(value)",
        "        return self.minted_markers is not None and isinstance(value, ReferenceMarker)",
    )], (T_READER, T_SANITIZATION)),
    m("L5-json-shape", "exempting references at L5 by JSON shape", SANITIZATION, [(
        "        if type(value) is ReferenceMarker:\n            return self._reference_marker(value)",
        "        if type(value) is ReferenceMarker:\n            return self._reference_marker(value)\n"
        "        if self.minted_markers is not None and isinstance(value, Mapping) and "
        "set(value) <= {\"secret_ref\", \"env_var_ref\", \"default_redacted\"} and value:\n"
        "            return dict(value)",
    )], (T_READER, T_SANITIZATION)),
    m("L5-default-on", "changing default-off L5 output", SANITIZATION, [(
        "        return self.minted_markers is not None and self.minted_markers.minted(value)",
        "        return type(value) is ReferenceMarker if self.minted_markers is None "
        "else self.minted_markers.minted(value)",
    )], (T_READER, T_SANITIZATION)),
    # HAMCP-162 Part 2 (H2): markers are trusted by registry identity only.
    m("H2-L5-type-not-identity", "exempting references at L5 by exact type without registry identity",
      SANITIZATION, [(
        "        return self.minted_markers is not None and self.minted_markers.minted(value)",
        "        return self.minted_markers is not None and type(value) is ReferenceMarker",
    )], (T_READER, T_SANITIZATION)),
    m("H2-registry-by-equality", "registry membership by marker equality instead of identity",
      SANITIZATION, [(
        "        return type(value) is ReferenceMarker and self._minted.get(id(value)) is value",
        "        return type(value) is ReferenceMarker and value in self._minted.values()",
    )], (T_READER, T_SANITIZATION)),
    m("H2-render-by-type", "rendering any ReferenceMarker, not only this read's minted ones", READER, [(
        "        if not registry.minted(value) or not value.valid():",
        "        if not value.valid():",
    )]),
    m("H2-exempt-by-shape", "exempting markers by shape (rendering marker-shaped mappings)", READER, [(
        "        if frozenset(value) in _COLLISION_KEY_SETS:\n"
        "            account.record(\"reference_marker_collision\")\n"
        "            return COLLISION_MARKER\n"
        "        return {key: render_markers(child, registry, account)",
        "        if frozenset(value) in _COLLISION_KEY_SETS:\n"
        "            return dict(value)\n"
        "        return {key: render_markers(child, registry, account)",
    ), (
        "if type(value) is dict and frozenset(value) in _COLLISION_KEY_SETS:", "if False:",
    )]),
    m("H2-redact-before-truncation", "redacting before node truncation (the superseded order)", READER, [(
        "        kept, omitted = truncate_breadth_first(parsed)\n"
        "        if omitted:\n"
        "            partial = True\n"
        "            limitations.append(f\"value_truncated:{omitted} nodes omitted\")\n"
        "        prefix = yaml_path.split(\".\")\n"
        "        walker = _L4(known_secrets, registry)\n"
        "        redacted_tree = walker.redact(kept, list(prefix), [segment.lower() for segment in prefix], 1)\n",
        "        prefix = yaml_path.split(\".\")\n"
        "        walker = _L4(known_secrets, registry)\n"
        "        redacted_tree = walker.redact(parsed, list(prefix), [segment.lower() for segment in prefix], 1)\n"
        "        redacted_tree, omitted = truncate_breadth_first(redacted_tree)\n"
        "        if omitted:\n"
        "            partial = True\n"
        "            limitations.append(f\"value_truncated:{omitted} nodes omitted\")\n",
    )]),
    # HAMCP-162 Part 2 (H1): no client-visible field bypasses the pipeline.
    m("H1-limitations-unsealed", "skipping sanitization on the limitations field", READER, [(
        "        self.account.includes.append(project_path(display, self.known_secrets))",
        "        self.account.includes.append(\".\".join(display))",
    ), (
        "        \"limitations\": fields[\"limitations\"],",
        "        \"limitations\": limitations,",
    )]),
    m("H1-limitations-seal-only", "relying on the final seal alone for include paths (no key projection)",
      READER, [(
        "        self.account.includes.append(project_path(display, self.known_secrets))",
        "        self.account.includes.append(\".\".join(display))",
    )]),
    m("H1-path-echo-raw", "echoing the requested path without the key projection", READER, [(
        "            \"yaml_path\": project_path(yaml_path.split(\".\"), known_secrets),",
        "            \"yaml_path\": yaml_path,",
    )]),
    m("H1-envelope-unsealed", "returning envelope fields around the final seal", READER, [(
        "    fields, sealed, sealed_categories = seal(",
        "    fields, sealed, sealed_categories = (lambda value, _ks: (value, 0, set()))(",
    )]),
    m("H1-gateway-success-unsealed", "returning gateway success fields around the seal", GATEWAY, [(
        "            if yaml_route:\n                # H1: no field of a YAML read response bypasses the pipeline.",
        "            if False:\n                # H1: no field of a YAML read response bypasses the pipeline.",
    )]),
    m("H1-gateway-failure-unsealed", "returning gateway failure fields around the seal", GATEWAY, [(
        "            if is_yaml_adapter(policy_entry):\n                # H1: error details",
        "            if False:\n                # H1: error details",
    )]),
    m("H1-seal-constants-unchecked", "keeping any string through the seal, not only source constants",
      READER, [(
        "        if original in vocabulary:\n            return original\n",
        "        if True:\n            return original\n",
    )]),
    m("mint-malformed", "minting a marker for a malformed reference", READER, [(
        "        match = pattern.fullmatch(value)\n", "        match = pattern.match(value)\n",
    )]),
    m("collision-skip", "skipping the reference-marker collision check", READER, [(
        "if type(value) is dict and frozenset(value) in _COLLISION_KEY_SETS:", "if False:",
    )]),
    m("env-default-kept", "keeping !env_var defaults", READER, [(
        "        match = _ENV_REF_DEFAULT.fullmatch(value)\n        if not match:\n            return None",
        "        match = None\n        if not match:\n            return None",
    )]),
    m("env-default-in-marker", "keeping !env_var defaults inside the marker", READER, [(
        "kind, name, default_redacted = \"env_var\", match[1], True",
        "kind, name, default_redacted = \"env_var\", match[0][len(\"!env_var \"):], True",
    )]),
    # Value scanners, errors and output bounds.
    m("pem-drop", "removing PEM detection", READER, [(
        "    if _PEM.search(text):\n        return \"pem\"", "    if False:\n        return \"pem\"",
    )]),
    m("error-text-passed", "passing upstream error text", READER, [(
        "            return YamlReadRefusal(FILE_NOT_FOUND)\n        return YamlReadRefusal(READ_FAILED)",
        "            return YamlReadRefusal(FILE_NOT_FOUND)\n"
        "        return YamlReadRefusal(READ_FAILED, details={\"upstream\": error})",
    )], BOTH),
    m("K2-definite-unavailable", "K2: claiming a definite component-unavailable", READER, [(
        "            return YamlReadRefusal(VALIDATION_OR_UNAVAILABLE)",
        "            return YamlReadRefusal(READ_FAILED)",
    )]),
    m("identity-skip-match", "skipping identity validation (match)", READER, [(
        "        or match[\"file\"] != ALLOWED_FILE\n        or match[\"yaml_path\"] != yaml_path\n",
        "",
    )]),
    m("identity-skip-payload", "skipping identity validation (payload)", READER, [(
        "        or payload[\"files_searched\"] != 1\n", "",
    ), (
        "        or payload[\"yaml_path\"] != yaml_path\n", "",
    )]),
    m("response-too-large-partial", "returning partial data on response_too_large", GATEWAY, [(
        "                if encoded_size + 8_000 > response_limit:\n"
        "                    raise _GatewayFailure(\"response_too_large\", dispatched=True)\n"
        "                completeness = \"partial\" if yaml_result.partial",
        "                if encoded_size + 8_000 > response_limit:\n"
        "                    data_value = dict(data_value, value=None, completeness=\"partial\")\n"
        "                completeness = \"partial\" if yaml_result.partial",
    )], BOTH),
    m("K7-drop", "K7: omitting the YAML 1.2 disclosure", GATEWAY, [(
        "completeness_warnings = [yaml_reader.K7_WARNING]", "completeness_warnings = []",
    )], BOTH),
    # Admission (D2, R4, R5) and Core.
    m("R4-hash-only", "admitting without the complete runtime-contract fingerprint", READER, [(
        "    if observed == binding.runtime_contract_fingerprint:\n",
        "    if True:\n",
    )], (T_ADMISSION,)),
    m("R4-ignore-fields", "ignoring the top-level field set", READER, [(
        "    if tuple(sorted(str(key) for key in tool)) != binding.top_level_fields:\n",
        "    if False:\n",
    )], (T_ADMISSION,)),
    m("order-counts-optional", "counting optional tools in the order fingerprint", GATEWAY, [(
        "            unreviewed_occurrences[name] += 1\n",
        "            if name == \"ha_config_get_yaml\":\n"
        "                observed_reviewed_order.append(name)\n"
        "            unreviewed_occurrences[name] += 1\n",
    )], (T_ADMISSION,)),
    m("aggregate-counts-optional", "counting the optional decision in the aggregate disposition", COORDINATOR, [(
        "            if item.profile_id is not None\n"
        "            and not self._profile_contract(item.capability_id).optional\n",
        "            if item.profile_id is not None\n",
    )], (T_ADMISSION,)),
    m("default-disposition", "a default disposition for an unlisted version", READER, [(
        "disposition = OPTIONAL_TOOL_DISPOSITIONS.get(release.version)",
        "disposition = OPTIONAL_TOOL_DISPOSITIONS.get(release.version, \"admitted\")",
    )], (T_ADMISSION,)),
    m("disposition-entry-removed", "an unlisted registry version (load assertion)", READER, [(
        "    \"8.4.1\": \"held\",\n", "",
    )], (T_ADMISSION,)),
    m("loader-no-validation", "not calling the load assertion", POLICY, [(
        "            validate_bindings_against_registry(registry)\n", "            pass\n",
    )], (T_ADMISSION,)),
    m("read-gateway-held-exposed", "exposing on read_gateway: held", READER, [(
        "    if release.provider_disposition(\"read_gateway\") != \"admitted\":\n",
        "    if False:\n",
    )], (T_ADMISSION,)),
    m("signed-selection-attached", "attaching the extension under a signed selection", READER, [(
        "    if signed_selection:\n        return \"optional_signed_selection\"\n",
        "    if False:\n        return \"optional_signed_selection\"\n",
    )], (T_ADMISSION,)),
    m("authority-token-no-extension", "omitting the extension from the authority fingerprint", HA_MCP, [(
        "        if self.optional_extension is not None:\n            material[\"optional_extension\"]",
        "        if False:\n            material[\"optional_extension\"]",
    )], (T_ADMISSION,)),
    m("no-acquire-route", "dispatching without acquire_route", GATEWAY, [(
        "                    lease.readmission_lease = coordinator.acquire_route(\n"
        "                        policy_entry.upstream_name,\n"
        "                        session_id=live_observation.session_id,\n"
        "                    )\n",
        "                    lease.readmission_lease = object()\n",
    )], (T_ADMISSION,)),
    m("no-commit-route", "dispatching without commit_route", GATEWAY, [(
        "                    commit = coordinator.commit_route(\n"
        "                        lease.readmission_lease,\n"
        "                        session_id=mapping.session_id,\n"
        "                    )\n",
        "                    commit = object()\n",
    )], (T_ADMISSION,)),
    m("stock-profile-attached", "attaching the route inside stock profile derivation", HA_MCP, [(
        "    digest_id = release.policy_sha256.removeprefix(\"sha256:\")[:16]\n"
        "    return CapabilityProfile(",
        "    from ..providers.upstream_yaml_reader import OPTIONAL_TOOL_BINDINGS as _BINDINGS\n"
        "    _binding = _BINDINGS.get((release.version, \"ha_config_get_yaml\"))\n"
        "    if _binding is not None:\n"
        "        capabilities = capabilities + optional_extension_profile(release, _binding).capabilities\n"
        "    digest_id = release.policy_sha256.removeprefix(\"sha256:\")[:16]\n"
        "    return CapabilityProfile(",
    )], (T_ADMISSION,)),
    m("core-mapping-removed", "removing the Core mapping", CORE_ROUTES, [(
        "    \"ha_config_get_yaml\": (\"core.basic_rest_read\", \"core.basic_websocket_read\"),\n", "",
    )], (T_ADMISSION,)),
    # H3 (correction batch 2): markers are trusted by registry identity only;
    # upstream marker-shaped text is scanned and neutralized, never skipped.
    m("H3-marker-skip-restored", "H3: restoring the pre-fix marker skip in L4 and L5", SANITIZATION, [(
        "        if marker_like_text(value):\n            self._record_field({MARKER_COLLISION_CATEGORY})\n"
        "            return MARKER_COLLISION\n",
        "        if REDACTION_MARKER.search(value):\n            self.identity_markers_only = False\n"
        "            try:\n                return self._sanitize_string(value)\n"
        "            finally:\n                self.identity_markers_only = True\n",
    )], (T_READER, T_SANITIZATION)),
    m("H3-marker-skip-restored-l4", "H3: restoring the marker skip with the L4 gates removed", READER, [(
        "                if marker_like_text(str(key)):", "                if False:",
    ), (
        "        if marker_like_text(value):\n            # H3:", "        if False:\n            # H3:",
    ), (
        "    def _key_scan(self, key: str) -> str | None:\n",
        "    def _key_scan(self, key: str) -> str | None:\n        return None\n",
    )]),
    m("H3-redacted-prefix-safe-l5", "H3: treating any [REDACTED prefix as safe in L5", SANITIZATION, [(
        "        if marker_like_text(value):\n            self._record_field({MARKER_COLLISION_CATEGORY})\n",
        "        if value.startswith(\"[REDACTED\"):\n            return value\n"
        "        if marker_like_text(value):\n            self._record_field({MARKER_COLLISION_CATEGORY})\n",
    )], (T_READER, T_SANITIZATION)),
    m("H3-redacted-prefix-safe-l4", "H3: treating any [REDACTED prefix as safe in L4", READER, [(
        "        if marker_like_text(value):\n            # H3:",
        "        if value.startswith(\"[REDACTED\"):\n            return value\n"
        "        if marker_like_text(value):\n            # H3:",
    )]),
    m("H3-redacted-prefix-safe-key", "H3: treating a [REDACTED key prefix as safe in L4", READER, [(
        "                if marker_like_text(str(key)):",
        "                if marker_like_text(str(key)) and not str(key).startswith(\"[REDACTED\"):",
    )]),
    m("H3-l4-key-gate-removed", "H3: dropping the L4 marker-shaped key gate", READER, [(
        "                if marker_like_text(str(key)):", "                if False:",
    )]),
    m("H3-l4-value-gate-removed", "H3: dropping the L4 marker-shaped value gate", READER, [(
        "        if marker_like_text(value):\n            # H3:", "        if False:\n            # H3:",
    )]),
    m("H3-key-scan-removed", "H3 sweep: no scanners on decoded or canonical key forms", READER, [(
        "                category = self._key_scan(str(key))\n", "                category = None\n",
    )]),
    m("H3-r11-scanner-skip", "H3 sweep: the SQLite shape skipping the scanners again", READER, [(
        "            # R11 exempts the exact SQLite shape from rule B only; the\n"
        "            # scanners below still run on it (H3 sweep).\n",
        "            return value\n",
    )]),
    m("H3-scheme-unvetted", "H3 sweep: keeping a scheme without the scanners", READER, [(
        " and _scheme_clean(match[1], known_secrets):", ":",
    )]),
    m("H3-relabel-by-text", "H3 sweep: relabel accounting by marker text", READER, [(
        "    return int(registry.emitted(before) and after is not before)",
        "    return int(type(before) is str and before != after and \"[REDACTED:\" in before)",
    )]),
    m("H3-render-without-registry", "H3: rendering an EngineeringMarker by type only", READER, [(
        "        if not registry.emitted(value) or not value.valid():", "        if not value.valid():",
    )]),
    m("H3-l5-without-registry", "H3: L5 keeping an EngineeringMarker by type only", SANITIZATION, [(
        "            and self.minted_markers.emitted(value)\n", "",
    )]),
    m("H3-emitted-by-equality", "H3: registry membership by equality instead of identity", SANITIZATION, [(
        "        return type(value) is EngineeringMarker and self._emitted.get(id(value)) is value",
        "        return isinstance(value, EngineeringMarker) and value in self._emitted.values()",
    )]),
    m("H3-marker-grammar-unchecked", "H3: keeping a registered marker of any text", SANITIZATION, [(
        "        return type(self.text) is str and _ENGINEERING_MARKER_TEXT.fullmatch(self.text) is not None",
        "        return type(self.text) is str",
    )]),
    m("H3-strict-key-raw", "H3: a sensitive key's text skipping the strict scan", SANITIZATION, [(
        "        if key_category and not self.identity_markers_only:", "        if key_category:",
    )], (T_READER, T_SANITIZATION)),
    m("H3-seal-default-mode", "H3: seal using the default marker-skipping sanitizer", READER, [(
        "        value, known_secrets=known_secrets, identity_markers_only=True\n",
        "        value, known_secrets=known_secrets, identity_markers_only=False\n",
    )]),
    m("H3-fold-no-nfd", "H3: no NFD, so precomposed combining marks survive", SANITIZATION, [(
        "    folded = unicodedata.normalize(\"NFD\", unicodedata.normalize(\"NFKC\", folded))",
        "    folded = unicodedata.normalize(\"NFKC\", folded)",
    )], (T_READER, T_SANITIZATION)),
    m("H3-fold-no-lookalikes", "H3: no look-alike table", SANITIZATION, [(
        "    return folded.translate(_MARKER_FOLD).casefold().translate(_MARKER_FOLD)",
        "    return folded.casefold()",
    )], (T_READER, T_SANITIZATION)),
    m("H3-fold-no-pretable", "H3: look-alike table only after NFKC", SANITIZATION, [(
        "    folded = text.translate(_MARKER_FOLD)\n", "    folded = text\n",
    )], (T_READER, T_SANITIZATION)),
    m("H3-fold-keeps-format", "H3: zero-width and combining characters kept", SANITIZATION, [(
        "if unicodedata.category(char) not in (\"Cf\", \"Mn\", \"Me\")", "if True",
    )], (T_READER, T_SANITIZATION)),
    m("H3-no-decode", "H3: marker shape judged on the raw text only", SANITIZATION, [(
        "        decoded = unquote(value, errors=\"replace\")\n", "        decoded = value\n",
    )], (T_READER, T_SANITIZATION)),
    m("H3-decode-bound-open", "H3: text still encoded after the bound treated as clean", SANITIZATION, [(
        "        value = decoded\n    return True\n", "        value = decoded\n    return False\n",
    )], (T_READER, T_SANITIZATION)),
    m("H3-no-truncated-word", "H3: only redacted, not truncated, treated as marker-shaped", SANITIZATION, [(
        "(?:redacted|truncated)\")", "(?:redacted)\")",
    )], (T_READER, T_SANITIZATION)),
]

_FAILURE_LINE = re.compile(r"^(FAIL|ERROR): (\S+) \(([^)]+)\)", re.MULTILINE)


def apply(tree: Path, mutation: dict) -> dict[Path, str]:
    target = tree / mutation["path"]
    original = target.read_text(encoding="utf-8")
    text = original
    for old, new in mutation["replacements"]:
        count = text.count(old)
        if count != 1:
            raise SystemExit(f"{mutation['id']}: fragment matched {count} times: {old!r}")
        text = text.replace(old, new)
    target.write_text(text, encoding="utf-8")
    return {target: original}


def run_tests(tree: Path, modules, timeout: int, *, failfast: bool) -> tuple[int, str, float]:
    environment = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
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
    selected = [item for item in MUTATIONS if not arguments.only or item["id"] in arguments.only]
    if len({item["id"] for item in MUTATIONS}) != len(MUTATIONS):
        raise SystemExit("duplicate mutation id")
    trees: "queue.Queue[Path]" = queue.Queue()
    for index in range(arguments.workers):
        tree = work / f"tree-{index}"
        copy_tree(tree)
        trees.put(tree)
    # The unmutated baseline must pass every module the mutations use.
    baseline_modules = sorted({module for item in selected for module in item["tests"]})
    tree = trees.get()
    code, output, seconds = run_tests(tree, baseline_modules, arguments.timeout, failfast=False)
    trees.put(tree)
    baseline = {"modules": baseline_modules, "returncode": code, "seconds": round(seconds, 1),
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
        result = {
            **mutation,
            "returncode": code,
            "killed": code != 0,
            "killed_by_import_or_syntax_error": import_error,
            "first_failures": failures[:3],
            "seconds": round(seconds, 1),
        }
        state = "KILLED" if result["killed"] else "SURVIVED"
        print(f"{state:8} {mutation['id']:34} {seconds:5.0f}s {failures[:1]}", flush=True)
        return result

    with concurrent.futures.ThreadPoolExecutor(max_workers=arguments.workers) as pool:
        results = list(pool.map(one, selected))
    survived = [item["id"] for item in results if not item["killed"]]
    report = {
        "baseline": baseline,
        "mutation_count": len(results),
        "killed": len(results) - len(survived),
        "survived": survived,
        "python": sys.version.split()[0],
        "results": results,
    }
    output_path = arguments.output.expanduser()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(report, indent=2, ensure_ascii=True) + "\n", encoding="utf-8")
    print(f"{report['killed']}/{len(results)} killed; survived: {survived}")
    return 0 if not survived else 1


if __name__ == "__main__":
    raise SystemExit(main())
