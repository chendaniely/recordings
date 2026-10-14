"""Install the pinned restic into .cache/bin/restic, checked against its release checksum.

    uv run python scripts/fetch_restic.py        (or: make restic)

For CI and the Mac. The Docker image installs the same version itself (docker/Dockerfile), and the
runbook's NAS spike downloads it with the same checksums; tests check that all three agree. The
checksums are from restic's v0.19.1 release SHA256SUMS, checked 2026-10-08; the darwin_arm64 one
was corrected on 2026-10-09 (the draft had darwin_amd64's). Nothing is installed system-wide.
"""

from __future__ import annotations

import bz2
import hashlib
import platform
import subprocess
import sys
import urllib.request
from pathlib import Path

VERSION = "0.19.1"
PINNED = {
    ("linux", "x86_64"): ("linux_amd64", "f415415624dcc452f2a02b8c33641791a8c6d6d3b65bbb3543fcf9a25151585c"),
    ("linux", "aarch64"): ("linux_arm64", "a5f64aaab53d51e311fa3829124c5b703f2d14cf187d8640b6be3b2b49376465"),
    ("darwin", "arm64"): ("darwin_arm64", "7be0a144ccc377880f294204aa271d76e4b79554b42a751151d425ce6ebac143"),
}
REPO = Path(__file__).resolve().parents[1]
TARGET = REPO / ".cache" / "bin" / "restic"


def installed() -> bool:
    if not TARGET.is_file():
        return False
    out = subprocess.run([str(TARGET), "version"], capture_output=True, text=True).stdout
    return f"restic {VERSION} " in out


def main() -> int:
    key = (platform.system().lower(), platform.machine().lower())
    if key not in PINNED:
        print(f"fetch_restic: no pinned restic for {key}", file=sys.stderr)
        return 1
    if installed():
        print(f"restic {VERSION} already in {TARGET.relative_to(REPO)}")
        return 0
    name, expected = PINNED[key]
    url = f"https://github.com/restic/restic/releases/download/v{VERSION}/restic_{VERSION}_{name}.bz2"
    with urllib.request.urlopen(url, timeout=120) as response:
        data = response.read()
    if hashlib.sha256(data).hexdigest() != expected:
        print("fetch_restic: the download does not match its pinned SHA-256", file=sys.stderr)
        return 1
    TARGET.parent.mkdir(parents=True, exist_ok=True)
    tmp = TARGET.with_suffix(".part")
    tmp.write_bytes(bz2.decompress(data))
    tmp.chmod(0o755)
    tmp.replace(TARGET)
    print(f"restic {VERSION} -> {TARGET.relative_to(REPO)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
