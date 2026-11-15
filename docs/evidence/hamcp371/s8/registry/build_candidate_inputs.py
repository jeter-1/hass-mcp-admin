"""HAMCP-371 S8: build the 9.2.0 candidate inputs and the contract review.

Run from the repository root with ``python -I``. Two commands:

- ``inputs``: writes the three ``candidate`` inputs into this directory:
  ``base-policy-9.2.0.json``, ``strict-tools-response-9.2.0.json`` and
  ``review-decisions-9.2.0.json``.
- ``contract-review --registry PATH``: writes
  ``docs/evidence/upstream-read-compatibility/ha-mcp-9.2.0-contract-review.json``
  from the 9.2.0 release that the registry at PATH loads (the pass-1 registry
  in ``.artifacts`` or the committed one; the review is the same for both
  because the policy is the same).

Add ``--check`` to either command to compare without writing.

Sources, each checked against its own ``SHA256SUMS`` where one exists:

- the S4 observation of the exact 9.2.0 standalone image (strict tools/list
  response and wire order) and the S4 descriptor diff from 9.1.2;
- the committed 9.2.0 registry capture;
- the reviewed 9.1.2 policy, the base for every field ``candidate`` does not
  overwrite (``candidate`` replaces each tool's input fingerprint,
  description, annotations, source evidence, classification and reason);
- the profiles-1 result that the App fs-off catalogue equals the standalone
  one (``s8/profiles-1/check-output.txt``).

Engineering binding decision (disclosed in the README): the base policy's
Engineering adapter tokens ``ha-mcp-9.1.2-*`` become ``ha-mcp-9.2.0-*``. Each
9.2.0 token is a separate compiled binding in this branch, bound to the 9.2.0
source commit and wire fingerprints. The 9.2.0 classifications are the 9.1.2
ones (S8 ruling: the 9.1.2 dispositions); every reason below states the 9.2.0
descriptor delta from the S4 diff, computed, not typed.
"""

import argparse
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path.cwd()
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "hass_mcp_engineering_beta"))
import review_upstream_read_release as review  # noqa: E402

HERE = ROOT / "docs/evidence/hamcp371/s8/registry"
S4 = ROOT / "docs/evidence/hamcp371/s4"
PROFILES_1 = ROOT / "docs/evidence/hamcp371/s8/profiles-1/check-output.txt"
EVIDENCE = ROOT / "docs/evidence/upstream-read-compatibility"
CAPTURE = EVIDENCE / "ha-mcp-9.2.0.json"
CONTRACT_REVIEW = EVIDENCE / "ha-mcp-9.2.0-contract-review.json"
BASE_POLICY_9_1_2 = (
    ROOT / "hass_mcp_engineering_beta/ha_mcp_engineering/upstream_tool_policy_9_1_2.json"
)
VERSION = "9.2.0"
SOURCE = "51e3aac788482a081807591cd5d58d07dc4e540b"
OLD_TOKEN = "ha-mcp-9.1.2-"
NEW_TOKEN = "ha-mcp-9.2.0-"
READS = "ha-mcp-9.2.0-public-reads-v1"
BLUEPRINT = "ha_manage_blueprints"
BLUEPRINT_TOKEN = "ha-mcp-9.2.0-blueprint-list-get-v1"
# The compiled wrappers: read routes whose classification is not automatic_read.
WRAPPERS = {
    BLUEPRINT: BLUEPRINT_TOKEN,
    "ha_get_app": "ha-mcp-9.2.0-platform-apps-v1",
    "ha_get_integration": "ha-mcp-9.2.0-platform-integrations-v1",
    "ha_get_system_health": "ha-mcp-9.2.0-platform-health-v1",
}
BLUEPRINT_UPSTREAM_ANNOTATIONS = {
    "destructiveHint": True, "idempotentHint": False, "openWorldHint": True,
    "readOnlyHint": False, "title": "Manage Blueprints",
}
FINGERPRINT_MAPS = (
    "reviewed_runtime_description_fingerprints",
    "reviewed_runtime_annotation_fingerprints",
    "reviewed_runtime_output_schema_fingerprints",
)
POLICY_NAME = "upstream_tool_policy_9_2_0.json"
PACKAGE = ROOT / "hass_mcp_engineering_beta/ha_mcp_engineering"
PROVENANCE = [
    "Exact official ha-mcp v9.2.0 OCI image captures (standalone index "
    "9de5bf6c twice per filesystem profile; amd64 App image 928c55d6 "
    "byte-identical in both, HAMCP-371 S8 profiles-1 and profiles-2; arm64 "
    "pinned by digest only) against synthetic loopback fixtures; no live "
    "Home Assistant.",
    "HAMCP-371 descriptor diff from 9.1.2 (S4), read-side source review at "
    "51e3aac7 (S2) and real-Core read receipts (S6, S7); independent review is "
    "a separate gate.",
]

