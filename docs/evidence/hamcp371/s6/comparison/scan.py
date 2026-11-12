"""Pattern scan for credential-shaped content in files (data only; python3 -I).

Complements the lane's own scan, which searched the run directory for the exact
bytes of each minted token while the tokens still existed.  Prints one line per
file with a hit, then a total.  Exit 1 on any hit.
"""
import re
import sys
from pathlib import Path

PATTERNS = {
    "jwt": re.compile(rb"eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}"),
    "bearer": re.compile(rb"[Bb]earer\s+[A-Za-z0-9._~+/-]{16,}"),
    "token_value": re.compile(
        rb"(?i)\"?(access_token|refresh_token|client_secret|password|api_key|"
        rb"HOMEASSISTANT_TOKEN|SUPERVISOR_TOKEN)\"?\s*[:=]\s*\"?[A-Za-z0-9._~+/-]{12,}"),
    "private_key": re.compile(rb"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    "long_base64url": re.compile(rb"(?<![A-Za-z0-9_-])[A-Za-z0-9_-]{80,}(?![A-Za-z0-9_-])"),
}


def files(arguments):
    for argument in arguments:
        path = Path(argument)
        if path.is_dir():
            yield from sorted(item for item in path.rglob("*") if item.is_file())
        else:
            yield path


def main():
    hits = scanned = 0
    for path in files(sys.argv[1:]):
        scanned += 1
        data = path.read_bytes()
        for name, pattern in PATTERNS.items():
            found = pattern.findall(data)
            if found:
                hits += len(found)
                print(f"HIT {name} x{len(found)} {path}")
    print(f"scanned={scanned} hits={hits}")
    return 1 if hits else 0


sys.exit(main())
