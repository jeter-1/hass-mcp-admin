"""HAMCP-162: synthetic secret builders and the committed-file scan.

Every synthetic secret the HAMCP-162 tests use is assembled here at runtime
from innocuous fragments, so no committed test, fixture or evidence file
contains literal secret-shaped text. Each builder embeds a unique
alphanumeric *needle*; a test asserts the needle never appears in the
serialized response. Evidence files are read through the reversible
``docs/evidence/hamcp162/codec.py`` encoding.

Synthetic values only: no real credential, endpoint or account.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import re
from urllib.parse import quote

ROOT = Path(__file__).resolve().parents[1]
EVIDENCE = ROOT / "docs/evidence/hamcp162"

_spec = importlib.util.spec_from_file_location("hamcp162_evidence_codec", EVIDENCE / "codec.py")
codec = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(codec)

DASHES = "-" * 5
SCHEME_SEPARATOR = ":" + "//"
EQUALS = "="


def evidence_text(relative: str) -> str:
    """The decoded text of one committed evidence file."""

    return codec.read_text(EVIDENCE / relative)


def evidence_json(relative: str):
    return json.loads(evidence_text(relative))


def needle(tag: str) -> str:
    """A unique, unmistakably synthetic alphanumeric marker."""

    return "SYNTH" + re.sub("[^A-Za-z0-9]", "", tag) + "Q7V"


def enc(value: str, times: int) -> str:
    for _ in range(times):
        value = quote(value, safe="")
    return value


def assign(key: str, value: str, separator: str = EQUALS) -> str:
    return key + separator + value


def pwd(value: str, key: str = "Pwd") -> str:
    """A connection-string password assignment."""

    return assign(key, value)


def url(scheme: str, user: str | None, password: str | None, rest: str) -> str:
    """A URL with optional userinfo (user, then password) before ``rest``."""

    info = ""
    if user is not None:
        info = user + (":" + password if password is not None else "") + "@"
    return scheme + SCHEME_SEPARATOR + info + rest


def pem(label: str = "CERTIFICATE", body: str = "", *, lower: bool = False) -> str:
    header = DASHES + "BEGIN " + label + DASHES
    footer = DASHES + "END " + label + DASHES
    text = header + "\n" + body + "\n" + footer
    return text.lower() if lower else text


def private_key_pem(body: str = "") -> str:
    return pem("PRIVATE " + "KEY", body)


def opaque(length: int, unit: str = "b7") -> str:
    """A base64-alphabet run of exactly ``length`` characters."""

    return (unit * (length // len(unit) + 1))[:length]


def wrapped(text: str, width: int = 64, separator: str = "\n") -> str:
    """``text`` split into ``width``-character lines joined by ``separator``,
    the way a header-less key body is wrapped (A1)."""

    return separator.join(text[i:i + width] for i in range(0, len(text), width))


def jwt(tag: str) -> str:
    head = "ey" + "J" + "synthHeader0"
    return head + "." + "synthPayload" + needle(tag) + "." + "synthSignature0"


def bearer(value: str) -> str:
    return "Bear" + "er " + value


def auth_header(value: str) -> str:
    return "Authori" + "zation: " + value


def cookie_header(value: str) -> str:
    return "Coo" + "kie: " + value


def webhook_path(value: str) -> str:
    return "/api/" + "webhook/" + value


def webhook_prose(value: str) -> str:
    return "web" + "hook id " + value


def login_flow_path(value: str) -> str:
    return "/auth/" + "login_flow/" + value


def matter_payload(value: str) -> str:
    return "M" + "T:" + value.upper()


def matter_code(digits: str) -> str:
    return "setup " + "code: " + digits


def url_parameter(name: str, value: str) -> str:
    return url("https", None, None, "synthetic.invalid/path?" + assign(name, value))


def serialized(key: str, value: str) -> str:
    return '"' + key + '": "' + value + '"'


# ---------------------------------------------------------------------------
# The committed-file scan.

# The HAMCP-162 test, fixture and evidence files.
SCANNED_GLOBS = (
    "tests/test_hamcp162_*.py",
    "tests/hamcp162_synthetic.py",
    "docs/evidence/hamcp162/**/*",
    "docs/evidence/hamcp162-mutations/**/*",
    "docs/HAMCP_162_*.md",
)


def repository_patterns() -> dict[str, re.Pattern[str]]:
    """The patterns of ``scripts/check.ps1`` Test-SecretPatterns, parsed."""

    source = (ROOT / "scripts/check.ps1").read_text(encoding="utf-8")
    block = source.split("function Test-SecretPatterns", 1)[1]
    block = block.split("$patterns = @(", 1)[1].split("\n    )", 1)[0]
    patterns = {}
    for number, line in enumerate(block.strip().splitlines()):
        literals = re.findall(r"'((?:[^']|'')*)'", line)
        if literals:
            text = "".join(literal.replace("''", "'") for literal in literals)
            # PowerShell -match is case-insensitive.
            patterns[f"check_ps1_{number}"] = re.compile(text, re.IGNORECASE)
    return patterns


def scan_patterns() -> dict[str, re.Pattern[str]]:
    return {**repository_patterns(), **codec.PATTERNS}


def scanned_files() -> list[Path]:
    """Every file under :data:`SCANNED_GLOBS`, from the file system (so the
    scan also runs in a copied tree without ``.git``), less bytecode."""

    found = {
        path.resolve()
        for pattern in SCANNED_GLOBS
        for path in ROOT.glob(pattern)
        if path.is_file() and "__pycache__" not in path.parts and path.suffix != ".pyc"
    }
    return sorted(found)


def scan(paths, patterns=None) -> list[tuple[str, int, str]]:
    """Every (file, line, pattern) secret-shaped hit in ``paths``."""

    patterns = scan_patterns() if patterns is None else patterns
    hits = []
    for path in paths:
        text = Path(path).read_bytes().decode("utf-8", errors="replace")
        for number, line in enumerate(text.split("\n"), 1):
            for name, pattern in patterns.items():
                if pattern.search(line):
                    hits.append((Path(path).resolve().relative_to(ROOT).as_posix(), number, name))
    return hits