# Text appended to a tool's reason; every claim cites the recorded review.
SPECIAL = {
    "ha_config_get_automation": (
        "Engineering sends only entity_id; the optional runtime related key is "
        "removed before projection."
    ),
    "ha_config_list_helpers": (
        "List mode only; describe is pinned off and the describe-only keys are "
        "never sent."
    ),
    "ha_get_skill_guide": (
        "R4 file-only: best-practices file reads only; inventory and file-list "
        "tiers report unavailable and are never synthesized."
    ),
    "ha_get_history": (
        f"Bound to the compiled {READS} adapter at the exact 9.2.0 input "
        "fingerprint. 9.2.0 reads an ISO time without an offset as HA local "
        "time where 9.1 forced UTC (tools_history.py at 51e3aac7). S8 interim: "
        "the adapter "
        "refuses ha_get_history before dispatch (history_9_2_unreviewed) until "
        "the S9 9.2.0 history module; no history request reaches 9.2.0."
    ),
    "ha_get_zone": (
        "Source adds a zone states read and the entity_icon, persons and "
        "device_trackers keys (S2 source findings (c))."
    ),
    "ha_list_services": (
        "Source adds a REST GET /config for the configured language (S2 source "
        "findings (a)); the public schema is unchanged."
    ),
    "ha_search": (
        "The 9.2.0 name key on result rows is reviewed at S11; until then rows "
        "keep the 8.5 public projection."
    ),
    "ha_get_device": (
        "Projected by the compiled device allowlist after the composite "
        "adapter; tools_registry.py and device_enrichment.py are unchanged "
        "from 9.1.2 (S8 source file list)."
    ),
    "ha_get_integration": (
        "Mixed upstream tool. Only the compiled ha-mcp-9.2.0-platform-"
        "integrations-v1 adapter is reachable, bound to the 9.2.0 input "
        "fingerprint; include_ws_commands and integration_type are never sent. "
        "9.2.0 source adds a component websocket command on this read (S2 "
        "source findings (a)); the result is projected through the positive "
        "allowlist."
    ),
    "ha_get_app": (
        "Mixed upstream tool. Only the compiled ha-mcp-9.2.0-platform-apps-v1 "
        "HAMCP-129 adapter is reachable with fixed upstream arguments and a "
        "positive allowlist projection; tools_addons.py is unchanged from "
        "9.1.2 (S8 source file list)."
    ),
    "ha_get_system_health": (
        "Mixed upstream tool. Only the compiled ha-mcp-9.2.0-platform-health-v1 "
        "HAMCP-129 adapter is reachable with the existing fixed sections; the "
        "tools_system.py change is the health docstring only (S8 source file "
        "list)."
    ),
    "ha_manage_blueprints": (
        "Mixed upstream tool. Only the compiled ha_get_blueprint list/get "
        "adapter (ha-mcp-9.2.0-blueprint-list-get-v1) is reachable; import, "
        "save, delete and substitute refuse before dispatch. The blueprint "
        "source files are unchanged from 9.1.2 (S8 source file list)."
    ),
    "ha_config_get_dashboard": (
        "Generic delegation unavailable; only the compiled list_dashboards and "
        "get_dashboard_config shapes are constructed; mode, query, describe and "
        "screenshots are prohibited. The 9.2.0 dashboard attestation is "
        "quarantined until SB, so the dashboard read refuses 9.2.0."
    ),
    "ha_config_set_dashboard": (
        "Reachable only through the governed F3 dashboard provider with "
        "readback verification, which refuses 9.2.0 while its dashboard "
        "attestation is quarantined (until SB). python_transform and "
        "screenshots stay unreachable."
    ),
    "ha_manage_backup": (
        "Only the compiled fixed Engineering wrapper or governed provider may "
        "name this tool; the backup provider stays held."
    ),
    "ha_get_operation_status": (
        "Operation status stays held for canary with no automatic exposure."
    ),
    "ha_get_hacs_info": (
        "HACS information has no reviewed read route and is unsupported until "
        "HAMCP-290. Never exposed or dispatched."
    ),
    "ha_get_camera_image": (
        "Binary image content is unsupported and never exposed or dispatched."
    ),
    "ha_report_issue": "Prohibited; never exposed or dispatched.",
}
BY_CLASS = {
    "automatic_read": (
        f"Admitted only through the compiled {READS} adapter, bound to the "
        "exact 9.2.0 input fingerprint, with the 9.1 wire arguments and the "
        "8.5-parity public schema; S8 interim projection by the compiled 9.1 "
        "tables until S10 closes the 9.2.0 key sets."
    ),
    "mixed_or_requires_wrapper": (
        "Generic delegation stays unavailable; only the compiled fixed "
        "Engineering wrapper or governed provider may name this tool."
    ),
    "persistent_write": (
        "Persistent write; unreachable through generic delegation and only "
        "existing governed providers may name it (no new write reachability)."
    ),
    "physical_or_high_risk_action": (
        "Physical or high-risk action; unreachable through generic delegation "
        "(no new reachability)."
    ),
    "held_for_canary": "",
    "prohibited": "",
    "unsupported": "",
}
# Tools whose SPECIAL text replaces the class text (not appended to it).
REPLACES_CLASS = {
    "ha_get_history", "ha_get_integration", "ha_get_app", "ha_get_system_health",
    "ha_manage_blueprints", "ha_config_get_dashboard",
    "ha_config_set_dashboard", "ha_manage_backup",
}


