"""HAMCP-265: the inventories the read-coverage matrix must stay in step with.

The read-coverage matrix (``docs/read_coverage_matrix.json`` and the generated
tables in ``docs/READ_COVERAGE_MATRIX.md``) maps every Home Assistant data
source to the Engineering read and write paths that reach it. This module
derives, from current source only, the four inventories that define those
paths:

* ``tool_catalog``: the static client-visible catalog (``CAPABILITIES`` and
  ``BETA_NATIVE_CAPABILITIES``) plus every reviewed ha-mcp tool policy and the
  compiled special read adapters behind the one public endpoint;
* ``reader_purposes``: the purpose-bound internal readers of the read gateway;
* ``core_profiles``: every compiled Core capability profile
  (``CORE_RUNTIME_CAPABILITY_PROFILES``);
* ``provider_registry``: the routing, direct-read, analytical-policy and
  capability-provider tables.

Each inventory is reduced to ``{item: digest}`` plus a section fingerprint.
``test_read_coverage_matrix`` fails when a live fingerprint differs from the
one recorded in the matrix and names the items added, removed or changed.

Usage (repository root):

    python tests/read_coverage_inventory.py --check
    python tests/read_coverage_inventory.py --write

``--write`` records the current inventories in the matrix, refreshes cited
line numbers from their anchors and regenerates the Markdown tables. It does
not map new items: the coverage checks in the test still fail until every new
tool, reader purpose and Core profile appears in a matrix entry.
"""

from __future__ import annotations

import argparse
import hashlib
import inspect
import json
from pathlib import Path
import re
import sys
from typing import Any


REPO = Path(__file__).resolve().parents[1]
BETA = REPO / "hass_mcp_engineering_beta"
RUNTIME = BETA / "ha_mcp_engineering"
MATRIX_JSON = REPO / "docs" / "read_coverage_matrix.json"
MATRIX_MD = REPO / "docs" / "READ_COVERAGE_MATRIX.md"
READINESS_MD = REPO / "docs" / "FEATURE_READINESS_MAP.md"
SECTIONS = ("tool_catalog", "reader_purposes", "core_profiles", "provider_registry")
GENERATED_BEGIN = "<!-- BEGIN GENERATED: tests/read_coverage_inventory.py --write -->"
GENERATED_END = "<!-- END GENERATED -->"
COMPLETENESS = ("complete", "partial", "unavailable")
REQUIREMENT_TYPES = (
    "new_reader_purpose",
    "new_signed_profile",
    "upstream_admission",
    "impossible_via_ha_api",
    "owner_decision",
    "feature_work",
)
_POLICY_FILE = re.compile(r"^upstream_tool_policy(?:_([0-9]+)_([0-9]+)_([0-9]+))?\.json$")

if str(BETA) not in sys.path:
    sys.path.insert(0, str(BETA))


def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


def _digest(value: Any) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _plain(value: Any) -> Any:
    """Return a JSON-stable copy: enums by value, sets sorted."""

    if isinstance(value, dict):
        return {str(_plain(k)): _plain(v) for k, v in value.items()}
    if isinstance(value, (set, frozenset)):
        return sorted(_plain(v) for v in value)
    if isinstance(value, (list, tuple)):
        return [_plain(v) for v in value]
    if hasattr(value, "value") and not isinstance(value, (str, int, float, bool)):
        return value.value
    return value


def _tool_catalog() -> dict[str, Any]:
    from ha_mcp_engineering import capabilities
    from ha_mcp_engineering.providers import (
        upstream_blueprint,
        upstream_platform_reads,
        upstream_reads_8_6,
        upstream_search_8_6,
        upstream_yaml_reader,
    )

    items: dict[str, Any] = {}
    for entry in capabilities.CAPABILITIES + capabilities.BETA_NATIVE_CAPABILITIES:
        items["native:" + entry["tool"]] = _plain(entry)
    for path in sorted(RUNTIME.glob("upstream_tool_policy*.json")):
        if not _POLICY_FILE.match(path.name):
            continue
        policy = json.loads(path.read_text(encoding="utf-8"))
        version = policy["reviewed_upstream_version"]
        for tool in policy["tools"]:
            items["ha_mcp_" + version + ":" + tool["upstream_name"]] = {
                "classification": tool["classification"],
                "exposed_name": tool.get("exposed_name"),
                "argument_restrictions": tool.get("argument_restrictions"),
                "input_schema_fingerprint": tool.get("input_schema_fingerprint"),
            }
    adapters = {
        upstream_reads_8_6.ADAPTER: sorted(upstream_reads_8_6.INPUT_FINGERPRINTS),
        upstream_search_8_6.ADAPTER: upstream_search_8_6.ENTRY_FINGERPRINT,
        upstream_yaml_reader.TOOL_NAME: "optional_reviewed_yaml_reader",
    }
    adapters.update(upstream_blueprint.ADAPTER_BINDINGS)
    adapters.update(upstream_platform_reads.ADAPTER_BINDINGS)
    for name, binding in adapters.items():
        items["adapter:" + name] = _plain(binding)
    return items


