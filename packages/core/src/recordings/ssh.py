"""SSH to the NAS (spec §15.1), shared by the rsync mirror, the NAS free-space check, and restic's
SFTP backend when SFTP is the fallback.

The key and the NAS's host key come from secret files (§5). Host keys are checked strictly against
that file, and nothing is written to ~/.ssh, because the containers' file systems are read-only.
"""

from __future__ import annotations

import shlex
import subprocess
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from recordings.config import Config, ConfigError, secret_path


def ssh_options(key: Path | None, known_hosts: Path | None, port: int | None) -> list[str]:
    """Options that ssh, sftp and `rsync -e ssh` all read. The port is `-o Port=`, because sftp's
    `-p` means "keep the times" and its port flag is `-P`. Host keys are always checked strictly,
    against the given file when there is one; callers that reach the NAS require it
    (require_known_hosts)."""
    # ConnectTimeout: a NAS that is off is given up on in seconds, not after TCP's own timeout
    # (minutes), which doctor and every backup's free-space check would otherwise wait out.
    out = ["-o", "BatchMode=yes", "-o", "ConnectTimeout=10", "-o", "ServerAliveInterval=60",
           "-o", "ServerAliveCountMax=240"]
    if key is not None:
        out += ["-i", str(key), "-o", "IdentitiesOnly=yes"]
    out += ["-o", "StrictHostKeyChecking=yes"]  # always: a host's ssh_config must not relax it
    if known_hosts is not None:
        # Only the pinned file is trusted: not the system's /etc/ssh/ssh_known_hosts either.
        out += ["-o", f"UserKnownHostsFile={known_hosts}", "-o", "GlobalKnownHostsFile=/dev/null"]
    if port is not None:
        out += ["-o", f"Port={port}"]
    return out


@dataclass(frozen=True)
class Nas:
    ssh: str | None  # an ssh destination, user@host: the mirror's account (§15.1)
    port: int | None
    key: Path | None
    known_hosts: Path | None

    def options(self) -> list[str]:
        return ssh_options(self.key, self.known_hosts, self.port)


def nas_from_config(cfg: Config, environ: Mapping[str, str]) -> Nas:
    section = cfg.data.get("nas", {})
    ssh, port = section.get("ssh"), section.get("port")
    if ssh is not None and (not isinstance(ssh, str) or not ssh.strip()):
        raise ConfigError('[nas] ssh must be an ssh destination, such as "mirror@nas"')
    if port is not None and (not isinstance(port, int) or isinstance(port, bool) or not 0 < port < 65536):
        raise ConfigError("[nas] port must be a port number")
    nas = Nas(ssh=ssh, port=port, key=secret_path("nas_ssh_key", environ),
              known_hosts=secret_path("nas_known_hosts", environ))
    if ssh is not None:
        require_known_hosts(nas)
    return nas


def require_known_hosts(nas: Nas) -> None:
    """Every use of ssh to the NAS pins its host key to the secret file (§15.1): the mirror sends
    the plaintext archive, private recordings included, over these options."""
    if nas.known_hosts is None:
        raise ConfigError("ssh to the NAS needs its host key: give RECORDINGS_NAS_KNOWN_HOSTS_FILE, "
                          "the path to a file of known hosts")


def _avail_kib(text: str) -> int | None:
    """The Avail column of `df -Pk` or of sftp's `df`, both in KiB: the number under the header
    word that starts with "Avail", on the line after that header."""
    lines = text.splitlines()
    for i, line in enumerate(lines[:-1]):
        words = line.split()
        column = next((j for j, word in enumerate(words) if word.startswith("Avail")), None)
        if column is not None:
            try:
                return int(lines[i + 1].split()[column])
            except (IndexError, ValueError):
                return None
    return None


def _sftp_quote(path: str) -> str:
    return '"' + path.replace("\\", "\\\\").replace('"', '\\"') + '"'


def nas_free_bytes(nas: Nas, path: str, *, runner=subprocess.run) -> int | None:
    """Free bytes on the NAS at `path`; None when [nas] ssh isn't set or neither `df` answers.

    First `df -Pk` over ssh, for an account with a shell. Then sftp's own `df`, which an SFTP-only
    account answers through the statvfs@openssh.com extension (sftp(1)): the NAS accounts are not
    admins, so they have no shell. `path` is as that account sees it; over SFTP on Synology, paths
    start at the shared folders, not /volume1.
    """
    if not nas.ssh or any(ord(c) < 32 for c in path):  # a newline would start another sftp command
        return None
    attempts = (
        (["ssh", *nas.options(), nas.ssh, "df", "-Pk", "--", shlex.quote(path)], None),
        (["sftp", "-b", "-", *nas.options(), nas.ssh], f"df {_sftp_quote(path)}\n"),
    )
    for cmd, stdin in attempts:
        try:
            proc = runner(cmd, input=stdin, capture_output=True, text=True, timeout=60)
        except (OSError, subprocess.TimeoutExpired):
            continue
        kib = _avail_kib(proc.stdout) if proc.returncode == 0 else None
        if kib is not None:
            return kib * 1024
    return None