def _sums(base):
    sums = {}
    for line in (base / "SHA256SUMS").read_text(encoding="utf-8").splitlines():
        digest, name = line.split("  ", 1)
        sums[name] = digest
    return sums


def _verified(base, relative):
    raw = (base / relative).read_bytes()
    if hashlib.sha256(raw).hexdigest() != _sums(base)[relative]:
        raise SystemExit(f"sha256 mismatch: {base / relative}")
    return raw


def _observation():
    observation = json.loads(_verified(S4, "ha-mcp-9.2.0/observation.json"))
    pages = observation["tools_list_pages"]
    if len(pages) != 1 or len(pages[0]["messages"]) != 1:
        raise SystemExit("S4 observation is not one tools/list page")
    response = pages[0]["messages"][0]
    order = [tool["name"] for tool in response["result"]["tools"]]
    if order != observation["tool_names_in_wire_order"] or len(order) != 77:
        raise SystemExit("S4 observation wire order is inconsistent")
    return response, order


def _diff():
    diff = json.loads(_verified(S4, "descriptor-diff-9.1.2-to-9.2.0.json"))
    tools = diff["tools"]
    if tools["added"] or tools["removed"] or tools["descriptor_fields_outside_the_known_set"]:
        raise SystemExit("S4 diff has additions, removals or unknown fields")
    if tools["changed_count"] != len(tools["changed"]) or tools["changed_count"] != 17:
        raise SystemExit("S4 diff changed count is inconsistent")
    return tools["changed"]


def _delta(name, changed):
    if name not in changed:
        return "descriptor unchanged from 9.1.2"
    item = changed[name]
    parts = []
    if "description" in item["fields_changed"]:
        parts.append("description")
    schema = item.get("inputSchema_diff") or {}
    for key in sorted(schema.get("properties_added") or {}):
        parts.append(f"input property {key} added")
    for key, value in sorted((schema.get("properties_changed") or {}).items()):
        parts.append(f"{key} {'/'.join(value['keys_changed'])}")
    for key in sorted(schema.get("properties_removed") or []):
        parts.append(f"input property {key} removed")
    for key in sorted(schema.get("other_keys_changed") or {}):
        parts.append(f"input schema {key}")
    return "descriptor changed from 9.1.2: " + ", ".join(parts)


def _base_policy():
    policy = json.loads(BASE_POLICY_9_1_2.read_bytes())
    renamed = 0
    for tool in policy["tools"]:
        tokens = []
        for token in tool["argument_restrictions"]:
            if token.startswith(OLD_TOKEN):
                token = NEW_TOKEN + token.removeprefix(OLD_TOKEN)
                renamed += 1
            elif token.startswith("ha-mcp-"):
                raise SystemExit(f"unexpected adapter token {token}")
            tokens.append(token)
        tool["argument_restrictions"] = tokens
    if renamed != 27:
        raise SystemExit(f"expected 27 adapter tokens, renamed {renamed}")
    return policy


