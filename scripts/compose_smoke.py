"""Run the real docker/compose.yml once, against temporary folders: init, the backup service's
jobs and /healthz.

    uv run python scripts/compose_smoke.py        (or: make deploy-smoke)

It proves, in the hardened containers and as this machine's user:
- every bind compose.yml needs exists, because create_host_path is false
- the image's user has this machine's UID and GID and a passwd entry, so ssh runs
  (`ssh -G nas` exits 255, "No user exists for uid", without one)
- `recordings init` works in `web`
- in `backup`, restic initialises a repository, backs up and passes the restore test, and rsync
  mirrors the archive, all for real
- `recordings doctor --role backup` finds no problem there: it sees the read-only archive mount,
  and the uid's passwd entry
- the web app answers /healthz, and passes the image's health check in the hardened container,
  which `make deploy`'s `--wait` relies on

The repository and the mirror are folders under the restore mount, so nothing contacts a NAS, and
the secret files are placeholders. It never touches docker/deploy.env or a real archive. The
network transports (rest-server, SFTP, rsync over ssh) are proven against the NAS itself by the
runbook's spike (docs/runbooks/first-run.md, step 1b).
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
PORT = 18000
SECRETS = (
    "restic_password",
    "rest_password",
    "nas_cacert",
    "nas_ssh_key",
    "nas_known_hosts",
    "ntfy_topic",
    "deadman_url",
)
CONFIG = """\
[archive]
writer_id = "smoke"

[backup]
repository = "/restore/smoke-repo"
extra_paths = ["/backup-extra/deploy.env"]
restore_scratch = "/restore"
cache_dir = "/state/cache/restic"

[mirror]
target = "/restore/smoke-mirror/"
"""
BACKUP_COMMANDS = (
    ("backup", "init"),
    ("backup", "run"),
    ("backup", "restore-test"),
    ("mirror",),
    ("doctor", "--role", "backup"),
)


class SmokeFailed(Exception):
    pass


def compose_run(compose: list[str], env: dict[str, str], *args: str, quiet: bool = False) -> None:
    proc = subprocess.run(
        [*compose, *args], env=env, capture_output=quiet, text=True, check=False
    )  # exit code read below
    if proc.returncode != 0:
        if quiet:
            print(proc.stderr, file=sys.stderr)
        raise SmokeFailed(f"`docker compose {' '.join(args)}` exited {proc.returncode}")


def wait_for_healthz(compose: list[str], env: dict[str, str]) -> None:
    deadline = time.monotonic() + 90
    while True:
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{PORT}/healthz", timeout=3) as r:
                if json.loads(r.read()) == {"ok": True, "demo": False}:
                    return
        except OSError:
            pass
        if time.monotonic() > deadline:
            subprocess.run([*compose, "logs", "web"], env=env, check=False)  # diagnostics only
            raise SmokeFailed("/healthz never answered")
        time.sleep(2)


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="recordings-smoke-") as tmp:
        base = Path(tmp)
        for name in ("archive", "state", "restore", "restore/smoke-mirror", "secrets"):
            (base / name).mkdir()
        for name in SECRETS:
            # Mode 600, as the runbook makes them. The ping URL has the form alerts and doctor check
            # (https): a closed port on the container's own loopback, so a ping fails at once and
            # nothing leaves the machine.
            value = (
                "https://127.0.0.1:9/smoke" if name == "deadman_url" else "smoke-test-placeholder"
            )
            path = base / "secrets" / name
            path.write_text(value + "\n", encoding="utf-8")
            path.chmod(0o600)
        (base / "config.toml").write_text(CONFIG, encoding="utf-8")
        deploy = base / "deploy.env"
        deploy.write_text(
            "\n".join(
                [
                    f"RECORDINGS_ARCHIVE_HOST={base / 'archive'}",
                    f"RECORDINGS_STATE_HOST={base / 'state'}",
                    f"RECORDINGS_RESTORE_HOST={base / 'restore'}",
                    f"RECORDINGS_SECRETS_HOST={base / 'secrets'}",
                    f"RECORDINGS_CONFIG_HOST={base / 'config.toml'}",
                    f"RECORDINGS_DEPLOY_ENV_HOST={deploy}",
                    "RECORDINGS_BIND=127.0.0.1",
                    f"RECORDINGS_PORT={PORT}",
                    f"RECORDINGS_UID={os.getuid()}",
                    f"RECORDINGS_GID={os.getgid()}",
                ]
            )
            + "\n",
            encoding="utf-8",
        )
        # Compose lets a shell variable beat --env-file, so an exported RECORDINGS_* would point the
        # smoke at real data: drop them all, as the root conftest does, and set the tag.
        env = {
            name: value
            for name, value in os.environ.items()
            if not name.startswith(("RECORDINGS_", "RESTIC_"))
        }
        env["RECORDINGS_IMAGE_TAG"] = "smoke"
        compose = [
            "docker",
            "compose",
            "-p",
            "recordings-smoke",
            "--env-file",
            str(deploy),
            "-f",
            str(REPO / "docker" / "compose.yml"),
        ]
        try:
            compose_run(compose, env, "build", "web")
            compose_run(compose, env, "run", "--rm", "web", "recordings", "init", "--json")
            compose_run(compose, env, "run", "--rm", "backup", "ssh", "-G", "nas", quiet=True)
            for command in BACKUP_COMMANDS:
                compose_run(compose, env, "run", "--rm", "backup", "recordings", *command, "--json")
            compose_run(compose, env, "up", "-d", "--wait", "--wait-timeout", "120", "web")
            wait_for_healthz(compose, env)
            for path, what in (
                (base / "archive" / "archive.json", "recordings init wrote no sentinel"),
                (base / "restore" / "smoke-repo" / "config", "restic made no repository"),
                (base / "restore" / "smoke-mirror" / "archive.json", "the mirror copied nothing"),
            ):
                if not path.is_file():
                    raise SmokeFailed(what)
            if list((base / "restore").glob("restore-*")):
                raise SmokeFailed("the restore test left its scratch copy behind")
        except SmokeFailed as exc:
            print(f"compose_smoke: {exc}", file=sys.stderr)
            return 1
        finally:
            # --rmi all removes recordings:smoke (an image with a custom tag, which --rmi local
            # keeps), so it never shows among `docker image ls recordings`' rollback tags.
            subprocess.run(
                [*compose, "down", "-v", "--rmi", "all"], env=env, check=False
            )  # cleanup
        print(
            "compose smoke: init, ssh, backup, restore test and mirror ran, and web answered "
            "/healthz, as this machine's user"
        )
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
