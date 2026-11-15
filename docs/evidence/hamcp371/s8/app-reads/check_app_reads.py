"""HAMCP-371 S8 Q7: check the 9.2.0 App registry reads and the revision gate.

Run from the repository root with ``python -I``. Reads only the committed
files in ``raw/``, after checking each against ``SHA256SUMS``. Prints, for
each architecture, the index and image manifest digests (each the sha256 of
the raw bytes the registry served), the image labels, the SLSA ``vcs:``
values, and the git blob id of the Dockerfile embedded in the provenance.

Exit 0 only if every Q7 gate holds: the image revision label
``org.opencontainers.image.revision`` equals ``REVISION`` on both
architectures. Exit 1 otherwise; the reasons are printed.
"""

import base64
import hashlib
import json
from pathlib import Path

BASE = Path(__file__).resolve().parent
RAW = BASE / "raw"
REVISION = "51e3aac788482a081807591cd5d58d07dc4e540b"
LABEL = "org.opencontainers.image.revision"
PLATFORM = {"amd64": ("amd64", "linux"), "aarch64": ("arm64", "linux")}


def _sums():
    sums = {}
    for line in (BASE / "SHA256SUMS").read_text(encoding="utf-8").splitlines():
        digest, name = line.split("  ", 1)
        sums[name] = digest
    return sums


def _read(sums, name):
    raw = (RAW / name).read_bytes()
    if hashlib.sha256(raw).hexdigest() != sums[name]:
        raise SystemExit(f"sha256 mismatch: {name}")
    return raw


def main():
    sums = _sums()
    if sorted(sums) != sorted(path.name for path in RAW.iterdir()):
        raise SystemExit("raw/ and SHA256SUMS list different files")
    failures = []
    for arch, (architecture, os_name) in PLATFORM.items():
        index_raw = _read(sums, f"index-{arch}.raw.json")
        manifest_raw = _read(sums, f"manifest-{arch}.raw.json")
        index_digest = "sha256:" + hashlib.sha256(index_raw).hexdigest()
        manifest_digest = "sha256:" + hashlib.sha256(manifest_raw).hexdigest()
        images = [
            item for item in json.loads(index_raw)["manifests"]
            if item.get("platform", {}).get("architecture") == architecture
        ]
        if [item["digest"] for item in images] != [manifest_digest]:
            raise SystemExit(f"{arch}: index does not name the read manifest")
        config = json.loads(_read(sums, f"config-{arch}.json"))
        if (config["architecture"], config["os"]) != (architecture, os_name):
            raise SystemExit(f"{arch}: config platform differs")
        labels = config["config"].get("Labels") or {}
        slsa = json.loads(_read(sums, f"provenance-{arch}.json"))["SLSA"]
        args = slsa["buildDefinition"]["externalParameters"]["request"]["root"]["request"]["args"]
        source = slsa["runDetails"]["metadata"]["buildkit_metadata"]["source"]["infos"][0]
        dockerfile = base64.b64decode(source["data"])
        blob = hashlib.sha1(b"blob %d\0" % len(dockerfile) + dockerfile).hexdigest()
        print(f"{arch}: index {index_digest}")
        print(f"{arch}: image manifest {manifest_digest} ({os_name}/{architecture})")
        print(f"{arch}: labels {json.dumps(labels, sort_keys=True)}")
        print(f"{arch}: cmd {config['config'].get('Cmd')} entrypoint {config['config'].get('Entrypoint')}")
        print(f"{arch}: slsa builder {slsa['runDetails']['builder']['id']}")
        for key in sorted(args):
            if key.startswith("vcs:"):
                print(f"{arch}: slsa {key} {args[key]}")
        print(f"{arch}: provenance {source['filename']} git blob {blob}")
        if labels.get(LABEL) != REVISION:
            failures.append(f"{arch}: {LABEL} is {labels.get(LABEL)!r}, not {REVISION}")
    for failure in failures:
        print(f"GATE FAIL {failure}")
    print("Q7 revision gate", "FAIL" if failures else "PASS")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