def _withhold_wrappers(policy):
    """Pass-1 base: the four compiled wrapper tokens withheld (HAMCP-319 method).

    ``candidate`` writes each tool's annotations from the capture and builds
    the runtime fingerprint maps over ``automatic_read`` rows only, while the
    loader requires them over every read route, compiled wrappers included.
    With the tokens present the loader refuses the policy
    (``policy_uncompiled_blueprint_adapter``, then
    ``policy_runtime_description_fingerprints_invalid``). ``finalize`` restores
    the tokens, the blueprint wrapper's public annotations and the wrappers'
    fingerprints.
    """

    policy = json.loads(json.dumps(policy))
    for name, token in WRAPPERS.items():
        row = [tool for tool in policy["tools"] if tool["upstream_name"] == name]
        if len(row) != 1 or row[0]["argument_restrictions"] != [token]:
            raise SystemExit(f"base policy {name} row is not the compiled wrapper")
        row[0]["argument_restrictions"] = []
    return policy


def _decisions(policy, changed):
    decisions = []
    for tool in sorted(policy["tools"], key=lambda item: item["upstream_name"]):
        name, classification = tool["upstream_name"], tool["classification"]
        # Only the compiled reads have a recorded 9.2.0 source review (S2 is
        # the read side); every other tool cites its descriptor only.
        compiled = any(token.startswith(NEW_TOKEN) for token in tool["argument_restrictions"])
        text = [
            f"Exact 9.2.0 {_delta(name, changed)} (HAMCP-371 S4 diff)"
            + ("; 51e3aac7 read-side source reviewed (HAMCP-371 S2 source "
               "findings)." if compiled else ".")
        ]
        if name not in REPLACES_CLASS and BY_CLASS[classification]:
            text.append(BY_CLASS[classification])
        if name in SPECIAL:
            text.append(SPECIAL[name])
        reason = " ".join(text)
        if len(reason.encode("utf-8")) > 1000:
            raise SystemExit(f"reason too long for {name}")
        decisions.append(
            {"tool_name": name, "policy_classification": classification, "reason": reason}
        )
    return decisions


def _write(path, value, check):
    encoded = review.canonical_json(value) + b"\n"
    if check:
        same = path.exists() and path.read_bytes() == encoded
        print(f"{path.relative_to(ROOT)}", "up to date" if same else "differs")
        return same
    path.write_bytes(encoded)
    print(f"wrote {path.relative_to(ROOT)} sha256 {hashlib.sha256(encoded).hexdigest()}")
    return True


def inputs(check):
    response, _ = _observation()
    changed = _diff()
    policy = _base_policy()
    capture = json.loads(CAPTURE.read_bytes())
    if {tool["name"] for tool in capture["tools"]} != {
        tool["upstream_name"] for tool in policy["tools"]
    }:
        raise SystemExit("capture and base policy tool names differ")
    ok = _write(HERE / "base-policy-9.2.0.json", _withhold_wrappers(policy), check)
    ok &= _write(HERE / "strict-tools-response-9.2.0.json", response, check)
    ok &= _write(HERE / "review-decisions-9.2.0.json", _decisions(policy, changed), check)
    return 0 if ok else 1


def _review_text(name, classification, changed):
    item = changed[name]
    if classification == "automatic_read":
        tail = f"Admitted only through the compiled {READS} adapter at the 9.2.0 fingerprint."
        if name == "ha_get_history":
            tail = "Refused before dispatch until the S9 9.2.0 history module."
    elif name == "ha_config_set_dashboard":
        tail = "Governed F3 provider only; refuses 9.2.0 while the attestation is quarantined."
    elif classification == "prohibited":
        tail = "Prohibited; never exposed or dispatched."
    elif classification == "mixed_or_requires_wrapper" and name in WRAPPERS:
        tail = "Only the compiled wrapper may name it; generic delegation unavailable."
    elif classification == "mixed_or_requires_wrapper":
        tail = "No Engineering wrapper or provider names it and generic delegation is unavailable, so it is unreachable."
    else:
        tail = "Persistent write; never exposed or dispatched through generic delegation."
    return f"{_delta(name, changed).capitalize()}. {tail}"


