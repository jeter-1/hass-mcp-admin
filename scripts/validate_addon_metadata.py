"""Fail-closed metadata validation for production and beta Home Assistant add-ons."""

from __future__ import annotations

import argparse
import ast
from dataclasses import dataclass
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
from typing import Callable, Iterable

from awesomeversion import AwesomeVersion
import yaml


PRODUCTION_SLUG = "hass_mcp_admin"
BETA_SLUG = "hass_mcp_engineering_beta"
PRODUCTION_NAME = "HA MCP Engineering Server"
BETA_NAME = "HA MCP Engineering Server Beta"
PRODUCTION_VERSION = "1.1.2"
BETA_VERSION = "2.0.0-rc2-dev12"
BETA_IMAGE = "ghcr.io/jeter-1/hass-mcp-engineering-beta"
FINAL_RC3_VERSION = "2.0.0-rc.3"
NEXT_VERSION_PATH = Path(".release/next-version")
NON_RELEASE_BETA_PATHS = frozenset({"hass_mcp_engineering_beta/AGENTS.md"})
PRODUCTION_PORT = 8099
BETA_PORT = 8100
BETA_INGRESS_PORT = 8110
MIN_ACCESS_SECRET_LENGTH = 24
EXTERNAL_CHECK_TIMEOUT_SECONDS = 60
EXPECTED_BETA_SCHEMA = {
    "access_secret": "str",
    "upstream_dashboard_mcp_url": "password",
    "upstream_trust_registry_enabled": "bool",
    "upstream_trust_registry_public_key": "str",
    "dependency_index_prewarm": "bool",
    "prewarm_enabled": "bool",
    "prewarm_startup_delay_seconds": "float",
    "prewarm_retry_delay_seconds": "float",
    "dependency_index_soft_ttl_seconds": "float",
    "dependency_index_hard_ttl_seconds": "float",
    "rate_limit_per_minute": "int",
    "rate_limit_burst": "int",
    "trust_cf_connecting_ip": "bool",
    "trusted_proxy_cidrs": ["str"],
    "audit_enabled": "bool",
    "audit_path": "str",
    "audit_max_payload_chars": "int",
    "log_level": "list(DEBUG|INFO|WARNING|ERROR)",
    "ha_timeout_seconds": "float",
    "response_size_limit": "int",
    "redaction_enabled": "bool",
    "destructive_services": ["str"],
}
SEMVER = re.compile(
    r"^(?P<major>0|[1-9]\d*)\.(?P<minor>0|[1-9]\d*)\.(?P<patch>0|[1-9]\d*)"
    r"(?:-(?P<prerelease>[0-9A-Za-z.-]+))?$"
)
REGISTRY_TAG_NOT_FOUND = re.compile(
    r"manifest unknown|no such manifest|"
    r"unexpected status from HEAD request.+404 Not Found",
    re.IGNORECASE,
)


class MetadataValidationError(RuntimeError):
    pass


@dataclass(frozen=True)
class ValidationReport:
    production_version: str
    beta_version: str
    compared_version: str | None
    beta_changed: bool
    production_changed: bool
    same_version_correction: bool
    staged_release_version: str | None


def read_yaml(path: Path) -> dict:
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise MetadataValidationError(f"Invalid YAML metadata: {path.name}") from exc
    if not isinstance(data, dict):
        raise MetadataValidationError(f"Metadata must be a mapping: {path.name}")
    return data


def read_python_constant(path: Path, name: str):
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except (OSError, SyntaxError) as exc:
        raise MetadataValidationError(f"Unable to read Python metadata: {path.name}") from exc
    for node in tree.body:
        if isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            if any(isinstance(target, ast.Name) and target.id == name for target in targets):
                try:
                    return ast.literal_eval(node.value)
                except (ValueError, TypeError) as exc:
                    raise MetadataValidationError(f"{name} must be a literal") from exc
    raise MetadataValidationError(f"Missing Python metadata constant: {name}")


def configured_port(config: dict) -> int:
    ports = config.get("ports")
    if not isinstance(ports, dict) or len(ports) != 1:
        raise MetadataValidationError("Each add-on must publish exactly one MCP port")
    key, value = next(iter(ports.items()))
    try:
        container_port = int(str(key).split("/", 1)[0])
        host_port = int(value)
    except (TypeError, ValueError) as exc:
        raise MetadataValidationError("Add-on port metadata must be numeric") from exc
    if container_port != host_port:
        raise MetadataValidationError("Container and host MCP ports must match")
    return host_port


def version_key(version: str):
    if not SEMVER.fullmatch(version):
        raise MetadataValidationError(f"Invalid semantic version: {version}")
    try:
        return AwesomeVersion(version)
    except Exception as exc:
        raise MetadataValidationError(f"Invalid semantic version: {version}") from exc


