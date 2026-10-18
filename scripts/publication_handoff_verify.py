"""Verify a publication handoff in the read-only detect-release job.

This entry point is the only publication script that imports the locked YAML
parser, from the hashed lock installed in detect-release. Write jobs never run
it; they run the self-contained, stdlib-only ``publication_handoff``.
"""

import argparse
from pathlib import Path
import re
import zipfile

import publication_handoff as handoff
from publication_handoff import Refusal, require, sha


def config_version(ref, run_git):
    """Bind the bounded version spelling to one semantic root YAML field.

    Compose nodes without constructing objects or expanding alias graphs.
    This is eligibility, not full configuration or release validation. The
    locked parser is imported only by this read-only verifier: the handoff,
    receipt and claim modules that write jobs run remain stdlib (HAMCP-204).
    """
    text = run_git("show", f"{sha(ref)}:hass_mcp_engineering_beta/config.yaml")
    require(len(text.encode("utf-8")) <= 65_536, "release_version_unavailable")
    try:
        import yaml
    except ImportError as exc:
        raise Refusal("release_version_parser_unavailable") from exc
    try:
        root = yaml.compose(text, Loader=yaml.SafeLoader)
    except (yaml.YAMLError, RecursionError) as exc:
        raise Refusal("release_version_unavailable") from exc
    string_tag = "tag:yaml.org,2002:str"
    require(isinstance(root, yaml.MappingNode) and root.tag == "tag:yaml.org,2002:map",
            "release_version_unavailable")
    # Merge and complex keys cannot supply implicit version authority. Inspect
    # nodes before any constructor can discard duplicate or aliased root keys.
    require(all(isinstance(key, yaml.ScalarNode) and key.tag == string_tag
                for key, _ in root.value), "release_version_unavailable")
    versions = [value for key, value in root.value if key.value == "version"]
    require(len(versions) == 1 and isinstance(versions[0], yaml.ScalarNode)
            and versions[0].tag == string_tag, "release_version_unavailable")
    candidates = [line for line in text.splitlines()
                  if re.match(r"^(?:version|[\"']version[\"'])[ \t]*:", line)]
    require(len(candidates) == 1, "release_version_unavailable")
    value = r"[0-9A-Za-z][0-9A-Za-z.+-]{0,63}"
    match = re.fullmatch(rf"version:[ \t]*(?:\"({value})\"|'({value})'|({value}))[ \t]*",
                         candidates[0])
    require(match is not None, "release_version_unavailable")
    version = next(item for item in match.groups() if item is not None)
    require(version == versions[0].value, "release_version_unavailable")
    return version


def verify(run_id, attempt, authority, api=handoff.read_api, run_git=handoff.git):
    return handoff.verify(run_id, attempt, authority, api, run_git, version_of=config_version)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id", type=int)
    parser.add_argument("--attempt", type=int)
    parser.add_argument("--authority")
    parser.add_argument("--github-output", type=Path)
    args = parser.parse_args()
    try:
        result = verify(args.run_id, args.attempt, args.authority)
        if args.github_output:
            with args.github_output.open("a") as output:
                for key, value in result.items():
                    output.write(f"{key}={value}\n")
    except Refusal as exc:
        parser.exit(1, "Publication handoff refused: " + str(exc) + "\n")
    except (ValueError, KeyError, TypeError, AttributeError, OSError, zipfile.BadZipFile):
        # Do not print arbitrary API data, artifact contents or subprocess errors.
        parser.exit(1, "Publication handoff refused: evidence missing, stale or mismatched.\n")
    print(handoff.json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
