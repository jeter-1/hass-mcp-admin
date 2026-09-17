"""Compiled list/get adapter; the mixed upstream tool is never a public tool.

The public schema is the exact shipped 8.4.3 getter schema. Only two constructed
argument shapes can reach 8.5.0; upstream data cannot select an action.
"""
from copy import deepcopy
import re

VERSION = "8.5.0"
ADAPTER = "ha-mcp-8.5.0-blueprint-list-get-v1"
SOURCE = "311d6dc273fb4e9a5b8cde0de15f69472a64fe44"
INPUT_FINGERPRINT = "6a71b650a3286f3fd5cf80e71b7f96735b1fac98c45ffeb839519988c5ae71d9"
PUBLIC_SCHEMA = {'additionalProperties': False,
 'properties': {'domain': {'default': 'automation',
                           'description': "Blueprint domain: 'automation' or 'script'",
                           'type': 'string'},
                'path': {'anyOf': [{'type': 'string'}, {'type': 'null'}],
                         'default': None,
                         'description': 'Blueprint path to get details for (e.g., '
                                        "'homeassistant/motion_light.yaml'). If "
                                        'omitted, lists all blueprints in the '
                                        'domain.'}},
 'type': 'object'}


def is_blueprint_adapter(entry) -> bool:
    return (
        entry.upstream_name == "ha_manage_blueprints"
        and entry.exposed_name == "ha_get_blueprint"
        and entry.classification == "mixed_or_requires_wrapper"
        and entry.input_schema_fingerprint == INPUT_FINGERPRINT
        and entry.argument_restrictions == (ADAPTER,)
        and entry.reviewed_annotations.read_only
        and not entry.reviewed_annotations.destructive
    )


def public_schema(entry, observed: dict) -> dict:
    return deepcopy(PUBLIC_SCHEMA if is_blueprint_adapter(entry) else observed)


def read_arguments(arguments: dict) -> dict:
    if not isinstance(arguments, dict) or set(arguments) - {"domain", "path"}:
        raise ValueError("blueprint_read_arguments_invalid")
    domain, path = arguments.get("domain", "automation"), arguments.get("path")
    if domain not in ("automation", "script"):
        raise ValueError("blueprint_read_domain_invalid")
    if path is None:
        return {"action": "list", "domain": domain}
    # Installed relative YAML identity only: no URLs, traversal, encoded path,
    # query, wildcard, control characters or caller-selected fallback location.
    if (
        not isinstance(path, str)
        or not 1 <= len(path) <= 512
        or not re.fullmatch(r"[A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.-]+)*\.ya?ml", path)
        or any(part in {".", ".."} for part in path.split("/"))
    ):
        raise ValueError("blueprint_read_path_invalid")
    return {"action": "get", "domain": domain, "path": path}


def validate_result(payload, arguments: dict) -> None:
    expected = read_arguments(arguments)
    if not isinstance(payload, dict) or payload.get("success") is not True:
        raise ValueError("blueprint_read_response_invalid")
    data = payload.get("data")
    if not isinstance(data, dict) or data.get("domain") != expected["domain"]:
        raise ValueError("blueprint_read_response_identity_invalid")
    if expected["action"] == "get":
        if (
            data.get("path") != expected["path"]
            or not isinstance(data.get("metadata"), dict)
        ):
            raise ValueError("blueprint_read_response_identity_invalid")
        if "config" in data and not isinstance(data["config"], dict):
            raise ValueError("blueprint_read_config_invalid")
        if "yaml" in data and not isinstance(data["yaml"], str):
            raise ValueError("blueprint_read_yaml_invalid")
    elif (
        not isinstance(data.get("blueprints"), list)
        or type(data.get("count")) is not int
        or data["count"] != len(data["blueprints"])
    ):
        raise ValueError("blueprint_read_list_incomplete")


def completeness(payload) -> tuple[bool, list[str]]:
    data = payload.get("data", {}) if isinstance(payload, dict) else {}
    if "path" not in data:
        return False, []
    if not isinstance(data.get("config"), dict):
        return True, [
            "Blueprint metadata is available; the installed full configuration "
            "is unavailable. Source YAML, when present, is not proof of installed content."
        ]
    if data.get("yaml_source") not in {"component", "file", "tools_entry"}:
        return True, [
            "Blueprint content provenance does not establish the complete "
            "installed configuration."
        ]
    return False, []