def is_newer_version(candidate: str, deployed: str) -> bool:
    return version_key(candidate) > version_key(deployed)


def staged_release_version(repo_root: Path, advertised_version: str) -> str | None:
    declaration = repo_root / NEXT_VERSION_PATH
    if not declaration.exists():
        return None
    try:
        lines = declaration.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise MetadataValidationError("Unable to read staged release declaration") from exc
    if len(lines) != 1 or not lines[0] or lines[0] != lines[0].strip():
        raise MetadataValidationError(
            "Staged release declaration must contain exactly one version"
        )
    candidate = lines[0]
    if not is_newer_version(candidate, advertised_version):
        raise MetadataValidationError(
            "Staged release version must be newer than advertised metadata"
        )
    if not version_key(candidate) < version_key(FINAL_RC3_VERSION):
        raise MetadataValidationError(
            "Staged development version must remain below final RC3"
        )
    return candidate


def validate_config_pair(production: dict, beta: dict, *, minimum_secret_length: int) -> None:
    if production.get("name") != PRODUCTION_NAME:
        raise MetadataValidationError("Production name changed")
    if beta.get("name") != BETA_NAME:
        raise MetadataValidationError("Beta name changed")
    if production.get("slug") != PRODUCTION_SLUG:
        raise MetadataValidationError("Production slug changed")
    if beta.get("slug") != BETA_SLUG:
        raise MetadataValidationError("Beta slug changed")
    if production.get("slug") == beta.get("slug"):
        raise MetadataValidationError("Production and beta slugs collide")
    if str(production.get("version")) != PRODUCTION_VERSION:
        raise MetadataValidationError("Production version changed")
    if "image" in production:
        raise MetadataValidationError("Production image publication metadata is not approved")
    if str(beta.get("version")) != BETA_VERSION:
        raise MetadataValidationError("Beta/RC version changed")
    if beta.get("image") != BETA_IMAGE:
        raise MetadataValidationError("Beta/RC registry image is missing or invalid")

    production_port = configured_port(production)
    beta_port = configured_port(beta)
    if production_port != PRODUCTION_PORT:
        raise MetadataValidationError("Production port changed")
    if beta_port != BETA_PORT:
        raise MetadataValidationError("Beta port changed")
    if production_port == beta_port:
        raise MetadataValidationError("Production and beta ports collide")
    if beta.get("ingress") is not True:
        raise MetadataValidationError("Beta external approval Ingress must be enabled")
    if beta.get("panel_admin") is not True:
        raise MetadataValidationError("Beta approval panel must remain administrator-only")
    if beta.get("ingress_port") != BETA_INGRESS_PORT:
        raise MetadataValidationError("Beta approval Ingress port changed")
    if f"{BETA_INGRESS_PORT}/tcp" in (beta.get("ports") or {}):
        raise MetadataValidationError("Beta approval Ingress port must not be host mapped")
    if beta.get("auth_api"):
        raise MetadataValidationError("Beta must not enable unnecessary auth_api access")

    options = beta.get("options")
    schema = beta.get("schema")
    if not isinstance(options, dict) or not isinstance(schema, dict):
        raise MetadataValidationError("Beta options and schema must be mappings")
    if set(options) != set(EXPECTED_BETA_SCHEMA):
        raise MetadataValidationError("Beta options do not match the approved option set")
    if schema != EXPECTED_BETA_SCHEMA:
        raise MetadataValidationError("Beta option schema changed or is invalid")
    if "access_secret" not in options or "access_secret" not in schema:
        raise MetadataValidationError("Beta access_secret configuration is required")
    access_schema = str(schema["access_secret"])
    if access_schema != "str" or access_schema.endswith("?"):
        raise MetadataValidationError("Beta access_secret must be a required string")
    if minimum_secret_length != MIN_ACCESS_SECRET_LENGTH:
        raise MetadataValidationError("Beta access_secret minimum length changed")
    if options["access_secret"] != "" or production.get("options", {}).get("access_secret") != "":
        raise MetadataValidationError("Access secrets must not be stored in add-on metadata")
    if options["redaction_enabled"] is not True:
        raise MetadataValidationError("Beta redaction must remain enabled")
    if options["trust_cf_connecting_ip"] is not False:
        raise MetadataValidationError("Forwarded client-IP trust must default to disabled")
    if options["trusted_proxy_cidrs"] != []:
        raise MetadataValidationError("Trusted proxy CIDRs must default to an empty list")
    if options["dependency_index_prewarm"] is not False:
        raise MetadataValidationError("Legacy dependency index prewarm alias must remain disabled")
    if options["prewarm_enabled"] is not True:
        raise MetadataValidationError("Dependency index prewarm must default to enabled")
    if options["prewarm_startup_delay_seconds"] != 45:
        raise MetadataValidationError("Dependency index prewarm startup delay changed")
    if options["prewarm_retry_delay_seconds"] < 300:
        raise MetadataValidationError("Dependency index prewarm retry delay is too short")
    soft_ttl = options["dependency_index_soft_ttl_seconds"]
    hard_ttl = options["dependency_index_hard_ttl_seconds"]
    if soft_ttl != 600 or hard_ttl != 3600 or hard_ttl <= soft_ttl:
        raise MetadataValidationError("Dependency index freshness defaults are invalid")
    for key in ("rate_limit_per_minute", "rate_limit_burst", "audit_max_payload_chars", "response_size_limit"):
        if not isinstance(options[key], int) or isinstance(options[key], bool) or options[key] <= 0:
            raise MetadataValidationError(f"Beta option {key} must be a positive integer")
    if not isinstance(options["ha_timeout_seconds"], (int, float)) or options["ha_timeout_seconds"] <= 0:
        raise MetadataValidationError("Beta option ha_timeout_seconds must be positive")
    if not isinstance(options["destructive_services"], list) or not all(
        isinstance(value, str) and value for value in options["destructive_services"]
    ):
        raise MetadataValidationError("Beta destructive_services must be a list of names")


