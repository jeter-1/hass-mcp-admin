"""Validate and apply one staged Engineering Beta release version."""

from __future__ import annotations

import argparse
import importlib.util
from pathlib import Path
import re

from awesomeversion import AwesomeVersion


FINAL_RC3_VERSION = "2.0.0-rc.3"
NEXT_VERSION_PATH = Path(".release/next-version")
AUTHORITATIVE_VERSION_FILES = (
    (
        Path("hass_mcp_engineering_beta/config.yaml"),
        re.compile(r'(?m)^version: "([^"]+)"$'),
        'version: "{version}"',
    ),
    (
        Path("hass_mcp_engineering_beta/ha_mcp_engineering/version.py"),
        re.compile(r'(?m)^SERVER_VERSION = "([^"]+)"$'),
        'SERVER_VERSION = "{version}"',
    ),
    (
        Path("scripts/validate_addon_metadata.py"),
        re.compile(r'(?m)^BETA_VERSION = "([^"]+)"$'),
        'BETA_VERSION = "{version}"',
    ),
)


class PromotionError(RuntimeError):
    pass


def read_next_version(repo_root: Path) -> str:
    path = repo_root / NEXT_VERSION_PATH
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise PromotionError("The staged release declaration is missing") from exc
    lines = text.splitlines()
    if len(lines) != 1 or not lines[0] or lines[0] != lines[0].strip():
        raise PromotionError("The staged release declaration must contain one version")
    return lines[0]


def authoritative_versions(repo_root: Path) -> dict[Path, str]:
    versions: dict[Path, str] = {}
    for relative, pattern, _replacement in AUTHORITATIVE_VERSION_FILES:
        try:
            text = (repo_root / relative).read_text(encoding="utf-8")
        except OSError as exc:
            raise PromotionError("Unable to read authoritative release metadata") from exc
        matches = pattern.findall(text)
        if len(matches) != 1:
            raise PromotionError(
                f"Expected exactly one authoritative version in {relative.as_posix()}"
            )
        versions[relative] = matches[0]
    return versions


def validate_candidate(repo_root: Path) -> tuple[str, str]:
    candidate = read_next_version(repo_root)
    versions = authoritative_versions(repo_root)
    advertised = set(versions.values())
    if len(advertised) != 1:
        raise PromotionError("Advertised add-on, runtime, and validator versions differ")
    current = advertised.pop()
    try:
        current_version = AwesomeVersion(current)
        candidate_version = AwesomeVersion(candidate)
        final_rc3 = AwesomeVersion(FINAL_RC3_VERSION)
        if not candidate_version > current_version:
            raise PromotionError(
                "The staged release version must be newer than advertised"
            )
        if not candidate_version < final_rc3:
            raise PromotionError(
                "The staged development version must remain below final RC3"
            )
    except PromotionError:
        raise
    except Exception as exc:
        raise PromotionError("A release version is not AwesomeVersion-compatible") from exc
    validate_document_authority(repo_root, candidate)
    return current, candidate


def validate_document_authority(
    repo_root: Path, version: str
) -> dict[str, object]:
    resolution = staged_document_resolution(repo_root, version)
    if (
        resolution.get("resolution_status") != "exact"
        or resolution.get("active_release_notes") == "unknown"
        or resolution.get("active_acceptance_document") == "unknown"
    ):
        raise PromotionError(
            "The staged release requires exact version-matched release notes and "
            "acceptance authority"
        )
    return resolution


def staged_document_resolution(repo_root: Path, version: str) -> dict[str, object]:
    """Use the context resolver as the single release-document authority."""

    context_path = Path(__file__).with_name("codex-context.py")
    spec = importlib.util.spec_from_file_location(
        "_codex_context_release_authority", context_path
    )
    if spec is None or spec.loader is None:
        raise PromotionError("Unable to load the release-document authority resolver")
    module = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(module)
        return module.resolve_documents(repo_root, version)
    except PromotionError:
        raise
    except Exception as exc:
        raise PromotionError("Unable to resolve staged release authority") from exc


def apply_candidate(repo_root: Path) -> tuple[str, str]:
    current, candidate = validate_candidate(repo_root)
    for relative, pattern, replacement in AUTHORITATIVE_VERSION_FILES:
        path = repo_root / relative
        text = path.read_text(encoding="utf-8")
        updated, count = pattern.subn(
            replacement.format(version=candidate),
            text,
        )
        if count != 1:
            raise PromotionError(
                f"Unable to update authoritative version in {relative.as_posix()}"
            )
        path.write_text(updated, encoding="utf-8", newline="")
    (repo_root / NEXT_VERSION_PATH).unlink()
    promoted = authoritative_versions(repo_root)
    if set(promoted.values()) != {candidate}:
        raise PromotionError("Release metadata did not converge on the staged version")
    return current, candidate


def parse_args(argv: list[str] | None = None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--repo-root",
        type=Path,
        default=Path(__file__).resolve().parents[1],
    )
    action = parser.add_mutually_exclusive_group()
    action.add_argument("--apply", action="store_true")
    action.add_argument("--validate-authority", metavar="VERSION")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    repo_root = args.repo_root.resolve()
    if args.validate_authority:
        validate_document_authority(repo_root, args.validate_authority)
        print(
            "Validated exact release-document authority for "
            f"{args.validate_authority}."
        )
        return 0
    current, candidate = (
        apply_candidate(repo_root)
        if args.apply
        else validate_candidate(repo_root)
    )
    action = "Applied" if args.apply else "Validated"
    print(f"{action} staged Engineering release {current} -> {candidate}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