def _reader_purposes() -> dict[str, Any]:
    from ha_mcp_engineering.providers import upstream_read_gateway as gateway

    items: dict[str, Any] = {}
    for purpose, (paths, event) in gateway.YAML_READER_PURPOSES.items():
        items["yaml_reader:" + purpose] = {"paths": sorted(paths), "audit_event": event}
    for name, member in inspect.getmembers(gateway.UpstreamReadGateway, inspect.isfunction):
        if name.endswith("_reader") and not name.startswith("_"):
            items["gateway_reader:" + name] = str(inspect.signature(member))
    return items


def _core_profiles() -> dict[str, Any]:
    from ha_mcp_engineering.ha_core_readmission import profiles

    compiled = {p.capability_id for p in profiles.CORE_CAPABILITY_PROFILES}
    items: dict[str, Any] = {}
    for profile in profiles.CORE_RUNTIME_CAPABILITY_PROFILES:
        mapping = profile.to_mapping()
        mapping["probe_profile"] = profile.capability_id in compiled
        items[profile.capability_id] = mapping
    return items


def _provider_registry() -> dict[str, Any]:
    from ha_mcp_engineering import capabilities
    from ha_mcp_engineering.providers import routing

    items: dict[str, Any] = {}
    for tool, capability in routing.TOOL_CAPABILITY_POLICY.items():
        items["tool_capability:" + tool] = _plain(capability)
    policy = routing.RoutingPolicy()
    for capability in routing.ProviderCapability:
        decision = policy.resolve(capability)
        items["route:" + capability.value] = {
            "route": decision.route.value,
            "preferred_provider": decision.preferred_provider,
            "fallback_providers": list(decision.fallback_providers),
            "explicit_direct_fallback_allowed": decision.explicit_direct_fallback_allowed,
        }
    for tool, entry in routing.ANALYTICAL_PROVIDER_POLICIES.items():
        items["analytical_policy:" + tool] = _plain(entry)
    for tool, entry in routing.DIRECT_HA_READ_POLICIES.items():
        items["direct_read_policy:" + tool] = _plain(entry)
    items["direct_ha_tool_exceptions"] = sorted(routing.DIRECT_HA_TOOL_EXCEPTIONS)
    for row in capabilities.CAPABILITY_PROVIDER_MATRIX:
        items["provider_matrix:" + row["tool"]] = _plain(row)
    return items


def compute_inventory() -> dict[str, dict[str, str]]:
    """Return ``{section: {item: digest}}`` derived from current source."""

    raw = {
        "tool_catalog": _tool_catalog(),
        "reader_purposes": _reader_purposes(),
        "core_profiles": _core_profiles(),
        "provider_registry": _provider_registry(),
    }
    return {
        section: {key: _digest(value)[:16] for key, value in sorted(raw[section].items())}
        for section in SECTIONS
    }


def section_fingerprint(items: dict[str, str]) -> str:
    return _digest(items)


def inventory_record() -> dict[str, Any]:
    return {
        section: {"fingerprint": section_fingerprint(items), "items": items}
        for section, items in compute_inventory().items()
    }


