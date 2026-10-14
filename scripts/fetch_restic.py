"""Install the pinned restic into .cache/bin/restic, checked against its release checksum.

    uv run python scripts/fetch_restic.py        (or: make restic)

For CI and the Mac. The Docker image installs the same version itself (docker/Dockerfile), and the
runbook's NAS spike downloads it with the same checksums; tests check that all three agree. The
checksums are from restic's v0.19.1 release SHA256SUMS, checked 2026-10-08; the darwin_arm64 one
was corrected on 2026-10-09 (the draft had darwin_amd64's). Nothing is installed system-wide.

The decompressed binary is pinned too (BINARY_SHA256), and so is what `installed()` accepts: a file
already in .cache/bin is run only if its own SHA-256 matches, not because it prints a version.
Those hashes were computed on 2026-10-14 by downloading each release archive, checking it against
the SHA-256 in PINNED, and hashing its bz2-decompressed content. The downloads are size-capped.
"""

from __future__ import annotations

import bz2
import hashlib
import platform
import sys
import urllib.request
from pathlib import Path

VERSION = "0.19.1"
PINNED = {
    ("linux", "x86_64"): ("linux_amd64", "f415415624dcc452f2a02b8c33641791a8c6d6d3b65bbb3543fcf9a25151585c"),
    ("linux", "aarch64"): ("linux_arm64", "a5f64aaab53d51e311fa3829124c5b703f2d14cf187d8640b6be3b2b49376465"),
    ("darwin", "arm64"): ("darwin_arm64", "7be0a144ccc377880f294204aa271d76e4b79554b42a751151d425ce6ebac143"),
}
BINARY_SHA256 = {  # the decompressed restic, by the first element of each PINNED value
    "linux_amd64": "20d4142678d0d95ec11a4759def1b73fd9190abc9ca19e4b62d067c0b387e639",
    "linux_arm64": "2fb45ac6f9071b6f20eb883953a188f9e7c7cb6bbe43c67a2e47ada4e85ee7f0",
    "darwin_arm64": "06582569ff2f10e1935a6f12187c76db02f0bae99e6e54098f2cdc374766d768",
}
MAX_DOWNLOAD = 32 * 1024 * 1024  # the archives are about 10 MB
MAX_BINARY = 128 * 1024 * 1024  # the binaries are about 30 MB
REPO = Path(__file__).resolve().parents[1]
TARGET = REPO / ".cache" / "bin" / "restic"


def installed(binary_sha256: str) -> bool:
    """The file in .cache/bin is the pinned binary itself, by hash: nothing is run to find out."""
    try:
        return hashlib.sha256(TARGET.read_bytes()).hexdigest() == binary_sha256
    except OSError:
        return False


def main() -> int:
    key = (platform.system().lower(), platform.machine().lower())
    if key not in PINNED:
        print(f"fetch_restic: no pinned restic for {key}", file=sys.stderr)
        return 1
    name, expected = PINNED[key]
    if installed(BINARY_SHA256[name]):
        print(f"restic {VERSION} already in {TARGET.relative_to(REPO)}")
        return 0
    url = f"https://github.com/restic/restic/releases/download/v{VERSION}/restic_{VERSION}_{name}.bz2"
    with urllib.request.urlopen(url, timeout=120) as response:
        data = response.read(MAX_DOWNLOAD + 1)
    if len(data) > MAX_DOWNLOAD or hashlib.sha256(data).hexdigest() != expected:
        print("fetch_restic: the download is too large or does not match its pinned SHA-256",
              file=sys.stderr)
        return 1
    binary = bz2.BZ2Decompressor().decompress(data, MAX_BINARY + 1)
    if len(binary) > MAX_BINARY or hashlib.sha256(binary).hexdigest() != BINARY_SHA256[name]:
        print("fetch_restic: the decompressed binary does not match its pinned SHA-256",
              file=sys.stderr)
        return 1
    TARGET.parent.mkdir(parents=True, exist_ok=True)
    tmp = TARGET.with_suffix(".part")
    tmp.write_bytes(binary)
    tmp.chmod(0o755)
    tmp.replace(TARGET)
    print(f"restic {VERSION} -> {TARGET.relative_to(REPO)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
