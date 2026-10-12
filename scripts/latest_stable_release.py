"""Resolve the newest stable Core and ha-mcp releases for the nightly early warning.

Observation only (HAMCP-154). The result never changes admission policy, the
signed registries, any upstream HOLD or ``ci/support-window.yaml``; it only says
whether the newest stable releases are inside the support window and pins the
newest Core image by digest for one disposable contract run paired with the
window's current, reviewed ha-mcp source. Drafts and prereleases are refused,
as is any tag that is not a plain ``X.Y.Z`` release.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
from pathlib import Path
import re
import subprocess
import urllib.request

ROOT = Path(__file__).resolve().parent.parent
RELEASES = {
    "core": "https://api.github.com/repos/home-assistant/core/releases/latest",
    "ha_mcp": "https://api.github.com/repos/homeassistant-ai/ha-mcp/releases/latest",
}
TAG = re.compile(r"v?([0-9]{1,4}\.[0-9]{1,2}\.[0-9]{1,3})")
DIGEST = re.compile(r"sha256:[0-9a-f]{64}")
MAX_RESPONSE = 1_000_000


class Refusal(ValueError):
    pass


def require(condition, category):
    if not condition:
        raise Refusal(category)


def _support_window():
    spec = importlib.util.spec_from_file_location("support_window", ROOT / "scripts/support_window.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def stable_version(payload, component):
    """Return the plain release version from a GitHub ``releases/latest`` payload."""

    require(isinstance(payload, dict), component + "_release_malformed")
    require(payload.get("draft") is False and payload.get("prerelease") is False, component + "_not_stable")
    tag = payload.get("tag_name")
    match = TAG.fullmatch(tag) if isinstance(tag, str) else None
    require(match is not None, component + "_tag_not_plain_release")
    return match.group(1)


def key(version):
    return tuple(int(part) for part in version.split("."))


def classify(version, slots):
    """Name the window slot holding ``version``, else whether it is newer or older."""

    filled = [slot for slot in ("current", "candidate", "rollback") if slots[slot] is not None]
    for slot in filled:
        if slots[slot]["version"] == version:
            return slot
    newest = max(key(slots[slot]["version"]) for slot in filled)
    return "newer_than_window" if key(version) > newest else "older_than_window"


def real_ha_row(window, core_version, core_digest, core_slot):
    require(DIGEST.fullmatch(core_digest or "") is not None, "core_digest_invalid")
    upstream = window["ha_mcp"]["current"]
    return {
        "lane": "latest-ha-" + core_version.replace(".", "-"),
        "core_window_slot": core_slot,
        "ha_version": core_version,
        "ha_image": f"ghcr.io/home-assistant/home-assistant:{core_version}@{core_digest}",
        "ha_mcp_version": upstream["version"],
        "ha_mcp_image": f"ghcr.io/homeassistant-ai/ha-mcp:{upstream['version']}@{upstream['image_index_digest']}",
        "ha_mcp_source_commit": upstream["source_commit"],
        "ha_mcp_source_sha256": upstream["source_archive_sha256"],
    }


def summary(window, core_version, ha_mcp_version, core_digest, ha_mcp_digest):
    core_slot = classify(core_version, window["core"])
    ha_mcp_slot = classify(ha_mcp_version, window["ha_mcp"])
    return {
        "core": {"version": core_version, "image_index_digest": core_digest, "window_slot": core_slot},
        "ha_mcp": {"version": ha_mcp_version, "image_index_digest": ha_mcp_digest, "window_slot": ha_mcp_slot},
        "inside_window": core_slot in {"current", "candidate", "rollback"}
        and ha_mcp_slot in {"current", "candidate", "rollback"},
        "real_ha": {"include": [real_ha_row(window, core_version, core_digest, core_slot)]},
    }


def fetch_release(url, token):
    headers = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}
    if token:
        headers["Authorization"] = "Bearer " + token
    request = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(request, timeout=30) as response:  # noqa: S310 - fixed https URL
        raw = response.read(MAX_RESPONSE + 1)
    require(len(raw) <= MAX_RESPONSE, "release_response_bound")
    return json.loads(raw)


def index_digest(image):
    result = subprocess.run(
        ["docker", "buildx", "imagetools", "inspect", image, "--format", "{{json .Manifest}}"],
        check=True, capture_output=True, text=True, timeout=120,
    )
    digest = json.loads(result.stdout).get("digest")
    require(isinstance(digest, str) and DIGEST.fullmatch(digest), "image_digest_unresolved")
    return digest


def write_outputs(path, values):
    with Path(path).open("a", encoding="utf-8") as stream:
        for name, value in values.items():
            text = json.dumps(value, sort_keys=True, separators=(",", ":"))
            require("\n" not in text, "output_newline")
            stream.write(f"{name}={text}\n")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("operation", choices=("resolve",))
    parser.add_argument("--github-output", type=Path, required=True)
    args = parser.parse_args()
    try:
        window = _support_window().load()
        token = os.environ.get("GITHUB_TOKEN", "")
        core = stable_version(fetch_release(RELEASES["core"], token), "core")
        ha_mcp = stable_version(fetch_release(RELEASES["ha_mcp"], token), "ha_mcp")
        result = summary(window, core, ha_mcp,
                         index_digest(f"ghcr.io/home-assistant/home-assistant:{core}"),
                         index_digest(f"ghcr.io/homeassistant-ai/ha-mcp:{ha_mcp}"))
        write_outputs(args.github_output, {"real_ha": result["real_ha"], "summary": result})
        print(json.dumps(result, sort_keys=True, indent=1))
    except (Refusal, OSError, ValueError, subprocess.SubprocessError) as exc:
        category = str(exc) if isinstance(exc, Refusal) else type(exc).__name__
        parser.exit(1, "Latest stable resolution refused: " + category + "\n")


if __name__ == "__main__":
    main()
