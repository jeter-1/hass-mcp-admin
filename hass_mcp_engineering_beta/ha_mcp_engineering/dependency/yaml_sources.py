"""HAMCP-264: YAML template and script references for the dependency index.

The only reader is the reviewed ``ha_config_get_yaml`` route captured for the
``dependency_index`` purpose (``UpstreamReadGateway.yaml_config_reader``),
which fixes the readable paths (``template``, ``script``,
``homeassistant.packages``, ``homeassistant``) and its own audit event. A
reader captured for any other purpose is refused before any read.

- ``!secret`` and ``!env_var`` are never resolved (the reader reads
  ``configuration.yaml`` only). Their reference markers are replaced by a
  constant before extraction, and only a count is kept: no reference name is
  emitted, logged, audited or hashed.
- Includes, package directories and labeled top-level keys (``template x:``)
  are not read; each makes the row partial, never absent.
- UI template helpers are not read (their only configuration path is an
  options flow that can write); a non-zero registry count keeps the template
  row partial.

The result joins the HAMCP-227 diagnostic partition: it never enters the
governance-bound snapshot findings or fingerprint.
"""

from __future__ import annotations

import asyncio
from collections import Counter
from dataclasses import dataclass, field
import hashlib
import json
import re
import time
from typing import Any, Callable

from ..recorder_settings.projection import gateway_envelope, packages_status
from ..untrusted_text import screen
from .extraction import extract_document_with_obligations
from .models import DependencyFinding, SourceCoverageItem

READER_PURPOSE = "dependency_index"
YAML_PROVIDER = "upstream_read_gateway"
TEMPLATE_PATH = "template"
SCRIPT_PATH = "script"
PACKAGES_PATH = "homeassistant.packages"
CORE_PATH = "homeassistant"
MAX_YAML_PACKAGES = 200
MAX_YAML_TEMPLATE_SOURCES = 1_000
MAX_YAML_SCRIPT_SOURCES = 1_000
MAX_YAML_FINDINGS = 10_000
YAML_READ_SECONDS = 5.0
YAML_PHASE_SECONDS = 15.0
REFERENCE_WITHHELD = "<reference_withheld>"
# Core 2026.11.0 ``template/config.py`` CONFIG_SECTION_SCHEMA platform keys.
TEMPLATE_PLATFORM_KEYS = (
    "alarm_control_panel", "binary_sensor", "button", "climate", "cover",
    "device_tracker", "event", "fan", "image", "light", "lock", "media_player",
    "number", "select", "sensor", "switch", "update", "vacuum", "water_heater",
    "weather",
)
# Legacy slug-keyed mappings still accepted in a modern section.
TEMPLATE_LEGACY_KEYS = ("binary_sensors", "sensors")
TEMPLATE_SHARED_KEYS = (
    "trigger_variables", "triggers", "trigger", "conditions", "condition",
    "actions", "action", "variables",
)
SCOPE_WARNINGS = {
    "template": ("Only template entities in configuration.yaml and its inline packages are read; "
                 "included files and UI template helpers are not."),
    "script": ("Only scripts in configuration.yaml and its inline packages are read here; "
               "scripts.yaml and other included files are not followed."),
}
LABELED_KEYS_WARNING = (
    "Labeled top-level keys ('{key} <label>:') cannot be named by the reader's "
    "path form, so their absence is not proven."
)
UI_HELPER_WARNING = (
    "{count} UI template helper{plural} not read; their only configuration path is "
    "an options flow that can write."
)
TEMPLATE_COUNT_WARNING = (
    "{count} template-platform entity registry entr{suffix} observed; this is a lower "
    "bound, because YAML template entities without a unique_id are not in the registry."
)
UNAVAILABLE_WARNINGS = {
    "yaml_reader_not_admitted": (
        "The dependency_index YAML reader is not admitted (for example ha-mcp without "
        "its filesystem tools, where ha_config_get_yaml is not offered); no YAML "
        "configuration was read."
    ),
    "yaml_reader_purpose_mismatch": (
        "The supplied YAML reader was not captured for the dependency_index purpose; "
        "it was not used."
    ),
    "provider_authority_changed": (
        "YAML reader authority changed; cached YAML evidence is withheld."
    ),
}
_SCRIPT_KEY = re.compile(r"[a-z0-9_]{1,128}\Z", re.ASCII)
_LABEL = re.compile(r"[A-Za-z0-9_-]{1,64}\Z", re.ASCII)
_REFERENCE_TEXT = re.compile(r"\s*!(?:secret|env_var)\b", re.IGNORECASE)