def describe_drift(section: str, recorded: dict[str, str], live: dict[str, str]) -> str:
    added = sorted(set(live) - set(recorded))
    removed = sorted(set(recorded) - set(live))
    changed = sorted(k for k in set(live) & set(recorded) if live[k] != recorded[k])
    parts = [f"{section} changed without a matching read-coverage matrix update"]
    for label, names in (("added", added), ("removed", removed), ("changed", changed)):
        if names:
            shown = ", ".join(names[:12]) + (f" (+{len(names) - 12} more)" if len(names) > 12 else "")
            parts.append(f"{label}: {shown}")
    parts.append(
        "Update the affected entries in docs/read_coverage_matrix.json (read paths, "
        "write paths, admission, completeness, gaps), then run "
        "'python tests/read_coverage_inventory.py --write' and review the diff."
    )
    return "; ".join(parts)


def load_matrix() -> dict[str, Any]:
    return json.loads(MATRIX_JSON.read_text(encoding="utf-8"))


def iter_refs(matrix: dict[str, Any]):
    """Yield every citation in the matrix."""

    for source in matrix["sources"]:
        for kind in ("read_paths", "write_paths", "gaps"):
            for item in source.get(kind, ()):
                for ref in item.get("refs", ()):
                    yield source["id"], ref
    for item in matrix.get("non_source_tools", ()):
        for ref in item.get("refs", ()):
            yield "non_source_tools", ref


def _refresh_line(ref: dict[str, Any]) -> None:
    lines = (REPO / ref["path"]).read_text(encoding="utf-8").splitlines()
    hits = [n for n, text in enumerate(lines, 1) if ref["anchor"] in text]
    if not hits:
        raise SystemExit(f"anchor not found: {ref['path']}: {ref['anchor']!r}")
    if ref.get("line") not in hits:
        ref["line"] = min(hits, key=lambda n: abs(n - int(ref.get("line") or 0)))


def _cell(text: Any) -> str:
    return str(text).replace("|", "\\|").replace("\n", " ")


def _refs(item: dict[str, Any]) -> str:
    return "<br>".join(f"`{r['path'].split('/')[-1]}:{r['line']}`" for r in item.get("refs", ()))