def run_git(repo_root: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=repo_root,
        text=True,
        capture_output=True,
        check=False,
    )
    if result.returncode:
        raise MetadataValidationError("Git metadata validation failed")
    return result.stdout


def changed_paths(repo_root: Path, base_ref: str) -> set[str]:
    paths: set[str] = set()
    for args in (
        ("diff", "--name-only", f"{base_ref}...HEAD"),
        ("diff", "--name-only"),
        ("diff", "--cached", "--name-only"),
    ):
        paths.update(
            line.strip().replace("\\", "/")
            for line in run_git(repo_root, *args).splitlines()
            if line.strip()
        )
    return paths


def version_from_ref(repo_root: Path, base_ref: str) -> str:
    content = run_git(
        repo_root,
        "show",
        f"{base_ref}:hass_mcp_engineering_beta/config.yaml",
    )
    try:
        config = yaml.safe_load(content)
        return str(config["version"])
    except (yaml.YAMLError, KeyError, TypeError) as exc:
        raise MetadataValidationError("Unable to read beta version from base ref") from exc


def _run_external(
    repo_root: Path,
    *args: str,
    env: dict[str, str] | None = None,
    input_text: str | None = None,
) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            list(args),
            cwd=repo_root,
            text=True,
            capture_output=True,
            check=False,
            env=env,
            input=input_text,
            timeout=EXTERNAL_CHECK_TIMEOUT_SECONDS,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise MetadataValidationError(
            "Unable to execute the unreleased RC integrity check"
        ) from exc


def assert_unreleased_rc(repo_root: Path, version: str) -> None:
    match = SEMVER.fullmatch(version)
    if version != BETA_VERSION or not match or not match.group("prerelease"):
        raise MetadataValidationError(
            "Same-version correction is restricted to the configured RC prerelease"
        )

    release_tag = f"v{version}"
    tag_result = _run_external(
        repo_root,
        "git",
        "ls-remote",
        "--exit-code",
        "--tags",
        "origin",
        f"refs/tags/{release_tag}",
    )
    if tag_result.returncode == 0:
        raise MetadataValidationError(
            f"Same-version correction is prohibited after tag {release_tag} exists"
        )
    if tag_result.returncode != 2 or tag_result.stdout.strip():
        raise MetadataValidationError(
            f"Unable to prove release tag {release_tag} is absent"
        )

    version_image = f"{BETA_IMAGE}:{version}"
    with tempfile.TemporaryDirectory(prefix="hamcp-rc-integrity-") as docker_config:
        anonymous_env = dict(os.environ)
        anonymous_env["DOCKER_CONFIG"] = docker_config
        registry_token = os.environ.get("HAMCP_GHCR_READ_TOKEN", "")
        registry_actor = os.environ.get("GITHUB_ACTOR", "")
        if registry_token and not registry_actor:
            raise MetadataValidationError(
                "Authenticated registry integrity check is incompletely configured"
            )
        if registry_token:
            login_result = _run_external(
                repo_root,
                "docker",
                "login",
                "ghcr.io",
                "--username",
                registry_actor,
                "--password-stdin",
                env=anonymous_env,
                input_text=registry_token,
            )
            if login_result.returncode != 0:
                raise MetadataValidationError(
                    "Unable to authenticate the read-only registry integrity check"
                )
        image_result = _run_external(
            repo_root,
            "docker",
            "buildx",
            "imagetools",
            "inspect",
            version_image,
            env=anonymous_env,
        )
    image_output = "\n".join((image_result.stdout, image_result.stderr))
    if image_result.returncode == 0:
        raise MetadataValidationError(
            f"Same-version correction is prohibited after image {version_image} exists"
        )
    exact_image_not_found = re.search(
        rf"(?:^|\s)ERROR:\s+{re.escape(version_image)}:\s+not found(?:\s|$)",
        image_output,
        re.IGNORECASE,
    )
    if not REGISTRY_TAG_NOT_FOUND.search(image_output) and not exact_image_not_found:
        raise MetadataValidationError(
            f"Unable to prove registry image {version_image} is absent"
        )