def _release(registry_path):
    from ha_mcp_engineering import upstream_tool_policy as policy_module

    registry = policy_module.load_reviewed_upstream_release_registry(registry_path)
    release = [item for item in registry.releases if item.version == VERSION]
    if len(release) != 1:
        raise SystemExit("registry has no single 9.2.0 release")
    return release[0]


def contract_review(registry_path, check):
    value = _contract_review_value(_release(registry_path))
    return 0 if _write(CONTRACT_REVIEW, value, check) else 1


def _contract_review_value(release):
    from ha_mcp_engineering import upstream_tool_policy as policy_module
    from ha_mcp_engineering.providers.upstream_contracts import (
        CONTRACT_FAMILY_V3,
        normalize_runtime_contract,
    )

    if release.source_commit != SOURCE:
        raise SystemExit("9.2.0 release source commit differs")
    response, order = _observation()
    changed = _diff()
    capture = json.loads(CAPTURE.read_bytes())
    by_name = {tool["name"]: tool for tool in capture["tools"]}
    tools = [by_name[name] for name in order]
    if "app_fs_off_capture_equals_standalone_s4 True" not in PROFILES_1.read_text(encoding="utf-8"):
        raise SystemExit("profiles-1 does not record the App fs-off catalogue equal to S4")
    validation = policy_module.validate_reviewed_release_catalog(
        release,
        observed_server_name=release.server_name,
        observed_upstream_version=release.version,
        observed_protocol_version=policy_module.REVIEWED_UPSTREAM_PROTOCOL,
        tools=tools,
    )
    if not validation.valid:
        raise SystemExit("9.2.0 standalone catalogue does not validate")
    strict = policy_module.schema_fingerprint({"tools": tools})
    if strict != release.strict_full_contract_fingerprint or strict != policy_module.schema_fingerprint(
        {"tools": response["result"]["tools"]}
    ):
        raise SystemExit("strict full-contract fingerprint differs")
    raw = policy_module.catalog_fingerprint(tools)
    if raw != release.catalog_fingerprint:
        raise SystemExit("raw catalogue fingerprint differs")
    classes = {item.upstream_name: item.classification for item in release.policy.tools}
    dashboard_8_0_0 = {
        tool["name"]: tool
        for tool in json.loads((EVIDENCE / "ha-mcp-8.0.0.json").read_bytes())["tools"]
    }["ha_config_get_dashboard"]
    value = {
        "artifact_scope": {
            "addon_artifacts": release.addon_artifact_digests_by_platform,
            "admitted_artifact_family": policy_module.EXACT_OCI_ARTIFACT_FAMILY,
            "excluded_release_assets": sorted(policy_module.EXCLUDED_RELEASE_PAGE_ASSETS),
            "exclusion_reason": (
                "Only the immutable official standalone and Home Assistant App "
                "OCI artifacts are admitted."
            ),
            "oci_image_revision_label": release.image_revision,
            "standalone_image_index_digest": release.image_index_digest,
            "standalone_platform_manifests": release.architecture_image_digests_by_platform,
        },
        "changed_runtime_descriptors": {
            name: {
                "changed_components": sorted(changed[name]["fields_changed"]),
                "classification": classes[name],
                "review": _review_text(name, classes[name], changed),
            }
            for name in sorted(changed)
        },
        "classification_counts": release.policy.classification_counts,
        "dashboard": {
            "contract_family": CONTRACT_FAMILY_V3,
            "descriptor_equal_to_8_0_0_standalone": (
                by_name["ha_config_get_dashboard"] == dashboard_8_0_0
            ),
            "runtime_fingerprint": normalize_runtime_contract(
                by_name["ha_config_get_dashboard"],
                protocol_version=policy_module.REVIEWED_UPSTREAM_PROTOCOL,
                contract_family=CONTRACT_FAMILY_V3,
            ).runtime_fingerprint,
        },
        "decision": (
            "HAMCP-371 exact ha-mcp 9.2.0 admission (51e3aac7, official OCI "
            "images only) with the 9.1.2 dispositions. Seventeen descriptors "
            "changed from 9.1.2 (listed, from the S4 diff); the other 60, "
            "including the dashboard getter, are byte-identical. Standalone "
            "tools/list captured twice per filesystem profile (77 and 82 "
            "tools); the exact amd64 App image produced byte-identical "
            "catalogues in both (profiles-1, profiles-2); the arm64 App image "
            "is pinned by digest only. No _meta.ha_mcp.policy block. The "
            "dashboard attestation is quarantined until SB; ha_get_history "
            "refuses before dispatch until S9; ha_get_hacs_info stays "
            "unsupported; ha_get_operation_status stays held; backup, HACS, "
            "issue-report and integration-write tools stay unreachable. The "
            "9.2.0 release page lists no assets (read 2026-11-15). Local "
            "candidate only; CI, independent review, installed admission, "
            "publication and version change are separate gates."
        ),
        "held_tools": sorted(
            item.upstream_name for item in release.policy.tools
            if item.classification == "held_for_canary"
        ),
        "review_format_version": policy_module.EXACT_OCI_ARTIFACT_EVIDENCE_FORMAT_VERSION,
        "runtime_catalog": {
            "addon_raw_catalog_fingerprint": raw,
            "addon_strict_full_contract_fingerprint": strict,
            "advertised_tool_count": release.advertised_tool_count,
            "aggregate_fingerprint_model": (
                policy_module.REVIEWED_NORMALIZED_CATALOG_FINGERPRINT_MODEL_V1
            ),
            "controlling_evidence": "exact immutable OCI runtime tools/list",
            "normalized_aggregate_fingerprint": validation.normalized_catalog_fingerprint,
            "policy_only_standalone_addon_differences": [],
            "runtime_contract_fingerprint_model": release.runtime_contract_fingerprint_model,
            "runtime_tool_order": order,
            "standalone_raw_catalog_fingerprint": raw,
            "standalone_strict_full_contract_fingerprint": strict,
        },
        "upstream": {
            "protocol_version": policy_module.REVIEWED_UPSTREAM_PROTOCOL,
            "server_name": "ha-mcp",
            "source_commit": SOURCE,
            "source_tag": f"v{VERSION}",
            "version": VERSION,
        },
    }
    return json.loads(json.dumps(value))


