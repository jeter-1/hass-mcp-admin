"""HAMCP-132 default-mode credential cases, reused for Home Assistant names.

Owner direction for HAMCP-226/228: Home Assistant's own identifiers and names
are screened with ``credentials.screen`` in its default mode. These are the
default-mode shapes the HAMCP-132 reviewer suites (``test_pr294_*``) proved,
collected so the dependency, integrity, helper and baseline paths can run the
same matrix. Every value is synthetic and assembled at test time.
"""

from __future__ import annotations

from urllib.parse import quote

import test_hamcp132_configuration_definitions as t

KNOWN_SECRET = t.ACCESS_SECRET
DB_PASSWORD = t.DB_PASSWORD
INVISIBLES = (0x200B, 0x200C, 0x200D, 0x2060, 0xFEFF, 0xAD)
CATEGORIES = (0x1F, 0x200E, 0xE123, 0x378, 0x2028, 0x2029, 0x034F, 0x115F, 0xFE0F)
BENIGN = ("Token refresh", "Password reset reminder", "°C", "Küche", "Lamp 💡",
          "Kitchen 20 °C, Küche 😀")


def credential_url(scheme: str = "postgresql") -> str:
    return t.db_url(scheme)


def withheld_cases() -> dict[str, tuple[str, str]]:
    """``label -> (value, needle)``: each value must be withheld, never shown."""

    needle = DB_PASSWORD
    cases = {scheme: (t.db_url(scheme), needle)
             for scheme in ("postgresql", "mysql", "mssql", "mongodb", "redis", "mqtt", "http", "https")}
    cases.update({
        "query": ("https://synthetic.invalid/?password=" + needle, needle),
        "encoded": (quote(t.db_url(), safe=""), needle),
        "double_encoded": (quote(quote(t.db_url(), safe=""), safe=""), needle),
        "bare": (t.DB_USER + ":" + needle + "@synthetic.invalid", needle),
        "nfkc": ("".join(chr(ord(x) + 65248) if 33 <= ord(x) <= 126 else x for x in t.db_url()), needle),
        "ratio_division": (t.db_url().replace(":", "∶").replace("/", "∕"), needle),
        "cyrillic_o_password": ("https://synthetic.invalid/?passwоrd=" + needle, needle),
        "known_secret": ("Token refresh " + KNOWN_SECRET, KNOWN_SECRET),
        "known_secret_upper": (KNOWN_SECRET.upper(), KNOWN_SECRET.upper()),
        "known_secret_swapcase": (KNOWN_SECRET.swapcase(), KNOWN_SECRET.swapcase()),
        "pem": ("-----BEGIN " + "PRIVATE KEY-----\n" + needle + "\n-----END PRIVATE KEY-----", needle),
    })
    for point in INVISIBLES:
        for encoded in (False, True):
            marker = quote(chr(point), safe="") if encoded else chr(point)
            cases[f"invisible_{point:x}_{'encoded' if encoded else 'raw'}"] = (
                "https://synthetic.invalid/?pass" + marker + "word=" + needle, needle)
    for point in (9, 10, 13):
        for rounds in range(3):
            marker = chr(point)
            for _ in range(rounds):
                marker = quote(marker, safe="")
            cases[f"control_{point:x}_{rounds}"] = (
                "https://synthetic.invalid/?pass" + marker + "word=" + needle, needle)
    for word in ("pαssword", "pаsswοrd"):
        cases["confusable_" + word.encode("unicode_escape").decode()] = (
            "https://synthetic.invalid/?" + word + "=" + needle, needle)
    return cases


def unverifiable_cases() -> dict[str, str]:
    """Format, control and unassigned characters: never shown as written."""

    cases = {}
    for point in CATEGORIES:
        for rounds in range(3):
            text = "Probe" + chr(point) + "tail"
            for _ in range(rounds):
                text = quote(text, safe="", errors="surrogatepass")
            cases[f"category_{point:x}_{rounds}"] = text
    return cases