def validate_repository(
    repo_root: Path,
    *,
    base_ref: str,
    expected_version: str | None = None,
    deployed_version: str | None = None,
    paths: Iterable[str] | None = None,
    unreleased_integrity_check: Callable[[Path, str], None] | None = None,
) -> ValidationReport:
    production = read_yaml(repo_root / "hass_mcp_admin" / "config.yaml")
    beta = read_yaml(repo_root / "hass_mcp_engineering_beta" / "config.yaml")
    beta_version_py = read_python_constant(
        repo_root
        / "hass_mcp_engineering_beta"
        / "ha_mcp_engineering"
        / "version.py",
        "SERVER_VERSION",
    )
    minimum_secret_length = read_python_constant(
        repo_root
        / "hass_mcp_engineering_beta"
        / "ha_mcp_engineering"
        / "configuration.py",
        "MIN_ACCESS_SECRET_LENGTH",
    )
    validate_config_pair(
        production,
        beta,
        minimum_secret_length=minimum_secret_length,
    )

    beta_version = str(beta.get("version", ""))
    version_key(beta_version)
    staged_version = staged_release_version(repo_root, beta_version)
    if beta_version_py != beta_version:
        raise MetadataValidationError("Beta add-on and server versions differ")
    if expected_version and beta_version != expected_version:
        raise MetadataValidationError("Configured beta version does not match ExpectedVersion")

    path_set = set(paths) if paths is not None else changed_paths(repo_root, base_ref)
    production_changed = any(path.startswith("hass_mcp_admin/") for path in path_set)
    beta_changed = any(
        path.startswith("hass_mcp_engineering_beta/")
        and path not in NON_RELEASE_BETA_PATHS
        for path in path_set
    )
    if production_changed:
        raise MetadataValidationError("Production add-on files were modified")

    comparison = deployed_version or version_from_ref(repo_root, base_ref)
    same_version_correction = False
    if deployed_version or beta_changed:
        if not is_newer_version(beta_version, comparison):
            if (
                beta_changed
                and beta_version == comparison
                and staged_version is not None
            ):
                pass
            elif (
                beta_changed
                and beta_version == comparison
                and unreleased_integrity_check is not None
            ):
                unreleased_integrity_check(repo_root, beta_version)
                same_version_correction = True
            else:
                raise MetadataValidationError(
                    "Beta version was not bumped above the deployed/base version"
                )

    return ValidationReport(
        production_version=str(production.get("version", "")),
        beta_version=beta_version,
        compared_version=comparison,
        beta_changed=beta_changed,
        production_changed=production_changed,
        same_version_correction=same_version_correction,
        staged_release_version=staged_version,
    )


def parse_args(argv: list[str] | None = None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--base-ref", default="origin/main")
    parser.add_argument("--expected-version")
    parser.add_argument("--deployed-version")
    parser.add_argument(
        "--allow-unreleased-same-version",
        action="store_true",
        help="Allow an equal-version RC correction only after fail-closed remote tag and registry checks.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        report = validate_repository(
            args.repo_root.resolve(),
            base_ref=args.base_ref,
            expected_version=args.expected_version,
            deployed_version=args.deployed_version,
            unreleased_integrity_check=(
                assert_unreleased_rc if args.allow_unreleased_same_version else None
            ),
        )
    except MetadataValidationError as exc:
        print(f"Metadata validation failed: {exc}", file=sys.stderr)
        return 1
    print("Metadata validation passed.")
    print(f"Production: {PRODUCTION_SLUG} v{report.production_version} port {PRODUCTION_PORT}")
    print(f"Beta: {BETA_SLUG} v{report.beta_version} port {BETA_PORT}")
    if report.compared_version:
        print(f"Version comparison baseline: {report.compared_version}")
    if report.same_version_correction:
        print("Unreleased same-version RC integrity gate: passed")
    if report.staged_release_version:
        print(
            "Staged release declaration: "
            f"{report.beta_version} -> {report.staged_release_version}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