@dataclass(frozen=True)
class YamlSources:
    """YAML template and script evidence, with one coverage row each."""

    findings: tuple[DependencyFinding, ...]
    template_coverage: SourceCoverageItem
    script_coverage: SourceCoverageItem
    fingerprint: str
    authority_current: Callable[[], bool] | None = None
    # (source_type, source_id, source_entity_id, reason_code) per bounded source.
    terminals: tuple[tuple[str, str, str | None, str], ...] = ()
    script_ids: tuple[str, ...] = ()
    profile: dict[str, Any] = field(default_factory=dict, compare=False)


def _digest(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode()
    ).hexdigest()


def withhold_references(value: Any, counter: Counter) -> Any:
    """``value`` with every secret or env reference replaced by a constant."""

    if type(value) is dict:
        if "secret_ref" in value or "env_var_ref" in value:
            counter["references_withheld"] += 1
            return REFERENCE_WITHHELD
        return {key: withhold_references(child, counter) for key, child in value.items()}
    if type(value) is list:
        return [withhold_references(child, counter) for child in value]
    if isinstance(value, str) and _REFERENCE_TEXT.match(value):
        counter["references_withheld"] += 1
        return REFERENCE_WITHHELD
    return value


def _is_include(value: Any) -> bool:
    return isinstance(value, str) and value.lstrip().lower().startswith("!include")


def _envelope_gaps(envelope: dict, path: str, gaps: Counter) -> None:
    """Count the reader's partial reasons without emitting include paths."""

    for item in envelope["limitations"]:
        if not isinstance(item, str):
            continue
        if item.startswith("include_not_followed"):
            gaps[f"{path}_include_not_followed"] += 1
        elif item.startswith(("value_truncated:", "value_depth_limited:", "strings_truncated:")):
            gaps[f"{path}_value_truncated"] += 1
    if envelope["completeness"] != "complete" and not gaps:
        gaps[f"{path}_read_partial"] += 1


def unavailable_rows(
    reason: str, *, ui_helpers: int = 0, registry_templates: int | None = None,
    duration_ms: float = 0.0,
) -> tuple[SourceCoverageItem, SourceCoverageItem]:
    """Template and YAML script rows when nothing could be read."""

    warning = UNAVAILABLE_WARNINGS.get(reason, f"YAML configuration read unavailable: {reason}.")
    template_warnings = [warning, *_template_registry_warnings(ui_helpers, registry_templates)]
    policy = f"dependency_index YAML reader unavailable ({reason}); no YAML configuration read; no fallback"
    return (
        SourceCoverageItem(
            "template", "none", "template_configuration", "unavailable", 0, 0,
            template_warnings, duration_ms, policy=policy,
            obligation_ledger_completeness="unavailable",
        ),
        SourceCoverageItem(
            "script", "none", "script_yaml_configuration", "unavailable", 0, 0,
            [warning], duration_ms, policy=policy,
            obligation_ledger_completeness="unavailable",
        ),
    )


def _template_registry_warnings(ui_helpers: int, registry_templates: int | None) -> list[str]:
    warnings = []
    if ui_helpers:
        warnings.append(UI_HELPER_WARNING.format(count=ui_helpers, plural="" if ui_helpers == 1 else "s"))
    if registry_templates is not None:
        warnings.append(TEMPLATE_COUNT_WARNING.format(
            count=registry_templates, suffix="y" if registry_templates == 1 else "ies",
        ))
    return warnings