def _final_policy(pass1):
    """Pass 2 policy: pass 1 with the binary-owned public fields restored.

    As for 9.1.0 and 9.1.2 (HAMCP-319 two-pass method), the published
    description of each tool and the blueprint wrapper's public annotations are
    Engineering's 8.5-parity public surface, not upstream metadata
    (``upstream_read_gateway.py``: "Publish only the bounded reviewed
    description from the manifest"). They are restored from the reviewed 9.1.2
    policy. Every 9.2.0 descriptor assertion (input fingerprint, runtime
    description, annotation and output-schema fingerprints, classification and
    reason) stays as ``candidate`` wrote it from the 9.2.0 capture.
    """

    policy = json.loads(pass1.read_bytes())
    public = {
        tool["upstream_name"]: tool
        for tool in json.loads(BASE_POLICY_9_1_2.read_bytes())["tools"]
    }
    if {tool["upstream_name"] for tool in policy["tools"]} != set(public):
        raise SystemExit("pass-1 policy tools differ from 9.1.2")
    for tool in policy["tools"]:
        name = tool["upstream_name"]
        if name in WRAPPERS:
            if tool["argument_restrictions"] != []:
                raise SystemExit(f"pass-1 {name} row was not withheld")
            tool["argument_restrictions"] = [WRAPPERS[name]]
        if name == BLUEPRINT:
            tool["reviewed_annotations"] = dict(public[name]["reviewed_annotations"])
        elif tool["reviewed_annotations"] != public[name]["reviewed_annotations"]:
            # The 9.2.0 annotations equal 9.1.2's (S4: no annotation field
            # changed); a difference is a review question, not a restore.
            raise SystemExit(f"9.2.0 annotations differ from 9.1.2 for {name}")
        tool["description"] = public[name]["description"]
    # The wrappers' runtime fingerprints, from the 9.2.0 capture with the
    # functions ``candidate`` uses for the automatic reads; those 23 stay as
    # ``candidate`` wrote them.
    from ha_mcp_engineering.upstream_tool_policy import (
        runtime_annotation_fingerprint,
        runtime_description_fingerprint,
        schema_fingerprint,
    )

    captured = {tool["name"]: tool for tool in json.loads(CAPTURE.read_bytes())["tools"]}

    def annotation(tool):
        # As ``read_annotation_fingerprint``: the compiled blueprint wrapper
        # binds the exact upstream (destructive) annotations by fingerprint.
        if tool["name"] == BLUEPRINT:
            if tool.get("annotations") != BLUEPRINT_UPSTREAM_ANNOTATIONS:
                raise SystemExit("9.2.0 blueprint annotations are not the reviewed ones")
            return schema_fingerprint(tool["annotations"])
        return runtime_annotation_fingerprint(tool.get("annotations")) or ""

    derive = (
        lambda tool: runtime_description_fingerprint(tool.get("description")) or "",
        annotation,
        lambda tool: schema_fingerprint(tool.get("outputSchema")),
    )
    for key, function in zip(FINGERPRINT_MAPS, derive):
        values = policy[key]
        if set(values) & set(WRAPPERS) or any(
            values[name] != function(captured[name]) for name in values
        ):
            raise SystemExit(f"pass-1 {key} is not the capture's")
        values.update({name: function(captured[name]) for name in WRAPPERS})
        policy[key] = dict(sorted(values.items()))
    return policy


