#!/usr/bin/env python3
"""HAMCP-371 SR-a: rebuild a component tree listing from an upstream git store.

The listing has one ``commit <sha>`` line, one ``<sha256> <size> <mode> <path>``
line per blob under the prefix (git ls-tree order), and a final
``FILES n BYTES b MISSING m SHA1_MISMATCH k`` line. With ``--list`` the rebuilt
listing must equal the committed list byte for byte.

The store is untrusted data and is read with git plumbing only, with lazy fetch
off, so a missing blob is counted as MISSING and never fetched.
"""

from __future__ import annotations

import argparse
import hashlib
import os
import subprocess
import sys
from pathlib import Path


def _git(store: Path, *args: str, stdin: bytes | None = None) -> bytes:
    env = dict(os.environ, GIT_NO_LAZY_FETCH="1", GIT_TERMINAL_PROMPT="0")
    return subprocess.run(
        ["git", "-C", str(store), *args],
        input=stdin,
        capture_output=True,
        check=True,
        env=env,
    ).stdout


def build_listing(store: Path, commit: str, prefix: str) -> bytes:
    resolved = _git(store, "rev-parse", "--verify", f"{commit}^{{commit}}").decode().strip()
    if resolved != commit:
        raise SystemExit(f"commit mismatch: {resolved} != {commit}")
    entries = []
    for raw in _git(store, "ls-tree", "-r", "-z", commit, "--", prefix).split(b"\0"):
        if not raw:
            continue
        meta, path = raw.split(b"\t", 1)
        mode, kind, oid = meta.decode().split()
        if kind != "blob":
            raise SystemExit(f"unexpected {kind} entry: {path!r}")
        entries.append((mode, oid, path.decode()))
    lines = [f"commit {commit}"]
    total = missing = mismatch = 0
    for mode, oid, path in entries:
        try:
            data = _git(store, "cat-file", "blob", oid)
        except subprocess.CalledProcessError:
            missing += 1
            lines.append(f"MISSING {oid} {mode} {path}")
            continue
        header = f"blob {len(data)}\0".encode()
        if hashlib.sha1(header + data).hexdigest() != oid:
            mismatch += 1
        total += len(data)
        lines.append(f"{hashlib.sha256(data).hexdigest()} {len(data)} {mode} {path}")
    lines.append(f"FILES {len(entries)} BYTES {total} MISSING {missing} SHA1_MISMATCH {mismatch}")
    return ("\n".join(lines) + "\n").encode()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--store", type=Path, required=True)
    parser.add_argument("--commit", required=True)
    parser.add_argument("--prefix", default="custom_components/ha_mcp_tools")
    parser.add_argument("--list", type=Path, help="committed listing to compare")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    listing = build_listing(args.store, args.commit, args.prefix)
    if args.output:
        args.output.write_bytes(listing)
    footer = listing.decode().rstrip("\n").rsplit("\n", 1)[-1]
    print(f"rebuilt sha256 {hashlib.sha256(listing).hexdigest()} {footer}")
    complete = footer.endswith("MISSING 0 SHA1_MISMATCH 0")
    if args.list is not None:
        committed = args.list.read_bytes()
        equal = committed == listing
        print(f"committed sha256 {hashlib.sha256(committed).hexdigest()} equal {str(equal).lower()}")
        return 0 if equal and complete else 1
    return 0 if complete else 1


if __name__ == "__main__":
    sys.exit(main())