def _registry_maps(registry: list) -> tuple[dict[str, list[str]], dict[str, list[str]], int, int]:
    """YAML template and script unique-id maps, UI helper count, template count."""

    templates: dict[str, list[str]] = {}
    scripts: dict[str, list[str]] = {}
    ui_helpers = 0
    template_rows = 0
    for row in registry:
        if not isinstance(row, dict):
            continue
        entity, unique, platform = row.get("entity_id"), row.get("unique_id"), row.get("platform")
        if platform == "template":
            template_rows += 1
            if row.get("config_entry_id"):
                ui_helpers += 1
                continue
        if not isinstance(entity, str) or not isinstance(unique, str):
            continue
        if platform == "template" and not row.get("config_entry_id"):
            templates.setdefault(unique, []).append(entity)
        elif platform == "script" and entity.startswith("script."):
            scripts.setdefault(unique, []).append(entity)
    return templates, scripts, ui_helpers, template_rows


def _single(mapping: dict[str, list[str]], key: Any, enabled: bool) -> str | None:
    if not enabled or not isinstance(key, str):
        return None
    matches = mapping.get(key, [])
    return matches[0] if len(matches) == 1 else None


def _domain_values(document: dict, domain: str) -> list[Any]:
    """Values of ``domain`` and ``domain <label>`` keys of an inline package."""

    values = []
    for key in sorted(k for k in document if isinstance(k, str)):
        if key == domain or key.startswith(f"{domain} "):
            values.append(document[key])
    return values


def _template_documents(
    value: Any, origin: str, gaps: Counter,
) -> list[tuple[str, dict, Any, str | None]]:
    """(source_id, config, registry unique id, name) per template document."""

    if value is None:
        return []
    if _is_include(value):
        gaps["template_include_not_followed"] += 1
        return []
    sections = value if isinstance(value, list) else [value]
    documents = []
    for index, section in enumerate(sections):
        if _is_include(section):
            gaps["template_include_not_followed"] += 1
            continue
        if not isinstance(section, dict):
            gaps["template_section_invalid"] += 1
            continue
        # ``template[0].sensor[2]`` in configuration.yaml, ``packages.<name>.template[0]...``.
        prefix = f"{origin}.template[{index}]" if origin else f"template[{index}]"
        if "use_blueprint" in section:
            gaps["template_blueprint_not_expanded"] += 1
            documents.append((prefix, section, None, None))
            continue
        shared = {key: section[key] for key in TEMPLATE_SHARED_KEYS if key in section}
        if shared:
            documents.append((prefix, shared, None, None))
        section_uid = section.get("unique_id") if isinstance(section.get("unique_id"), str) else None
        for platform in (*TEMPLATE_PLATFORM_KEYS, *TEMPLATE_LEGACY_KEYS):
            if platform not in section:
                continue
            entries = section[platform]
            if _is_include(entries):
                gaps["template_include_not_followed"] += 1
                continue
            if platform in TEMPLATE_LEGACY_KEYS:
                if not isinstance(entries, dict):
                    gaps["template_entity_invalid"] += 1
                    continue
                items = [
                    (f"{platform}.{slug}" if isinstance(slug, str) and _LABEL.fullmatch(slug)
                     else f"{platform}[{position}]", entry)
                    for position, (slug, entry) in enumerate(entries.items())
                ]
            else:
                listed = entries if isinstance(entries, list) else [entries]
                items = [(f"{platform}[{position}]", entry) for position, entry in enumerate(listed)]
            for label, entry in items:
                if not isinstance(entry, dict):
                    gaps["template_include_not_followed" if _is_include(entry) else "template_entity_invalid"] += 1
                    continue
                uid = entry.get("unique_id") if isinstance(entry.get("unique_id"), str) else None
                registry_uid = (f"{section_uid}-{uid}" if section_uid and uid else uid)
                name = entry.get("name") if isinstance(entry.get("name"), str) else None
                if name is not None and ("{{" in name or "{%" in name):
                    name = None
                documents.append((f"{prefix}.{label}", entry, registry_uid, name))
    return documents