def finalize(pass1_dir, workdir, check):
    """Restore the public fields, bind the contract review, emit the entry.

    Writes the final policy to the package, the contract review to the
    evidence directory and ``registry-entry-9.2.0.json`` to WORKDIR (appended
    to the registry with ``append-json-entry``). With ``--check``, compares
    the policy and the review with the committed files and the entry with the
    committed registry row.
    """

    from ha_mcp_engineering import upstream_tool_policy as policy_module

    entry = json.loads((pass1_dir / "registry-entry-9.2.0.json").read_bytes())
    if (entry["approval_status"] != "candidate_unapproved"
            or entry["policy_resource"] != POLICY_NAME
            or "artifact_evidence_resource" in entry):
        raise SystemExit("pass-1 entry is not an unbound candidate")
    policy = _final_policy(pass1_dir / POLICY_NAME)
    policy_bytes = review.canonical_json(policy) + b"\n"
    entry["approval_status"] = "reviewed"
    entry["review_provenance"] = PROVENANCE
    entry["policy_sha256"] = "sha256:" + hashlib.sha256(policy_bytes).hexdigest()
    registry_format = json.loads(
        (PACKAGE / "upstream_release_registry.json").read_bytes()
    )["registry_format_version"]
    workdir.mkdir(parents=True, exist_ok=True)
    (workdir / POLICY_NAME).write_bytes(policy_bytes)

    def load(value):
        path = workdir / "registry.json"
        path.write_bytes(review.canonical_json(
            {"default_version": VERSION, "registry_format_version": registry_format,
             "releases": [value]}
        ) + b"\n")
        return _release(path)

    evidence = _contract_review_value(load(entry))
    evidence_bytes = review.canonical_json(evidence) + b"\n"
    entry["artifact_evidence_resource"] = CONTRACT_REVIEW.relative_to(ROOT).as_posix()
    entry["artifact_evidence_sha256"] = (
        "sha256:" + hashlib.sha256(evidence_bytes).hexdigest()
    )
    if check:
        committed = [
            item for item in json.loads(
                (PACKAGE / "upstream_release_registry.json").read_bytes()
            )["releases"] if item["version"] == VERSION
        ]
        ok = _write(PACKAGE / POLICY_NAME, policy, True)
        ok &= _write(CONTRACT_REVIEW, evidence, True)
        same = committed == [entry]
        print("registry 9.2.0 entry", "up to date" if same else "differs")
        return 0 if ok and same else 1
    _write(CONTRACT_REVIEW, evidence, False)
    # The bound entry must load with its artifact evidence validated.
    load(entry)
    _write(PACKAGE / POLICY_NAME, policy, False)
    _write(workdir / "registry-entry-9.2.0.json", entry, False)
    return 0


def main():
    parser = argparse.ArgumentParser()
    commands = parser.add_subparsers(dest="command", required=True)
    first = commands.add_parser("inputs")
    first.add_argument("--check", action="store_true")
    second = commands.add_parser("contract-review")
    second.add_argument("--registry", type=Path, required=True)
    second.add_argument("--check", action="store_true")
    third = commands.add_parser("finalize")
    third.add_argument("--pass1", type=Path, required=True)
    third.add_argument("--workdir", type=Path, required=True)
    third.add_argument("--check", action="store_true")
    args = parser.parse_args()
    if args.command == "inputs":
        return inputs(args.check)
    if args.command == "finalize":
        return finalize(args.pass1.resolve(), args.workdir.resolve(), args.check)
    return contract_review(args.registry, args.check)


if __name__ == "__main__":
    raise SystemExit(main())