def render_markdown_block(matrix: dict[str, Any]) -> str:
    """Render the generated tables of ``docs/READ_COVERAGE_MATRIX.md``."""

    out = [GENERATED_BEGIN, ""]
    out.append("### Summary")
    out.append("")
    out.append("| Group | Source | Completeness | Read paths | Write paths | Gaps |")
    out.append("|---|---|---|---|---|---|")
    for s in matrix["sources"]:
        out.append(
            f"| {_cell(s['group'])} | [{_cell(s['name'])}](#{s['id'].replace('_', '-')}) "
            f"| {s['completeness']} | {len(s['read_paths'])} | {len(s['write_paths'])} "
            f"| {', '.join(g['id'] for g in s['gaps']) or '-'} |"
        )
    out.append("")
    if matrix.get("notes"):
        out.extend(["### Matrix-wide notes", ""])
        out.extend(f"- {_cell(note)}" for note in matrix["notes"])
        out.append("")
    if matrix.get("helper_types"):
        out.extend(["### Helper definition readability by type (former HAMCP-230)", ""])
        out.append(
            "| Helper type | Source | Definition store | UI definition read | YAML definition read "
            "| Definition completeness | Create | Update | Delete | State write | Gaps |"
        )
        out.append("|---|---|---|---|---|---|---|---|---|---|---|")
        for h in matrix["helper_types"]:
            out.append(
                f"| `{h['helper_type']}` | [{h['source']}](#{h['source'].replace('_', '-')}) "
                f"| {_cell(h['definition_store'])} | {_cell(h['ui_definition_read'])} "
                f"| {_cell(h['yaml_definition_read'])} | {h['definition_completeness']} "
                f"| {_cell(h['governed_create'])} | {_cell(h['governed_update'])} "
                f"| {_cell(h['governed_delete'])} | {_cell(h['state_write'])} | {', '.join(h['gaps']) or '-'} |"
            )
        out.append("")
    group = None
    for s in matrix["sources"]:
        if s["group"] != group:
            group = s["group"]
            out.extend([f"### {group}", ""])
        out.append(f'<a id="{s["id"].replace("_", "-")}"></a>')
        out.append(f"#### {s['name']} (`{s['id']}`)")
        out.append("")
        out.append(f"Completeness: **{s['completeness']}**. {_cell(s['completeness_reason'])}")
        out.append("")
        out.append("| Read path | Provider | Purpose binding | Admission | Completeness | Detail | Code |")
        out.append("|---|---|---|---|---|---|---|")
        if not s["read_paths"]:
            out.append("| none | - | - | - | unavailable | - | - |")
        for p in s["read_paths"]:
            out.append(
                f"| `{_cell(p['tool'])}` | {_cell(p['provider'])} | {_cell(p['purpose_binding'])} "
                f"| {_cell(p['admission'])} | {p['completeness']} | {_cell(p['detail'])} | {_refs(p)} |"
            )
        out.append("")
        if s["write_paths"]:
            out.append("| Write path | Operations | Policy | Admission | Detail | Code |")
            out.append("|---|---|---|---|---|---|")
            for p in s["write_paths"]:
                out.append(
                    f"| `{_cell(p['tool'])}` | {', '.join(p['operations'])} | {_cell(p['policy'])} "
                    f"| {_cell(p['admission'])} | {_cell(p['detail'])} | {_refs(p)} |"
                )
        else:
            out.append("Write paths: none.")
        out.append("")
        if s["gaps"]:
            out.append("| Gap | Missing | Requirement type | Requirement to close | Register |")
            out.append("|---|---|---|---|---|")
            for g in s["gaps"]:
                out.append(
                    f"| {g['id']} | {_cell(g['missing'])} | {g['requirement_type']} "
                    f"| {_cell(g['requirement'])} | {', '.join(g.get('register', ())) or '-'} |"
                )
            out.append("")
    if matrix.get("non_source_tools"):
        out.extend(["### Tools that read no Home Assistant data source directly", ""])
        out.append("| Tool | Why | Code |")
        out.append("|---|---|---|")
        for t in matrix["non_source_tools"]:
            out.append(f"| `{_cell(t['tool'])}` | {_cell(t['reason'])} | {_refs(t)} |")
        out.append("")
    out.extend(["### Compiled Core profile index", ""])
    out.append("| Core profile | Probe profile | Used by | Note |")
    out.append("|---|---|---|---|")
    for capability_id, entry in sorted(matrix["core_profile_index"].items()):
        out.append(
            f"| `{capability_id}` | {'yes' if entry['probe_profile'] else 'no'} "
            f"| {', '.join(entry['used_by'])} | {_cell(entry['note'])} |"
        )
    out.append("")
    out.extend(["### Reader purpose index", ""])
    out.append("| Reader | Used by | Note |")
    out.append("|---|---|---|")
    for key, entry in sorted(matrix["reader_purpose_index"].items()):
        out.append(f"| `{key}` | {', '.join(entry['used_by'])} | {_cell(entry['note'])} |")
    out.append("")
    out.append("### Recorded inventory fingerprints")
    out.append("")
    out.append("| Inventory | Items | Fingerprint |")
    out.append("|---|---|---|")
    for section in SECTIONS:
        rec = matrix["inventory"][section]
        out.append(f"| {section} | {len(rec['items'])} | `{rec['fingerprint'][:16]}` |")
    out.extend(["", GENERATED_END])
    return "\n".join(out)


def generated_block(text: str) -> str:
    start = text.index(GENERATED_BEGIN)
    end = text.index(GENERATED_END, start) + len(GENERATED_END)
    return text[start:end]


def write() -> None:
    matrix = load_matrix()
    for _, ref in iter_refs(matrix):
        _refresh_line(ref)
    matrix["inventory"] = inventory_record()
    MATRIX_JSON.write_text(json.dumps(matrix, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    text = MATRIX_MD.read_text(encoding="utf-8")
    MATRIX_MD.write_text(
        text.replace(generated_block(text), render_markdown_block(matrix)), encoding="utf-8"
    )


def check() -> int:
    matrix = load_matrix()
    live = compute_inventory()
    failures = [
        describe_drift(section, matrix["inventory"][section]["items"], live[section])
        for section in SECTIONS
        if matrix["inventory"][section]["fingerprint"] != section_fingerprint(live[section])
    ]
    for failure in failures:
        print(failure)
    return 1 if failures else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--check", action="store_true")
    mode.add_argument("--write", action="store_true")
    args = parser.parse_args(argv)
    if args.write:
        write()
        return 0
    return check()


if __name__ == "__main__":
    raise SystemExit(main())