def _script_documents(value: Any, gaps: Counter) -> list[tuple[str, dict]]:
    if value is None:
        return []
    if _is_include(value):
        gaps["script_include_not_followed"] += 1
        return []
    if not isinstance(value, dict):
        gaps["script_section_invalid"] += 1
        return []
    documents = []
    for key, config in value.items():
        if not isinstance(key, str) or not _SCRIPT_KEY.fullmatch(key):
            gaps["script_key_invalid_or_withheld"] += 1
            continue
        if _is_include(config):
            gaps["script_include_not_followed"] += 1
            continue
        if not isinstance(config, dict) or (
            not isinstance(config.get("sequence"), list) and not isinstance(config.get("use_blueprint"), dict)
        ):
            gaps["script_configuration_invalid"] += 1
            continue
        if isinstance(config.get("use_blueprint"), dict):
            gaps["script_blueprint_not_expanded"] += 1
        documents.append((key, config))
    return documents


async def collect_yaml_sources(
    reader: Any, registry: list, *, registry_complete: bool, secret: str,
    clock: Callable[[], float] = time.monotonic,
) -> YamlSources:
    """Read and analyse YAML template and script definitions (at most four reads)."""

    started = clock()
    templates_by_uid, scripts_by_uid, ui_helpers, template_rows = _registry_maps(registry)
    if reader is None or getattr(reader, "purpose", None) != READER_PURPOSE:
        reason = "yaml_reader_not_admitted" if reader is None else "yaml_reader_purpose_mismatch"
        template_row, script_row = unavailable_rows(
            reason, ui_helpers=ui_helpers, registry_templates=template_rows,
        )
        return YamlSources((), template_row, script_row, _digest(["yaml_unavailable", reason]),
                           profile={"read_attempts": 0, "read_time_ms": 0.0, "reason": reason})

    current = reader.current
    deadline = started + YAML_PHASE_SECONDS
    reads = 0
    read_time = 0.0
    lost_authority = False

    def is_current() -> bool:
        try:
            return bool(current())
        except Exception:
            return False

    async def read(path: str) -> tuple[dict | None, str | None]:
        nonlocal reads, read_time, lost_authority
        if not is_current():
            lost_authority = True
            return None, "provider_authority_changed"
        remaining = deadline - clock()
        if remaining <= 0:
            return None, "yaml_phase_deadline"
        reads += 1
        began = time.monotonic()
        try:
            result = await asyncio.wait_for(reader.read(path), min(YAML_READ_SECONDS, remaining))
        except asyncio.CancelledError:
            raise
        except asyncio.TimeoutError:
            return None, "timeout"
        except Exception:
            # Exception text and provider values never enter the report.
            return None, "yaml_read_failed"
        finally:
            read_time += (time.monotonic() - began) * 1000
        if not is_current():
            lost_authority = True
            return None, "provider_authority_changed"
        return gateway_envelope(result, path)

    counter: Counter = Counter()
    template_gaps: Counter = Counter()
    script_gaps: Counter = Counter()
    template_env, template_failure = await read(TEMPLATE_PATH)
    script_env, script_failure = await read(SCRIPT_PATH)
    packages_env, packages_failure = await read(PACKAGES_PATH)
    core_env, core_failure = None, None
    if packages_env is not None and not packages_env["found"]:
        core_env, core_failure = await read(CORE_PATH)
    package_state = packages_status(packages_env, packages_failure, core_env, core_failure)
    del core_env
    if lost_authority:
        template_row, script_row = unavailable_rows(
            "provider_authority_changed", ui_helpers=ui_helpers, registry_templates=template_rows,
            duration_ms=(clock() - started) * 1000,
        )
        return YamlSources((), template_row, script_row,
                           _digest(["yaml_unavailable", "provider_authority_changed"]),
                           profile={"read_attempts": reads, "read_time_ms": read_time,
                                    "reason": "provider_authority_changed"})

    template_docs: list[tuple[str, dict, Any, str | None]] = []
    script_docs: list[tuple[str, dict]] = []
    if template_env is not None:
        _envelope_gaps(template_env, TEMPLATE_PATH, template_gaps)
        if template_env["found"]:
            template_docs.extend(_template_documents(
                withhold_references(template_env["value"], counter), "", template_gaps,
            ))
    if script_env is not None:
        _envelope_gaps(script_env, SCRIPT_PATH, script_gaps)
        if script_env["found"]:
            script_docs.extend(_script_documents(withhold_references(script_env["value"], counter), script_gaps))
    del template_env, script_env

    # Packages: only an inline mapping of inline package mappings is read.
    packages_read = 0
    if package_state == "present" and packages_env is not None:
        value = withhold_references(packages_env["value"], counter)
        package_gaps: Counter = Counter()
        _envelope_gaps(packages_env, "packages", package_gaps)
        if not isinstance(value, dict):
            package_gaps["packages_not_inline"] += 1
            value = {}
        names = sorted(name for name in value if isinstance(name, str))
        if len(value) > len(names):
            package_gaps["package_name_invalid"] += len(value) - len(names)
        if len(names) > MAX_YAML_PACKAGES:
            package_gaps["package_limit_exceeded"] += 1
        for position, name in enumerate(names[:MAX_YAML_PACKAGES]):
            package = value[name]
            label = (f"packages.{name}" if _LABEL.fullmatch(name) and screen(name, (secret,) if secret else ()) is None
                     else f"packages[{position}]")
            if not isinstance(package, dict):
                package_gaps["package_not_inline"] += 1
                continue
            packages_read += 1
            # ``template`` and ``template <label>`` sections, numbered as one list
            # (Core merges them), so positional ids stay distinct.
            sections = [item for value in _domain_values(package, "template") if value is not None
                        for item in (value if isinstance(value, list) else [value])]
            template_docs.extend(_template_documents(sections or None, label, template_gaps))
            for section in _domain_values(package, "script"):
                script_docs.extend(_script_documents(section, script_gaps))
        template_gaps.update(package_gaps)
        script_gaps.update(package_gaps)
    elif package_state != "absent":
        reason = "packages_unreadable" if package_state == "unreadable" else "packages_not_determined"
        template_gaps[reason] += 1
        script_gaps[reason] += 1
    del packages_env

    findings: list[DependencyFinding] = []
    terminals: list[tuple[str, str, str | None, str]] = []
    documents: list[tuple[str, str, str]] = []
    findings_bounded = False

    def analyse(source_type: str, source_id: str, config: dict, entity: str | None,
                name: str | None, gaps: Counter) -> None:
        nonlocal findings_bounded
        try:
            extracted, unresolved, obligations = extract_document_with_obligations(
                source_type=source_type, source_id=source_id, config=config,
                source_entity_id=entity, source_name=name, secret=secret,
            )
        except Exception:
            gaps[f"{source_type}_extraction_failed"] += 1
            return
        documents.append((source_type, source_id, _digest(config)))
        if unresolved:
            gaps[f"{source_type}_dynamic_reference_unresolved"] += len(unresolved)
        failures = sorted({str(item.reason_code) for item in obligations
                           if item.outcome == "coverage_failure"})
        if failures:
            gaps[f"{source_type}_extraction_coverage_failure"] += 1
            terminals.extend((source_type, source_id, entity, reason) for reason in failures)
        for item in extracted:
            if len(findings) >= MAX_YAML_FINDINGS:
                findings_bounded = True
                break
            findings.append(item)

    if len(template_docs) > MAX_YAML_TEMPLATE_SOURCES:
        template_gaps["yaml_template_source_limit_exceeded"] += 1
        terminals.append(("template", "dependency_provider", None, "yaml_template_source_limit_exceeded"))
    for source_id, config, uid, name in template_docs[:MAX_YAML_TEMPLATE_SOURCES]:
        entity = _single(templates_by_uid, uid, registry_complete)
        analyse("template", source_id, config, entity, name, template_gaps)
    seen: set[str] = set()
    script_ids: list[str] = []
    unique_scripts = []
    for key, config in script_docs:
        if key in seen:
            script_gaps["script_duplicate_key"] += 1
            continue
        seen.add(key)
        unique_scripts.append((key, config))
    if len(unique_scripts) > MAX_YAML_SCRIPT_SOURCES:
        script_gaps["yaml_script_source_limit_exceeded"] += 1
        terminals.append(("script", "dependency_provider", None, "yaml_script_source_limit_exceeded"))
    for key, config in unique_scripts[:MAX_YAML_SCRIPT_SOURCES]:
        entity = _single(scripts_by_uid, key, registry_complete)
        alias = config.get("alias") if isinstance(config.get("alias"), str) else None
        analyse("script", key, config, entity, alias, script_gaps)
        script_ids.append(key)
    if findings_bounded:
        for source_type, gaps in (("template", template_gaps), ("script", script_gaps)):
            gaps["yaml_finding_limit_exceeded"] += 1
            terminals.append((source_type, "dependency_provider", None, "yaml_finding_limit_exceeded"))
    if not registry_complete:
        template_gaps["entity_registry_incomplete"] += 1
        script_gaps["entity_registry_incomplete"] += 1
    if ui_helpers:
        template_gaps["ui_template_helpers_not_read"] += ui_helpers
    findings.sort(key=lambda item: item.evidence_id)
    duration = (clock() - started) * 1000
    policy = (f"{reader.provenance}; purpose {READER_PURPOSE}; configuration.yaml and its inline "
              "packages only; !secret and !env_var never resolved; no fallback")

    def row(source_type: str, env_failure: str | None, gaps: Counter,
            extra: list[str]) -> SourceCoverageItem:
        count = sum(1 for item in findings if item.source_type == source_type)
        if env_failure is not None:
            completeness = "unavailable"
            gaps[f"{source_type}_read_{env_failure}"] += 1
        else:
            # Labeled top-level keys cannot be excluded, so a row is at most partial.
            completeness = "partial"
        warnings = [
            *([f"{source_type} read failed: {env_failure}"] if env_failure else []),
            SCOPE_WARNINGS[source_type],
            LABELED_KEYS_WARNING.format(key=source_type),
            *extra,
            *(f"{key}: {value}" for key, value in sorted(gaps.items())),
        ]
        if counter["references_withheld"]:
            warnings.append(f"secret_or_env_references_withheld: {counter['references_withheld']}")
        return SourceCoverageItem(
            source_type, YAML_PROVIDER if env_failure is None else "none",
            "template_configuration" if source_type == "template" else "script_yaml_configuration",
            completeness, evidence_count=count if env_failure is None else 0,
            failed_item_count=sum(gaps.values()), warnings=warnings, duration_ms=duration,
            policy=policy, obligation_ledger_completeness="unavailable",
        )

    template_row = row("template", template_failure, template_gaps,
                       _template_registry_warnings(ui_helpers, template_rows))
    script_row = row("script", script_failure, script_gaps, [])
    if template_failure is not None:
        findings = [item for item in findings if item.source_type != "template"]
    if script_failure is not None:
        findings = [item for item in findings if item.source_type != "script"]
        script_ids = []
    return YamlSources(
        tuple(findings), template_row, script_row,
        _digest([sorted(documents), [item.evidence_id for item in findings],
                 [(r.source_type, r.completeness, r.failed_item_count) for r in (template_row, script_row)]]),
        current,
        tuple(sorted(set(terminals), key=lambda item: (item[0], item[1], item[2] or "", item[3]))),
        tuple(script_ids),
        {"read_attempts": reads, "read_time_ms": round(read_time, 3),
         "template_sources": min(len(template_docs), MAX_YAML_TEMPLATE_SOURCES),
         "script_sources": len(script_ids), "inline_packages": packages_read,
         "package_state": package_state, "ui_template_helpers": ui_helpers,
         "references_withheld": counter["references_withheld"]},
    )
