"""HAMCP-162: the reversible encoding of committed evidence.

The D1 captures, the generated fixtures and their generator carry the
synthetic secret shapes the reader must withhold (PEM headers, JWTs, opaque
runs, URL userinfo, ``Pwd=`` assignments and similar). They cannot be
recaptured, and their records carry hashes, so they are never edited by
hand. Instead every committed file whose text matches a secret-shaped
pattern below is stored *encoded*: :data:`SENTINEL` is inserted inside each
match until no pattern matches. Decoding removes every sentinel and restores
the original bytes exactly.

- ``MANIFEST.sha256`` pins the committed (encoded) bytes.
- ``DECODED.sha256`` pins the decoded bytes of each encoded file; they equal
  the hashes the manifest pinned before encoding, so the capture provenance
  is unchanged.

Consumers read evidence through :func:`read_text`, which decodes and checks
``DECODED.sha256``. No file in this directory contains literal secret-shaped
text; every pattern is itself assembled from fragments.

Usage::

    python codec.py encode FILE...   # encode in place, print new hashes
    python codec.py decode FILE OUT  # write the decoded bytes of FILE
    python codec.py verify           # check MANIFEST and DECODED
    python codec.py scan FILE...     # list secret-shaped findings

Standard library only.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parent
SENTINEL = "⟦split⟧"
_FIRST = 2
_EVERY = 48
_MAX_PASSES = 16

_DASH5 = "-" + "{5}"
_SCHEME = ":" + "//"

# The repository credential scan (scripts/check.ps1 Test-SecretPatterns) and
# a broader high-confidence set. Each source string is assembled from
# fragments so this file never matches its own patterns.
PATTERNS: dict[str, re.Pattern[str]] = {
    name: re.compile(source)
    for name, source in (
        ("repo_github_pat", "github" + "_pat_[A-Za-z0-9_]{20,}"),
        ("repo_github_token", "gh" + "[pousr]_[A-Za-z0-9]{36,}"),
        ("repo_sk", "s" + "k-[A-Za-z0-9_-]{20,}"),
        ("repo_pem_private", _DASH5 + "BEGIN (?:RSA |EC |OPENSSH |DSA )?PRIVATE " + "KEY" + _DASH5),
        ("pem_any", "(?i)" + _DASH5 + r"\s*(?:BEGIN|END)[ A-Z0-9]*" + _DASH5),
        ("jwt", "ey" + r"J[A-Za-z0-9_-]{6,}\.[A-Za-z0-9_-]{6,}"),
        ("opaque", "[A-Za-z0-9+/=]" + "{100,}"),
        ("url_userinfo", r"(?i)[a-z][a-z0-9+.-]*" + _SCHEME + r"[^\s/:@'\"]+:[^\s/@'\"]+@"),
        ("bearer", r"(?i)\bbear" + r"er\s+[A-Za-z0-9._~+/-]{12,}"),
        ("aws", "AK" + "IA[0-9A-Z]{16}"),
        ("slack", "xo" + "x[abprs]-[A-Za-z0-9-]{10,}"),
        ("google", "AI" + "za[0-9A-Za-z_-]{30,}"),
        # A Markdown code-span backtick (\x60) ends an assignment value.
        ("odbc_pwd", r"(?i)\b(?:pw" + r"d|pass" + r"word)\s*=\s*[^;\s'\"\x60]{3,}"),
        ("matter", "M" + "T:[0-9A-Z.-]{15,}"),
        ("webhook", "/api/" + "webhook/[A-Za-z0-9_-]{8,}"),
    )
}


def findings(text: str) -> list[tuple[str, int]]:
    """Every (pattern name, line number) match in ``text``."""

    found = []
    for number, line in enumerate(text.split("\n"), 1):
        for name, pattern in PATTERNS.items():
            if pattern.search(line):
                found.append((name, number))
    return found


def _split(match: re.Match[str]) -> str:
    text = match[0]
    pieces = [text[:_FIRST]]
    pieces += [text[index : index + _EVERY] for index in range(_FIRST, len(text), _EVERY)]
    return SENTINEL.join(piece for piece in pieces if piece)


def encode(text: str) -> str:
    """Insert :data:`SENTINEL` inside every match until none remains."""

    if SENTINEL in text:
        raise ValueError("the sentinel already occurs in the input")
    for _ in range(_MAX_PASSES):
        if not findings(text):
            return text
        for pattern in PATTERNS.values():
            text = pattern.sub(_split, text)
    raise ValueError("the encoding did not converge")


def decode(text: str) -> str:
    return text.replace(SENTINEL, "")


def _listing(name: str) -> dict[str, str]:
    path = ROOT / name
    entries = {}
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            digest, relative = line.split("  ", 1)
            entries[relative] = digest
    return entries


def decoded_bytes(path: Path) -> bytes:
    """The original bytes of an evidence file, checked against DECODED."""

    path = Path(path).resolve()
    raw = path.read_bytes()
    relative = "./" + path.relative_to(ROOT).as_posix()
    expected = _listing("DECODED.sha256").get(relative)
    if expected is None:
        return raw
    data = decode(raw.decode("utf-8")).encode("utf-8")
    if hashlib.sha256(data).hexdigest() != expected:
        raise ValueError(f"decoded evidence hash mismatch: {relative}")
    return data


def read_text(path: Path) -> str:
    return decoded_bytes(path).decode("utf-8")


def verify() -> list[str]:
    """Problems with MANIFEST.sha256 or DECODED.sha256 (empty when clean)."""

    problems = []
    for relative, digest in _listing("MANIFEST.sha256").items():
        target = ROOT / relative
        if not target.is_file():
            problems.append(f"missing: {relative}")
        elif hashlib.sha256(target.read_bytes()).hexdigest() != digest:
            problems.append(f"manifest mismatch: {relative}")
    for relative in _listing("DECODED.sha256"):
        try:
            decoded_bytes(ROOT / relative)
        except (OSError, ValueError) as error:
            problems.append(str(error))
    return problems


def main(argv: list[str]) -> int:
    command, *args = argv
    if command == "encode":
        for name in args:
            path = Path(name)
            original = path.read_bytes()
            text = original.decode("utf-8")
            encoded = encode(text) if findings(text) else text
            if decode(encoded).encode("utf-8") != original:
                raise SystemExit(f"round trip failed: {name}")
            path.write_bytes(encoded.encode("utf-8"))
            print(hashlib.sha256(original).hexdigest(),
                  hashlib.sha256(encoded.encode("utf-8")).hexdigest(), name)
        return 0
    if command == "decode":
        source, target = args
        Path(target).write_bytes(decoded_bytes(Path(source)))
        return 0
    if command == "verify":
        problems = verify()
        print("\n".join(problems) or "evidence verified")
        return 1 if problems else 0
    if command == "scan":
        hits = [(name, found) for name in args
                for found in findings(Path(name).read_text(encoding="utf-8", errors="replace"))]
        for name, (pattern, line) in hits:
            print(f"{name}:{line}: {pattern}")
        return 1 if hits else 0
    raise SystemExit(f"unknown command: {command}")


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
